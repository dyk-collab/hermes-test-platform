"""Run a single eval task through the hermes CLI and collect the result.

Flow (see EVAL_PLATFORM_PLAN.md §6):
  1. ``hermes chat -q "PROMPT" -Q ...``  → stdout = answer, stderr = `session_id: <id>`
  2. measure subprocess wall-clock (ended_at is null for oneshot runs)
  3. ``hermes sessions export``           → full trajectory + metrics
"""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from typing import Optional

from .config import resolve_hermes
from .session import Session, export_session

_SESSION_ID_RE = re.compile(r"session_id:\s*(\S+)")

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
) -> RunResult:
    """Run one prompt and return a RunResult with the parsed session attached.

    The three boolean flags map to hermes CLI switches (defaults keep the prior
    headless behaviour = all on); runner presets can toggle them per run.
    """
    hermes = hermes_bin or resolve_hermes()

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
    if source:
        cmd += ["--source", source]
    if max_turns is not None:
        cmd += ["--max-turns", str(max_turns)]

    result = RunResult(case_id=case_id, prompt=prompt)

    t0 = time.monotonic()
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        result.wall_clock = time.monotonic() - t0
        result.error = f"hermes run timed out after {timeout}s"
        return result
    result.wall_clock = time.monotonic() - t0

    result.answer = proc.stdout.strip()
    result.stderr = proc.stderr
    result.returncode = proc.returncode

    if proc.returncode != 0:
        result.error = f"hermes exited {proc.returncode}: {proc.stderr.strip()[:500]}"
        return result

    m = _SESSION_ID_RE.search(proc.stderr)
    if not m:
        result.error = "could not find `session_id:` in hermes stderr"
        return result
    result.session_id = m.group(1)

    try:
        result.session = export_session(result.session_id, hermes_bin=hermes)
    except Exception as exc:  # noqa: BLE001 - surface any export/parse failure
        result.error = f"session export failed: {exc}"

    return result
