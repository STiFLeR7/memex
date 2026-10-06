"""Agent-native field pilot: a seeded live/shadow crossover and a counts-only report.

A participant installs memex once and keeps working with their agents. The
weekly mode comes from the schedule written here (see `modes.read_mode`), and
the evidence comes from memex's own trace and the repository's Git history.
Nobody logs anything by hand. Optional input is a one-word label on a
correction and a still-using check-in.

The report holds counts only: no source, prompts, paths, reasons, tool input
or transcripts. It is written locally; memex sends nothing anywhere.
"""
from __future__ import annotations

import json
import random
import sqlite3
import subprocess
import time
from pathlib import Path

from memex.runtime.modes import WEEK, pilot_path, pilot_week, read_pilot

REPORT_VERSION = "memex-pilot-report.v1"
VERDICTS = ("useful", "false")


def _write(registration, pilot: dict) -> None:
    path = pilot_path(registration)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(pilot, indent=1), encoding="utf-8")


def start(registration, participant: str, *, weeks: int = 4, seed: int | None = None,
          now: float | None = None) -> dict:
    """Begin a crossover: week 1's mode is a seeded coin flip, then the modes alternate."""
    if read_pilot(registration) and not read_pilot(registration).get("stopped_at"):
        raise ValueError("a pilot is already running in this repository; stop it first")
    if weeks < 2:
        raise ValueError("a crossover needs at least two weeks")
    seed = random.SystemRandom().randrange(2**31) if seed is None else seed
    first = random.Random(seed).choice(("live", "shadow"))
    other = "shadow" if first == "live" else "live"
    pilot = {"participant": participant, "started_at": time.time() if now is None else now, "seed": seed,
             "schedule": [first if w % 2 == 0 else other for w in range(weeks)], "labels": {}, "checkins": []}
    _write(registration, pilot)
    return pilot


def stop(registration, now: float | None = None) -> dict:
    pilot = _require(registration)
    pilot["stopped_at"] = time.time() if now is None else now
    _write(registration, pilot)
    return pilot


def _require(registration) -> dict:
    pilot = read_pilot(registration)
    if pilot is None:
        raise ValueError("no pilot in this repository; run `memex v1 pilot start` first")
    return pilot


def _events(registration, pilot: dict) -> list[dict]:
    path = Path(registration.runtime_path)
    if not path.exists():
        return []
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        rows = db.execute("SELECT at, harness, native_session_id, attempt_id, event, tool_name, gate, insertion, "
                          "reason, reconsidered FROM live_trace WHERE at >= ? ORDER BY ordering",
                          (pilot["started_at"],)).fetchall()
    except sqlite3.OperationalError:  # no trace table yet: nothing has run
        rows = []
    finally:
        db.close()
    return [dict(r) for r in rows]


def _is_correction(event: dict) -> bool:
    return (event["event"] == "action_check" and event["gate"] == "prevented") or \
        (event["event"] == "shadow" and event["gate"] == "shadow_would_prevent")


def corrections(registration) -> list[dict]:
    """Recent corrections, shown locally so the participant can label them. Never put in the report."""
    pilot = _require(registration)
    return [{"attempt_id": e["attempt_id"], "at": e["at"], "week": pilot_week(pilot, e["at"]),
             "mode": "shadow" if e["event"] == "shadow" else "live", "tool": e["tool_name"],
             "reason": (e["reason"] or "")[:200], "label": pilot["labels"].get(e["attempt_id"])}
            for e in _events(registration, pilot) if _is_correction(e) and e["attempt_id"]]


def label(registration, attempt_id: str, verdict: str) -> dict:
    if verdict not in VERDICTS:
        raise ValueError(f"verdict must be one of {VERDICTS}")
    pilot = _require(registration)
    if attempt_id not in {c["attempt_id"] for c in corrections(registration)}:
        raise ValueError("no correction with that id in this pilot")
    pilot["labels"][attempt_id] = verdict
    _write(registration, pilot)
    return pilot


def checkin(registration, still_using: bool, now: float | None = None) -> dict:
    pilot = _require(registration)
    now = time.time() if now is None else now
    pilot["checkins"].append({"week": pilot_week(pilot, now) or len(pilot["schedule"]), "still_using": still_using})
    _write(registration, pilot)
    return pilot


def _git_count(root: str, since: float, until: float, *grep: str) -> int | None:
    command = ["git", "-C", root, "rev-list", "--count", "--all", f"--since={int(since)}", f"--until={int(until)}"]
    command += [f"--grep={g}" for g in grep]
    try:
        return int(subprocess.run(command, capture_output=True, text=True, check=True).stdout.strip())
    except (OSError, ValueError, subprocess.CalledProcessError):
        return None


def report(registration, now: float | None = None) -> dict:
    """Counts per pilot week. Unknown values are None, never zero."""
    pilot = _require(registration)
    now = time.time() if now is None else now
    end = min(now, pilot.get("stopped_at") or now, pilot["started_at"] + WEEK * len(pilot["schedule"]))
    events = _events(registration, pilot)
    weeks = []
    for index, mode in enumerate(pilot["schedule"]):
        lo = pilot["started_at"] + WEEK * index
        hi = min(lo + WEEK, end)
        if hi <= lo:
            break
        rows = [e for e in events if lo <= e["at"] < hi]
        corrected = [e for e in rows if _is_correction(e)]
        verdicts = [pilot["labels"].get(e["attempt_id"]) for e in corrected]
        weeks.append({
            "week": index + 1, "mode": mode, "complete": hi - lo >= WEEK,
            "hosts": sorted({e["harness"] for e in rows}),
            "sessions": len({e["native_session_id"] for e in rows if e["event"] == "session_start"}),
            "packets_confirmed": sum(1 for e in rows if e["event"] == "delivery" and e["insertion"] == "confirmed"),
            "action_checks": sum(1 for e in rows if e["event"] == "action_check"),
            "unverified_checks": sum(1 for e in rows if e["event"] == "action_check" and e["gate"] == "advisory"),
            "corrections": len(corrected),
            "reconsidered": sum(1 for e in corrected if e["reconsidered"]),
            "labeled_useful": verdicts.count("useful"), "labeled_false": verdicts.count("false"),
            "unlabeled": verdicts.count(None),
            "commits": _git_count(registration.root, lo, hi),
            "reverts": _git_count(registration.root, lo, hi, "^Revert"),
        })
    return {"kind": "pilot-report", "version": REPORT_VERSION, "participant": pilot["participant"],
            "weeks_planned": len(pilot["schedule"]), "weeks_observed": sum(w["complete"] for w in weeks),
            "sessions": sum(w["sessions"] for w in weeks), "stopped": bool(pilot.get("stopped_at")),
            "checkins": pilot["checkins"], "weeks": weeks}
