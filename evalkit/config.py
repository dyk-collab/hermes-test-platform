"""Configuration & path resolution for evalkit.

The only external dependency is the installed ``hermes`` executable. We resolve
it once (env override → PATH → common install location) so every subprocess
call uses a stable absolute path.
"""

from __future__ import annotations

import os
import shutil
from pathlib import Path

# Where this repo lives; runs/ and datasets/ are resolved relative to it.
REPO_ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = REPO_ROOT / "runs"
DATASETS_DIR = REPO_ROOT / "datasets"
# Named runner-parameter presets (model/flags/timeout/...), chosen at run time.
PRESETS_FILE = REPO_ROOT / "runner_presets.json"

# Fallback install location used by the hermes installer on macOS/Linux.
_DEFAULT_HERMES = Path.home() / ".local" / "bin" / "hermes"


def resolve_hermes() -> str:
    """Return an absolute path to the ``hermes`` executable.

    Precedence: ``HERMES_BIN`` env var → ``$PATH`` → ~/.local/bin/hermes.
    Raises ``FileNotFoundError`` with an actionable message if none found.
    """
    override = os.environ.get("HERMES_BIN")
    if override:
        p = Path(override).expanduser()
        if p.is_file():
            return str(p)
        raise FileNotFoundError(f"HERMES_BIN points to a non-file: {override}")

    on_path = shutil.which("hermes")
    if on_path:
        return on_path

    if _DEFAULT_HERMES.is_file():
        return str(_DEFAULT_HERMES)

    raise FileNotFoundError(
        "Could not find the `hermes` executable. Install Hermes Agent, or set "
        "HERMES_BIN to its absolute path."
    )
