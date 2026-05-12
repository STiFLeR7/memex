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

async def write_decision(decision, modules: list[str], commit_sha: str) -> None:
    """
    Writes a technical decision to Graphiti.
    """
    client = await get_graph_client()
    now = datetime.now(UTC)

    try:
        # Validate
        DecisionNode(
            text=decision.text,
            rationale=decision.rationale,
            scope=decision.scope,
            created_at=now,
            source_commit=commit_sha
        )
    except ValidationError as e:
        raise MemexSchemaError("DecisionNode", e.errors())

    await client.add_episode(
        name=f"decision_{commit_sha[:8]}",
        episode_body=f"Decision: {decision.text}. Rationale: {decision.rationale}. Scope: {decision.scope}. Affected modules: {', '.join(modules)}",
        source_description=f"git commit {commit_sha}",
        reference_time=now
    )
