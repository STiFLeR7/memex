"""v1 rollout modes, per worktree: `off`, `shadow` or `live`.

Kept dependency-free because every host hook reads it. Installed hooks
without a mode file are `live`, which is how they behaved before modes
existed. Guarded writes are a separate, independent option.

An active field pilot (`memex v1 pilot start`) sets the mode by its weekly
crossover schedule, so nobody switches modes by hand. An explicit `off`,
including `memex v1 rollback`, always wins.
"""
import json
import time
from pathlib import Path

MODES = ("off", "shadow", "live")
WEEK = 7 * 24 * 3600


def mode_path(registration) -> Path:
    return Path(registration.common_dir) / "memex" / "v1-mode.json"


def pilot_path(registration) -> Path:
    return Path(registration.common_dir) / "memex" / "v1-pilot.json"


def read_modes(registration) -> dict:
    try:
        data = json.loads(mode_path(registration).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def read_pilot(registration) -> dict | None:
    try:
        data = json.loads(pilot_path(registration).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) and data.get("schedule") else None


def pilot_week(pilot: dict, now: float) -> int | None:
    """The 1-based pilot week at `now`, or None before the start, after the last week or once stopped."""
    if pilot.get("stopped_at"):
        return None
    week = int((now - pilot["started_at"]) // WEEK) + 1
    return week if 1 <= week <= len(pilot["schedule"]) else None


def read_mode(registration, now: float | None = None) -> str:
    value = read_modes(registration).get(registration.worktree_id, {})
    mode = value.get("mode") if isinstance(value, dict) else value
    if mode != "off":
        pilot = read_pilot(registration)
        week = pilot_week(pilot, time.time() if now is None else now) if pilot else None
        if week is not None:
            return pilot["schedule"][week - 1]
    return mode if mode in MODES else "live"
