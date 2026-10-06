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
| S02 Codex action semantics | **Closed** in P4 against codex-cli 0.157.1: app-server threads run hooks and deny prevents a pending patch; `codex exec` runs none. Findings in [20_PHASE4_VERIFICATION.md](20_PHASE4_VERIFICATION.md) | Finished before W12 |
| S03 graph transaction/recovery | Indexing owner source-verifies existing Graphiti/Neo4j driver transaction API, candidate-view publication and replay completion markers | Finish before W03. Produce W03–W05 implementation plan with real method signatures and failure tests |
| S04 statistical protocol | **Closed** 6 October 2026: pilot run, Part B and amendments A1–A4 frozen and pushed (`d048648`) before any confirmatory trial; analyzed once ([22](22_PHASE5_PROTOCOL.md), [23](23_PHASE5_VERIFICATION.md)) | Finished before P5 confirmation. No post-hoc gate editing |
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

## Completed prerequisite spikes

S01: measured Claude Code 2.1.289 directly. A synchronous `PreToolUse` deny prevents the pending mutation and its reason reaches the model, which reconsiders; deny is honored under `bypassPermissions`; a hook that overruns its timeout does not block; parallel edit calls are serialized per target; `defer` ends a non-interactive turn rather than failing open; and an agent correctly overrides a correction it can disprove. The supported route is deny-and-reconsider, with no wrapper or SDK fallback needed on this host. Closes with P3; [18_PHASE3_VERIFICATION.md](18_PHASE3_VERIFICATION.md) records the evidence and the resulting abstain policy. S02 closed with P4. S04 closed with P5: the protocol was frozen before the confirmatory trials, and the negative efficacy result is published in [24](24_PHASE5_RELEASE_READINESS.md).

S03: verified Graphiti Neo4jDriver session/execute_write interfaces; tested atomic rollback, completion, concurrent uniqueness, replay and graph-before-SQLite acknowledgement on native Neo4j. S05: real temporary Git tests cover linked worktrees, stable common/worktree IDs, Windows path aliases, detached/unborn HEAD, configured hooksPath and preserved hooks. Both close with P1; [14_PHASE1_VERIFICATION.md](14_PHASE1_VERIFICATION.md) records decisions and evidence.


## Phase 2 implementation decisions

W06–W08 implement R03/R04/R05/R09/R10/R15 using immutable graph proofs, per-view deterministic checks and independent bounded SQLite streams. [16_PHASE2_VERIFICATION.md](16_PHASE2_VERIFICATION.md) records F01/F04/F05/F06/F07/F08/F10/F13/F14/F15/F16 evidence and review fixes. Catch-up at action boundaries closes subscribe/snapshot event gaps without depending on transient notifications. Cold/warm indexing cost, eager dirty propagation and graph-history selection remain optimization work; no efficacy claim follows from these correctness tests.

Critical review fixes enforce source authorization independently of lifecycle and keep unavailable responses assertion-free. Already-known corrections survive outages. Packet/coverage bounds are checked before strict schema construction. Shared bounded CPU worker processes keep AST parsing off the event loop and retain occupied slots until jobs finish. Native authority/authentication bindings and guarded write critical sections remain later integration responsibilities; a source hash and receipt never prove compliance.


## Phase 3 implementation decisions

W09–W11 implement R06/R10/R11/R12 on one host. V1-009 is satisfied for Claude Code by measurement rather than by hook naming: the correction is delivered through a synchronous deny that prevents the pending mutation, and the agent's reconsideration is observed in the native stream.

Two decisions are worth carrying forward. **The adapter only ever subtracts permission.** It emits `deny` or no decision; `allow` would bypass the user's permission policy on a memex outage, and a measured `defer` abandons the pending action in a non-interactive session instead of failing open. **A hook payload is not an authenticator.** The principal comes from an owner-only capability file in the Git common directory and the memex session is an HMAC of the native session under that secret, so a forged payload cannot address another principal's stream.

V1-011 and V1-012 are unchanged and now have measured edges. The gate is a check, not a lock: a hook timeout fails open, opaque shell actions are explicitly not certified, and a writer arriving after the check is not prevented. No claim is made that an action check protects against arbitrary writers.

## Phase 3 hardening decisions

Three findings against the original P3 result were corrected; [18_PHASE3_VERIFICATION.md](18_PHASE3_VERIFICATION.md) records the reproductions.

