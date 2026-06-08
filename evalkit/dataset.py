"""Load an eval dataset (YAML) into typed Case objects.

Dataset format: a YAML list of cases. See datasets/tasks.yaml and PLAN §4.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml


@dataclass
class Case:
    id: str
    prompt: str
    type: str = "task"  # report grouping only
    toolsets: list[str] | None = None
    skills: list[str] | None = None
    model: str | None = None  # case-level override (usually set on the run cmd instead)
    provider: str | None = None
    graders: list[dict[str, Any]] = field(default_factory=list)

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> "Case":
        if "id" not in d:
            raise ValueError(f"case is missing required 'id': {d!r}")
        if "prompt" not in d:
            raise ValueError(f"case {d['id']!r} is missing required 'prompt'")
        return cls(
            id=str(d["id"]),
            prompt=str(d["prompt"]),
            type=str(d.get("type", "task")),
            toolsets=d.get("toolsets"),
            skills=d.get("skills"),
            model=d.get("model"),
            provider=d.get("provider"),
            graders=list(d.get("graders") or []),
        )


def load_dataset_text(text: str, *, source: str = "<text>") -> list[Case]:
    """Parse a YAML dataset *string* into a list of Cases (validates id uniqueness).

    Shared by :func:`load_dataset` (file path) and the web backend's live
    validation, so editing in the browser and loading from disk apply the exact
    same rules. ``source`` only labels error messages.
    """
    data = yaml.safe_load(text) or []
    if not isinstance(data, list):
        raise ValueError(
            f"dataset {source} must be a YAML list of cases, got {type(data).__name__}"
        )

    cases = [Case.from_dict(c) for c in data]
    seen: set[str] = set()
    for c in cases:
        if c.id in seen:
            raise ValueError(f"duplicate case id in dataset: {c.id!r}")
        seen.add(c.id)
    return cases


def load_dataset(path: str | Path) -> list[Case]:
    """Parse a YAML dataset file into a list of Cases (validates id uniqueness)."""
    p = Path(path)
    if not p.is_file():
        raise FileNotFoundError(f"dataset not found: {p}")
    return load_dataset_text(p.read_text(), source=str(p))
