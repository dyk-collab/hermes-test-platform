"""timing grader — assert wall-clock / turn / token / cost thresholds.

Spec fields (all optional; each present field is an upper bound that must hold):
  max_seconds:    float   runner wall-clock (preferred) must be ≤ this
  max_api_calls:  int     session.api_call_count ≤ this
  max_tool_calls: int     session.tool_call_count ≤ this
  max_output_tokens: int  session.output_tokens ≤ this
  max_total_tokens:  int  input+output+reasoning ≤ this
  max_cost_usd:   float   estimated_cost_usd ≤ this (skipped if cost unknown)

Data source: runner wall-clock + export counts/tokens/cost. See PLAN §5.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import GradeResult, register

if TYPE_CHECKING:
    from ..runner import RunResult


@register("timing")
def timing_grader(run: "RunResult", spec: dict[str, Any]) -> GradeResult:
    session = run.session
    checks: list[str] = []
    failures: list[str] = []
    details: dict[str, Any] = {}

    # ---- wall-clock (from runner; fall back to export timestamps) ----
    max_seconds = spec.get("max_seconds")
    if max_seconds is not None:
        elapsed = run.wall_clock
        if elapsed is None and session is not None:
            elapsed = session.duration_seconds()
        details["wall_clock"] = elapsed
        if elapsed is None:
            failures.append("max_seconds set but no timing available")
        elif elapsed > max_seconds:
            failures.append(f"took {elapsed:.1f}s > {max_seconds}s")
        else:
            checks.append(f"{elapsed:.1f}s ≤ {max_seconds}s")

    # ---- export-derived metrics (need a session) ----
    def _bound(spec_key: str, attr: str, label: str, getter=None) -> None:
        limit = spec.get(spec_key)
        if limit is None:
            return
        if session is None:
            failures.append(f"{spec_key} set but no session")
            return
        val = getter(session) if getter else getattr(session, attr)
        details[attr if not getter else spec_key] = val
        if val is None:
            failures.append(f"{spec_key} set but {label} unavailable")
        elif val > limit:
            failures.append(f"{label} {val} > {limit}")
        else:
            checks.append(f"{label} {val} ≤ {limit}")

    _bound("max_api_calls", "api_call_count", "api_calls")
    _bound("max_tool_calls", "tool_call_count", "tool_calls")
    _bound("max_output_tokens", "output_tokens", "output_tokens")
    _bound(
        "max_total_tokens", "", "total_tokens",
        getter=lambda s: s.input_tokens + s.output_tokens + s.reasoning_tokens,
    )

    # ---- cost (skip gracefully when provider reports unknown) ----
    max_cost = spec.get("max_cost_usd")
    if max_cost is not None:
        if session is None:
            failures.append("max_cost_usd set but no session")
        elif session.cost_status == "unknown" or session.estimated_cost_usd is None:
            checks.append("cost unknown (skipped)")
        elif session.estimated_cost_usd > max_cost:
            failures.append(f"cost ${session.estimated_cost_usd} > ${max_cost}")
            details["estimated_cost_usd"] = session.estimated_cost_usd
        else:
            checks.append(f"cost ${session.estimated_cost_usd} ≤ ${max_cost}")

    if not checks and not failures:
        return GradeResult(
            "timing", False,
            reason="timing grader had no thresholds (set max_seconds/max_*_tokens/etc.)",
        )

    passed = not failures
    reason = "; ".join(failures) if failures else "; ".join(checks)
    return GradeResult("timing", passed, score=1.0 if passed else 0.0, reason=reason, details=details)