**The core's current result is authoritative, always.** An adapter may cache rendered output for idempotency, but only alongside the verdict it answers, and must discard it when the core changes its mind. The original replay path returned a stale non-denial after the core had expired the attempt.

**V1-012 is sharpened: acknowledgement requires per-packet evidence.** Preparing a response, printing it, and the host inserting it are three different events. Only evidence that identifies a specific packet in a specific session advances the accepted baseline; a later hook invocation, a successful stdout write or a zero exit code does not. Unconfirmed packets stay pending and are re-offered, and recovery is bounded. Where a host offers no such evidence, retain pending state and record the limitation rather than acknowledge optimistically.

**A phase gate must discriminate.** A native demonstration whose objective assertions hold whether or not the premise changed proves interception, not reconsideration. The exit now requires the originally proposed mutation to fail an objective contract that the revised one passes. A new attempt identifier and a `reconsidered` flag are not evidence of semantic reconsideration.

## Phase 3 acceptance decisions

Two further findings were corrected after the hardening pass; [18_PHASE3_VERIFICATION.md](18_PHASE3_VERIFICATION.md) records both reproductions.

**Identity is not delivery, and the carrier decides.** A valid per-packet marker proves which packet some text belongs to. It never proves that the text was inserted into a session. Evidence is admissible only from a record the host writes *because it used the output* — on this client, an injected-context attachment or an error tool result naming the denied call — and never from the host's log of a hook's own stdout, which is written whether the output was used or not and at any exit code. Matching is structural and per-record, validating the session, the record shape against the packet kind, and for a correction the call it answers. The transcript is located through the client's own per-session layout, because a path supplied in a hook payload is a claim rather than a fact. This sharpens V1-012 a second time: the first pass moved acknowledgement behind per-packet evidence, and this one defines which evidence counts.

**A schema addition is not applied by declaring it.** `CREATE TABLE IF NOT EXISTS` leaves an existing table exactly as it was, so every column added after a release needs an explicit, idempotent `ALTER TABLE` guarded by `PRAGMA table_info`, tolerant of losing the race to a concurrent process, and paired with a defined conservative reading of rows written before it. The control plane is shared by concurrent adapter processes and must never require a manual reset or lose task state to an upgrade.

## Phase 4 decisions

**Codex integrates through app-server threads, not `codex exec`.** S02 measured no hook running under `exec`, from either the project layer or session flags. The app-server is the protocol Codex's own IDE extension and desktop app use, and in its threads `PreToolUse` deny prevents a pending patch.

**memex never grants hook trust.** Untrusted hooks do not run in app-server threads, and trust cannot be set from session flags. A real installation is trusted by the user's own `/hooks` review. Fixtures trust their own session-flag hooks for one thread through the app-server's `bypass_hook_trust` override and persist nothing.

**One adapter lifecycle, many hosts.** The replay rule, the four-state ledger, denial chains and the complete-packet rule live once in `HostAdapter`. A host contributes only what was measured to differ.

**V1-012 is sharpened a third time: a packet must arrive whole.** A Codex truncation kept both ends of a packet, and so its marker. Acknowledgement now requires the exact packet text around its marker, on both hosts.

**V1-011 is implemented with fencing on the write path.** A token stored in SQLite and checked before a host's own write would not stop a paused holder. The guard rechecks holder, generation, expiry and expected hashes inside the same SQLite write transaction that replaces the files.

**Recovery is part of every protected section, not of construction.** A guard that already existed could commit while an older interrupted write's intent was unresolved, and a later guard then replayed it over the newer write. Every guarded operation now resolves intents first, inside its own write lock. An intent records before and after hashes, so recovery writes only over bytes the interrupted commit verified; anything else is held for an explicit operator decision.

**A confirmation commits with the baseline it certifies.** Claiming `confirmed` and then acknowledging left a window where a stopped process stranded a confirmation the core never made. The ledger transition, the core's baseline update and the trace event now share one transaction on the shared control plane.

**Native fixtures track what they write to client configuration.** Codex writes trust for every fixture directory into the configuration it runs under. Native runs use the logins already on the machine (or dedicated homes), snapshot the client configuration first, and afterwards remove only the entries that are new and name the run's own fixture directories.

**Fixture isolation is part of a native measurement.** A user's failing global plugin told an agent memex was offline and changed its first proposal. Native fixtures now load only their own project settings, per invocation.
