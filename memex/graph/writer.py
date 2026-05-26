import logging
from datetime import datetime, UTC
from pydantic import ValidationError
from memex.graph.client import get_graph_client
from memex.graph.schema import SymbolNode, DecisionNode, Dependency
from memex.extractor.treesitter import SymbolDelta

logger = logging.getLogger(__name__)

class MemexSchemaError(Exception):
    """Raised when node data fails Pydantic validation."""
    def __init__(self, model_name: str, errors: list):
        self.model_name = model_name
        self.errors = errors
        super().__init__(f"Validation failed for {model_name}: {errors}")


class MemexWriteError(Exception):
    """Raised when node creation fails or cannot be verified."""


#: Structured-symbol MERGE. Mirrors the post-hoc Cypher pattern already used
#: by write_decision / write_lockfile_delta (ARCHITECTURE-v0.3.0 §4 Q1): the
#: NL add_episode call gives Graphiti a search surface, but the *structured*
#: fields predict_impact relies on (`file`, `kind`, `line`, `type='Symbol'`,
#: `repo_path`) are never parsed out of NL prose, so we write them inline.
#: Without this, `predict_impact`'s `MATCH (src:Entity) WHERE src.file=$file`
#: matches nothing and the tool returns empty for every file (BUG_0.3.6.md).
_SYMBOL_MERGE_QUERY = """
MERGE (s:Entity {name: $name, file: $file, repo_path: $repo})
  ON CREATE SET s.type = 'Symbol',
                s.kind = $kind,
                s.signature = $signature,
                s.line = $line,
                s.valid_from = $now,
                s.valid_until = NULL,
                s.source_commit = $source_commit,
                s.write_policy = 'locked',
                s.access_count = 0,
                s.last_reinforced_at = $now
  ON MATCH SET  s.type = 'Symbol',
                s.kind = $kind,
                s.signature = $signature,
                s.line = $line,
                s.valid_until = NULL,
                s.source_commit = coalesce($source_commit, s.source_commit),
                s.last_reinforced_at = $now
"""


async def _merge_structured_symbol(
    client, sym, repo_root: str | None, now, source_commit: str | None
) -> None:
    """Materialize a queryable Symbol node alongside its NL episode."""
    try:
        await client.driver.execute_query(
            _SYMBOL_MERGE_QUERY,
            params={
                "name": sym.name,
                "file": sym.file,
                "repo": repo_root,
                "kind": sym.kind,
                "signature": sym.signature,
                "line": sym.line,
                "now": now,
                "source_commit": source_commit,
            },
        )
    except Exception:
        logger.warning(
            "structured Symbol MERGE failed for %s in %s; predict_impact may "
            "not see this symbol until the next index pass",
            sym.name,
            sym.file,
            exc_info=True,
        )


async def write_symbol_delta(
    delta: SymbolDelta,
    source_commit: str | None = None,
    repo_root: str | None = None,
) -> None:
    """
    Writes a SymbolDelta to Graphiti.

    Each added/modified symbol is written twice, by design:
      1. ``add_episode`` — NL prose so Graphiti's search/embeddings see it.
      2. a post-hoc structured MERGE (:Entity {type:'Symbol'}) carrying the
         queryable ``file``/``kind``/``line``/``repo_path`` props that
         ``predict_impact`` traverses. (v0.3.7 Layer 1)
    """
    client = await get_graph_client()
    now = datetime.now(UTC)
    episodes_skipped = 0

    # 1. Added symbols
    for sym in delta.added:
        try:
            # Validate
            SymbolNode(
                name=sym.name,
                kind=sym.kind,
                signature=sym.signature,
                file=sym.file,
                line=sym.line,
                valid_from=now,
                source_commit=source_commit
            )
        except ValidationError as e:
            raise MemexSchemaError("SymbolNode", e.errors())

        # Deterministic, LLM-free structured node FIRST — predict_impact depends
        # on it and must not be blocked by Gemini quota / rate limits.
        await _merge_structured_symbol(client, sym, repo_root, now, source_commit)

        # NL episode is a best-effort search surface. If the LLM extraction
        # fails (e.g. 429 spend cap) we log and move on — the structured node
        # above is already persisted.
        try:
            await client.add_episode(
                name=sym.name,
                episode_body=f"Symbol {sym.name} ({sym.kind}) added to {sym.file}. Signature: {sym.signature}. Line: {sym.line}",
                source_description=f"tree-sitter parse{' (commit ' + source_commit + ')' if source_commit else ''}",
                reference_time=now
            )
        except Exception:
            episodes_skipped += 1
            logger.warning(
                "add_episode failed for symbol %s (%s); structured node was "
                "still written, NL search surface skipped",
                sym.name,
                sym.file,
                exc_info=True,
            )

    # 1b. Modified symbols — refresh the structured node's signature/line so
    # predict_impact sees current shape. No new episode (the symbol already
    # has one); just keep the queryable node accurate.
    for sym in delta.modified:
        await _merge_structured_symbol(client, sym, repo_root, now, source_commit)

    # 2. Removed symbols
    for sym in delta.removed:
        # Invalidate in graph
        query = """
        MATCH (s:Entity {name: $name})
        WHERE (s.type = 'Symbol' OR s.name CONTAINS 'Symbol') AND s.file = $file
        SET s.valid_until = $now
        """
        await client.driver.execute_query(query, params={
            "name": sym.name,
            "file": sym.file,
            "now": now
        })

    return {
        "symbols": len(delta.added) + len(delta.modified),
        "episodes_skipped": episodes_skipped,
    }

