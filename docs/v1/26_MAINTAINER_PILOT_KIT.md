# Independent field pilot (agent-native)

Status: **ready, not started.** No independent installation has been recruited or has reported, so the adoption evidence for release ([07](07_EVALUATION.md), [10](10_MIGRATION_AND_RELEASE.md)) is **pending**. The project's own trials, including real-repository replays, are not a substitute and are never reported as adoption evidence.

## Why the design changed (6 October 2026)

memex is used by coding agents, not by people at a keyboard. Once installed, its hooks deliver context and corrections to Claude Code or Codex automatically, so nobody "uses memex by hand".

The first version of this kit asked people to keep a manual log of every agent task for four weeks. That measured the participant's patience more than memex. It was replaced before any participant data existed, by maintainer decision. The amended gate is in 07: at least 3 independent installations, each reaching 4 complete pilot weeks or 50 agent sessions.

## What a participant does

About 10 minutes once, then nothing.

1. Follow [25_ONBOARDING.md](25_ONBOARDING.md): `memex v1 install claude` and/or `memex v1 install codex`, then `memex v1 doctor`.
2. Start the pilot under a pseudonym:
   ```
   memex v1 pilot start --participant P1
   ```
3. Keep working with your agents as usual.
4. At the end:
   ```
   memex v1 pilot checkin yes        # or no
   memex v1 pilot report             # writes memex-pilot-report.json; read it, then send it
   ```

Optional, any time:
- **Label corrections:** `memex v1 pilot corrections` lists recent corrections on your screen only. `memex v1 pilot label <id> useful` or `memex v1 pilot label <id> false` records your judgment.
- **Check progress:** `memex v1 pilot status` shows this week's mode.
- **Stop:** `memex v1 rollback` stops memex at once; an explicit `off` always wins over the schedule. `memex v1 pilot stop` ends the pilot early and returns the worktree to its own mode.

## Design

- **Crossover without manual switching.** `pilot start` writes a seeded schedule: week 1 is a coin flip between `live` and `shadow`, then the modes alternate. The mode check every hook already performs follows it. Shadow weeks run the full check but deliver and deny nothing, recording what live mode would have corrected. So each installation supplies its own control weeks, and a volunteer risks no interruptions in shadow weeks.
- **Evidence is collected automatically,** from memex's local trace and the repository's Git history, per week:
  - sessions and hosts;
  - confirmed packet deliveries;
  - action checks, and checks that could not be verified;
  - corrections (live) or would-be corrections (shadow);
  - corrections the agent reconsidered;
  - labels, if any;
  - commits and reverts in that week.
- **Unknown is not zero.** A value that could not be measured is `null`, and summaries report how many weeks it was known for.
- **Qualifying:** an installation qualifies at 4 complete weeks or 50 agent sessions, whichever comes first. A participant who stops early is still counted and reported.

## Consent and data minimization

- Participation is voluntary and can stop at any time, without a reason.
- **The report holds counts only.** No source code, paths, file names, prompts, diffs, transcripts, tool input, correction text or credentials. `summarize_reports` rejects any report with extra fields.
- Participants choose their own pseudonym, and repositories are never named.
- memex makes no network calls for the pilot. The participant reads the report file and sends it themselves.
- Published results are aggregates, and say how many installations each number comes from.

## Analysis

```
python -m memex.evaluation.pilot_kit reports reports/*.json
```

The summary gives:
- participants, and how many qualify;
- still-using at the last check-in;
- for live weeks against shadow weeks: sessions, action checks, corrections, reconsiderations, labels (useful, false, unlabeled), and commits and reverts with known counts.

The status is `pending` until at least 3 installations qualify. The results are descriptive field evidence. Efficacy belongs to preregistered trials ([22](22_PHASE5_PROTOCOL.md)).

## Recruiting

Invite people who already found memex: users of the PyPI and npm packages, the Glama and AI Agents Listing entries, the Claude Code plugin marketplace, and GitHub Discussions. Offer it as an opt-in **shadow-first beta**: install, run a pilot, and send one counts-only file at the end. Participants are not paid by results.

## Current status

| Item | State |
| --- | --- |
| Automatic schedule, report, labels, check-in | Ready: `memex/runtime/pilot.py`, `memex v1 pilot …`, `tests/test_phase5_field_pilot.py` |
| Cross-participant summary with the amended gate | Ready: `memex.evaluation.pilot_kit reports` |
| Manual log kit (superseded) | Kept for anyone who prefers it: `pilot_kit template` and `summarize` |
| Recruitment | Not started; needs the maintainer to invite participants |
| Observations | None |
| Release evidence | **Pending** |
