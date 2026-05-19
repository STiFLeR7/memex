import logging
from datetime import datetime, UTC
from pydantic import ValidationError
from memex.graph.client import get_graph_client
from memex.graph.schema import SymbolNode, DecisionNode, ModuleNode
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
