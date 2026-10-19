# Phase 7 plan: after S05

Status: **adopted 19 October 2026; W25 in progress.** Nothing here changes S05. Its result ([28](28_PHASE6_RESULTS.md)) stands as published: efficacy, precision and latency failed. Under its stopping rules S05 is not extended, rerun or re-gated. Every gate below is judged again only by a new preregistered evaluation, S06, run once.

## Diagnosis

### 0. Real repositories get no claims (found while planning; not an S05 gate)

Live Context delivers, checks and corrects **claims**: `ClaimRevision`s bound to `EvidenceRef`s. In the released code, only the evaluation creates them. `put_claim` and `put_evidence` are called from `memex/evaluation/fixtures.py` and `memex/evaluation/core_demo.py`, and from nowhere else. `actions._project` deliberately never projects legacy (v0.9) facts. So on a user's repository the packet has no claims, and no check or resume notice can ever fire. Every S04/S05 result describes seeded fixtures, not what an installation does.

The 3-developer pilot would observe this. It must not start until W25 lands.

### 1. Efficacy: the design cannot show a difference

The strongest baseline, C, had 0/56 stale failures, as did B, D and E. Only A (no memex) failed, at 44/56. That ceiling comes from the design, not from chance:

- C is fresh per-action retrieval through memex's own engine. It denies an edit whenever the fresh packet differs from what this session was last given. That is already session-relative. D is hash-bound notes, which also deny on a changed hash.
- Under A1 the change lands between turns. Every arm with a resume hook (B, C, D, E) receives current context before the first edit.
- Part A's original timing (a change after the first tool call) failed for the opposite reason: the agent read code that had already changed (A1's record).

The question the gate asks, "does E beat C and D on stale failures", likely has no positive answer on any history where C and D see the change before the edit. The index already set the rule for this case: *"If the full system cannot beat fresh retrieval and hash-bound notes on that condition, reduce the scope to an incremental release."* What S05 does show is the effect of memex as a whole. Stale failures fell from 79% (A) to 0% for every memex arm.

### 2. Precision: comment-only changes

All 8 of E's incorrect corrections are on `cosmetic` histories. A comment added to the dependency changes its file hash. Source evidence with `predicate="hash"` is then `needs_revalidation` (`verification.evidence_check`: `source_hash_changed`). With those 8 removed, E's precision is 56/56.

### 3. Latency: a fixed floor plus the check

Warm unchanged checks, pooled S05 values (`output/v1-evidence/phase6/confirmatory/analysis.json`):

| Arm | Core check p50 / p95 ms | End to end p50 / p95 ms |
| --- | --- | --- |
| B (no check) | none | 80 / 122 |
| D (hash compare) | 1.5 / 2.7 | 84 / 140 |
| C | 62 / 126 | 141 / 238 |
| E | 68 / 110 | 160 / 275 |

About 80 ms at p50 and 120–140 ms at p95 is a **floor** paid with no check work: interpreter start, the client, the socket round trip, and the service's work outside the check. E's p95 then adds its check and the tail from 3 concurrent trials. To pass under 200 ms p95, both the floor and E's check must fall. One known inflation applies only to measured runs: for the evaluation's timed modules the client imports `ctypes` to read its own creation time, which costs time inside the measured window. Production hooks do not do this.

## Decisions (maintainer, 19 October 2026)

**Decided:** D1 = (b) plus the bounded (a) spike (W22). D2 = W25 first.


- **D1, the efficacy hypothesis for S06.**
  - (a) Keep "E beats C and D" and first search for a history class where C and D fail before the edit, as development-only exploration.
  - (b) Preregister memex vs no memex (E vs A) as the primary efficacy test, with E non-inferior to C and D on outcomes and resources.
  - Either way S05 stays failed. Recommendation: (b), plus a bounded (a) spike. If the spike finds no discriminating class, apply the index's scope rule: describe E's added value as targeting and resume notices, not stale-failure reduction over C.
- **D2, ordering.** Recommendation: W25 (claims in real repositories) comes before S06 and before the pilot. Without it, S06 measures fixtures that no user can reproduce.

## Work

| Work | Deliverable | Exit evidence |
| --- | --- | --- |
| **W25 Claim production** | A production path that creates evidence-bound claims on a real repository. Candidates, smallest first: (1) `record_decision` / `record_problem` bind the new claim to the files and symbols they name; (2) structural claims derived from the index (a symbol exists, its signature, its callers) with `symbol_exists` / code-hash evidence; (3) passing tests as `test` evidence. Design note first; legacy facts stay `legacy_unverified`. | On a fresh clone of a real repository (memex itself): a session gets a non-empty packet; a teammate's change to a cited symbol produces a correction at the next check; an unrelated change produces none. |
| **W21 Precision** | Code-hash evidence: a `predicate="code"` whose digest covers the Python token stream without comments or blank lines (stdlib `tokenize`). It is stored beside the byte hash on `FileStructure` and the graph `File` node. Byte-hash evidence keeps today's behavior. Other languages fall back to the byte hash. | Development fixtures: a comment-only or blank-line change is not flagged; every semantic mechanism still is (recall regression tests); F01–F16 and the full suite pass. |
| **W20 Latency** | (1) Profile the floor into its parts at parallel 1 and 3: process start, client, connect, the service outside the check, reply. (2) Read the client's creation time without importing `ctypes` inside the measured window. (3) Cut the service's non-check work and E's check: capture, listing and graph reads. A compiled client is considered only if the profile shows interpreter start dominates. | Development microbenchmarks and smoke trials at parallel 3: E's warm end-to-end p95 ≤ 170 ms, leaving margin under the 200 ms gate. |
| **W22 Efficacy spike** (if D1 includes (a)) | Development-only histories where the change lands after the agent reads the dependency and before its first edit, within one turn; also claims whose evidence the session never read. | A written yes or no: does any class produce C or D stale failures where E has none? |
| **W23 S06 preregistration** | Protocol and frozen JSON, after W25, W21 and W20 land. New held-out templates; H01–H48 become development data. Every gate value as in S05, apart from the hypothesis D1 selects. Parallel 3; analysis run once. | Committed and pushed before the first S06 trial. |
| **W24 Pilot** | The 3-developer pilot ([26](26_MAINTAINER_PILOT_KIT.md)), started after W25. | Install, use and rework outcomes from independent machines. |

Order: W25, then W21 and W20 in parallel, then W22 if chosen, then W23 and S06, with W24 alongside S06.

## Not done

S05's verdict, data and thresholds are not changed. H01–H48 are not reused as confirmatory data. The S06 sample is not enlarged to chase an effect. No gate is narrowed after S06's data are seen.