#: CALLS-edge MERGE. Resolution is deliberately CONSERVATIVE: a call-site's
#: callee name is linked only when it resolves to exactly ONE structured Symbol
#: node in the repo (``size(cs)=1``). Ambiguous names (e.g. a `run` defined in
#: five files) produce no edge in v1 rather than fan-out false coupling. Calls
#: to stdlib/builtins (no Symbol node) naturally produce no edge. This is what
#: predict_impact traverses, so precision beats recall here. (v0.3.7 Layer 2)
_CALL_EDGE_QUERY = """
MATCH (caller:Entity {name: $caller, file: $file, repo_path: $repo})
WHERE caller.type = 'Symbol'
MATCH (callee:Entity {name: $callee, repo_path: $repo})
WHERE callee.type = 'Symbol'
WITH caller, collect(DISTINCT callee) AS cs
WHERE size(cs) = 1
UNWIND cs AS callee
MERGE (caller)-[r:CALLS]->(callee)
  ON CREATE SET r.created_at = $now,
                r.expired_at = NULL,
                r.line = $line,
                r.last_reinforced_at = $now
  ON MATCH SET  r.expired_at = NULL,
                r.line = $line,
                r.last_reinforced_at = $now
RETURN count(r) AS n
"""


async def write_call_edges(calls, repo_root: str | None = None) -> int:
    """Persist CALLS edges for a file's resolved call-sites.

    Expects :class:`memex.extractor.treesitter.CallEdge` items. Returns the
    number of edges written/refreshed. Best-effort per edge — a failure on one
    call-site is logged and does not abort the rest.
    """
    if not calls:
        return 0

    client = await get_graph_client()
    now = datetime.now(UTC)
    written = 0

    for edge in calls:
        # Skip self-recursion: a function calling itself isn't cross-symbol
        # coupling and would just be noise.
        if edge.caller == edge.callee:
            continue
        try:
            res = await client.driver.execute_query(
                _CALL_EDGE_QUERY,
                params={
                    "caller": edge.caller,
                    "callee": edge.callee,
                    "file": edge.file,
                    "repo": repo_root,
                    "now": now,
                    "line": edge.line,
                },
            )
            if res.records:
                written += int(res.records[0].get("n") or 0)
        except Exception:
            logger.warning(
                "CALLS edge write failed for %s -> %s in %s",
                edge.caller,
                edge.callee,
                edge.file,
                exc_info=True,
            )

    return written


async def _get_episode_uuid(client, episode_name: str, episode_uuid: str | None) -> str | None:
    """
    Returns episode_uuid if already known.
    If None, attempts a fallback Cypher lookup by episode name:
        MATCH (n:Entity) WHERE n.name = $name RETURN coalesce(n.uuid, elementId(n)) as uuid LIMIT 1
    Returns None if still not found.
    """
    if episode_uuid is not None:
        return episode_uuid
    query = "MATCH (n:Entity) WHERE n.name = $name RETURN coalesce(n.uuid, elementId(n)) as uuid LIMIT 1"
    try:
        res = await client.driver.execute_query(query, params={"name": episode_name})
        if res.records:
            return res.records[0]["uuid"]
    except Exception:
        pass
    return None


