"""Unit tests for ``memex.graph.cluster_summary``.

Tests for synthesize_cluster_summary() and refresh_cluster_summaries()
without requiring live Neo4j, Gemini or network access.
"""

from __future__ import annotations
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch
import pytest

from memex.graph.cluster_summary import (
    synthesize_cluster_summary,
    refresh_cluster_summaries,
)


@pytest.mark.asyncio
async def test_synthesize_returns_single_sentence():
    # Mock Gemini Client
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "This is a single sentence summary of the cluster."
    mock_client.models.generate_content.return_value = mock_response

    with patch("google.genai.Client", return_value=mock_client), \
         patch("memex.config.get_config") as mock_get_config:
        mock_get_config.return_value = SimpleNamespace(gemini_api_key="fake_key")

        summary = await synthesize_cluster_summary(
            cluster_name="test-cluster",
            member_modules=["a.py", "b.py"],
            decision_texts=["Adopt pattern X", "Use library Y"]
        )

        assert summary == "This is a single sentence summary of the cluster."


@pytest.mark.asyncio
async def test_synthesize_truncates_decisions():
    # Mock Gemini Client
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = "Summary sentence."
    mock_client.models.generate_content.return_value = mock_response

    with patch("google.genai.Client", return_value=mock_client), \
         patch("memex.config.get_config") as mock_get_config:
        mock_get_config.return_value = SimpleNamespace(gemini_api_key="fake_key")

        # Pass 25 decisions, limit to max_decisions=5
        decisions = [f"Decision {i}" for i in range(25)]
        await synthesize_cluster_summary(
            cluster_name="test-cluster",
            member_modules=["a.py"],
            decision_texts=decisions,
            max_decisions=5
        )

        # Inspect prompt passed to generate_content
        call_args = mock_client.models.generate_content.call_args
        prompt = call_args[1]["contents"] if call_args else ""
        assert "Decision 0" in prompt
        assert "Decision 4" in prompt
        assert "Decision 5" not in prompt


@pytest.mark.asyncio
async def test_refresh_skips_existing_summaries():
    mock_client = AsyncMock()
    mock_driver = AsyncMock()
    mock_client.driver = mock_driver

    # Mock records returned by execute_query
    mock_record = MagicMock()
    mock_record.data.return_value = {
        "cluster_name": "cluster-1",
        "members": ["module_a.py"],
        "decisions": ["Adopt library X"]
    }
    mock_result = MagicMock()
    mock_result.records = [mock_record]
    mock_driver.execute_query.return_value = mock_result

    # Mock Gemini Client
    mock_gemini = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = "Summary 1"
    mock_gemini.models.generate_content.return_value = mock_resp

    with patch("memex.graph.client.get_graph_client", return_value=mock_client), \
         patch("google.genai.Client", return_value=mock_gemini), \
         patch("memex.config.get_config") as mock_get_config:
        mock_get_config.return_value = SimpleNamespace(gemini_api_key="fake_key", repo_root="/fake")

        res = await refresh_cluster_summaries("/fake", force=False)
        assert res == {"cluster-1": "Summary 1"}

        # Check that the first query includes "AND c.summary IS NULL"
        args = mock_driver.execute_query.call_args_list[0]
        query = args[0][0]
        assert "AND c.summary IS NULL" in query


@pytest.mark.asyncio
async def test_refresh_force_regenerates_all():
    mock_client = AsyncMock()
    mock_driver = AsyncMock()
    mock_client.driver = mock_driver

    # Mock records returned by execute_query
    mock_record = MagicMock()
    mock_record.data.return_value = {
        "cluster_name": "cluster-1",
        "members": ["module_a.py"],
        "decisions": ["Adopt library X"]
    }
    mock_result = MagicMock()
    mock_result.records = [mock_record]
    mock_driver.execute_query.return_value = mock_result

    # Mock Gemini Client
    mock_gemini = MagicMock()
    mock_resp = MagicMock()
    mock_resp.text = "Summary 1"
    mock_gemini.models.generate_content.return_value = mock_resp

    with patch("memex.graph.client.get_graph_client", return_value=mock_client), \
         patch("google.genai.Client", return_value=mock_gemini), \
         patch("memex.config.get_config") as mock_get_config:
        mock_get_config.return_value = SimpleNamespace(gemini_api_key="fake_key", repo_root="/fake")

        res = await refresh_cluster_summaries("/fake", force=True)
        assert res == {"cluster-1": "Summary 1"}

        # Check that the first query does NOT include "AND c.summary IS NULL"
        args = mock_driver.execute_query.call_args_list[0]
        query = args[0][0]
        assert "AND c.summary IS NULL" not in query


@pytest.mark.asyncio
async def test_refresh_skips_empty_clusters():
    mock_client = AsyncMock()
    mock_driver = AsyncMock()
    mock_client.driver = mock_driver

    # Cluster with no member modules
    mock_record = MagicMock()
    mock_record.data.return_value = {
        "cluster_name": "cluster-1",
        "members": [],
        "decisions": []
    }
    mock_result = MagicMock()
    mock_result.records = [mock_record]
    mock_driver.execute_query.return_value = mock_result

    with patch("memex.graph.client.get_graph_client", return_value=mock_client):
        res = await refresh_cluster_summaries("/fake", force=True)
        assert res == {}
        # verify no write-back query (execute_query only called once for fetch)
        assert mock_driver.execute_query.call_count == 1
