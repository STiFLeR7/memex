# Decisions, spikes and risk register

Status: baseline design derived from the maintainer-approved thesis. Accepted entries guide planning; they do not claim implemented or empirically validated behavior. Changing an accepted entry requires recording the reason, impacted requirements and new acceptance evidence.

## Architecture decisions

| ADR | Decision | Status / consequence |
| --- | --- | --- |
| V1-001 | Maintain evidence validity of delivered context | Accepted direction; efficacy must still be proven |
| V1-002 | Keep Neo4j/Graphiti authoritative for engineering knowledge | Accepted; no substrate rewrite |
| V1-003 | Use stdlib SQLite for local journal/cursors/receipts | Accepted baseline; no second semantic-memory store or distributed lock claim |
| V1-004 | Separate repository/worktree/content/index identity | Accepted; branch name and remote URL are insufficient |
| V1-005 | Immutable claim revisions with per-view verification | Accepted; branch-local drift cannot globally invalidate a shared revision |
| V1-006 | Necessary/alternative supports are explicit | Accepted; impact adjacency is not proof dependency |
| V1-007 | Changed hash means revalidation, not automatic false | Accepted; authority and applicability remain independent |
| V1-008 | Task/session subscriptions are bounded control state | Accepted; no raw transcript or personal-memory ingestion |
| V1-009 | Corrections require verified action reconsideration | Accepted; hook naming/notifications do not establish delivery timing |
| V1-010 | Claude Code and Codex concurrent operation is a v1 gate | Accepted scope; separate-worktree and shared-checkout cases required |
| V1-011 | Guarded writes are opt-in and capability-scoped | Accepted; cooperating target leases do not protect arbitrary writers |
| V1-012 | Fail open with explicit unavailable freshness | Accepted; no fabricated current certificate or receipt |
| V1-013 | Preserve legacy interfaces and authority, distinguish legacy verification | Accepted; additive reversible migration |
| V1-014 | Compare against fresh retrieval and hash-bound notes | Accepted; failure to beat them kills the defining v1 claim |
| V1-015 | Python first, report unsupported structural coverage | Accepted baseline; broader language semantics need separate evidence |

## Bounded implementation spikes

| Spike | Owner / required output | Entry / exit |
| --- | --- | --- |
| S01 Claude action semantics | Integration owner records installed client version, synchronous interception, denied/batched-action feedback and native reconsideration trace | Begin alongside P1; finish before W09. Select supported hook/SDK route or report no viable native route |
| S02 Codex action semantics | Integration owner records actual installed hook/wrapper API, session identity, shell/edit coverage and native ordering | Begin after contracts freeze; finish before W12. Advisory-only output cannot pass |
| S03 graph transaction/recovery | Indexing owner source-verifies existing Graphiti/Neo4j driver transaction API, candidate-view publication and replay completion markers | Finish before W03. Produce W03–W05 implementation plan with real method signatures and failure tests |
| S04 statistical protocol | Evaluation owner runs labeled development pilot when an execution environment is available, freezes sample size/margins/recall/cost/tail limits before disjoint confirmatory trials | Finish before P5 confirmation. No post-hoc gate editing |
| S05 Windows/Git identity | Runtime owner verifies linked worktree discovery, path aliases/case handling, common-dir and hooksPath composition | Finish W05. Preserve existing hooks; no destructive clone/checkout experiments |

These are explicit decisions with bounded outcomes and owners, rather than unspecified implementation holes. If a spike fails, revise the affected contract/roadmap honestly before building a workaround that weakens the product claim.

## Risks and responses

| Risk | Consequence | Mitigation / stop signal |
| --- | --- | --- |
| Inferred support links are wrong | Unnecessary corrections or missed stale claims | Explicit supported claims first; separate proposed supports from verified ones; measure recall/precision |
| Structural graph lags or keeps removed edges | Runtime amplifies stale context | P1 is a hard dependency; action-target/support hash checks catch lag |
| Partial graph writes appear complete | Mixed-generation context | Candidate view + graph completion record; no certificate from partial coverage |
| Two stores crash between commits | Lost/duplicated events | At-least-once replay with graph idempotency markers; test both crash boundaries |
| Mutable branch data overwrites another view | Incorrect current facts across agents | Worktree overlays and per-view verification; isolation violation blocks release |
| Host inserts correction too late | Agent executes obsolete pending patch | Native action-order proof; reconsideration or supported wrapper required |
| Project-wide dedup hides a session's update | Agent never receives correction | Session/task cursors; explicit resume/compaction resync |
| Rapid edits cause retry/interruption loops | Development becomes slower | Two-attempt ceiling, relevance filtering and explicit conflict result |
| Changed bytes treated as changed policy | Approved rule silently revoked | Distinct authority/applicability and necessary/alternative supports |
| Lease mistaken for universal protection | Overwrites still happen | Guard only cooperating bounded writes; declare bypass/opaque-action coverage |
| Shared checkout Git commit includes others' staged changes | Ownership confusion | Worktree-level mutation coordination and staged-diff advisory; no auto staging/reset |
| Graph hash/evidence crosses permissions | Unauthorized engineering-context disclosure | Principal-scoped projection and task lookup; source ACLs remain authoritative |
| Existing infrastructure creates onboarding friction | OSS users fail to reach first value | Existing-service recipe, diagnostic capability report, isolated contributor demo |
| Same benefit as much simpler baseline | Complexity without product value | Kill/reduce scope if C/D match E on defining condition |

## Change control

The maintainer's product approval is recorded; no additional brainstorming approval is required to write these plans. Later implementation follows the current user instruction and accepted phase scope. A plan author may resolve routine implementation choices, but must not quietly remove concurrent-client support, replace the storage substrate, weaken action ordering or declare unmeasured efficacy established.

Every material change records affected R/W/F IDs, rationale and evidence. Keep current source facts in the audit and research conclusions in the research document; do not rewrite historical measured results to match a desired narrative.