async def write_decision(decision, modules: list[str], commit_sha: str, confidence: float = 1.0, source: str = "watcher") -> None:
    """
    Writes a technical decision to Graphiti.
    """
    client = await get_graph_client()
    now = datetime.now(UTC)

    # v0.3.0: preserve any v0.3.0 fields set by the synthesizer (validated,
    # base_confidence) and seed last_reinforced_at = created_at so the
    # computed-confidence helper in memex.graph.confidence has an anchor.
    # Use a sentinel-check style so MagicMock instances (which auto-create
    # attributes) fall through to the defaults cleanly.
    def _real_attr(obj, name, default):
        val = getattr(obj, name, default)
        # MagicMock auto-creates attributes; reject anything that isn't a
        # plain JSON-friendly scalar of the expected type.
        if val is default:
            return default
        if isinstance(default, bool) and not isinstance(val, bool):
            return default
        if isinstance(default, (int, float)) and not isinstance(val, (int, float)):
            return default
        if isinstance(default, str) and not isinstance(val, str):
            return default
        return val

    validated = bool(_real_attr(decision, "validated", False))
    base_confidence = float(_real_attr(decision, "base_confidence", confidence))
    decision_source = _real_attr(decision, "source", source) or source

    try:
        # Validate
        DecisionNode(
            text=decision.text,
            rationale=decision.rationale,
            scope=decision.scope,
            created_at=now,
            source_commit=commit_sha,
            confidence=confidence,
            source=decision_source,
            validated=validated,
            base_confidence=base_confidence,
            last_reinforced_at=now,
        )
    except ValidationError as e:
        raise MemexSchemaError("DecisionNode", e.errors())

    episode_name = f"decision_{commit_sha[:8]}"
    result = await client.add_episode(
        name=episode_name,
        episode_body=(
            f"Decision: {decision.text}. Rationale: {decision.rationale}. "
            f"Scope: {decision.scope}. Affected modules: {', '.join(modules)} "
            f"(Confidence: {confidence}, Source: {decision_source}, "
            f"Validated: {validated}, BaseConfidence: {base_confidence})"
        ),
        source_description=f"git commit {commit_sha}",
        reference_time=now,
    )

    # Post-hoc Cypher SET for programmatic flags Graphiti doesn't parse from
    # NL (ARCHITECTURE-v0.3.0 §4, Q1). Without this the v0.3.0 fields are
    # validated by Pydantic but never reach Neo4j as queryable properties, so
    # `memex review` ordering, count_unvalidated_decisions, and TempValid
    # computed-confidence all silently fall back to defaults.
    episode_uuid = getattr(getattr(result, "episode", None), "uuid", None)
    episode_uuid = await _get_episode_uuid(client, episode_name, episode_uuid)
    if episode_uuid is None:
        raise MemexWriteError(
            f"write_decision: episode '{episode_name}' not found after add_episode "
            f"and fallback query. Graphiti may be in an inconsistent state."
        )
    else:
        set_query = """
        MATCH (n:Entity)
        WHERE n.uuid = $uuid OR elementId(n) = $uuid
        SET n.validated = $validated,
            n.base_confidence = $base_confidence,
            n.last_reinforced_at = $now,
            n.source = $source,
            n.source_commit = $commit_sha,
            n.write_policy = 'open',
            n.access_count = coalesce(n.access_count, 0)
        """
        try:
            await client.driver.execute_query(
                set_query,
                params={
                    "uuid": episode_uuid,
                    "validated": validated,
                    "base_confidence": base_confidence,
                    "now": now,
                    "source": decision_source,
                    "commit_sha": commit_sha,
                },
            )
        except Exception:
            logger.warning(
                "post-hoc property SET failed for decision %s; v0.3.0 fields "
                "may be missing on the node and require backfill",
                episode_name,
                exc_info=True,
            )


# ---------------------------------------------------------------------------
# v0.3.1 Deliverable 5 — IMPORTS edges + Dependency nodes from lockfiles
# ---------------------------------------------------------------------------


#: Initial confidence anchor for IMPORTS edges. Lockfile-derived edges
#: come from deterministic AST parsing — higher than the watcher Decision
#: default (0.6) but not 1.0 so two-regime decay can still surface
#: long-stale imports.
_IMPORT_EDGE_BASE_CONFIDENCE = 0.9

_DEPENDENCY_BASE_CONFIDENCE = 0.95


