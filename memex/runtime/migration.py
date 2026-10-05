"""W18: v1 rollout modes, legacy migration and rollback.

Modes, per worktree, in `<common>/memex/v1-mode.json`:

* `off`: host hooks abstain; no packet, no check, nothing recorded as delivered.
* `shadow`: checks run and their would-be outcomes are recorded, but nothing is
  delivered and nothing is denied. Shadow never claims an agent was corrected.
* `live`: the native action loop (the default, which is how installed hooks
  behaved before modes existed).

Guarded writes are a separate, independent option (`memex.runtime.guard`).

Legacy migration never rewrites a legacy node. It adds v1 labels beside them:

1. detect versions and record the graph connection without credentials;
2. inventory: dry-run counts by legacy type, before any write;
3. map each legacy repository path to a repository/worktree identity from the
   Git checkout itself, never from a matching remote URL;
4. ensure the additive v1 constraints;
5. publish a complete structural view from current source bytes;
6. import legacy decisions and problems into a separate `MemexLegacyClaim`
   projection with `legacy_unverified` coverage, preserving approved authority,
   timestamps and confidence, in bounded batches. Each batch and its checkpoint
   commit in one transaction, so an interruption resumes exactly where it
   stopped and a repeated run writes nothing new.

Legacy claims are withheld from verified current context: they have no
reconstructable evidence bytes or applicable view, so they are available for
explicit historical retrieval only.
"""
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
import time
from importlib import metadata
from pathlib import Path

SCHEMA_VERSION = "memex.v1.migration.1"
LEGACY_KINDS = ("Decision", "Problem")
DEFAULT_BATCH = 100


# --------------------------------------------------------------------------- #
# Modes
# --------------------------------------------------------------------------- #

from memex.runtime.modes import MODES, mode_path, read_mode  # noqa: E402
from memex.runtime.modes import read_modes as _modes  # noqa: E402


def set_mode(registration, mode: str) -> dict:
    """Switch one worktree's mode.

    Entering `live` from anything else forces every bound session of this
    worktree to resynchronize: old receipts do not show that a restarted or
    long-idle model still retains its context, so the next check delivers a
    full replacement packet before any affected action proceeds.
    """
    if mode not in MODES:
        raise ValueError(f"mode is one of {MODES}")
    previous = read_mode(registration)
    modes = _modes(registration)
    modes[registration.worktree_id] = {"mode": mode, "changed_at": time.time(), "previous": previous}
    path = mode_path(registration)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(modes), encoding="utf-8")
    os.replace(temporary, path)
    resynchronized = 0
    if mode == "live" and previous != "live":
        resynchronized = require_resynchronization(registration)
    return {"worktree_id": registration.worktree_id, "previous": previous, "mode": mode,
            "resynchronized_sessions": resynchronized}


def require_resynchronization(registration) -> int:
    """Mark every bound session of this worktree as not retaining its context."""
    try:
        db = sqlite3.connect(registration.runtime_path, timeout=10)
    except sqlite3.Error:
        return 0
    try:
        with db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "live_adapter_sessions" not in tables:
                return 0
            return db.execute("UPDATE live_adapter_sessions SET context_retained=0").rowcount
    finally:
        db.close()


# --------------------------------------------------------------------------- #
# Rollback
# --------------------------------------------------------------------------- #

