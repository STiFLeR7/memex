"""Unit tests for ``memex.graph.cluster_summary``.

The persistence layer is tested against a stub graph client (no Neo4j
required). The HDBSCAN grouping uses synthetic embedding vectors that
form geometrically obvious clusters so the test isn't flaky.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from memex.graph.cluster_summary import (
    DecisionRecord,
    SUMMARISER_ACTOR,
    TopicCluster,
    _build_summary_prompt,
    _normalise_topic_label,
    group_decisions_by_topic,
    run_cluster_summary_pass,
    summarise_topic,
    write_cluster_summary,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_records_two_topics(module: str = "memex/watcher/handlers.py") -> list[DecisionRecord]:
    """Two well-separated synthetic topic clouds inside one module.

    Topic A centroid ≈ (10, 0, 0); Topic B centroid ≈ (0, 10, 0). HDBSCAN
    with ``min_cluster_size=3`` should split them cleanly.
    """
    out: list[DecisionRecord] = []
    for i in range(4):
        out.append(
            DecisionRecord(
                id=f"a-{i}",
                text=f"topic A decision {i}",
                module_path=module,
                embedding=[10.0 + i * 0.01, 0.0, 0.0],
                base_confidence=0.6 + i * 0.01,
            )
        )
    for i in range(4):
        out.append(
            DecisionRecord(
                id=f"b-{i}",
                text=f"topic B decision {i}",
                module_path=module,
                embedding=[0.0, 10.0 + i * 0.01, 0.0],
                base_confidence=0.7 + i * 0.01,
            )
        )
    return out


# ---------------------------------------------------------------------------
# group_decisions_by_topic
# ---------------------------------------------------------------------------


def test_group_decisions_by_topic_skips_modules_below_min_cluster_size() -> None:
    recs = [
        DecisionRecord(
            id=str(i), text=f"d{i}", module_path="a.py",
            embedding=[float(i)], base_confidence=0.6,
        )
        for i in range(2)
    ]
    assert group_decisions_by_topic(recs) == []


def test_group_decisions_by_topic_splits_two_clouds() -> None:
    out = group_decisions_by_topic(_make_records_two_topics())
    assert len(out) == 2, f"expected 2 topic clusters, got {len(out)}"
    sizes = sorted(len(tc.members) for tc in out)
    assert sizes == [4, 4]
    # Members of each topic share the same id-prefix (a-* or b-*)
    for tc in out:
        prefixes = {m.id[0] for m in tc.members}
        assert len(prefixes) == 1


def test_topic_cluster_max_base_confidence_is_member_max() -> None:
    members = [
        DecisionRecord(id="x", text="t", module_path="m.py",
                       embedding=[1.0], base_confidence=0.5),
        DecisionRecord(id="y", text="t", module_path="m.py",
                       embedding=[1.0], base_confidence=0.9),
    ]
    tc = TopicCluster(module_path="m.py", cluster_id=0, members=members)
    assert tc.max_base_confidence == 0.9


def test_group_decisions_by_topic_skips_records_without_embedding() -> None:
    recs = _make_records_two_topics()
    # Strip embeddings from half the records
    for r in recs[::2]:
        r.embedding = []
    out = group_decisions_by_topic(recs)
    # Remaining 4 records can't form 2 clusters of size 3 — depending on
    # geometry HDBSCAN may produce 0 or 1 cluster. We assert non-crash and
    # that no topic contains an empty-embedding record.
    for tc in out:
        for m in tc.members:
            assert m.embedding


# ---------------------------------------------------------------------------
# Prompt + label helpers
# ---------------------------------------------------------------------------


def test_build_summary_prompt_lists_each_decision_as_bullet() -> None:
    prompt = _build_summary_prompt(["first decision", "second decision"])
    assert "- first decision" in prompt
    assert "- second decision" in prompt
    assert "JSON" in prompt


def test_normalise_topic_label_strips_punctuation_and_caps_three_tokens() -> None:
    assert _normalise_topic_label("Cluster Summary: Bug Fixes!!", fallback="fb") == "cluster-summary-bug"
    assert _normalise_topic_label("", fallback="fb") == "fb"


# ---------------------------------------------------------------------------
# Gemini Flash interaction (mocked)
# ---------------------------------------------------------------------------


class _StubGeminiClient:
    """Minimal stand-in for ``google.genai.Client`` covering the surface
    that :func:`summarise_topic` touches."""

    def __init__(self, payload: dict | Exception):
        self._payload = payload
        self.models = self  # for `.models.generate_content`
        self.calls = 0

    def generate_content(self, *, model, contents, config=None):  # noqa: D401, ARG002
        self.calls += 1
        if isinstance(self._payload, Exception):
            raise self._payload
        import json
        return SimpleNamespace(text=json.dumps(self._payload))


@pytest.mark.asyncio
async def test_summarise_topic_returns_payload_on_success() -> None:
    cluster = TopicCluster(
        module_path="m.py", cluster_id=0,
        members=[
            DecisionRecord(id="a", text="adopt X", module_path="m.py",
                           embedding=[1.0], base_confidence=0.6),
            DecisionRecord(id="b", text="adopt Y", module_path="m.py",
                           embedding=[1.0], base_confidence=0.6),
            DecisionRecord(id="c", text="adopt Z", module_path="m.py",
                           embedding=[1.0], base_confidence=0.6),
        ],
    )
    gem = _StubGeminiClient({"text": "Chose X over Y", "topic_label": "Decision Set"})
    out = await summarise_topic(cluster, gemini_client=gem)
    assert out == {"text": "Chose X over Y", "topic_label": "decision-set"}


@pytest.mark.asyncio
async def test_summarise_topic_empty_cluster_returns_none() -> None:
    cluster = TopicCluster(module_path="m.py", cluster_id=0, members=[])
    out = await summarise_topic(cluster)
    assert out is None


@pytest.mark.asyncio
async def test_summarise_topic_recovers_from_failure(monkeypatch) -> None:
    """3-attempt retry: fail twice, succeed on third."""
    cluster = TopicCluster(
        module_path="m.py", cluster_id=0,
        members=[DecisionRecord(id="a", text="t", module_path="m.py",
                                embedding=[1.0], base_confidence=0.6)],
    )

    class _Flaky:
        def __init__(self) -> None:
            self.models = self
            self.calls = 0

        def generate_content(self, *, model, contents, config=None):  # noqa: ARG002
            self.calls += 1
            if self.calls < 3:
                raise RuntimeError("transient")
            import json
            return SimpleNamespace(
                text=json.dumps({"text": "ok", "topic_label": "okay label"})
            )

    flaky = _Flaky()
    # Patch asyncio.sleep to a no-op so the test doesn't wait through backoff.
    import asyncio

    async def _no_wait(*_args, **_kwargs):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_wait)
    out = await summarise_topic(cluster, gemini_client=flaky)
    assert out == {"text": "ok", "topic_label": "okay-label"}
    assert flaky.calls == 3


# ---------------------------------------------------------------------------
# write_cluster_summary — uses a stub Neo4j client
# ---------------------------------------------------------------------------


class _StubGraphClient:
    """Records every ``add_episode`` + ``driver.execute_query`` call."""

    def __init__(self) -> None:
        self.episodes: list[dict] = []
        self.queries: list[tuple[str, dict]] = []
        self.driver = self

    async def add_episode(self, **kwargs) -> Any:  # noqa: D401
        self.episodes.append(kwargs)
        # Mimic Graphiti's nested response shape
        episode = SimpleNamespace(uuid=f"ep-{len(self.episodes)}")
        return SimpleNamespace(episode=episode)

    async def execute_query(self, query: str, params: dict | None = None) -> Any:
        self.queries.append((query, params or {}))
        return SimpleNamespace(records=[])


@pytest.mark.asyncio
async def test_write_cluster_summary_persists_node_and_edges() -> None:
    client = _StubGraphClient()
    members = [
        DecisionRecord(id=f"d-{i}", text=f"decision {i}", module_path="m.py",
                       embedding=[1.0, 2.0], base_confidence=0.5 + i * 0.1)
        for i in range(3)
    ]
    cluster = TopicCluster(module_path="m.py", cluster_id=4, members=members)
    payload = {"text": "Synthesised summary text", "topic_label": "topic-name"}

    ok = await write_cluster_summary(client, "/repo", cluster, payload)

    assert ok is True
    assert len(client.episodes) == 1
    # 1 SET + 3 SUMMARISED_INTO edges
    assert len(client.queries) == 1 + len(members)
    set_query, set_params = client.queries[0]
    assert "ClusterSummary" in set_query
    assert set_params["topic_label"] == "topic-name"
    assert set_params["source_count"] == 3
    # max(base_confidence) over members: 0.5, 0.6, 0.7 → 0.7
    assert set_params["base_confidence"] == pytest.approx(0.7)


@pytest.mark.asyncio
async def test_write_cluster_summary_rejects_empty_payload() -> None:
    client = _StubGraphClient()
    cluster = TopicCluster(
        module_path="m.py", cluster_id=0,
        members=[DecisionRecord(id="a", text="t", module_path="m.py",
                                embedding=[1.0], base_confidence=0.6)],
    )
    ok = await write_cluster_summary(client, "/r", cluster, {"text": "", "topic_label": "x"})
    assert ok is False
    assert client.episodes == []


def test_summariser_actor_is_allowed_to_write_locked_cluster_summary() -> None:
    """ACL check that the persistence layer relies on."""
    from memex.graph.schema import check_write_policy

    # Should not raise.
    check_write_policy("ClusterSummary", SUMMARISER_ACTOR)


# ---------------------------------------------------------------------------
# Orchestrator (no client → returns topic count without writing)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_cluster_summary_pass_without_client_counts_topics() -> None:
    recs = _make_records_two_topics()
    out = await run_cluster_summary_pass(repo_root="/r", decisions=recs)
    assert out["topics_found"] == 2
    assert out["summaries_written"] == 0  # no client → no writes
