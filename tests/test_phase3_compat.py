"""W11: Hermes stays as it was, and the MCP projection adds resources, not tools."""
import inspect
import json
import os
import subprocess

import pytest

from memex.context.live import PacketBudget, SessionIdentity
from memex.context.revision import RepositoryView
from memex.integrations import hermes_provider
from memex.integrations.hermes_provider import HermesMemexProvider
from memex.integrations.mcp_live import LiveWorkingSetProjection, attach_live_resources, negotiate
from memex.runtime.tasks import TaskStore


def session(name="claude"):
    return SessionIdentity(harness="claude_code", native_session_id=name,
                           memex_session_id="cc-" + name, principal_id="owner")


def view():
    return RepositoryView("repo-1", "worktree-1", None, 1, 1, "sha256:" + "a" * 64)


# --------------------------------------------------------------------------- #
# Hermes compatibility
# --------------------------------------------------------------------------- #

def test_hermes_provider_contract_is_unchanged():
    provider = HermesMemexProvider()
    assert provider.name == "memex"
    assert provider.is_available() is True
    # Read-only: no tools are contributed through the Hermes surface.
    assert provider.get_tool_schemas() == []
    # Phase 3 must not have added a transcript path to the provider.
    assert inspect.signature(provider.prefetch).parameters.keys() == {"query", "session_id"}
    assert set(inspect.signature(provider.sync_turn).parameters) == {
        "user_content", "assistant_content", "session_id", "messages"}


def test_sync_turn_remains_transcript_free():
    provider = HermesMemexProvider()
    provider.initialize("session-1", repo_path=os.getcwd())
    before = dict(provider.__dict__)
    assert provider.sync_turn("a user turn", "an assistant turn",
                              session_id="session-1",
                              messages=[{"role": "user", "content": "secret"}]) is None
    # Nothing about the turn was retained on the provider.
    assert provider.__dict__ == before
    body = inspect.getsource(provider.sync_turn)
    assert "self." not in body.split('"""')[-1]


def test_prefetch_timeout_and_budget_defaults_are_unchanged():
    assert hermes_provider.DEFAULT_TIMEOUT_SECONDS == 7.0
    assert hermes_provider.DEFAULT_MAX_ITEMS == 8
    assert hermes_provider.DEFAULT_MAX_CHARS == 12_000
    provider = HermesMemexProvider()
    assert provider._timeout == 7.0
    assert provider._max_items == 8
    assert provider._max_chars == 12_000


def test_prefetch_without_initialization_fails_open_quietly():
    provider = HermesMemexProvider()
    assert provider.prefetch("anything") == ""


# --------------------------------------------------------------------------- #
# MCP projection
# --------------------------------------------------------------------------- #

def test_public_tool_surface_does_not_grow():
    """Fourteen tools: eight read, four write, two analytic. Resources are not tools."""
    source = subprocess.run(
        ["git", "show", "HEAD:memex/mcp_server/server.py"],
        capture_output=True, text=True, check=True).stdout
    committed = source.count("        Tool(")
    current = (open("memex/mcp_server/server.py", encoding="utf-8").read()).count("        Tool(")
    assert current == committed == 14


@pytest.fixture
def projection(tmp_path):
    tasks = TaskStore(tmp_path / "runtime.sqlite",
                      authenticate=lambda s: s.principal_id == "owner")
    current = {"session": session()}
    live = LiveWorkingSetProjection(tasks, resolve_session=lambda: current["session"],
                                    clock=lambda: 100.0)
    return tasks, live, current


def test_projection_lists_and_reads_only_the_requesting_session(projection):
    tasks, live, current = projection
    frame = tasks.create(session(), view(), "intent", (), (), (("api.py", "complete"),),
                         PacketBudget(), now=100.0, ttl=600)
    listed = live.list_resources()
    assert [entry["uri"] for entry in listed] == [live.uri(frame.task_id)]
    payload = json.loads(live.read_resource(live.uri(frame.task_id)))
    assert payload["task_id"] == frame.task_id
    assert payload["view_id"] == view().view_id
    assert payload["notice"].startswith("A resource read reports")

    # Another session sees nothing and cannot read by URI.
    current["session"] = session("codex-run")
    assert live.list_resources() == []
    with pytest.raises(PermissionError):
        live.read_resource(live.uri(frame.task_id))


def test_projection_refuses_an_unauthenticated_principal(projection):
    tasks, live, current = projection
    frame = tasks.create(session(), view(), "intent", (), (), (), PacketBudget(), now=100.0, ttl=600)
    current["session"] = None
    assert live.list_resources() == []
    with pytest.raises(PermissionError):
        live.read_resource(live.uri(frame.task_id))
    current["session"] = SessionIdentity(harness="claude_code", native_session_id="x",
                                         memex_session_id="y", principal_id="intruder")
    with pytest.raises(PermissionError):
        live.read_resource(live.uri(frame.task_id))


def test_projection_rejects_a_malformed_uri(projection):
    _, live, _ = projection
    for bad in ("http://example.com/x", "memex+task://", "memex+task://t/other", "memex+task://t"):
        with pytest.raises(ValueError):
            live.task_id(bad)


def test_projection_reports_an_incomplete_payload_instead_of_truncating(projection):
    tasks, live, _ = projection
    frame = tasks.create(session(), view(), "intent", (), (), (), PacketBudget(), now=100.0, ttl=600)
    live.max_characters = 200
    payload = json.loads(live.read_resource(live.uri(frame.task_id)))
    assert payload["complete"] is False
    assert payload["resync_required"] is True
    assert payload["items"] == []


def test_negotiation_selects_the_polling_fallback_on_the_installed_sdk(projection):
    """Measured, not assumed: this SDK advertises resources.subscribe=false."""
    from mcp.server.lowlevel.server import Server

    _, live, _ = projection
    server = Server("memex-test", version="0.0.0")
    bare = negotiate(server)
    assert bare.subscribe is False and bare.mode == "unavailable"

    agreed = attach_live_resources(server, live)
    assert agreed.subscribe is False
    assert agreed.mode == "poll"
    assert "subscribe=false" in agreed.reason
    assert agreed.fallback

    capabilities = server.get_capabilities(__import__(
        "mcp.server.lowlevel", fromlist=["NotificationOptions"]).NotificationOptions(), {})
    assert capabilities.resources is not None
    assert capabilities.resources.subscribe is False
    # Attaching resources must not have introduced a tool handler.
    assert capabilities.tools is None