def rollback(registration, *, now: float | None = None) -> dict:
    """Disable v1 for one worktree, keeping everything needed to recover.

    Live adapters go to `off`, guarded writes return to context mode and
    cooperating leases are expired so no writer stays blocked. The journal,
    repository mapping, evidence, migration records, trace and any unresolved
    interrupted write are kept. Legacy nodes were never changed, so legacy
    readers see exactly what they saw before migration.
    """
    from memex.runtime import guard

    now = time.time() if now is None else now
    mode = set_mode(registration, "off")
    was_guarded = guard.guarded(registration)
    guard.set_mode(registration, "context")
    expired, unresolved = 0, 0
    db = sqlite3.connect(registration.runtime_path, timeout=30)
    try:
        with db:
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if "guard_leases" in tables:
                expired = db.execute("UPDATE guard_leases SET expires_at=? WHERE worktree_id=? AND expires_at>?",
                                     (now, registration.worktree_id, now)).rowcount
        intent_dir = Path(registration.common_dir) / "memex" / "guard" / registration.worktree_id
        unresolved = len(list(intent_dir.glob("intent-*.json"))) if intent_dir.exists() else 0
    finally:
        db.close()
    return {"worktree_id": registration.worktree_id, "mode": mode, "guarded_was_on": was_guarded,
            "leases_expired": expired, "unresolved_interrupted_writes_kept": unresolved,
            "kept": ["journal", "repository mapping", "evidence and claims", "migration records", "trace"]}


# --------------------------------------------------------------------------- #
# Legacy migration
# --------------------------------------------------------------------------- #

def detect() -> dict:
    """Runtime and configuration versions, with the graph location but never credentials."""
    from memex.config import get_config
    try:
        uri = get_config().neo4j_uri
    except Exception:  # noqa: BLE001 - configuration may be incomplete during a dry run
        uri = os.getenv("NEO4J_URI") or os.getenv("MEMEX_LIVE_NEO4J_URI")
    return {"memex": metadata.version("memex-mcp"), "migration_schema": SCHEMA_VERSION,
            "graph_uri": uri, "credentials": "not recorded"}


async def inventory(driver, repo_path: str) -> dict:
    """Dry-run counts of what a legacy repository holds. Writes nothing."""
    result = await driver.execute_query(
        "MATCH (n) WHERE n.repo_path = $repo RETURN coalesce(n.type, labels(n)[0]) AS kind, count(n) AS n "
        "ORDER BY kind", params={"repo": repo_path})
    counts = {row["kind"] or "untyped": row["n"] for row in result.records}
    ambiguous = await driver.execute_query(
        "MATCH (n:Entity) WHERE n.repo_path = $repo AND n.type IS NULL AND n.name CONTAINS 'Decision' "
        "RETURN count(n) AS n", params={"repo": repo_path})
    return {"repo_path": repo_path, "by_kind": counts,
            "importable": sum(counts.get(k, 0) for k in LEGACY_KINDS),
            "structural_rebuilt_not_imported": sum(counts.get(k, 0) for k in ("Symbol", "Module")),
            "untyped_decision_named_not_imported": ambiguous.records[0]["n"]}


def map_repository(repo_path: str) -> dict:
    """Identity from the checkout itself. A path that is not a Git checkout stays unmapped."""
    from memex.runtime.views import discover_repository
    path = Path(repo_path)
    if not path.is_dir():
        return {"legacy_repo_path": repo_path, "status": "unmapped", "reason": "path does not exist"}
    try:
        registration = discover_repository(path)
    except Exception as exc:  # noqa: BLE001 - reported, never guessed
        return {"legacy_repo_path": repo_path, "status": "unmapped", "reason": f"not a Git checkout: {exc}"}
    return {"legacy_repo_path": repo_path, "status": "mapped", "repo_id": registration.repo_id,
            "worktree_id": registration.worktree_id, "root": registration.root,
            "linked_worktree": Path(registration.common_dir).resolve() != (Path(registration.root) / ".git").resolve()}


async def ensure_schema(driver) -> None:
    """Additive constraints only. No legacy label or index is touched."""
    for statement in (
        "CREATE CONSTRAINT memex_legacy_claim_identity IF NOT EXISTS "
        "FOR (n:MemexLegacyClaim) REQUIRE (n.repo_id, n.legacy_key) IS UNIQUE",
        "CREATE CONSTRAINT memex_migration_identity IF NOT EXISTS "
        "FOR (n:MemexMigration) REQUIRE (n.repo_id, n.migration_id) IS UNIQUE",
        "CREATE CONSTRAINT memex_repository_mapping IF NOT EXISTS "
        "FOR (n:MemexRepositoryMapping) REQUIRE n.legacy_repo_path IS UNIQUE",
    ):
        await driver.execute_query(statement)


