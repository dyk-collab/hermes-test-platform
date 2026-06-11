"""Run a single eval task through the hermes CLI and collect the result.

Flow (see EVAL_PLATFORM_PLAN.md §6):
  1. ``hermes chat -q "PROMPT" -Q ...``  → stdout = answer, stderr = `session_id: <id>`
  2. measure subprocess wall-clock (ended_at is null for oneshot runs)
  3. ``hermes sessions export``           → full trajectory + metrics
"""

from __future__ import annotations

import os
import re
import signal
import subprocess
import time
import uuid
from dataclasses import dataclass
from threading import Event
from typing import Optional

from .config import resolve_hermes
from .session import Session, export_session, export_session_by_source

_SESSION_ID_RE = re.compile(r"session_id:\s*(\S+)")
_TOO_MANY_REQUESTS_RE = re.compile(r"too many requests", re.IGNORECASE)
MAX_TOO_MANY_REQUESTS_RETRIES = 3

# Defaults that make a run headless & reproducible. --ignore-rules keeps the
# eval env clean (no AGENTS.md/SOUL.md/memory injection); --source tool keeps
# eval sessions out of the user's normal session list.
DEFAULT_FLAGS = ["--yolo", "--accept-hooks", "--ignore-rules"]


@dataclass
class RunResult:
    case_id: str
    prompt: str
    answer: str = ""  # final response (hermes stdout)
    session_id: Optional[str] = None
    wall_clock: Optional[float] = None  # measured subprocess seconds
    returncode: int = 0
    session: Optional[Session] = None  # parsed export (None if export failed)
    stderr: str = ""
    error: Optional[str] = None  # populated on failure to run/parse
    diagnostics: str = ""  # session-scoped Hermes logs captured after a failure
    retry_count: int = 0  # retries after the initial attempt
    session_source: str = ""  # unique source tag used to recover interrupted sessions

    @property
    def ok(self) -> bool:
        """True if hermes ran cleanly, we got a session_id, and export parsed."""
        return (
            self.error is None
            and self.returncode == 0
            and self.session_id is not None
            and self.session is not None
        )


def run_prompt(
    prompt: str,
    *,
    case_id: str = "adhoc",
    model: Optional[str] = None,  # default: don't pass -m → use configured default
    provider: Optional[str] = None,
    toolsets: Optional[list[str]] = None,
    skills: Optional[list[str]] = None,
    profile: Optional[str] = None,
    source: str = "tool",
    max_turns: Optional[int] = None,
    timeout: float = 600.0,
    yolo: bool = True,
    accept_hooks: bool = True,
    ignore_rules: bool = True,
    hermes_bin: Optional[str] = None,
    cancel_event: Optional[Event] = None,
) -> RunResult:
    """Run one prompt, retrying model rate-limit failures up to three times."""
    retry_count = 0
    total_wall_clock = 0.0

    while True:
        result = _run_prompt_once(
            prompt,
            case_id=case_id,
            model=model,
            provider=provider,
            toolsets=toolsets,
            skills=skills,
            profile=profile,
            source=source,
            max_turns=max_turns,
            timeout=timeout,
            yolo=yolo,
            accept_hooks=accept_hooks,
            ignore_rules=ignore_rules,
            hermes_bin=hermes_bin,
            cancel_event=cancel_event,
        )
        total_wall_clock += result.wall_clock or 0.0
        result.wall_clock = total_wall_clock
        result.retry_count = retry_count

        if not _is_too_many_requests_failure(result):
            return result
        if retry_count >= MAX_TOO_MANY_REQUESTS_RETRIES:
            return result
        if cancel_event is not None and cancel_event.is_set():
            return result

        retry_count += 1
        if cancel_event is not None:
            if cancel_event.wait(timeout=2 ** (retry_count - 1)):
                return result
        else:
            time.sleep(2 ** (retry_count - 1))


