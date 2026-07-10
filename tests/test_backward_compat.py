"""Phase 02 Plan 04 — NET-11 end-to-end backward-compatibility regression
suite.

Every prior plan in this phase (02-01 through 02-03) defaults toward
backward compatibility individually, but nothing until now proves the FULL
stack — registry -> resolve_principal -> principal_ctx -> handle_call_tool
-> check_write_policy — preserves today's (v0.6.1) behavior end-to-end when
no one has configured anything. This is the phase-gate verification
02-RESEARCH.md's Validation Architecture calls for (T-02-14).

Four independent scenarios, each proving a distinct slice of the upgrade
boundary:
  1. Legacy registry, no `role` field — the migration sentinel.
  2. Empty registry — an HTTP deployment with zero keys is exactly as
     locked-down as today, not more, not less.
  3. Stdio-equivalent write path (principal_ctx unset) — writes proceed as
     principal_id="local", role="admin".
  4. A legacy plaintext-only key authenticates against the newly-authenticated
     /graph endpoint without any operator action required.
"""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi.testclient import TestClient
from mcp.server import Server

from memex.watcher import registry
from memex.graph.schema import Principal


@pytest.fixture
def temp_registry(tmp_path):
    """Isolated registry.json per test — mirrors tests/test_registry.py's
    fixture so this suite never touches the session-scoped shared registry
    file (see tests/conftest.py) or any other test's state."""
    reg_path = tmp_path / "registry.json"
    old_path = registry.REGISTRY_PATH
    registry.REGISTRY_PATH = reg_path
    yield reg_path
    registry.REGISTRY_PATH = old_path


# ---------------------------------------------------------------------------
# Scenario 1: Legacy registry, no role field
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_legacy_registry_no_role_field_resolves_to_admin_sentinel(temp_registry):
    """A hand-constructed registry record shaped exactly like a v0.6.1
    record (name/key/created_at only — no role, no key_hash, no
    principal_id). `validate_key()` must still return True for the correct
    token, and `resolve_principal()` must resolve to role="admin" with
    principal_id == the legacy record's name (the migration sentinel from
    Plan 02-01, re-verified here end-to-end)."""
    legacy_token = "mx_deadbeefdeadbeefdeadbeefdeadbeef"

    legacy = registry._load_registry()
    legacy.keys.append({
        "name": "pre-upgrade-dev",
        "key": legacy_token,
        "created_at": "2025-01-01T00:00:00",
    })
    registry._save_registry(legacy)

    # validate_key() — the boolean auth model v0.6.1 callers still rely on.
    assert registry.validate_key(legacy_token) is True

    # resolve_principal() — the new identity-carrying abstraction.
    principal = await registry.resolve_principal(legacy_token)
    assert principal is not None
    assert principal.role == "admin"
    assert principal.principal_id == "pre-upgrade-dev"


# ---------------------------------------------------------------------------
# Scenario 2: Empty registry
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_registry_resolves_nothing(temp_registry):
    """No keys configured at all. `resolve_principal("")` and
    `resolve_principal("anything")` both return None — matches today's
    validate_key-on-empty-registry behavior (always False/unauthenticated).
    An HTTP deployment with zero keys configured is exactly as locked-down
    as it is today, not more, not less."""
    assert registry.get_keys() == []

    assert await registry.resolve_principal("") is None
    assert await registry.resolve_principal("anything") is None
    # The boolean model agrees — same empty-registry behavior preserved.
    assert registry.validate_key("anything") is False


# ---------------------------------------------------------------------------
# Scenario 3: Stdio-equivalent write path (principal_ctx unset)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_stdio_equivalent_write_path_uses_local_admin_identity():
    """With principal_ctx never set (simulating stdio transport, where no
    HTTP layer exists to set it), handle_call_tool("record_decision", ...)
    against a mocked graph client must proceed using principal_id="local",
    role="admin" — the identity actually passed through to
    check_write_policy, not just write success. Decision is `open` tier so
    the write succeeds regardless of role; this asserts the identity, per
    the plan's explicit instruction."""
    from memex.mcp_server.server import handle_call_tool
    from memex.mcp_server.principal_ctx import principal_ctx

    # Sanity: nothing set by a prior test in this (or another) module.
    assert principal_ctx.get(None) is None

    mock_result = MagicMock()
    mock_result.episode.uuid = "backward-compat-dec-1"

    with (
        patch("memex.mcp_server.tools_write.get_graph_client") as mock_get_client,
        patch("memex.mcp_server.tools_write.check_write_policy") as mock_check_policy,
    ):
        mock_client = AsyncMock()
        mock_client.add_episode.return_value = mock_result
        mock_get_client.return_value = mock_client

        result = await handle_call_tool(
            "record_decision",
            {
                "text": "a genuinely novel test decision for backward-compat verification",
                "repo": "/test/repo",
            },
        )

    # Identity actually passed through — the NET-11 backward-compatibility
    # backbone: no principal configured (stdio, or HTTP that never touched
    # Phase 02's CLI flags) == today's implicit full-access behavior.
    mock_check_policy.assert_called_once()
    call = mock_check_policy.call_args
    assert call.kwargs.get("principal_id") == "local"
    assert call.kwargs.get("role") == "admin"

    # And the write itself proceeded (Decision is open tier).
    assert "Error" not in result[0].text
    assert "decision recorded" in result[0].text


# ---------------------------------------------------------------------------
# Scenario 4: Legacy admin key over HTTP, against the newly-authenticated
# /graph endpoint
# ---------------------------------------------------------------------------


@patch("memex.mcp_server.http.get_graph_client")
def test_legacy_admin_key_authenticates_against_authenticated_graph_endpoint(
    mock_get_client, temp_registry
):
    """Using TestClient against memex.mcp_server.http.create_app, with a
    legacy plaintext-only key in the registry (no role, no key_hash — a
    pre-upgrade v0.6.1 key), a request to /graph with that bearer token
    succeeds (200) — a pre-upgrade key continues to authenticate against
    the newly-authenticated /graph endpoint (NET-10) without any operator
    action required. Uses the REAL resolve_principal (not mocked) so this
    exercises the full registry -> resolve_principal -> require_principal
    chain end-to-end."""
    from memex.mcp_server.http import create_app

    legacy_token = "mx_legacyadminkey00000000000000"
    legacy = registry._load_registry()
    legacy.keys.append({
        "name": "pre-upgrade-admin",
        "key": legacy_token,
        "created_at": "2025-01-01T00:00:00",
    })
    registry._save_registry(legacy)

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_execute = AsyncMock()
    mock_client.driver.execute_query = mock_execute
    empty_result = MagicMock()
    empty_result.records = []
    mock_execute.side_effect = [empty_result, empty_result]

    mock_server = MagicMock(spec=Server)
    app = create_app(mock_server, "/fake/repo")

    with TestClient(app) as client:
        response = client.get(
            "/graph", headers={"Authorization": f"Bearer {legacy_token}"}
        )

    assert response.status_code == 200
    data = response.json()
    assert data == {"nodes": [], "edges": []}
