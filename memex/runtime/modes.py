"""v1 rollout modes, per worktree: `off`, `shadow` or `live`.

Kept dependency-free because every host hook reads it. Installed hooks
without a mode file are `live`, which is how they behaved before modes
existed. Guarded writes are a separate, independent option.
"""
import json
from pathlib import Path

MODES = ("off", "shadow", "live")


def mode_path(registration) -> Path:
    return Path(registration.common_dir) / "memex" / "v1-mode.json"


def read_modes(registration) -> dict:
    try:
        data = json.loads(mode_path(registration).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def read_mode(registration) -> str:
    value = read_modes(registration).get(registration.worktree_id, {})
    mode = value.get("mode") if isinstance(value, dict) else value
    return mode if mode in MODES else "live"
