"""Independent-maintainer pilot: observation-log schema, validation and summary.

usage:
  python -m memex.evaluation.pilot_kit template > my-log.jsonl   # one example record per kind
  python -m memex.evaluation.pilot_kit summarize logs/*.jsonl     # aggregate several participants

Each participant keeps one JSONL file. The schema records outcomes and
counts only: no source code, no prompts, no transcripts and no credentials
(see docs/v1/26_MAINTAINER_PILOT_KIT.md). The summary reports exactly what
the participants recorded and how many recorded it. A participant who
stopped is still counted as a participant.
"""
from __future__ import annotations

import json
import sys
from collections import defaultdict
from pathlib import Path

KINDS = {
    "install": {"required": ("participant", "date", "host", "succeeded", "minutes"),
                "optional": ("problems",)},
    "task": {"required": ("participant", "date", "week", "mode", "host", "task_kind", "corrections_shown",
                          "useful_corrections", "false_interruptions", "rework_needed", "human_intervention",
                          "minutes"),
             "optional": ("model_cost_usd", "notes_category")},
    "checkin": {"required": ("participant", "date", "week", "still_using"),
                "optional": ("would_recommend", "reason_category")},
}
MODES = ("live", "shadow")
FORBIDDEN = ("code", "prompt", "transcript", "token", "password", "api_key", "diff")


def validate(record: dict) -> list[str]:
    """Problems with one record; an empty list means it is usable."""
    kind = record.get("kind")
    if kind not in KINDS:
        return [f"unknown kind {kind!r}"]
    problems = [f"missing {name}" for name in KINDS[kind]["required"] if name not in record]
    allowed = {"kind", *KINDS[kind]["required"], *KINDS[kind]["optional"]}
    problems += [f"unexpected field {name!r} (the log records outcomes only)" for name in record if name not in allowed]
    problems += [f"field {name!r} looks like captured content" for name in record
                 if any(word in name.lower() for word in FORBIDDEN)]
    if kind == "task":
        if record.get("mode") not in MODES:
            problems.append("mode must be live or shadow")
        shown, useful, false = (record.get(k, 0) for k in ("corrections_shown", "useful_corrections",
                                                         "false_interruptions"))
        if isinstance(shown, int) and isinstance(useful, int) and isinstance(false, int) and useful + false > shown:
            problems.append("useful + false corrections exceed corrections shown")
    return problems


def load(paths) -> tuple[list[dict], list[str]]:
    records, problems = [], []
    for path in paths:
        for number, line in enumerate(Path(path).read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                record = json.loads(line)
            except ValueError:
                problems.append(f"{path}:{number}: not JSON")
                continue
            issues = validate(record)
            if issues:
                problems += [f"{path}:{number}: {issue}" for issue in issues]
            else:
                records.append(record)
    return records, problems


def summarize(records: list[dict]) -> dict:
    participants = sorted({r["participant"] for r in records})
    installs = [r for r in records if r["kind"] == "install"]
    tasks = [r for r in records if r["kind"] == "task"]
    checkins = [r for r in records if r["kind"] == "checkin"]
    by_mode: dict = defaultdict(list)
    for task in tasks:
        by_mode[task["mode"]].append(task)
    last_week: dict = {}
    for c in checkins:
        if c["week"] >= last_week.get(c["participant"], {"week": -1})["week"]:
            last_week[c["participant"]] = c

    def mode_summary(rows):
        shown = sum(r["corrections_shown"] for r in rows)
        return {"tasks": len(rows), "corrections_shown": shown,
                "useful_corrections": sum(r["useful_corrections"] for r in rows),
                "false_interruptions": sum(r["false_interruptions"] for r in rows),
                "rework_rate": (sum(bool(r["rework_needed"]) for r in rows) / len(rows)) if rows else None,
                "intervention_rate": (sum(bool(r["human_intervention"]) for r in rows) / len(rows)) if rows else None,
                "minutes_mean": (sum(r["minutes"] for r in rows) / len(rows)) if rows else None,
                "cost_known": f"{sum(1 for r in rows if r.get('model_cost_usd') is not None)}/{len(rows)}",
                "cost_usd_total": sum(r["model_cost_usd"] for r in rows if r.get("model_cost_usd") is not None)
                if any(r.get("model_cost_usd") is not None for r in rows) else None}

    return {
        "participants": len(participants),
        "install_success": f"{sum(bool(r['succeeded']) for r in installs)}/{len(installs)}",
        "weeks_observed": max((r["week"] for r in tasks + checkins), default=0),
        "still_using_at_last_checkin": f"{sum(bool(c['still_using']) for c in last_week.values())}/{len(participants)}",
        "by_mode": {mode: mode_summary(by_mode.get(mode, [])) for mode in MODES},
        "evidence_status": "pending" if len(participants) < 3 else "reported",
    }


TEMPLATE = [
    {"kind": "install", "participant": "P1", "date": "2026-10-20", "host": "claude", "succeeded": True,
     "minutes": 25, "problems": "none"},
    {"kind": "task", "participant": "P1", "date": "2026-10-21", "week": 1, "mode": "live", "host": "claude",
     "task_kind": "bugfix", "corrections_shown": 1, "useful_corrections": 1, "false_interruptions": 0,
     "rework_needed": False, "human_intervention": False, "minutes": 40, "model_cost_usd": None,
     "notes_category": "dependency changed by teammate"},
    {"kind": "checkin", "participant": "P1", "date": "2026-10-26", "week": 1, "still_using": True,
     "would_recommend": True, "reason_category": "caught a stale assumption"},
]


def main(argv=None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    if not argv or argv[0] == "template":
        for record in TEMPLATE:
            print(json.dumps(record))
        return 0
    if argv[0] == "summarize":
        records, problems = load(argv[1:])
        for problem in problems:
            print("rejected:", problem, file=sys.stderr)
        print(json.dumps(summarize(records), indent=1))
        return 1 if problems else 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
