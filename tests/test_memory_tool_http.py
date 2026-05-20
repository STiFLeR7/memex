"""Tests for the memory-tool FastAPI shim.

The shim is a thin wrapper — these tests verify:

1. Same payload via HTTP as via the in-process ``MemexAsyncMemoryTool``.
2. Bearer-token auth is enforced.

We use FastAPI's ``TestClient`` (sync requests) since the shim's logic
is synchronous from the client's perspective.
"""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from memex.memory_tool.http import create_app
from memex.memory_tool.scratch import ScratchStore
from memex.memory_tool.server import MemexAsyncMemoryTool
from memex.memory_tool.projection import GraphProjection
from anthropic.types.beta import (
    BetaMemoryTool20250818ViewCommand,
)


def _make_components(tmp_path):
    repo_root = str(tmp_path / "repo")
    import os

    os.makedirs(repo_root, exist_ok=True)

    async def empty_client_factory():
        class _Driver:
            async def execute_query(self, *_args, **_kwargs):
                class _R:
                    records: list[Any] = []

                return _R()

        class _C:
            driver = _Driver()

        return _C()

    projection = GraphProjection(repo_root, client_factory=empty_client_factory)
    scratch = ScratchStore(
        repo_root=repo_root, db_path=str(tmp_path / "scratch.db")
    )
    tool = MemexAsyncMemoryTool(
        repo_root=repo_root, projection=projection, scratch=scratch
    )
    app = create_app(repo_root, tool=tool, bearer_key="test-bearer")
    return tool, app


def test_http_shim_requires_bearer_auth(tmp_path):
    _, app = _make_components(tmp_path)
    client = TestClient(app)

    # Missing header → 401.
    resp = client.post(
        "/memory/view", json={"path": "/memories"}
    )
    assert resp.status_code == 401

    # Wrong key → 401.
    resp = client.post(
        "/memory/view",
        json={"path": "/memories"},
        headers={"Authorization": "Bearer wrong-key"},
    )
    assert resp.status_code == 401


def test_http_shim_health_open(tmp_path):
    _, app = _make_components(tmp_path)
    client = TestClient(app)
    resp = client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"


async def test_http_shim_returns_same_payload_as_in_process(tmp_path):
    """A round-trip via HTTP must produce byte-identical content to a
    direct in-process call."""
    tool, app = _make_components(tmp_path)
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-bearer"}

    # Create a scratch file via HTTP.
    resp = client.post(
        "/memory/create",
        json={
            "path": "/memories/scratch/s/a.md",
            "file_text": "alpha\nbeta\ngamma",
        },
        headers=headers,
    )
    assert resp.status_code == 200
    http_payload = resp.json()
    assert http_payload["is_error"] is False
    assert "File created successfully" in http_payload["content"]

    # View via HTTP.
    resp = client.post(
        "/memory/view",
        json={"path": "/memories/scratch/s/a.md"},
        headers=headers,
    )
    assert resp.status_code == 200
    http_view = resp.json()["content"]

    # View via the same in-process tool — must match exactly.
    in_proc_view = await tool.view(
        BetaMemoryTool20250818ViewCommand.model_construct(
            command="view",
            path="/memories/scratch/s/a.md",
            view_range=None,
        )
    )
    assert http_view == in_proc_view


def test_http_shim_returns_redirect_error_for_graph_zone_writes(tmp_path):
    _, app = _make_components(tmp_path)
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-bearer"}
    resp = client.post(
        "/memory/create",
        json={
            "path": "/memories/repos/x/graph/decisions/foo.md",
            "file_text": "won't fly",
        },
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    # The redirect is returned as a *successful* tool result (a string
    # the model reads) — not an HTTP 4xx. ``is_error`` is False because
    # the tool ran; the message itself signals the redirect.
    assert body["is_error"] is False
    assert "read-only" in body["content"]
    assert "record_decision" in body["content"]


def test_http_shim_str_replace_error_returns_is_error_true(tmp_path):
    _, app = _make_components(tmp_path)
    client = TestClient(app)
    headers = {"Authorization": "Bearer test-bearer"}
    # Create then try replacing nonexistent text.
    client.post(
        "/memory/create",
        json={"path": "/memories/scratch/s/a.md", "file_text": "hello"},
        headers=headers,
    )
    resp = client.post(
        "/memory/str_replace",
        json={
            "path": "/memories/scratch/s/a.md",
            "old_str": "absent",
            "new_str": "X",
        },
        headers=headers,
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["is_error"] is True
    assert "did not appear verbatim" in body["content"]
