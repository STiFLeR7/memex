# Claude Code and Codex concurrency

Status: verified in P4 ([20_PHASE4_VERIFICATION.md](20_PHASE4_VERIFICATION.md)). Claude Code and Codex ran live together in separate worktrees and in one checkout, in both orderings, and two live clients raced guarded writes. Every row of the acceptance matrix below has executed evidence. Applies to simultaneous Claude Code and Codex sessions in separate worktrees or the same checkout. See [04_CONTEXT_CONTRACTS.md](04_CONTEXT_CONTRACTS.md).

## Shared repository, separate views and sessions

One local repository coordinator serves both clients. Each adapter registers a namespaced session and an explicit checkout. Sessions share engineering knowledge only where its evidence and applicability match their views. They never share delivery cursors.

| Identity | Separate worktrees | Shared checkout |
| --- | --- | --- |
| Repository | Same `repo_id` from common Git repository | Same `repo_id` |
| Worktree | Distinct `worktree_id` | Same `worktree_id` |
| Content/index generations | Independent per worktree | Shared ordered generations |
| Session/task | Distinct for every host run | Distinct for every host run |
| Delivered claim revisions | Per task/session | Per task/session |
| Write coordination | Independent paths, integration awareness | Optional cooperating target guards |

Git worktrees share repository data but have distinct working trees and per-worktree state. Discover them through Git commands, not by assuming every checkout contains a `.git` directory. Respect configured hook locations. [Git worktree documentation](https://git-scm.com/docs/git-worktree).

## Scenario A: independent worktrees

Claude uses view A at commit X; Codex uses view B at commit X with a changed API. The B overlay marks B's dependent claims dirty. A continues to receive evidence appropriate to X and its own overlay. Memex does not replace A's facts with B's unmerged observations.

An optional cross-task advisory can say "another registered task is changing this interface in worktree B," if both tasks are visible to the caller. This is planned coordination context, not a claim about A's current code. Do not expose private branches or another task's intent across authorization boundaries.

When A merges/cherry-picks/rebases onto changed code, invalidate A from its actual new contents and ancestry. B's test result does not automatically certify A's merge result. A conflict-resolution edit creates a new view requiring its own verification. Same relative filename or symbol name across views cannot collapse their structured identities.

Human-approved repository rules are shared according to explicit applicability and approval history. A branch-local proposal cannot supersede a globally approved rule. Concurrent contradictory proposals are separate candidates until an authorized review resolves them.

## Scenario B: two clients in one checkout

Both clients see the same evolving filesystem. Suppose Claude received claim revision C1 at generation 10. Codex changes its supporting API; generation 11 changes the claim's verification. Before Claude's affected action, its adapter checks source hashes and receives a correction. Codex may also need the correction if its task depends on C1; a receipt from Claude cannot suppress that delivery.

Different-file actions with disjoint evidence generally proceed. Overlapping evidence can matter even when target files differ: changing an interface in one file can invalidate a caller assumption elsewhere. Conversely, editing an unrelated file should not interrupt all sessions merely because the shared generation increased.

Attribution is optional when changes arrive from the filesystem. A guarded adapter can attach its session/action ID to a write event. An external editor is recorded as an external/unknown actor; Memex must not infer that Claude or Codex made a change from timing alone.

## Two concurrency guarantees

**Default: context coordination.** Detect known drift, return scoped corrections, request reconsideration through capable adapters and report unknown freshness. There remains a time gap between a check and a native write. Ordinary hooks do not guarantee prevention of simultaneous overwrites.

**Opt-in: guarded writes.** Both clients route supported mutations through a cooperating adapter that acquires a short-lived target lease, verifies expected hashes, executes the bounded write while retaining the lease, captures the result, then releases it. Multi-file leases are acquired in canonical path order or all-or-none to avoid deadlock. Lease records carry a fencing generation; expired/replaced holders cannot use the same token for another guarded write.

The protection is scoped to participating adapters in this local coordinator. Arbitrary shell commands, editors, Git operations or another machine may bypass it. Recheck expected target hashes inside the guard, report unsupported actions explicitly and do not silently auto-merge patches. If either client lacks the guarded-write capability, describe the shared session as context-coordinated only.

Repository-wide Git mutations that alter HEAD/index/checkout require a worktree-wide guard among participating clients, because file guards alone cannot protect them. Shared-checkout commits can include another agent's staged changes; provide a staged-diff/ownership advisory before commit and never automatically stage, commit, reset or discard another contributor's work.

## Conflict response

Example response to a stale pending edit:

```json
{
  "outcome": "replan",
  "reason": "target_changed_since_read",
  "worktree_id": "worktree-main",
  "expected_hash": "sha256:old",
  "observed_hash": "sha256:new",
  "instruction": "Re-read the target and reconsider this edit against the new view."
}
```

This returns control to the agent rather than repeatedly asking the user for permission. If two incompatible intended changes still overlap after two automatic reconsideration attempts, surface the unresolved conflict and stop retrying that mutation. Memex can explain the conflicting evidence and registered task scopes; it does not choose whose product decision wins.

If the service is unavailable, default fail-open continuation explicitly loses context freshness and guarded-write assurance. A user may choose a strict cooperating-write policy separately; do not silently turn all Memex outages into a global development lock.

## Required acceptance matrix

| Case | Expected result |
| --- | --- |
| Two worktrees, same path, different API | Each session receives its own applicable claim/evidence |
| Unmerged B edit | A's current facts remain unchanged |
| Merge into A | A revalidates against merge contents before affected action |
| Shared checkout, body-only API change | Dependent caller receives correction despite unchanged signature |
| Shared checkout, unrelated change | No material interruption |
| Both sessions received C1 | Each independently receives and acknowledges its correction |
| Shared file changed after read | Stale expected hash requests replan |
| Guarded writers race on one file | Only a matching, current guarded write succeeds; loser replans |
| External write bypasses guard | Drift/coverage reported; no absolute prevention claim |
| Lease holder crashes | Lease expires, fencing rejects stale holder, no permanent lock |
| Concurrent Git mutation | Worktree guard or explicit unsupported/advisory outcome |
| Session IDs collide across hosts | Namespaced identities prevent cursor or task leakage |
| Compaction/resume | Fresh working-set delivery or verified continuity, no assumed retention |

Run the native-host matrix with both Claude-first and Codex-first orderings. A mocked fixture demonstrates core semantics only; P4 additionally requires real clients and recorded action ordering.

## Measured implementation (P4)

**Default mode** needs no new mechanism: each host session has its own task, cursor and receipts over the shared core, and its own host-namespaced adapter state. One host's acknowledgement never suppresses another's correction, which the native shared-checkout gate showed in both orderings.

**Guarded mode** is `memex/runtime/guard.py`, reached through the opt-in `guard_read`/`guard_write` server. Its supported set is whole-file create, update, delete and rename inside one worktree, singly or all-or-nothing, plus `git commit --only` of named paths under a worktree-wide lease. The fencing generation is enforced inside the guarded commit's SQLite write transaction, so a paused, replaced holder fails at commit. In guarded mode both adapters deny native edits rather than let them bypass the guard. Editors, shell writes, external Git and other machines remain outside it; their drift is detected at the next participating write, not prevented.

**Worktree isolation** comes from the P2 core: per-view verification, worktree-scoped claims and evidence, and test evidence bound to its tested view. P4 proved it on real linked worktrees, including merge, conflict resolution, cherry-pick and rebase.