async def write_lockfile_delta(
    repo_root: str,
    dependencies: list[Dependency],
    imports: list[tuple[str, str, dict]],
) -> dict[str, int]:
    """Persist Dependency nodes + Module IMPORTS edges from lockfile parsing.

    Wired into :func:`memex.watcher.handlers.handle_lockfile_change`. Both
    writes follow the v0.3.0 hybrid pattern (Q1 in ARCHITECTURE §4):

    - Dependency nodes use ``client.add_episode`` so the NL Graphiti
      pipeline can still surface them in search, with a post-hoc Cypher
      SET for the v0.3.0 fields (``write_policy``, ``last_reinforced_at``,
      ``base_confidence``) that Graphiti doesn't parse from NL.

    - IMPORTS edges are pure structure (no NL surface). We MERGE the
      Module endpoints (idempotent — the watcher may have already
      created them) and MERGE the ``IMPORTS`` edge with the v0.3.0
      fields written inline so future composite-reranker filters
      (``WHERE r.expired_at IS NULL``) see them.

    Returns a ``{"deps_written": N, "edges_written": M}`` summary used by
    the watcher log line so re-runs are observable.
    """
    client = await get_graph_client()
    now = datetime.now(UTC)
    deps_written = 0
    edges_written = 0

    # 1. Dependencies — episode + post-hoc SET
    for dep in dependencies:
        episode_name = f"dependency_{dep.ecosystem}_{dep.name}"
        try:
            result = await client.add_episode(
                name=episode_name,
                episode_body=(
                    f"Dependency: {dep.name} version {dep.version} "
                    f"({dep.ecosystem} ecosystem)."
                ),
                source_description=f"lockfile scan in {repo_root}",
                reference_time=now,
            )
        except Exception:
            logger.warning(
                "lockfile: add_episode failed for dependency %s",
                episode_name,
                exc_info=True,
            )
            continue

        episode_uuid = getattr(getattr(result, "episode", None), "uuid", None)
        set_query = """
        MATCH (n:Entity)
        WHERE n.uuid = $uuid OR elementId(n) = $uuid
        SET n.type = 'Dependency',
            n.ecosystem = $ecosystem,
            n.version = $version,
            n.last_updated = $now,
            n.last_reinforced_at = $now,
            n.base_confidence = $base_confidence,
            n.write_policy = 'locked',
            n.repo_path = $repo,
            n.access_count = coalesce(n.access_count, 0)
        """
        if episode_uuid is None:
            logger.debug(
                "lockfile: dependency %s missing episode.uuid; "
                "skipping v0.3.0 SET to avoid mis-targeting",
                episode_name,
            )
        else:
            try:
                await client.driver.execute_query(
                    set_query,
                    params={
                        "uuid": episode_uuid,
                        "ecosystem": dep.ecosystem,
                        "version": dep.version,
                        "now": now,
                        "base_confidence": _DEPENDENCY_BASE_CONFIDENCE,
                        "repo": repo_root,
                    },
                )
                deps_written += 1
            except Exception:
                logger.warning(
                    "lockfile: post-hoc SET failed for dependency %s",
                    episode_name,
                    exc_info=True,
                )

    # 2. IMPORTS edges — MERGE Module endpoints + MERGE edge
    # We rely on the watcher having created Entity rows for these modules
    # under name=module_path; if they don't exist yet we create them with
    # type='Module' so the edge always has both endpoints. The watcher's
    # symbol pass will fill in language / created_at on its next visit.
    edge_query = """
    MERGE (src:Entity {name: $from_path, repo_path: $repo})
      ON CREATE SET src.type = 'Module',
                    src.created_at = $now,
                    src.write_policy = 'locked',
                    src.access_count = 0
    MERGE (dst:Entity {name: $to_path, repo_path: $repo})
      ON CREATE SET dst.type = 'Module',
                    dst.created_at = $now,
                    dst.write_policy = 'locked',
                    dst.access_count = 0
    MERGE (src)-[r:IMPORTS]->(dst)
      ON CREATE SET r.created_at = $now,
                    r.base_confidence = $base_confidence,
                    r.kind = $kind,
                    r.expired_at = NULL,
                    r.last_reinforced_at = $now
      ON MATCH SET  r.last_reinforced_at = $now,
                    r.kind = $kind,
                    r.expired_at = NULL
    """
    for from_module, to_module, meta in imports:
        from_path = _dotted_to_repo_path(from_module)
        to_path = _dotted_to_repo_path(to_module)
        if not from_path or not to_path or from_path == to_path:
            continue
        try:
            await client.driver.execute_query(
                edge_query,
                params={
                    "from_path": from_path,
                    "to_path": to_path,
                    "repo": repo_root,
                    "now": now,
                    "base_confidence": _IMPORT_EDGE_BASE_CONFIDENCE,
                    "kind": meta.get("kind", "import"),
                },
            )
            edges_written += 1
        except Exception:
            logger.warning(
                "lockfile: IMPORTS edge write failed for %s -> %s",
                from_path,
                to_path,
                exc_info=True,
            )

    return {"deps_written": deps_written, "edges_written": edges_written}


def _dotted_to_repo_path(name: str) -> str:
    """Map a dotted Python module name back to its repo-relative path.

    ``extract_module_imports`` returns dotted names (``memex.watcher.handlers``);
    Module nodes elsewhere are stored under ``name=<repo-relative path>``
    (``memex/watcher/handlers.py``). We append ``.py`` because the import
    extractor only walks Python today. If the input already looks like a
    path (contains ``/`` or ends with a known extension), passthrough.
    """
    if not name:
        return ""
    if "/" in name or "\\" in name:
        return str(name).replace("\\", "/")
    if "." in name and not name.endswith(".py"):
        return name.replace(".", "/") + ".py"
    return name
