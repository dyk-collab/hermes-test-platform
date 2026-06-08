"""tool_call grader — assert the agent invoked the expected tools correctly.

Spec fields (all optional except you'll usually set one of must_*):
  must_call:      str | [str]   every named tool must appear at least once
  must_call_any:  [str]         at least one of these must appear
  must_not_call:  str | [str]   none of these may appear
  args_match:     dict          some call to a must_call'd tool has args ⊇ this
                                (subset match on parsed function.arguments)
  expect_success: bool          the matched tool call's result must (not) be an error

Data source: Session.all_tool_calls() + tool_result_for(). See PLAN §5.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .base import GradeResult, register

if TYPE_CHECKING:
    from ..runner import RunResult
    from ..session import Session, ToolCall

# Heuristic markers that a role=tool message reported a failure.
_ERROR_MARKERS = ("error", "traceback", "failed", "exception", "not found", "denied")


def _as_list(v: Any) -> list[str]:
    if v is None:
        return []
    if isinstance(v, str):
        return [v]
    return [str(x) for x in v]


def _is_subset(want: dict[str, Any], got: dict[str, Any]) -> bool:
    """True if every key/value in `want` is present and equal in `got`."""
    for k, v in want.items():
        if k not in got or got[k] != v:
            return False
    return True


def _result_is_error(content: str) -> bool:
    low = content.lower()
    return any(marker in low for marker in _ERROR_MARKERS)


@register("tool_call")
def tool_call_grader(run: "RunResult", spec: dict[str, Any]) -> GradeResult:
    session: "Session | None" = run.session
    if session is None:
        return GradeResult("tool_call", False, reason="no session (run failed or export missing)")

    calls = session.all_tool_calls()
    names = [c.name for c in calls]
    name_set = set(names)
    checks: list[str] = []  # human-readable trace of what was asserted

    must_call = _as_list(spec.get("must_call"))
    must_call_any = _as_list(spec.get("must_call_any"))
    must_not_call = _as_list(spec.get("must_not_call"))
    args_match = spec.get("args_match")
    expect_success = spec.get("expect_success")

    # must_call: each named tool present
    for tool in must_call:
        if tool not in name_set:
            return GradeResult(
                "tool_call", False,
                reason=f"expected tool {tool!r} was not called (called: {names or 'none'})",
                details={"called": names},
            )
        checks.append(f"called {tool}")

    # must_call_any: at least one present
    if must_call_any and not (name_set & set(must_call_any)):
        return GradeResult(
            "tool_call", False,
            reason=f"none of {must_call_any} were called (called: {names or 'none'})",
            details={"called": names},
        )
    if must_call_any:
        checks.append(f"called one of {must_call_any}")

    # must_not_call: none present
    for tool in must_not_call:
        if tool in name_set:
            return GradeResult(
                "tool_call", False,
                reason=f"forbidden tool {tool!r} was called",
                details={"called": names},
            )
    if must_not_call:
        checks.append(f"avoided {must_not_call}")

    # args_match (+ optional expect_success): find a matching call among the
    # must_call'd tools (or any call if must_call wasn't given).
    if args_match is not None:
        candidates = [c for c in calls if (not must_call or c.name in must_call)]
        matched: "ToolCall | None" = None
        for c in candidates:
            if _is_subset(args_match, c.arguments):
                matched = c
                break
        if matched is None:
            return GradeResult(
                "tool_call", False,
                reason=f"no tool call had arguments ⊇ {args_match}",
                details={"calls": [{"name": c.name, "arguments": c.arguments} for c in candidates]},
            )
        checks.append(f"{matched.name} args matched {args_match}")

        if expect_success is not None:
            res = session.tool_result_for(matched.id)
            content = (res.content if res else "") or ""
            is_err = _result_is_error(content)
            ok = (not is_err) if expect_success else is_err
            if not ok:
                state = "errored" if is_err else "succeeded"
                return GradeResult(
                    "tool_call", False,
                    reason=f"expected tool result success={expect_success} but it {state}",
                    details={"result_preview": content[:200]},
                )
            checks.append(f"result success={expect_success}")

    elif expect_success is not None:
        # No args_match: judge success on the first call of the (first) must_call tool.
        target = must_call[0] if must_call else (names[0] if names else None)
        if target is None:
            return GradeResult("tool_call", False, reason="expect_success set but no tool was called")
        call = next(c for c in calls if c.name == target)
        res = session.tool_result_for(call.id)
        content = (res.content if res else "") or ""
        is_err = _result_is_error(content)
        ok = (not is_err) if expect_success else is_err
        if not ok:
            state = "errored" if is_err else "succeeded"
            return GradeResult(
                "tool_call", False,
                reason=f"expected {target} result success={expect_success} but it {state}",
                details={"result_preview": content[:200]},
            )
        checks.append(f"{target} result success={expect_success}")

    if not checks:
        return GradeResult(
            "tool_call", False,
            reason="tool_call grader had no assertions (set must_call/must_call_any/etc.)",
        )

    return GradeResult(
        "tool_call", True, score=1.0,
        reason="; ".join(checks),
        details={"called": names},
    )