def _run_prompt_once(
    prompt: str,
    *,
    case_id: str = "adhoc",
    model: Optional[str] = None,
    provider: Optional[str] = None,
    toolsets: Optional[list[str]] = None,
    skills: Optional[list[str]] = None,
    profile: Optional[str] = None,
    source: str = "tool",
    max_turns: Optional[int] = None,
    timeout: float = 600.0,
    yolo: bool = True,
    accept_hooks: bool = True,
    ignore_rules: bool = True,
    hermes_bin: Optional[str] = None,
    cancel_event: Optional[Event] = None,
) -> RunResult:
    """Run one prompt attempt and return its exported session and diagnostics.

    The three boolean flags map to hermes CLI switches (defaults keep the prior
    headless behaviour = all on); runner presets can toggle them per run.
    """
    hermes = hermes_bin or resolve_hermes()
    attempt_source = f"{source}-evalkit-{uuid.uuid4().hex}"

    flags: list[str] = []
    if yolo:
        flags.append("--yolo")
    if accept_hooks:
        flags.append("--accept-hooks")
    if ignore_rules:
        flags.append("--ignore-rules")

    cmd = [hermes, "chat", "-q", prompt, "-Q", *flags]
    if model:
        cmd += ["-m", model]
    if provider:
        cmd += ["--provider", provider]
    if toolsets:
        cmd += ["-t", ",".join(toolsets)]
    if skills:
        cmd += ["-s", ",".join(skills)]
    if profile:
        cmd += ["-p", profile]
    if attempt_source:
        cmd += ["--source", attempt_source]
    if max_turns is not None:
        cmd += ["--max-turns", str(max_turns)]

    result = RunResult(case_id=case_id, prompt=prompt)
    result.session_source = attempt_source
    if cancel_event is not None and cancel_event.is_set():
        result.error = "cancelled before start"
        return result

    t0 = time.monotonic()
    try:
        proc = subprocess.Popen(  # noqa: S603 - hermes binary is resolved/configured locally
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        while True:
            if cancel_event is not None and cancel_event.is_set():
                _terminate_process(proc)
                stdout, stderr = proc.communicate()
                result.wall_clock = time.monotonic() - t0
                result.answer = stdout.strip()
                result.stderr = stderr
                result.returncode = proc.returncode if proc.returncode is not None else -1
                result.error = "cancelled"
                _attach_failure_context(
                    result,
                    hermes_bin=hermes,
                    attempt_source=attempt_source,
                )
                return result
            try:
                stdout, stderr = proc.communicate(timeout=0.2)
                break
            except subprocess.TimeoutExpired:
                if time.monotonic() - t0 > timeout:
                    _terminate_process(proc)
                    stdout, stderr = proc.communicate()
                    result.wall_clock = time.monotonic() - t0
                    result.answer = stdout.strip()
                    result.stderr = stderr
                    result.returncode = proc.returncode if proc.returncode is not None else -1
                    result.error = f"hermes run timed out after {timeout}s"
                    _attach_failure_context(
                        result,
                        hermes_bin=hermes,
                        attempt_source=attempt_source,
                    )
                    return result
    except subprocess.TimeoutExpired:
        result.wall_clock = time.monotonic() - t0
        result.error = f"hermes run timed out after {timeout}s"
        _attach_failure_context(
            result,
            hermes_bin=hermes,
            attempt_source=attempt_source,
        )
        return result
    result.wall_clock = time.monotonic() - t0

    result.answer = stdout.strip()
    result.stderr = stderr
    result.returncode = proc.returncode

    m = _SESSION_ID_RE.search(stderr)
    if m:
        result.session_id = m.group(1)
        try:
            result.session = export_session(result.session_id, hermes_bin=hermes)
        except Exception as exc:  # noqa: BLE001 - retain both run and export context
            result.diagnostics = f"Session export failed: {exc}"

    if proc.returncode != 0:
        result.error = f"hermes exited {proc.returncode}: {stderr.strip()[:500]}"
        if result.session_id:
            logs = collect_session_logs(result.session_id, hermes_bin=hermes)
            result.diagnostics = "\n".join(
                part for part in (result.diagnostics, logs) if part
            )
        return result

    if not m:
        result.error = "could not find `session_id:` in hermes stderr"
        return result

    if result.session is None:
        result.error = result.diagnostics or "session export failed"

    return result


def _is_too_many_requests_failure(result: RunResult) -> bool:
    """True only for Hermes exit-1 failures caused by model rate limiting."""
    if result.returncode != 1:
        return False
    text = "\n".join(
        part for part in (result.error, result.stderr, result.diagnostics) if part
    )
    return bool(_TOO_MANY_REQUESTS_RE.search(text))


def _attach_failure_context(
    result: RunResult,
    *,
    hermes_bin: str,
    attempt_source: Optional[str] = None,
) -> None:
    """Best-effort context for interrupted runs that otherwise look blank."""
    if result.stderr and result.session_id is None:
        m = _SESSION_ID_RE.search(result.stderr)
        if m:
            result.session_id = m.group(1)

    if result.session_id is None and attempt_source:
        last_error: Exception | None = None
        for attempt in range(10):
            try:
                result.session = export_session_by_source(
                    attempt_source,
                    hermes_bin=hermes_bin,
                )
                result.session_id = result.session.id
                break
            except Exception as exc:  # noqa: BLE001 - diagnostic only
                last_error = exc
                if attempt < 9:
                    time.sleep(0.5)
        if result.session is None and last_error is not None:
            result.diagnostics = f"Session recovery by source failed: {last_error}"

    if result.session_id and result.session is None:
        try:
            result.session = export_session(result.session_id, hermes_bin=hermes_bin)
        except Exception as exc:  # noqa: BLE001 - diagnostic only
            result.diagnostics = f"Session export failed: {exc}"

    parts: list[str] = []
    if result.error:
        parts.append(result.error)
    if result.session_id:
        parts.append(f"session_id: {result.session_id}")
    else:
        parts.append("No session_id was captured before the process was stopped.")
    if result.stderr:
        parts.append("Hermes stderr:\n" + result.stderr.strip()[-4000:])
    if result.session_id:
        logs = collect_session_logs(result.session_id, hermes_bin=hermes_bin)
        if logs:
            parts.append("Hermes logs:\n" + logs[-4000:])
    result.diagnostics = "\n\n".join(
        part for part in (result.diagnostics, *parts) if part
    )


def collect_session_logs(
    session_id: str,
    *,
    hermes_bin: Optional[str] = None,
    lines: int = 200,
) -> str:
    """Return Hermes logs for one failed session without masking the run error."""
    hermes = hermes_bin or resolve_hermes()
    try:
        proc = subprocess.run(
            [hermes, "logs", "--session", session_id, "-n", str(lines)],
            capture_output=True,
            text=True,
            timeout=15,
        )
    except Exception as exc:  # noqa: BLE001 - diagnostics are best-effort
        return f"Could not collect Hermes logs: {exc}"

    output = "\n".join(part.strip() for part in (proc.stdout, proc.stderr) if part.strip())
    if proc.returncode != 0:
        return output or f"`hermes logs` exited {proc.returncode}"
    return output


def _terminate_process(proc: subprocess.Popen[str]) -> None:
    """Stop hermes and any child process it started for this eval case."""
    if proc.poll() is not None:
        return
    try:
        os.killpg(proc.pid, signal.SIGTERM)
    except Exception:
        proc.terminate()
    try:
        proc.wait(timeout=2)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except Exception:
            proc.kill()
