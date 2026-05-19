import logging
from datetime import datetime, UTC
from pydantic import ValidationError
from memex.graph.client import get_graph_client
from memex.graph.schema import SymbolNode, DecisionNode, ModuleNode, Dependency
from memex.extractor.treesitter import SymbolDelta

logger = logging.getLogger(__name__)

class MemexSchemaError(Exception):
    """Raised when node data fails Pydantic validation."""
    def __init__(self, model_name: str, errors: list):
        self.model_name = model_name
        self.errors = errors
        super().__init__(f"Validation failed for {model_name}: {errors}")

async def write_symbol_delta(delta: SymbolDelta, source_commit: str | None = None) -> None:
    """
    Writes a SymbolDelta to Graphiti.
    """
    client = await get_graph_client()
    now = datetime.now(UTC)

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

        await client.add_episode(
            name=sym.name,
            episode_body=f"Symbol {sym.name} ({sym.kind}) added to {sym.file}. Signature: {sym.signature}. Line: {sym.line}",
            source_description=f"tree-sitter parse{' (commit ' + source_commit + ')' if source_commit else ''}",
            reference_time=now
        )

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
    # computed-confidence all silently fall back to defaults. Best-effort —
    # if the post-hoc SET fails we log but don't fail the write (the episode
    # is already in the graph; missing flags can be backfilled later).
    episode_uuid = getattr(getattr(result, "episode", None), "uuid", None)
    if episode_uuid is None:
        # Without a uuid the SET would have to fall back to a name match,
        # which is brittle (collisions across commits with the same short
        # SHA, or across repos). Better to log and skip than to mis-target
        # the wrong node. Reviewer non-blocker finding from review pass 2.
        logger.warning(
            "decision %s written but Graphiti returned no episode.uuid; "
            "v0.3.0 property SET skipped to avoid mis-targeting a sibling node",
            episode_name,
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
