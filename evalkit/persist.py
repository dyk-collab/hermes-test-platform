"""Persistence helpers shared by the CLI and the web service.

Layout of a run directory (PLAN §3):
    runs/<run-id>/
      manifest.json
      raw/<case-id>.json      run metadata + stored `hermes sessions export`
      graded/<case-id>.json   per-case grade detail
      report.json / report.md
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .dataset import Case
from .runner import RunResult
from .session import Session


def metrics(run: RunResult) -> dict[str, Any]:
    """Flatten the metrics a report/UI needs out of a RunResult's session."""
    s = run.session
    if s is None:
        return {"wall_clock": run.wall_clock}
    return {
        "wall_clock": run.wall_clock,
        "tool_calls": s.tool_call_count,
        "api_calls": s.api_call_count,
        "input_tokens": s.input_tokens,
        "output_tokens": s.output_tokens,
        "reasoning_tokens": s.reasoning_tokens,
        "total_tokens": s.input_tokens + s.output_tokens + s.reasoning_tokens,
        "cost": s.estimated_cost_usd,
        "cost_status": s.cost_status,
        "model": s.model,
    }


def save_raw(run_dir: Path, case: Case, run: RunResult) -> None:
    """Persist one case's run (export + graders) so `grade` is self-contained."""
    (run_dir / "raw").mkdir(parents=True, exist_ok=True)
    payload = {
        "case_id": case.id,
        "type": case.type,
        "prompt": case.prompt,
        "toolsets": case.toolsets,
        "graders": case.graders,
        "answer": run.answer,
        "session_id": run.session_id,
        "wall_clock": run.wall_clock,
        "returncode": run.returncode,
        "retry_count": run.retry_count,
        "error": run.error,
        "diagnostics": run.diagnostics[-20000:] if run.diagnostics else "",
        "stderr": run.stderr[-2000:] if run.stderr else "",
        "session": run.session.raw if run.session else None,
    }
    (run_dir / "raw" / f"{case.id}.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2)
    )


def load_raw(path: Path) -> tuple[dict[str, Any], RunResult]:
    """Reconstruct a RunResult (with Session) from a stored raw file."""
    d = json.loads(path.read_text())
    session = Session.from_export_dict(d["session"]) if d.get("session") else None
    run = RunResult(
        case_id=d["case_id"],
        prompt=d.get("prompt", ""),
        answer=d.get("answer", ""),
        session_id=d.get("session_id"),
        wall_clock=d.get("wall_clock"),
        returncode=d.get("returncode", 0),
        retry_count=d.get("retry_count", 0),
        session=session,
        stderr=d.get("stderr", ""),
        error=d.get("error"),
        diagnostics=d.get("diagnostics", ""),
    )
    return d, run
