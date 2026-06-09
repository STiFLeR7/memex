"""Cluster-level summary synthesis using Gemini Flash.

Generates a concise one-line summary for each cluster based on
its member modules and their associated decisions.
"""

from __future__ import annotations
import logging
import asyncio
from typing import Optional, Any

logger = logging.getLogger(__name__)


async def _embed_text(text: str) -> list[float]:
    """Embed ``text`` via the configured Gemini embedding model.
    Retained for backward compatibility with watcher handlers and test files.
    """
    try:
        from google import genai
        from memex.config import get_config

        cfg = get_config()
        client = genai.Client(api_key=cfg.gemini_api_key)
        model = getattr(cfg, "gemini_embedding_model", "models/text-embedding-004")
        resp = await asyncio.to_thread(
            client.models.embed_content, model=model, contents=text
        )
        emb = getattr(resp, "embedding", None) or getattr(resp, "embeddings", None)
        if hasattr(emb, "values"):
            return list(emb.values)
        if isinstance(emb, list) and emb and hasattr(emb[0], "values"):
            return list(emb[0].values)
        if isinstance(emb, list):
            return list(emb)
        return []
    except Exception:
        logger.debug("summary: embedding failed for %r", text[:60], exc_info=True)
        return []


async def synthesize_cluster_summary(
    cluster_name: str,
    member_modules: list[str],
    decision_texts: list[str],
    *,
    max_decisions: int = 20,
) -> str:
    """Generate a one-line summary of a cluster's purpose.

    Uses Gemini Flash to synthesize member modules and their decisions
    into a single descriptive sentence.

    Args:
        cluster_name: TF-IDF-derived cluster name
        member_modules: List of module file paths in this cluster
        decision_texts: Recent decision texts associated with these modules
        max_decisions: Cap on decision texts sent to LLM

    Returns:
        A single sentence summarizing the cluster's architectural role.
    """
    try:
        from google import genai
        from memex.config import get_config

        cfg = get_config()
        client = genai.Client(api_key=cfg.gemini_api_key)
    except Exception as e:
        logger.warning("Failed to initialize Gemini client: %s", e)
        return f"Cluster containing modules: {', '.join(member_modules[:3])}"

    truncated = decision_texts[:max_decisions]
    prompt = (
        f"You are summarizing a code module cluster named '{cluster_name}'.\n"
        f"It contains {len(member_modules)} modules: {', '.join(member_modules[:10])}"
        f"{'...' if len(member_modules) > 10 else ''}.\n"
        f"Recent architectural decisions about these modules:\n"
        + "\n".join(f"- {d}" for d in truncated)
        + "\n\nWrite ONE sentence (max 30 words) describing this cluster's "
        "architectural role and purpose. Be specific, not generic."
    )

    try:
        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-2.5-flash",
            contents=prompt,
        )
        return (response.text or "").strip()
    except Exception as e:
        logger.warning("Failed to generate cluster summary for %s: %s", cluster_name, e)
        return f"Cluster containing modules: {', '.join(member_modules[:3])}"


async def refresh_cluster_summaries(
    repo_root: str,
    *,
    force: bool = False,
) -> dict[str, str]:
    """Refresh summaries for all clusters in a repo.

    Only synthesizes for clusters where summary IS NULL (or force=True).

    Returns:
        Dict mapping cluster_name → summary.
    """
    from memex.graph.client import get_graph_client
    from memex.config import canonical_repo_path

    client = await get_graph_client()
    repo = canonical_repo_path(repo_root)

    # Fetch clusters needing summaries
    query = """
    MATCH (c:Entity {type: 'Cluster', repo_path: $repo})
    WHERE c.expired_at IS NULL
    """ + ("" if force else "AND c.summary IS NULL") + """
    OPTIONAL MATCH (c)-[r:CONTAINS]->(m:Entity {type: 'Module'})
    WHERE r.expired_at IS NULL
    WITH c, collect(m.name) AS members
    OPTIONAL MATCH (d:Entity {type: 'Decision'})-[:RELATES_TO]->(m2:Entity)
    WHERE m2.name IN members AND d.expired_at IS NULL
    WITH c, members, collect(DISTINCT d.name) AS decisions
    RETURN c.name AS cluster_name, members, decisions
    """
    result = await client.driver.execute_query(query, params={"repo": repo})

    summaries: dict[str, str] = {}
    for record in result.records:
        data = record.data()
        name = data["cluster_name"]
        members = data["members"] or []
        decisions = data["decisions"] or []

        if not members:
            continue

        summary = await synthesize_cluster_summary(name, members, decisions)
        summaries[name] = summary

        # Write back to graph
        await client.driver.execute_query(
            "MATCH (c:Entity {type: 'Cluster', name: $name, repo_path: $repo}) "
            "SET c.summary = $summary",
            params={"name": name, "repo": repo, "summary": summary},
        )
        logger.info("Cluster '%s' summary: %s", name, summary)

    return summaries
