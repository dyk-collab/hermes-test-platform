"""Grader interface, result type, and registry.

A grader is a callable that inspects a :class:`~evalkit.runner.RunResult` against
a spec (a dict from the dataset, e.g. ``{"kind": "tool_call", "must_call": ...}``)
and returns a :class:`GradeResult`.

Graders register themselves by ``kind`` via the :func:`register` decorator so the
dataset can refer to them by name. :func:`grade_case` runs all graders attached to
one case and aggregates them.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Callable

if TYPE_CHECKING:  # avoid a runtime import cycle (runner imports nothing from here)
    from ..runner import RunResult


@dataclass
class GradeResult:
    """Outcome of one grader on one case."""

    kind: str
    passed: bool
    score: float | None = None  # 0..1 (or grader-defined); None if not scored
    reason: str = ""
    details: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "passed": self.passed,
            "score": self.score,
            "reason": self.reason,
            "details": self.details,
        }


# A grader takes (run_result, spec_dict) and returns a GradeResult.
Grader = Callable[["RunResult", dict[str, Any]], GradeResult]

_REGISTRY: dict[str, Grader] = {}


def register(kind: str) -> Callable[[Grader], Grader]:
    """Decorator: register a grader function under ``kind``."""

    def deco(fn: Grader) -> Grader:
        if kind in _REGISTRY:
            raise ValueError(f"grader kind already registered: {kind!r}")
        _REGISTRY[kind] = fn
        return fn

    return deco


def get_grader(kind: str) -> Grader:
    try:
        return _REGISTRY[kind]
    except KeyError:
        known = ", ".join(sorted(_REGISTRY)) or "(none)"
        raise KeyError(f"unknown grader kind {kind!r}; registered: {known}")


def grade_case(run: "RunResult", grader_specs: list[dict[str, Any]]) -> list[GradeResult]:
    """Run every grader spec attached to a case against the run result.

    A spec that fails to run (bad config, grader error) becomes a failed
    GradeResult rather than raising — one broken grader shouldn't sink the batch.
    """
    results: list[GradeResult] = []
    for spec in grader_specs:
        kind = spec.get("kind")
        if not kind:
            results.append(GradeResult("(missing-kind)", False, reason="grader spec has no 'kind'"))
            continue
        try:
            grader = get_grader(kind)
            results.append(grader(run, spec))
        except Exception as exc:  # noqa: BLE001 - turn grader crashes into a failed result
            results.append(GradeResult(kind, False, reason=f"grader error: {exc}"))
    return results
