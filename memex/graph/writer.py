import logging
from datetime import datetime, UTC
from memex.graph.client import get_graph_client
from memex.graph.schema import Symbol, Decision, Module
from memex.extractor.treesitter import SymbolDelta

logger = logging.getLogger(__name__)

async def write_symbol_delta(delta: SymbolDelta, source_commit: str | None = None) -> None:
    """
    Writes symbol changes to the Graphiti temporal graph.
    """
    client = await get_graph_client()
    now = datetime.now(UTC)

    # Handle Added Symbols
    for sym in delta.added:
        # 1. Add Episode for Module
        # name: short descriptor, episode_body: full text for extraction
        await client.add_episode(
            name=f"module_added_{sym.file}",
            episode_body=f"File {sym.file} was added to the project.",
            source_description=f"git commit {source_commit}" if source_commit else "file change",
            reference_time=now
        )

        # 2. Add Episode for Symbol
        await client.add_episode(
            name=f"symbol_added_{sym.name}",
            episode_body=f"Symbol {sym.name} ({sym.kind}) was defined in {sym.file} at line {sym.line} with signature: {sym.signature}",
            source_description=f"git commit {source_commit}" if source_commit else "file change",
            reference_time=now
        )

    # Handle Modified Symbols
    for sym in delta.modified:
        await client.add_episode(
            name=f"symbol_modified_{sym.name}",
            episode_body=f"Symbol {sym.name} in {sym.file} was modified. New signature: {sym.signature}",
            source_description=f"git commit {source_commit}" if source_commit else "file change",
            reference_time=now
        )

    # Handle Removed Symbols
    for sym in delta.removed:
        await client.add_episode(
            name=f"symbol_removed_{sym.name}",
            episode_body=f"Symbol {sym.name} was removed from {sym.file}.",
            source_description=f"git commit {source_commit}" if source_commit else "file change",
            reference_time=now
        )

async def write_decision(decision: Decision, modules: list[str], commit_sha: str) -> None:
    """
    Writes a decision to the graph.
    """
    client = await get_graph_client()
    now = datetime.now(UTC)
    
    # Create the Decision episode
    await client.add_episode(
        name=f"decision_{commit_sha}",
        episode_body=f"Decision: {decision.text}. Rationale: {decision.rationale}. Scope: {decision.scope}. Affected modules: {', '.join(modules)}",
        source_description=f"git commit {commit_sha}",
        reference_time=now
    )
