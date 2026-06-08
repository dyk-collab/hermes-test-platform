"""Named runner-parameter presets, persisted as JSON.

A preset bundles the run-time runner knobs (model/provider, max_turns/timeout,
the three hermes flags, and optional profile / toolsets override) under a name,
so the user can define a few once and pick one from a dropdown when launching an
eval. Storage is a single JSON file (``config.PRESETS_FILE``); there is no DB.

A preset dict looks like::

    {
      "name": "fast",
      "model": null, "provider": null,
      "profile": null, "toolsets": null,         # toolsets = run-level override
      "max_turns": null, "timeout": 600.0,
      "yolo": true, "accept_hooks": true, "ignore_rules": true
    }

``None`` means "don't pass that flag" (runner falls back to hermes defaults).
"""

from __future__ import annotations

import json
import re
from typing import Any, Optional

from .config import PRESETS_FILE

# Canonical field set + defaults. New presets are normalized against this so the
# stored shape, the API, and the frontend form all agree.
DEFAULTS: dict[str, Any] = {
    "model": None,
    "provider": None,
    "profile": None,
    "toolsets": None,  # list[str] | None — overrides the case's toolsets when set
    "max_turns": None,  # int | None
    "timeout": 600.0,
    "yolo": True,
    "accept_hooks": True,
    "ignore_rules": True,
}

_NAME_RE = re.compile(r"^[A-Za-z0-9_\-. ]{1,40}$")


def _normalize(name: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Coerce a raw preset dict to the canonical shape (drops unknown keys)."""
    out: dict[str, Any] = {"name": name}
    for k, default in DEFAULTS.items():
        v = raw.get(k, default)
        if v == "":  # treat blank string inputs as "unset"
            v = None
        out[k] = v
    # light type coercion for the numeric fields
    if out["max_turns"] is not None:
        out["max_turns"] = int(out["max_turns"])
    if out["timeout"] is not None:
        out["timeout"] = float(out["timeout"])
    if isinstance(out["toolsets"], str):
        out["toolsets"] = [s.strip() for s in out["toolsets"].split(",") if s.strip()] or None
    return out


def _load_all() -> dict[str, dict[str, Any]]:
    if not PRESETS_FILE.is_file():
        return {}
    try:
        data = json.loads(PRESETS_FILE.read_text())
    except Exception:  # noqa: BLE001 - a corrupt file shouldn't crash the app
        return {}
    if not isinstance(data, dict):
        return {}
    return data


def _save_all(store: dict[str, dict[str, Any]]) -> None:
    PRESETS_FILE.write_text(json.dumps(store, ensure_ascii=False, indent=2))


def list_presets() -> list[dict[str, Any]]:
    """All presets, sorted by name (each includes its ``name``)."""
    store = _load_all()
    return [{"name": n, **p} for n, p in sorted(store.items())]


def get_preset(name: str) -> Optional[dict[str, Any]]:
    store = _load_all()
    p = store.get(name)
    return {"name": name, **p} if p is not None else None


def save_preset(name: str, raw: dict[str, Any]) -> dict[str, Any]:
    """Create or update a preset (upsert). Returns the normalized preset."""
    name = (name or "").strip()
    if not _NAME_RE.match(name):
        raise ValueError(
            f"invalid preset name {name!r} (1-40 chars: letters/digits/_-. and spaces)"
        )
    preset = _normalize(name, raw or {})
    store = _load_all()
    store[name] = {k: v for k, v in preset.items() if k != "name"}
    _save_all(store)
    return preset


def delete_preset(name: str) -> dict[str, Any]:
    store = _load_all()
    if name not in store:
        raise FileNotFoundError(f"preset not found: {name}")
    del store[name]
    _save_all(store)
    return {"name": name, "deleted": True}