WRITE_CLAIM = """
MERGE (c:MemexLegacyClaim {repo_id: $repo_id, legacy_key: $key})
  ON CREATE SET c.migrated_at = $now, c.migration_id = $migration_id
SET c += $props
"""


async def _import_batch(driver, *, repo_id: str, migration_id: str, repo_path: str, batch_size: int) -> dict:
    """One batch and its checkpoint, in one write transaction."""
    now = time.time()

    async def work(tx):
        result = await tx.run(
            "MERGE (m:MemexMigration {repo_id: $repo, migration_id: $id}) "
            "ON CREATE SET m.schema_version = $schema, m.cursor = '', m.completed = 0, m.status = 'running', "
            "m.started_at = $now, m.legacy_repo_path = $path "
            "RETURN m.cursor AS cursor, m.status AS status, m.completed AS completed",
            repo=repo_id, id=migration_id, schema=SCHEMA_VERSION, now=now, path=repo_path)
        checkpoint = await result.single()
        if checkpoint["status"] == "complete":
            return {"status": "complete", "completed": checkpoint["completed"], "batch_size": 0,
                    "cursor": checkpoint["cursor"]}
        result = await tx.run(
            "MATCH (n:Entity) WHERE n.repo_path = $path AND n.type IN $kinds "
            "AND coalesce(n.uuid, elementId(n)) > $cursor "
            "RETURN coalesce(n.uuid, elementId(n)) AS key, properties(n) AS props "
            "ORDER BY key LIMIT $batch",
            path=repo_path, kinds=list(LEGACY_KINDS), cursor=checkpoint["cursor"], batch=batch_size)
        rows = [record async for record in result]
        for row in rows:
            n = row["props"]
            await tx.run(WRITE_CLAIM, repo_id=repo_id, key=row["key"], now=now, migration_id=migration_id,
                         props=_legacy_projection(n, repo_path))
        cursor = rows[-1]["key"] if rows else checkpoint["cursor"]
        status = "complete" if len(rows) < batch_size else "running"
        await tx.run("MATCH (m:MemexMigration {repo_id: $repo, migration_id: $id}) "
                     "SET m.cursor = $cursor, m.completed = m.completed + $count, m.status = $status, "
                     "m.updated_at = $now",
                     repo=repo_id, id=migration_id, cursor=cursor, count=len(rows), status=status, now=now)
        return {"status": status, "completed": checkpoint["completed"] + len(rows), "batch_size": len(rows),
                "cursor": cursor}

    async with driver.session() as session:
        return await session.execute_write(work)


def _legacy_projection(n: dict, repo_path: str) -> dict:
    """What a legacy node becomes in the v1 projection. Its authority and history are kept."""
    created = n.get("created_at")
    return {
        "legacy_repo_path": repo_path, "kind": n.get("type"), "name": n.get("name"),
        "text": n.get("summary") or n.get("name"),
        "authority": "human_approved" if n.get("validated") else "inferred",
        "coverage": "legacy_unverified", "status": "legacy_unverified", "current_context_eligible": False,
        "legacy_created_at": None if created is None else str(created),
        "legacy_confidence": n.get("confidence"), "legacy_base_confidence": n.get("base_confidence"),
        "legacy_validated": bool(n.get("validated")), "legacy_corroborated": bool(n.get("corroborated")),
        "legacy_supersedes": n.get("supersedes"), "legacy_source": n.get("source"),
        "legacy_harness": n.get("harness"), "legacy_excluded": bool(n.get("excluded")),
        "schema_version": SCHEMA_VERSION,
    }


