"""Topic summarisation of Decisions within each Module (Deliverable 2).

HDBSCAN clusters Decision-embedding vectors per Module, then Gemini Flash
synthesises one :class:`memex.graph.schema.ClusterSummary` node per topic
cluster. Per ARCHITECTURE §10, this is **additive** — source Decisions
remain queryable (RAPTOR collapsed-tree mode), so the summary acts as a
denser entry point but doesn't replace its sources.

Pipeline:

1. For each Module under the repo, fetch its Decisions + per-Decision
   embedding (computed via ``google-genai`` if not already cached on
   the node).
2. Run HDBSCAN (``min_cluster_size=3``, ``min_samples=1``) over the
   embeddings.
3. For each cluster id ≥ 0, ask Gemini Flash to summarise the source
   texts into a single ``{text, topic_label}`` payload.
4. Persist a ClusterSummary node (write_policy='locked', actor='summariser')
   with ``base_confidence = max(source.base_confidence)``.
5. Edge ``SUMMARISED_INTO`` from each source Decision to the summary.

Heavy LLM round-trips are wrapped in ``asyncio.to_thread`` so the
event loop isn't blocked. The 3-attempt exponential backoff pattern is
copied verbatim from ``memex/synthesizer/commit.py``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Iterable, Optional

from memex.graph.schema import ClusterSummary, check_write_policy

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Tuning knobs (handoff §2 — initial values, callers may override)
# ---------------------------------------------------------------------------

MIN_CLUSTER_SIZE = 3
MIN_SAMPLES = 1

#: Marker value HDBSCAN uses for "noise" (no cluster). We skip noise.
HDBSCAN_NOISE = -1

SUMMARISER_ACTOR = "summariser"


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------


@dataclass
class DecisionRecord:
    """Loose-typed payload passed into the summariser.

    The full Decision node has many more fields; we only need a stable
    id, the text body, the source module path, an embedding vector,
    and ``base_confidence`` for the max-aggregation step.
    """

    id: str
    text: str
    module_path: str
    embedding: list[float]
    base_confidence: float = 0.6


@dataclass
class TopicCluster:
    """The intermediate output of :func:`group_decisions_by_topic`.

    One per HDBSCAN cluster within a single Module. Persistence
    composes these with Gemini Flash output into a ClusterSummary node.
    """

    module_path: str
    cluster_id: int
    members: list[DecisionRecord] = field(default_factory=list)

    @property
    def max_base_confidence(self) -> float:
        if not self.members:
            return 0.0
        return max(m.base_confidence for m in self.members)


# ---------------------------------------------------------------------------
# HDBSCAN grouping (pure on its inputs)
# ---------------------------------------------------------------------------


def group_decisions_by_topic(
    decisions: Iterable[DecisionRecord],
    *,
    min_cluster_size: int = MIN_CLUSTER_SIZE,
    min_samples: int = MIN_SAMPLES,
) -> list[TopicCluster]:
    """Run HDBSCAN per-Module and return non-noise topic clusters.

    Modules with fewer than ``min_cluster_size`` Decisions are skipped
    (HDBSCAN would assign everything to noise). Within a Module, points
    HDBSCAN labels ``-1`` (noise) are dropped — they don't form a topic.
    """
    by_module: dict[str, list[DecisionRecord]] = {}
    for d in decisions:
        if not d.embedding:
            continue
        by_module.setdefault(d.module_path, []).append(d)

    out: list[TopicCluster] = []
    for module_path, recs in by_module.items():
        if len(recs) < min_cluster_size:
            continue
        labels = _hdbscan_labels(
            [r.embedding for r in recs],
            min_cluster_size=min_cluster_size,
            min_samples=min_samples,
        )
        per_cluster: dict[int, list[DecisionRecord]] = {}
        for rec, label in zip(recs, labels):
            if label == HDBSCAN_NOISE:
                continue
            per_cluster.setdefault(int(label), []).append(rec)
        for cid, members in per_cluster.items():
            if len(members) < min_cluster_size:
                continue
            out.append(
                TopicCluster(
                    module_path=module_path,
                    cluster_id=cid,
                    members=members,
                )
            )
    out.sort(key=lambda tc: (tc.module_path, tc.cluster_id))
    return out


def _hdbscan_labels(
    vectors: list[list[float]],
    *,
    min_cluster_size: int,
    min_samples: int,
) -> list[int]:
    """Tiny wrapper around ``hdbscan.HDBSCAN`` so callers don't import it.

    Imported locally — ``hdbscan`` is an optional dep (``cluster`` extra).
    Callers should not reach this helper unless the extra is installed,
    which holds because this module is part of the cluster extra surface.
    """
    import hdbscan  # noqa: PLC0415
    import numpy as np  # noqa: PLC0415

    if not vectors:
        return []

    matrix = np.asarray(vectors, dtype=np.float32)
    clusterer = hdbscan.HDBSCAN(
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
        metric="euclidean",
    )
    return clusterer.fit_predict(matrix).tolist()


# ---------------------------------------------------------------------------
# Gemini Flash topic summarisation (3-attempt exponential backoff)
# ---------------------------------------------------------------------------


_LABEL_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_-]{2,}")


def _build_summary_prompt(texts: list[str]) -> str:
    """Construct the Gemini Flash prompt for a topic cluster."""
    joined = "\n".join(f"- {t.strip()}" for t in texts if t and t.strip())
    return (
        "Synthesise the architectural decisions below into one coherent "
        "summary. Preserve rationale and constraints. Return strict JSON "
        '{"text": "<summary>", "topic_label": "<3-word kebab-case label>"} '
        "and nothing else.\n\n"
        f"Decisions:\n{joined}\n"
    )


def _normalise_topic_label(raw: str, *, fallback: str) -> str:
    """Coerce arbitrary model output to a kebab-case label."""
    tokens = [t.lower() for t in _LABEL_TOKEN_RE.findall(raw or "")]
    if not tokens:
        return fallback
    return "-".join(tokens[:3])


async def summarise_topic(
    cluster: TopicCluster,
    *,
    gemini_client: Any = None,
    model: str = "gemini-2.0-flash",
    max_attempts: int = 3,
) -> Optional[dict[str, str]]:
    """Ask Gemini Flash to synthesise ``cluster`` into a topic summary.

    Returns ``{"text": ..., "topic_label": ...}`` on success, ``None`` on
    persistent failure (3 attempts with exponential backoff, mirroring
    :func:`memex.synthesizer.commit.extract_decisions`).
    """
    if not cluster.members:
        return None

    if gemini_client is None:
        try:
            from google import genai  # noqa: PLC0415
            from memex.config import get_config  # noqa: PLC0415

            cfg = get_config()
            gemini_client = genai.Client(api_key=cfg.gemini_api_key)
        except Exception:
            logger.warning("summary: gemini client unavailable", exc_info=True)
            return None

    prompt = _build_summary_prompt([m.text for m in cluster.members])
    fallback_label = f"topic-{cluster.module_path.replace('/', '-')}-{cluster.cluster_id}"

    for attempt in range(max_attempts):
        try:
            response = await asyncio.to_thread(
                gemini_client.models.generate_content,
                model=model,
                contents=prompt,
                config={"response_mime_type": "application/json"},
            )
            raw_text = getattr(response, "text", "") or ""
            data = json.loads(raw_text)
            text = (data.get("text") or "").strip()
            topic_label = _normalise_topic_label(
                data.get("topic_label", ""), fallback=fallback_label
            )
            if not text:
                continue
            return {"text": text, "topic_label": topic_label}
        except Exception as exc:
            err = str(exc).lower()
            if "429" in err or "rate limit" in err:
                wait = (2 ** attempt) + 1
                logger.warning(
                    "summary: gemini rate-limited; retrying in %ds", wait
                )
                await asyncio.sleep(wait)
                continue
            logger.warning(
                "summary: gemini call failed on attempt %d", attempt + 1,
                exc_info=True,
            )
            await asyncio.sleep((2 ** attempt) + 0.5)

    logger.error(
        "summary: gemini failed after %d attempts for %s#%d",
        max_attempts, cluster.module_path, cluster.cluster_id,
    )
    return None


# ---------------------------------------------------------------------------
# Persistence — ClusterSummary node + SUMMARISED_INTO edges
# ---------------------------------------------------------------------------


async def write_cluster_summary(
    client: Any,
    repo_path: str,
    cluster: TopicCluster,
    payload: dict[str, str],
) -> bool:
    """Persist a single ClusterSummary node + edges to its source Decisions.

    Returns True on success. Failures are logged and return False so the
    caller can record a partial summarisation pass.
    """
    check_write_policy("ClusterSummary", SUMMARISER_ACTOR)

    now = datetime.now(UTC)
    text = payload.get("text", "").strip()
    topic_label = payload.get("topic_label", "").strip()
    if not text or not topic_label:
        return False

    # Pydantic validation up front so a bad payload never reaches the DB.
    try:
        ClusterSummary(
            text=text,
            topic_label=topic_label,
            source_count=len(cluster.members),
            module_path=cluster.module_path,
            cluster_id=str(cluster.cluster_id),
            repo_path=repo_path,
            base_confidence=cluster.max_base_confidence,
            created_at=now,
        )
    except Exception:
        logger.warning(
            "summary: schema validation failed for %s#%d",
            cluster.module_path, cluster.cluster_id, exc_info=True,
        )
        return False

    episode_name = (
        f"cluster_summary_{cluster.module_path.replace('/', '_')}_{cluster.cluster_id}"
    )
    try:
        result = await client.add_episode(
            name=episode_name,
            episode_body=(
                f"Topic summary [{topic_label}] over {len(cluster.members)} "
                f"decisions in {cluster.module_path}: {text}"
            ),
            source_description="memex cluster_summary engine (HDBSCAN + Gemini Flash)",
            reference_time=now,
        )
    except Exception:
        logger.warning(
            "summary: add_episode failed for %s", episode_name, exc_info=True
        )
        return False

    episode_uuid = getattr(getattr(result, "episode", None), "uuid", None)
    if episode_uuid is None:
        logger.warning(
            "summary: %s missing episode.uuid; skipping post-hoc SET",
            episode_name,
        )
        return False

    set_query = """
    MATCH (n:Entity)
    WHERE n.uuid = $uuid OR elementId(n) = $uuid
    SET n.type = 'ClusterSummary',
        n.topic_label = $topic_label,
        n.module_path = $module_path,
        n.cluster_id = $cluster_id,
        n.repo_path = $repo,
        n.source_count = $source_count,
        n.base_confidence = $base_confidence,
        n.last_reinforced_at = $now,
        n.write_policy = 'locked',
        n.access_count = coalesce(n.access_count, 0)
    """
    try:
        await client.driver.execute_query(
            set_query,
            params={
                "uuid": episode_uuid,
                "topic_label": topic_label,
                "module_path": cluster.module_path,
                "cluster_id": str(cluster.cluster_id),
                "repo": repo_path,
                "source_count": len(cluster.members),
                "base_confidence": cluster.max_base_confidence,
                "now": now,
            },
        )
    except Exception:
        logger.warning(
            "summary: post-hoc SET failed for %s", episode_name, exc_info=True
        )
        return False

    # SUMMARISED_INTO edges (source Decision → ClusterSummary)
    edge_query = """
    MATCH (summary:Entity)
    WHERE summary.uuid = $summary_uuid OR elementId(summary) = $summary_uuid
    MATCH (src:Entity)
    WHERE src.uuid = $src_id OR elementId(src) = $src_id
    MERGE (src)-[r:SUMMARISED_INTO]->(summary)
      ON CREATE SET r.created_at = $now,
                    r.expired_at = NULL,
                    r.last_reinforced_at = $now
      ON MATCH SET  r.last_reinforced_at = $now,
                    r.expired_at = NULL
    """
    for member in cluster.members:
        try:
            await client.driver.execute_query(
                edge_query,
                params={
                    "summary_uuid": episode_uuid,
                    "src_id": member.id,
                    "now": now,
                },
            )
        except Exception:
            logger.debug(
                "summary: SUMMARISED_INTO edge %s -> %s skipped",
                member.id, episode_uuid, exc_info=True,
            )

    return True


# ---------------------------------------------------------------------------
# Top-level orchestrator (Neo4j-backed)
# ---------------------------------------------------------------------------


async def run_cluster_summary_pass(
    repo_root: str | Path,
    *,
    client: Any = None,
    decisions: Optional[list[DecisionRecord]] = None,
    min_cluster_size: int = MIN_CLUSTER_SIZE,
    min_samples: int = MIN_SAMPLES,
) -> dict[str, int]:
    """Run topic-clustering + summarisation across every Module in ``repo_root``.

    When ``client`` is None, callers MUST supply ``decisions`` (used by
    tests). In production, the orchestrator fetches Decisions + their
    cached embeddings from Neo4j, computes any missing embeddings via the
    Gemini embedding model, then runs HDBSCAN per Module.

    Returns ``{"topics_found": N, "summaries_written": M}``.
    """
    repo = str(Path(repo_root).resolve())
    out = {"topics_found": 0, "summaries_written": 0}

    records = decisions
    if records is None:
        if client is None:
            logger.info("summary: no client / no records — nothing to do")
            return out
        records = await _fetch_decisions_for_summary(client, repo)

    topics = group_decisions_by_topic(
        records,
        min_cluster_size=min_cluster_size,
        min_samples=min_samples,
    )
    out["topics_found"] = len(topics)
    if not topics:
        return out

    if client is None:
        # In test / preview mode return the topic count without writing.
        return out

    for topic in topics:
        payload = await summarise_topic(topic)
        if not payload:
            continue
        ok = await write_cluster_summary(client, repo, topic, payload)
        if ok:
            out["summaries_written"] += 1

    return out


async def _fetch_decisions_for_summary(
    client: Any, repo_path: str
) -> list[DecisionRecord]:
    """Return decisions + their embeddings for the repo.

    Embeddings come from Graphiti's stored ``name_embedding`` (if present)
    or are computed on-the-fly via the configured Gemini embedding model.
    Decisions without text or affected modules are skipped.
    """
    query = """
    MATCH (d:Entity)
    WHERE (d.type = 'Decision' OR d.name CONTAINS 'Decision')
      AND coalesce(d.excluded, false) = false
      AND d.repo_path = $repo
    OPTIONAL MATCH (d)-[r:MOTIVATES|RELATES_TO|MENTIONS]-(m:Entity)
    WHERE r.expired_at IS NULL
      AND (m.type = 'Module' OR m.name ENDS WITH '.py' OR m.name ENDS WITH '.js')
    WITH d, collect(DISTINCT m.name) as module_paths
    RETURN coalesce(d.uuid, elementId(d)) as id,
           d.name as text,
           module_paths,
           coalesce(d.name_embedding, []) as embedding,
           coalesce(d.base_confidence, 0.6) as base_confidence
    """
    res = await client.driver.execute_query(query, params={"repo": repo_path})

    records: list[DecisionRecord] = []
    for row in res.records:
        text = row.get("text") or ""
        if not text:
            continue
        paths = row.get("module_paths") or []
        embedding = list(row.get("embedding") or [])
        if not embedding:
            embedding = await _embed_text(text)
            if not embedding:
                continue
        bc = float(row.get("base_confidence", 0.6))
        # One DecisionRecord per (module, decision) pair so HDBSCAN runs
        # cleanly per-module. Most Decisions touch one module; a few touch
        # several and naturally appear in multiple module passes.
        for path in paths:
            if not path:
                continue
            records.append(
                DecisionRecord(
                    id=row["id"],
                    text=text,
                    module_path=path,
                    embedding=embedding,
                    base_confidence=bc,
                )
            )
    return records


async def _embed_text(text: str) -> list[float]:
    """Embed ``text`` via the configured Gemini embedding model."""
    try:
        from google import genai  # noqa: PLC0415
        from memex.config import get_config  # noqa: PLC0415

        cfg = get_config()
        client = genai.Client(api_key=cfg.gemini_api_key)
        model = getattr(cfg, "gemini_embedding_model", "models/text-embedding-004")
        resp = await asyncio.to_thread(
            client.models.embed_content, model=model, contents=text
        )
        # google-genai response shapes vary across versions; handle the
        # two we've seen in the wild.
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
