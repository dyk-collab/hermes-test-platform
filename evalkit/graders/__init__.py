"""Graders turn a run's trajectory into a pass/fail + score on one dimension.

Each grader is registered by a ``kind`` string (matching the ``kind`` field of a
grader spec in datasets/tasks.yaml). ``grade_case`` runs every grader attached to
a case and combines them (all must pass for the case to pass).

See EVAL_PLATFORM_PLAN.md §5.
"""

from __future__ import annotations

from .base import GradeResult, Grader, get_grader, grade_case, register
from . import tool_use as _tool_use  # noqa: F401 - registers the grader
from . import timing as _timing  # noqa: F401 - registers the grader
from . import llm_judge as _llm_judge  # noqa: F401 - registers the grader

__all__ = ["GradeResult", "Grader", "get_grader", "grade_case", "register"]
