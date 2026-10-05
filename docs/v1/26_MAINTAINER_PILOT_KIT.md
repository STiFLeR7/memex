# Independent-maintainer pilot kit

Status: **ready, not started.** No independent maintainer has been recruited or has reported. Until 3–5 real participants complete it, the adoption evidence for release ([07](07_EVALUATION.md), [10](10_MIGRATION_AND_RELEASE.md)) is **pending**. The project's own trials are not a substitute and are never reported as adoption evidence.

## Goal

Learn whether maintainers who did not build memex:
- can install it;
- receive useful corrections in their own work;
- keep using it;
- and at what cost in rework, interruptions and model spend.

## Design

- **Participants:** 3–5 independent maintainers, each on a repository they actively work on. At least two must use each host if possible. Participants are not paid by results.
- **Duration:** at least four weeks of ordinary work.
- **Crossover:** each participant alternates weekly between `live` and `shadow` mode, starting from a coin flip recorded before week 1. Shadow mode records what live mode would have corrected without intervening, so every participant supplies their own control weeks.
- **Tasks:** whatever the participant would do anyway. Participants log each task where an agent edited code.
- **Blinding:** where feasible, a second maintainer reviews a sample of patches without knowing the week's mode, and records only rework needed (yes/no).

## Steps for a participant

1. Follow [25_ONBOARDING.md](25_ONBOARDING.md): setup, `memex v1 install <host>`, `memex v1 doctor`.
2. Record one `install` line, whether it worked or not.
3. Set the week's mode with `memex v1 mode live` or `memex v1 mode shadow`.
4. After each agent task, record one `task` line.
5. At the end of each week, record one `checkin` line, then switch mode.
6. To stop at any time, run `memex v1 rollback`, record a final `checkin` with `still_using: false`, and send the log.

The schema and an example are produced by `python -m memex.evaluation.pilot_kit template`. Each line is one JSON object:

| Kind | Fields |
| --- | --- |
| `install` | participant, date, host, succeeded, minutes, problems |
| `task` | participant, date, week, mode, host, task_kind, corrections_shown, useful_corrections, false_interruptions, rework_needed, human_intervention, minutes, model_cost_usd (optional), notes_category (optional) |
| `checkin` | participant, date, week, still_using, would_recommend (optional), reason_category (optional) |

## Consent and data minimization

- Participation is voluntary and can stop at any time, without giving a reason.
- **The log records outcomes and counts only.** No source code, prompts, diffs, transcripts, tokens or credentials. The validator rejects fields that look like captured content.
- Participants pseudonymize themselves (P1, P2, …), and their repositories are never named in reports.
- Participants keep their own logs and send them at the end. Nothing is collected automatically, and memex makes no network calls on the pilot's behalf.
- Published results report only aggregates, and say how many participants each number comes from.

## Analysis

```powershell
python -m memex.evaluation.pilot_kit summarize logs/*.jsonl
```

The summary reports:
- participants;
- install success;
- weeks observed;
- how many participants were still using memex at their last check-in;
- for live versus shadow weeks:
  - corrections shown;
  - useful corrections;
  - false interruptions;
  - rework and intervention rates;
  - task minutes;
  - cost, with known/total counts.

Missing cost is unknown, not zero. With fewer than 3 participants, the status stays `pending`. Results are descriptive: a 3–5 person pilot cannot establish efficacy, and that question belongs to the preregistered trials in [22](22_PHASE5_PROTOCOL.md).

## Current status

| Item | State |
| --- | --- |
| Kit, schema, validator, summary | Ready (`memex/evaluation/pilot_kit.py`, `tests/test_phase5_pilot_kit.py`) |
| Recruitment | Not started; needs the maintainer to invite participants |
| Observations | None |
| Release evidence | **Pending** |
