# Proposed context and action contracts

Status: planned v1 interfaces. Schema version: `memex.live.v1`. Existing v0.9 APIs remain separate compatible projections. Do not describe these records as shipped.

## Identity and scope

| Record | Required fields / meaning |
| --- | --- |
| RepositoryView | `repo_id`, `worktree_id`, nullable `head_commit`, `content_generation`, `indexed_generation`, `manifest_hash`; optional descriptive branch name |
| SessionIdentity | `harness`, `native_session_id`, `memex_session_id`, `principal_id`; global identity is not a bare host session string |
| EvidenceRef | `evidence_id`, `repo_id`, source path/symbol/span or test reference, exact input/content hash, source kind, observed time |
| ClaimRevision | `claim_id`, `revision_id`, assertion, authority, applicability, support expression, explicit supersession references |
| VerificationRecord | Claim revision + view + support hashes + predicate/check version + status + checked time + result evidence |
| TaskWorkingSet | `task_id`, session, repository/worktree, normalized intent, acceptance references, selected claim revisions, dependency subscriptions, delivery baseline |

Generations are nonnegative integers; completed index generation cannot exceed observed content generation. View identity excludes mutable readiness progress: indexing completion must not change which content was observed. A missing HEAD is allowed for an unborn repository. A branch name is never an identity or validity predicate.

Claim revisions are immutable. Whether one remains supported is evaluated **per applicable view**. The same revision can be supported in worktree A and unsupported in worktree B. Never mutate a globally shared claim's state because one branch changed.

## Support semantics

`support_sets` is an OR of sets; all evidence members within a set are necessary (AND). A claim is supported if at least one complete permitted set verifies in the requested view. Empty support is not equivalent to verified truth. A human-approved constraint may use an approval record plus an explicit applicability predicate instead of depending on every governed file.

Derived claims can depend on exact predecessor revisions. Informational "related to" and call/impact edges are not support edges. Test evidence records the tested view/input hashes, test definition/environment reference, outcome and verification scope. Passing at generation 4 does not certify generation 5 merely because the test name is unchanged.

## Distinct state dimensions

| Dimension | Values | Rule |
| --- | --- | --- |
| Source coverage | `complete`, `pending`, `parse_error`, `unsupported`, `unavailable` | Partial coverage cannot produce a fresh certificate |
| Verification | `supported`, `needs_revalidation`, `unsupported`, `unknown`, `conflicted` | Changed hash initially triggers revalidation |
| Claim lifecycle | `active`, `superseded`, `revoked` | Lifecycle transitions require scoped evidence or approved policy |
| Authority | `observed`, `inferred`, `human_approved` | Fresh inferred text remains inferred |
| Delivery | `pending`, `host_accepted`, `failed`, `resync_required` | Acknowledgement is exposure, not model agreement |

Keep existing confidence and temporal fields, but never map high confidence to supported verification. Check both transaction/observation time and valid-time applicability; exclude future facts unavailable at the requested view/evaluation checkpoint.

## Core interface signatures

These define responsibilities for later implementation plans; records above must be concretized before dependent waves start.

```text
resolve_view(worktree_path: str) -> RepositoryView
record_change(event: ChangeEvent) -> str  # durable event_id
ensure_indexed(view: RepositoryView, deadline_ms: int) -> IndexStatus
open_task(request: OpenTaskRequest) -> TaskSnapshot
check_action(request: ActionRequest) -> ActionCheck
ack_delivery(receipt: DeliveryReceipt) -> None
close_task(task_id: str, session: SessionIdentity) -> None
```

Async variants are allowed where the established integration style requires them; preserve names and semantic outcomes. Authentication/principal checking must precede task lookup. An action request cannot choose another session's delivery cursor.

| Interface record | Fields and validation boundary |
| --- | --- |
| ChangeEvent | `event_id`, `repo_id`, `worktree_id`, kind, source paths, observed time, optional actor/action reference; content is recaptured before indexing, not trusted from the notification |
| IndexStatus | Requested view, published view, coverage status, completed structural generation, semantic readiness, retryable reason; pending work is not complete |
| OpenTaskRequest | SessionIdentity, RepositoryView, normalized intent, acceptance references, requested packet budget; authorized worktree registration required |
| TaskSnapshot | Task identity, RepositoryView, bounded claim revisions and verification records, stream sequence, coverage, expiry and continuation token |
| DeliveryReceipt | Session/task, delta sequence, view, adapter version, host acceptance time, action-attempt reference and insertion outcome |

These records are typed core interfaces, not a commitment to five new public MCP tools. W06–W08 concretize their strict serialized schemas before adapters consume them.

`ActionRequest` includes an idempotency/action-attempt identifier, task/session, repository view, action kind, normalized target paths, relevant expected source hashes, last acknowledged delta sequence and adapter capabilities. A retry has a new attempt ID linked to the original action, so a replan does not look like a duplicated completed write.

`ActionCheck.outcome` is one of `proceed`, `replan`, `resync_required`, `unavailable`. It includes the checked view, coverage, optional delta, reason and freshness deadline. `proceed` certifies only the declared checked scope at that point; it is not write authorization or an atomic write token. `unavailable` is fail-open continuation with explicit unknown coverage. Known material corrections must not be downgraded to unavailable just to let an unchanged pending edit run.

## Delta example

```json
{
  "schema_version": "memex.live.v1",
  "task_id": "task-1",
  "session_id": "memex-claude-1",
  "sequence": 7,
  "base_sequence": 6,
  "view_id": "view-42",
  "changes": [
    {
      "operation": "replace",
      "previous_revision": "claim-17-r1",
      "revision": "claim-17-r2",
      "reason": "supporting API changed in this worktree",
      "assertion": "send() now requires an explicit timeout",
      "evidence_ids": ["evidence-api-42", "evidence-test-42"]
    }
  ]
}
```

Other operations: `add`, `retract`, `uncertain`, `conflict`. A retraction always identifies the prior delivered revision; a replacement explains applicability to this action. Old text already in the model's prompt cannot be erased. Corrections explicitly identify that text as obsolete or unsupported.

Sequences are monotonic per task/session stream. A mismatched base requires a full bounded resynchronization. The server retries unacknowledged deltas idempotently; the adapter deduplicates by stream/sequence. Acknowledgement advances only that stream after successful host insertion. Session resume proves continuity with an explicit resume token; otherwise open a new delivery baseline. After compaction, resupply the bounded working set unless the host verifies retained context.

## Budgets and failures

Retain existing packet item/character budgets until a measured token accounting adapter is added. Delta budgets cannot silently omit necessary corrections. Overflow produces `resync_required` and an explicit reason. A full snapshot identifies which earlier packet/revisions it replaces.

Bound automatic reconsideration to two attempts per original action by default. Persistent drift yields an explicit concurrency/indexing condition and stops automated retry; a shared-write conflict is not solved by blindly retrying the same patch. Network/backend outages use the configured fail-open policy with unknown freshness and recorded missing receipt.

Delivery receipts contain session/task, delta sequence, view, adapter version, accepted time and action attempt reference. Completion/test outcome references are distinct records. No receipt may claim the agent complied solely because insertion succeeded.
