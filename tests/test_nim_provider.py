from types import SimpleNamespace

import pytest

from memex.config import load_config
from memex.graph import client as graph_client


def test_load_config_accepts_nvidia_provider_without_gemini_key(monkeypatch):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.setenv("NEO4J_URI", "bolt://localhost:7687")
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "test-password")
    monkeypatch.setenv("MEMEX_LLM_PROVIDER", "nvidia")
    monkeypatch.setenv("NVIDIA_NIM_API_KEY", "test-nim-key")
    monkeypatch.setenv("NVIDIA_NIM_BASE_URL", "https://nim.example/v1")
    monkeypatch.setenv("MEMEX_LLM_MODEL", "nvidia/test-model")
    monkeypatch.setenv("MEMEX_EMBEDDING_MODEL", "nvidia/test-embedding")
    monkeypatch.setenv("MEMEX_EMBEDDING_DIM", "2048")

    config = load_config()

    assert config.llm_provider == "nvidia"
    assert config.llm_api_key == "test-nim-key"
    assert config.llm_base_url == "https://nim.example/v1"
    assert config.llm_model == "nvidia/test-model"
    assert config.embedding_model == "nvidia/test-embedding"
    assert config.embedding_dim == 2048


@pytest.mark.asyncio
async def test_graph_client_uses_chat_compatible_clients_for_nvidia(monkeypatch):
    captured = {}

    class FakeLLM:
        def __init__(self, *, config, structured_output_mode):
            captured["llm_config"] = config
            captured["structured_output_mode"] = structured_output_mode

    class FakeEmbedder:
        def __init__(self, *, config):
            captured["embedder_config"] = config

    class FakeGraphiti:
        def __init__(self, **kwargs):
            captured["graphiti"] = kwargs
            self.driver = object()

    config = SimpleNamespace(
        llm_provider="nvidia",
        llm_api_key="test-nim-key",
        llm_base_url="https://nim.example/v1",
        llm_model="nvidia/test-model",
        gemini_api_key=None,
        gemini_model="unused-gemini-model",
        embedding_model="nvidia/test-embedding",
        embedding_dim=2048,
        neo4j_uri="bolt://localhost:7687",
        neo4j_user="neo4j",
        neo4j_password="test-password",
    )

    monkeypatch.setattr(graph_client, "get_config", lambda: config)
    monkeypatch.setattr(graph_client, "OpenAIGenericClient", FakeLLM)
    monkeypatch.setattr(graph_client, "OpenAIEmbedder", FakeEmbedder)
    monkeypatch.setattr(graph_client, "Graphiti", FakeGraphiti)

    await graph_client.GraphClient.reset()
    await graph_client.GraphClient.get_instance()

    assert captured["llm_config"].api_key == "test-nim-key"
    assert captured["llm_config"].base_url == "https://nim.example/v1"
    assert captured["llm_config"].model == "nvidia/test-model"
    assert captured["structured_output_mode"] == "json_object"
    assert captured["embedder_config"].api_key == "test-nim-key"
    assert captured["embedder_config"].base_url == "https://nim.example/v1"
    assert captured["embedder_config"].embedding_model == "nvidia/test-embedding"
    assert captured["embedder_config"].embedding_dim == 2048
