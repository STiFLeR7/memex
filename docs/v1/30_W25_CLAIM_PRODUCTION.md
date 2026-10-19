# W25: claims in real repositories (design note)

Status: **design, 19 October 2026**, for [29](29_PHASE7_PLAN.md) W25. To be implemented test-first.

## Problem

Live Context only delivers and checks v1 claims (`ClaimRevision` with `EvidenceRef`s, stored by `runtime/supports.ClaimStore` in Neo4j). Only the evaluation (`evaluation/fixtures.py`, `evaluation/core_demo.py`) creates them. The v0.9 write tools (`mcp_server/tools_write.py`) write legacy Graphiti nodes; `module` and `symbol` are free text there. `memex v1 migrate` copies legacy facts as `legacy_unverified`, and those are never projected. So on a user's repository every packet is empty.

A second limit appears as soon as claims exist. The host adapters deliver every applicable claim (`host_adapter`: no `selected_revision_ids`; `PacketBudget(max_items=32, max_characters=CONTEXT_LIMIT)`, which is 10,000 for Claude Code and 4,000 for Codex). Over budget, `tasks` empties the whole frame and sets `resync_required` (`packet_budget_exceeded`). A repository with a few dozen decisions would get no context at all.

## Design

### 1. Agent-recorded decisions and problems become claims when they name code

`record_decision` and `record_problem` keep writing the legacy node exactly as today. They gain one optional argument, `files` (repo-relative paths). After the legacy write succeeds:

1. **Resolve evidence** against the current repository view, from the same capture and parse the hook service uses:
   - Each path in `files` that exists becomes `EvidenceRef(source_kind="source", path, content_hash, predicate="hash")`. After W21 this becomes `predicate="code"`.
   - If `symbol` names a symbol found in one of those files, add `predicate="symbol_exists"` evidence for it.
   - With no `files`, `module` is used only if it resolves to exactly one file: a dotted module to a path, or an exact path.
   - Paths outside the repository, or denied by `authorize_source`, are rejected, not dropped silently.
2. **Write the claim:** `ClaimRevision(claim_id="legacy:<node uuid>", revision_id="r1", authority="inferred", assertion=text, support_sets=((every evidence id),))`.
   - Every named file must hold for the claim to stand.
   - `inferred` because an agent recorded it; agent proposals never self-promote (03).
   - `observed_at` is the record time; `worktree_ids` is empty. Evidence is checked per view, so another worktree with different bytes sees `needs_revalidation`, never a false `supported`.
3. **Lifecycle:**
   - `supersedes=<legacy id>` with an existing claim writes revision `r{n+1}` with `supersedes=(previous,)`.
   - `resolve_problem` writes a `revoked` revision of the problem's claim.
   - `invalidate_edge` stays legacy-only.
4. **The tool's reply says what happened.** Either "bound to `a.py`, `b.py#Foo`; memex will tell agents when this code changes", or "not bound to code (no files named); stored as a note, not checked". No v1 claim is created without evidence, so `no_explicit_support` claims never reach a packet.
5. **The tool description asks agents to pass `files`.** That is the whole adoption path; nothing is inferred by an LLM.

No new tool is added. Structural claims derived from the index and test evidence are left out until the pilot shows whether agent-recorded claims are enough. They would multiply claim counts, and the budget behavior below must hold first.

### 2. Packets select within the budget instead of emptying

This applies to session-start (full) packets in `tasks.compile`:
- Order candidates as follows: `human_approved` first; then claims whose evidence paths include files the session has already touched (only if delivery state records them; to be checked during implementation, otherwise this step is skipped); then newest `observed_at`.
- Take items while both `max_items` and `max_characters` hold.
- Record the rest as coverage `("$omitted", "<n> claims")`, so the agent and the trace know the packet is partial.
- At an action check, a claim bound to the action's target file that was not delivered yet is added (operation `add`, which exists today), subject to the same per-frame budget.

Correction overflow keeps today's contract: a delta larger than the budget still resynchronizes (F04, F15). Only the full packet changes, from emptying to selecting.

## Tests (written first)

- **Unit:** resolution of `files`/`module`/`symbol` to evidence; rejection of missing and out-of-repository paths; claim ids and authority; supersede and resolve lifecycles; the tool replies; the full packet selects by priority within items and characters; `$omitted` coverage; corrections still resynchronize on overflow.
- **Exit (29, W25):** on a fresh clone of a real repository (memex itself), with the Neo4j a user already runs:
  1. `record_decision(files=[...])` creates a claim.
  2. A new Claude Code session gets a non-empty packet.
  3. A second clone changes the cited symbol's behavior and pushes; after a pull, the next edit check delivers a correction.
  4. An unrelated change produces none.
  5. With 60 recorded claims, a Codex packet is non-empty and reports the omitted count.

## Not changed

Legacy nodes, `legacy_unverified` migration, the claim store's authorization rules, the verification algorithm (W21 adds a predicate, separately) and S05.