async def migrate(driver, repo_path: str, *, dry_run: bool = False, batch_size: int = DEFAULT_BATCH,
                  index: bool = True, max_batches: int | None = None, on_batch=None) -> dict:
    """Run (or preview) the migration for one legacy repository path.

    `max_batches` and `on_batch` exist so an interruption can be placed at a
    batch boundary in tests; production runs leave them unset. Re-running after
    any interruption, or after completion, is safe.
    """
    report: dict = {"detect": detect(), "repo_path": repo_path, "dry_run": dry_run,
                    "inventory": await inventory(driver, repo_path), "mapping": map_repository(repo_path)}
    mapping = report["mapping"]
    if dry_run:
        return report
    if mapping["status"] != "mapped":
        report["status"] = "skipped_unmapped"
        return report
    await ensure_schema(driver)
    await driver.execute_query(
        "MERGE (r:MemexRepositoryMapping {legacy_repo_path: $path}) "
        "ON CREATE SET r.created_at = $now "
        "SET r.repo_id = $repo_id, r.worktree_id = $worktree_id, r.root = $root, r.schema_version = $schema",
        params={"path": repo_path, "now": time.time(), "repo_id": mapping["repo_id"],
                "worktree_id": mapping["worktree_id"], "root": mapping["root"], "schema": SCHEMA_VERSION})
    if index:
        from memex.runtime.coordinator import RepositoryIndexer
        from memex.runtime.graph import StructuralGraphStore
        from memex.runtime.views import discover_repository
        result = await RepositoryIndexer(discover_repository(mapping["root"]), StructuralGraphStore(driver)).refresh()
        report["structural_view"] = {"view_id": result.view.view_id, "generation": result.view.content_generation}
    migration_id = f"legacy-import:{repo_path}"
    batches = 0
    while True:
        if max_batches is not None and batches >= max_batches:
            report["status"] = "interrupted"
            break
        row = await _import_batch(driver, repo_id=mapping["repo_id"], migration_id=migration_id,
                                  repo_path=repo_path, batch_size=batch_size)
        if row["batch_size"] == 0 and row["status"] == "complete" and batches == 0:
            report["status"] = "complete"
            break
        batches += 1
        if on_batch:
            on_batch(batches, row)
        if row["status"] == "complete":
            report["status"] = "complete"
            break
    state = await migration_state(driver, mapping["repo_id"], migration_id)
    report.update(batches_this_run=batches, checkpoint=state)
    return report


async def migration_state(driver, repo_id: str, migration_id: str) -> dict | None:
    result = await driver.execute_query(
        "MATCH (m:MemexMigration {repo_id: $repo, migration_id: $id}) RETURN m {.*} AS m",
        params={"repo": repo_id, "id": migration_id})
    return dict(result.records[0]["m"]) if result.records else None


async def legacy_claims(driver, repo_id: str) -> list[dict]:
    """Explicit historical retrieval. These never enter a verified current packet."""
    result = await driver.execute_query(
        "MATCH (c:MemexLegacyClaim {repo_id: $repo}) RETURN c {.*} AS c ORDER BY c.legacy_key",
        params={"repo": repo_id})
    return [dict(r["c"]) for r in result.records]


async def legacy_snapshot(driver, repo_path: str) -> str:
    """A canonical digest of everything legacy readers can see for one repository."""
    import hashlib
    nodes = await driver.execute_query(
        "MATCH (n) WHERE n.repo_path = $repo RETURN labels(n) AS labels, properties(n) AS props, elementId(n) AS id",
        params={"repo": repo_path})
    edges = await driver.execute_query(
        "MATCH (a)-[r]->(b) WHERE a.repo_path = $repo OR b.repo_path = $repo "
        "RETURN elementId(a) AS a, type(r) AS type, properties(r) AS props, elementId(b) AS b",
        params={"repo": repo_path})
    canonical = json.dumps({
        "nodes": sorted((json.dumps([sorted(r["labels"]), r["id"], r["props"]], sort_keys=True, default=str))
                        for r in nodes.records),
        "edges": sorted((json.dumps([r["a"], r["type"], r["b"], r["props"]], sort_keys=True, default=str))
                        for r in edges.records),
    }, sort_keys=True)
    return "sha256:" + hashlib.sha256(canonical.encode()).hexdigest()


def run(coroutine):
    return asyncio.run(coroutine)
