"""llm_judge grader — score the agent's final answer with a model as judge.

Mechanism (PLAN §5): re-run ``hermes chat -q "<judge prompt>" -Q --ignore-rules``
as an impartial judge. We hand it the original task, the agent's final answer, and
a rubric, and force it to emit ONLY a JSON object ``{score, pass, reason}``.

Spec fields:
  rubric:         str    grading criteria (required)
  pass_threshold: number 0..10; score ≥ threshold ⇒ pass (default 7)
  model:          str    optional judge-model override (default: hermes default;
                         a cheap model here keeps judging costs down)
  judge_timeout:  float  seconds (default 120)

NOTE: unlike tool_call/timing, this grader spends model tokens — so `grade` is no
longer free when a case uses it. run/grade are still separate (you can swap the
rubric and re-judge without re-running the agent), but each re-judge calls a model.
"""

from __future__ import annotations

import json
import re
import subprocess
from typing import TYPE_CHECKING, Any

from ..config import resolve_hermes
from .base import GradeResult, register

if TYPE_CHECKING:
    from ..runner import RunResult

_JSON_OBJ_RE = re.compile(r"\{.*\}", re.DOTALL)

_JUDGE_TEMPLATE = """You are an impartial grader. Score how well the ANSWER satisfies the TASK \
against the RUBRIC.

TASK:
{task}

ANSWER:
{answer}

RUBRIC:
{rubric}

Respond with ONLY a single JSON object, no prose, no code fences:
{{"score": <integer 0-10>, "pass": <true|false>, "reason": "<one short sentence>"}}
The "pass" field must reflect whether score >= {threshold}."""


def _extract_json(text: str) -> dict[str, Any] | None:
    """Pull the first {...} block out of judge stdout and parse it."""
    m = _JSON_OBJ_RE.search(text)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
        return obj if isinstance(obj, dict) else None
    except json.JSONDecodeError:
        return None


def _run_judge(prompt: str, model: str | None, timeout: float) -> tuple[str, str]:
    """Call hermes headless as the judge; return (stdout, stderr)."""
    hermes = resolve_hermes()
    cmd = [hermes, "chat", "-q", prompt, "-Q", "--ignore-rules"]
    if model:
        cmd += ["-m", model]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if proc.returncode != 0:
        raise RuntimeError(f"judge hermes exited {proc.returncode}: {proc.stderr.strip()[:300]}")
    return proc.stdout.strip(), proc.stderr


@register("llm_judge")
def llm_judge_grader(run: "RunResult", spec: dict[str, Any]) -> GradeResult:
    rubric = spec.get("rubric")
    if not rubric:
        return GradeResult("llm_judge", False, reason="llm_judge spec missing 'rubric'")

    answer = run.answer or (run.session.final_answer() if run.session else "")
    if not answer.strip():
        return GradeResult("llm_judge", False, reason="no answer to judge (empty agent output)")

    threshold = float(spec.get("pass_threshold", 7))
    model = spec.get("model")
    timeout = float(spec.get("judge_timeout", 120))

    judge_prompt = _JUDGE_TEMPLATE.format(
        task=run.prompt, answer=answer, rubric=rubric, threshold=threshold
    )

    try:
        stdout, _ = _run_judge(judge_prompt, model, timeout)
    except subprocess.TimeoutExpired:
        return GradeResult("llm_judge", False, reason=f"judge timed out after {timeout}s")
    except Exception as exc:  # noqa: BLE001 - surface judge failures as a failed grade
        return GradeResult("llm_judge", False, reason=f"judge error: {exc}")

    verdict = _extract_json(stdout)
    if verdict is None:
        return GradeResult(
            "llm_judge", False,
            reason="could not parse judge JSON",
            details={"judge_raw": stdout[:500]},
        )

    raw_score = verdict.get("score")
    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        return GradeResult(
            "llm_judge", False,
            reason=f"judge returned non-numeric score: {raw_score!r}",
            details={"judge_raw": stdout[:500]},
        )

    # Trust the numeric score against our threshold; the judge's own "pass" is advisory.
    passed = score >= threshold
    reason = str(verdict.get("reason", "")).strip()
    return GradeResult(
        "llm_judge", passed,
        score=score / 10.0,  # normalize to 0..1 like other graders
        reason=f"score {score:g}/10 (≥{threshold:g} ⇒ {'pass' if passed else 'fail'})"
        + (f" — {reason}" if reason else ""),
        details={"score_10": score, "threshold": threshold, "judge_reason": reason,
                 "judge_pass": verdict.get("pass"), "model": model},
    )
