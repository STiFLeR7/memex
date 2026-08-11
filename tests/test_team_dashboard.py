"""Auth-gating and cookie-safety tests for the Phase 05 session auth layer
(NET-20, 05-01-PLAN.md).

Test 1/2 exercise `/login` on the real app built by `create_app()` (the
same app `/graph`/`/stats`/`/report`/`/mcp` live on) — `SessionMiddleware`
and the auth router are wired into that app's `create_app()` per Task 1.

Test 3/4 build a small standalone FastAPI app (per the plan's behavior
spec) to exercise `require_session`/`require_role` as reusable dependencies
independent of the rest of memex's HTTP surface.
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import Depends, FastAPI
from fastapi.testclient import TestClient
from mcp.server import Server
from starlette.middleware.sessions import SessionMiddleware

from memex.graph.confidence import current_confidence
from memex.mcp_server.auth_session import create_auth_router, require_role, require_session
from memex.mcp_server.http import create_app


# ---------------------------------------------------------------------------
# NET-19 (05-03-PLAN.md) — static dashboard serving + mount-order regression
# ---------------------------------------------------------------------------


def test_dashboard_static_served():
    """GET /login.html and GET / (StaticFiles(html=True) index fallback)
    both return 200 with an HTML content-type — the dashboard's static
    assets are reachable without any IDE/repo-clone dependency."""
    mock_server = MagicMock(spec=Server)
    app = create_app(mock_server, "/fake/repo")

    with TestClient(app) as client:
        login_response = client.get("/login.html")
        root_response = client.get("/")

    assert login_response.status_code == 200
    assert "text/html" in login_response.headers["content-type"]

    assert root_response.status_code == 200
    assert "text/html" in root_response.headers["content-type"]


def test_dashboard_mount_does_not_shadow_api_routes():
    """The StaticFiles("/") mount is registered LAST in create_app() — if it
    were registered earlier (or the ordering regressed), it would shadow
    every API route that comes after it in Starlette's prefix-matching route
    order (05-RESEARCH.md, T-05-11). POST /notify and GET /health must still
    resolve to their real handlers, not a 404 from the static mount."""
    mock_server = MagicMock(spec=Server)
    app = create_app(mock_server, "/fake/repo")

    with TestClient(app) as client:
        notify_response = client.post("/notify")
        health_response = client.get("/health")

    assert notify_response.status_code == 200
    assert notify_response.json() == {"status": "ok"}

    assert health_response.status_code in (200, 503)
    assert health_response.json()["status"] in ("ok", "error")


@patch("memex.mcp_server.auth_session.validate_key")
def test_login_sets_safe_session_cookie(mock_validate_key):
    """POST /login with a valid key returns a 303 redirect to /index.html
    and a Set-Cookie header that is HttpOnly + SameSite=Lax but never
    contains the raw bearer key (05-RESEARCH.md Pitfall 4, T-05-04)."""
    mock_validate_key.return_value = True
    mock_server = MagicMock(spec=Server)
    app = create_app(mock_server, "/fake/repo")

    with TestClient(app) as client:
        response = client.post(
            "/login",
            data={"key": "mx_faketoken123"},
            follow_redirects=False,
        )

    assert response.status_code == 303
    assert response.headers.get("location") == "/index.html"

    set_cookie = response.headers.get("set-cookie")
    assert set_cookie is not None
    assert "httponly" in set_cookie.lower()
    assert "samesite=lax" in set_cookie.lower()
    assert "mx_faketoken123" not in set_cookie


@patch("memex.mcp_server.auth_session.validate_key")
def test_login_rejects_invalid_key(mock_validate_key):
    """POST /login with an invalid key returns 401 and sets no cookie."""
    mock_validate_key.return_value = False
    mock_server = MagicMock(spec=Server)
    app = create_app(mock_server, "/fake/repo")

    with TestClient(app) as client:
        response = client.post(
            "/login",
            data={"key": "mx_whatever"},
            follow_redirects=False,
        )

    assert response.status_code == 401
    assert response.headers.get("set-cookie") is None


def _build_standalone_app() -> FastAPI:
    """A minimal standalone app exercising require_session/require_role as
    reusable dependencies, independent of memex's main HTTP surface."""
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="test-secret")
    app.include_router(create_auth_router())

    @app.get("/_protected")
    async def protected(principal: str = Depends(require_session)):
        return {"principal": principal}

    @app.get("/_protected_role")
    async def protected_role(principal: str = Depends(require_role("admin"))):
        return {"principal": principal}

    return app


@patch("memex.mcp_server.auth_session.validate_key")
def test_require_session_gates_protected_route(mock_validate_key):
    """/_protected returns 401 with no session cookie; after logging in,
    replaying the session cookie returns 200 with the truncated principal.
    """
    app = _build_standalone_app()

    with TestClient(app) as client:
        response = client.get("/_protected")
        assert response.status_code == 401

        mock_validate_key.return_value = True
        posted_key = "mx_someuser12345"
        login_response = client.post(
            "/login",
            data={"key": posted_key},
            follow_redirects=False,
        )
        assert login_response.status_code == 303

        response = client.get("/_protected")
        assert response.status_code == 200
        assert response.json() == {"principal": posted_key[:11]}


@patch("memex.mcp_server.auth_session.resolve_principal")
@patch("memex.mcp_server.auth_session.validate_key")
def test_require_role_denies_insufficient_role(mock_validate_key, mock_resolve_principal):
    """require_role("admin") rejects a session whose resolved role is below
    admin — real role enforcement, not the old permissive stub (T-05-05
    resolved: session role now comes from Phase 02's resolve_principal()).
    """
    from memex.graph.schema import Principal

    app = _build_standalone_app()

    with TestClient(app) as client:
        mock_validate_key.return_value = True
        mock_resolve_principal.return_value = Principal(principal_id="someuser", role="viewer")
        login_response = client.post(
            "/login",
            data={"key": "mx_someuser12345"},
            follow_redirects=False,
        )
        assert login_response.status_code == 303

        response = client.get("/_protected_role")
        assert response.status_code == 403


@patch("memex.mcp_server.auth_session.resolve_principal")
@patch("memex.mcp_server.auth_session.validate_key")
def test_require_role_allows_sufficient_role(mock_validate_key, mock_resolve_principal):
    """require_role("admin") allows a session whose resolved role is admin."""
    from memex.graph.schema import Principal

    app = _build_standalone_app()

    with TestClient(app) as client:
        mock_validate_key.return_value = True
        mock_resolve_principal.return_value = Principal(principal_id="someuser", role="admin")
        login_response = client.post(
            "/login",
            data={"key": "mx_someuser12345"},
            follow_redirects=False,
        )
        assert login_response.status_code == 303

        response = client.get("/_protected_role")
        assert response.status_code == 200


@patch("memex.mcp_server.auth_session.resolve_principal")
@patch("memex.mcp_server.auth_session.validate_key")
def test_login_defaults_to_viewer_role_when_no_principal(mock_validate_key, mock_resolve_principal):
    """A key with no registered Principal (e.g. legacy/unregistered) fails
    safe to role="viewer" rather than crashing or defaulting to admin."""
    app = _build_standalone_app()

    with TestClient(app) as client:
        mock_validate_key.return_value = True
        mock_resolve_principal.return_value = None
        client.post("/login", data={"key": "mx_someuser12345"}, follow_redirects=False)

        response = client.get("/_protected_role")
        assert response.status_code == 403


# ---------------------------------------------------------------------------
# NET-18 (05-02-PLAN.md) — /team/* read endpoint tests
# ---------------------------------------------------------------------------


def _login(client: TestClient, mock_validate_key, key: str = "mx_someuser12345") -> None:
    """Shared login helper for the /team/* tests below — POSTs /login with
    `validate_key` patched True, so subsequent requests replay a valid
    session cookie."""
    mock_validate_key.return_value = True
    response = client.post("/login", data={"key": key}, follow_redirects=False)
    assert response.status_code == 303


@patch("memex.mcp_server.team.get_graph_client")
@patch("memex.mcp_server.team.TelemetryDB")
@patch("memex.mcp_server.auth_session.validate_key")
def test_team_activity_no_attribution_yet(mock_validate_key, mock_telemetry_db, mock_get_client):
    """Pre-Phase-01 state: TelemetryDB.get_stats returns tool-client call
    volume, but the graph-attribution query returns zero rows — the response
    must report attribution_available=False and by_principal=[] rather than
    silently passing off empty data as real per-principal attribution
    (05-RESEARCH.md Pitfall 2)."""
    mock_telemetry_instance = MagicMock()
    mock_telemetry_instance.get_stats.return_value = {
        "by_agent": [
            {"agent": "claude-code", "calls": 12, "tokens_returned": 500, "tokens_saved": 300},
        ]
    }
    mock_telemetry_db.return_value = mock_telemetry_instance

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_attribution_res = MagicMock()
    mock_attribution_res.records = []
    mock_client.driver.execute_query = AsyncMock(return_value=mock_attribution_res)

    mock_server = MagicMock(spec=Server)
    app = create_app(mock_server, "/fake/repo")
    with TestClient(app) as client:
        _login(client, mock_validate_key)
        response = client.get("/team/activity")

    assert response.status_code == 200
    data = response.json()
    assert data["attribution_available"] is False
    assert data["by_principal"] == []
    assert data["by_tool_client"] == [
        {"tool_client": "claude-code", "calls": 12, "tokens_returned": 500, "tokens_saved": 300}
    ]


@patch("memex.mcp_server.team.get_graph_client")
@patch("memex.mcp_server.team.TelemetryDB")
@patch("memex.mcp_server.auth_session.validate_key")
def test_team_activity_with_attribution(mock_validate_key, mock_telemetry_db, mock_get_client):
    """Once the graph-attribution query returns rows carrying a harness
    value, attribution_available flips to True and by_principal is
    populated with per-harness write counts."""
    mock_telemetry_instance = MagicMock()
    mock_telemetry_instance.get_stats.return_value = {
        "by_agent": [
            {"agent": "claude-code", "calls": 12, "tokens_returned": 500, "tokens_saved": 300},
        ]
    }
    mock_telemetry_db.return_value = mock_telemetry_instance

    mock_client = MagicMock()
    mock_get_client.return_value = mock_client
    mock_principal_record = MagicMock()
    mock_principal_record.data.return_value = {
        "principal": "claude-code-mx_someus",
        "decision_count": 3,
        "problem_count": 1,
    }
    mock_attribution_res = MagicMock()
    mock_attribution_res.records = [mock_principal_record]
    mock_client.driver.execute_query = AsyncMock(return_value=mock_attribution_res)

    mock_server = MagicMock(spec=Server)
    app = create_app(mock_server, "/fake/repo")
    with TestClient(app) as client:
        _login(client, mock_validate_key)
        response = client.get("/team/activity")

    assert response.status_code == 200
    data = response.json()
    assert data["attribution_available"] is True
    assert data["by_principal"] == [
        {"principal": "claude-code-mx_someus", "decision_count": 3, "problem_count": 1}
    ]


@patch("memex.mcp_server.team.get_graph_client")
@patch("memex.mcp_server.auth_session.validate_key")
def test_team_graph_requires_session(mock_validate_key, mock_get_client):
    """GET /team/graph with no session cookie returns 401; with a valid
    session it returns the same {"nodes": [...], "edges": [...]} shape as
    the existing unauthenticated /graph route."""
    mock_server = MagicMock(spec=Server)
    app = create_app(mock_server, "/fake/repo")

    with TestClient(app) as client:
        response = client.get("/team/graph")
        assert response.status_code == 401

        _login(client, mock_validate_key)

        mock_client = MagicMock()
        mock_get_client.return_value = mock_client

        mock_node_record = MagicMock()
        mock_node_record.data.return_value = {
            "id": "node-1",
            "name": "foo.py",
            "raw_type": "Module",
            "summary": "a py module",
            "created_at": "2026-05-23T12:00:00",
            "status": "",
            "scope": "",
            "source_commit": "",
        }
        mock_edge_record = MagicMock()
        mock_edge_record.data.return_value = {
            "source": "node-1",
            "target": "node-2",
            "type": "MOTIVATES",
            "created_at": "2026-05-23T12:05:00",
        }
        mock_nodes_res = MagicMock()
        mock_nodes_res.records = [mock_node_record]
        mock_edges_res = MagicMock()
        mock_edges_res.records = [mock_edge_record]
        mock_client.driver.execute_query = AsyncMock(side_effect=[mock_nodes_res, mock_edges_res])

        response = client.get("/team/graph")

    assert response.status_code == 200
    data = response.json()
    assert "nodes" in data
    assert "edges" in data
    assert len(data["nodes"]) == 1
    assert data["nodes"][0]["name"] == "foo.py"
    assert data["nodes"][0]["type"] == "Module"
    assert len(data["edges"]) == 1
    assert data["edges"][0]["type"] == "MOTIVATES"


# ---------------------------------------------------------------------------
# Task 2 — /team/confidence and /team/conflicts
# ---------------------------------------------------------------------------


def _confidence_row(module: str, base_confidence: float, days_ago: int, validated: bool) -> dict:
    last_reinforced = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return {
        "id": f"{module}-{days_ago}-{base_confidence}",
        "module": module,
        "base_confidence": base_confidence,
        "last_reinforced_at": last_reinforced.isoformat(),
        "created_at": last_reinforced.isoformat(),
        "validated": validated,
    }


@patch("memex.mcp_server.team.get_recent_decisions_raw")
@patch("memex.mcp_server.auth_session.validate_key")
def test_team_confidence_aggregation(mock_validate_key, mock_get_recent_decisions_raw):
    """/team/confidence's by_module avg_confidence must match the mean of
    current_confidence() computed independently over the same fixture rows
    in this test — not a re-derived formula."""
    fixture_rows = [
        _confidence_row("mod_a", base_confidence=0.6, days_ago=5, validated=False),
        _confidence_row("mod_a", base_confidence=0.6, days_ago=40, validated=False),  # stale-ish (regime 3)
        _confidence_row("mod_b", base_confidence=0.9, days_ago=1, validated=True),
    ]
    mock_get_recent_decisions_raw.return_value = fixture_rows

    mock_server = MagicMock(spec=Server)
    app = create_app(mock_server, "/fake/repo")
    with TestClient(app) as client:
        _login(client, mock_validate_key)
        response = client.get("/team/confidence")

    assert response.status_code == 200
    data = response.json()

    by_module = {entry["module"]: entry for entry in data["by_module"]}
    assert set(by_module.keys()) == {"mod_a", "mod_b"}

    mod_a_rows = [r for r in fixture_rows if r["module"] == "mod_a"]
    mod_b_rows = [r for r in fixture_rows if r["module"] == "mod_b"]
    expected_mod_a_avg = sum(current_confidence(r) for r in mod_a_rows) / len(mod_a_rows)
    expected_mod_b_avg = sum(current_confidence(r) for r in mod_b_rows) / len(mod_b_rows)

    assert by_module["mod_a"]["avg_confidence"] == _approx(expected_mod_a_avg)
    assert by_module["mod_b"]["avg_confidence"] == _approx(expected_mod_b_avg)
    assert by_module["mod_a"]["node_count"] == 2
    assert by_module["mod_b"]["node_count"] == 1

    from memex.graph.confidence import is_stale
    expected_mod_a_stale = sum(1 for r in mod_a_rows if is_stale(r))
    assert by_module["mod_a"]["stale_count"] == expected_mod_a_stale


def _approx(value: float, tol: float = 1e-9):
    """Tiny local float-compare helper (avoids importing pytest.approx just
    for one assertion pattern used twice in this module)."""
    class _Approx:
        def __eq__(self, other):
            return abs(other - value) < tol

    return _Approx()


def _conflict_row(module: str, idx: int) -> dict:
    now = datetime.now(timezone.utc)
    return {
        "id": f"{module}-{idx}",
        "module": module,
        "text": f"decision {module} {idx}",
        "valid_from": now.isoformat(),
        "valid_until": None,
        "created_at": now.isoformat(),
    }


@patch("memex.mcp_server.team.get_graph_client")
@patch("memex.mcp_server.team.get_recent_decisions_raw")
@patch("memex.mcp_server.auth_session.validate_key")
def test_team_conflicts_windowing_avoids_global_cap(
    mock_validate_key, mock_get_recent_decisions_raw, mock_get_client
):
    """80 decisions total (60 in module_a, 60 > the 50-item cap; 20 in
    module_b, well under it). Per-module windowing means module_a is capped
    and explicitly reported, while module_b's conflicts still come through —
    proving a combined 80-item global list would have hit
    detect_decision_conflicts's >50 early-return and silently zeroed out
    BOTH modules (05-RESEARCH.md Pitfall 1)."""
    module_a_rows = [_conflict_row("module_a", i) for i in range(60)]
    module_b_rows = [_conflict_row("module_b", i) for i in range(20)]
    mock_get_recent_decisions_raw.return_value = module_a_rows + module_b_rows

    mock_client = MagicMock()
    mock_client.similarity = AsyncMock(return_value=0.1)  # below default threshold 0.4
    mock_get_client.return_value = mock_client

    mock_server = MagicMock(spec=Server)
    app = create_app(mock_server, "/fake/repo")
    with TestClient(app) as client:
        _login(client, mock_validate_key)
        response = client.get("/team/conflicts")

    assert response.status_code == 200
    data = response.json()

    assert data["capped_modules"] == ["module_a"]
    assert data["modules_scanned"] == 2
    assert data["decisions_scanned"] == 80

    conflict_modules = {entry["module"] for entry in data["conflicts"]}
    assert "module_b" in conflict_modules
    module_b_conflicts = [c for c in data["conflicts"] if c["module"] == "module_b"]
    assert len(module_b_conflicts) > 0

    module_a_conflicts = [c for c in data["conflicts"] if c["module"] == "module_a"]
    assert len(module_a_conflicts) > 0
    assert len(module_a_conflicts) <= 50


def test_team_confidence_and_conflicts_require_session():
    """GET /team/confidence and GET /team/conflicts both return 401 with no
    session cookie."""
    mock_server = MagicMock(spec=Server)
    app = create_app(mock_server, "/fake/repo")
    with TestClient(app) as client:
        assert client.get("/team/confidence").status_code == 401
        assert client.get("/team/conflicts").status_code == 401


def test_team_routes_use_require_role_viewer():
    """All four /team/* routes must use Depends(require_role("viewer")),
    not a bare Depends(require_session) — inspects the route dependants'
    dependency callables directly rather than relying on behavior alone."""
    from memex.mcp_server.team import create_team_router

    router = create_team_router("/fake/repo")
    expected_paths = {"/team/activity", "/team/confidence", "/team/conflicts", "/team/graph"}
    seen_paths = set()

    for route in router.routes:
        seen_paths.add(route.path)
        dependant_names = [
            dep.call.__qualname__ for dep in route.dependant.dependencies
        ]
        # require_role("viewer") returns a closure named "_dependency"
        # defined inside require_role — its __qualname__ is
        # "require_role.<locals>._dependency", distinguishing it from a
        # bare require_session reference.
        assert any(
            name.startswith("require_role.<locals>") for name in dependant_names
        ), f"{route.path} is not gated by require_role(...): {dependant_names}"

    assert seen_paths == expected_paths


def test_activity_since_until_params_reach_cypher_query():
    """?since=<ISO date>&until=<ISO date> narrows the per-principal
    attribution Cypher query's created_at bound instead of the rolling
    `days` window. Bypasses auth via a patched require_role so the test
    exercises the query-building logic directly."""
    mock_server = MagicMock(spec=Server)

    async def _bypass_role(request=None):
        return "test-principal"

    with patch("memex.mcp_server.team.require_role", return_value=_bypass_role), patch(
        "memex.mcp_server.team.TelemetryDB"
    ) as mock_telemetry_cls, patch(
        "memex.mcp_server.team.get_graph_client", new=AsyncMock()
    ) as mock_get_client:
        mock_telemetry_cls.return_value.get_stats.return_value = {"by_agent": []}
        mock_client = AsyncMock()
        mock_client.driver.execute_query = AsyncMock(return_value=MagicMock(records=[]))
        mock_get_client.return_value = mock_client

        app = create_app(mock_server, "/fake/repo")
        with TestClient(app) as client:
            client.get("/team/activity", params={"since": "2026-08-01", "until": "2026-08-10"})

        called_params = mock_client.driver.execute_query.call_args.kwargs["params"]
        assert called_params["since"] == "2026-08-01"
        assert called_params["until"] == "2026-08-10"


def test_activity_since_only_reaches_cypher_query():
    """?since=<date> with no `until` still reaches the Cypher query params
    (as since="...", until=None) — the asymmetric `IS NULL OR` clause must
    not require both to be set."""
    mock_server = MagicMock(spec=Server)

    async def _bypass_role(request=None):
        return "test-principal"

    with patch("memex.mcp_server.team.require_role", return_value=_bypass_role), patch(
        "memex.mcp_server.team.TelemetryDB"
    ) as mock_telemetry_cls, patch(
        "memex.mcp_server.team.get_graph_client", new=AsyncMock()
    ) as mock_get_client:
        mock_telemetry_cls.return_value.get_stats.return_value = {"by_agent": []}
        mock_client = AsyncMock()
        mock_client.driver.execute_query = AsyncMock(return_value=MagicMock(records=[]))
        mock_get_client.return_value = mock_client

        app = create_app(mock_server, "/fake/repo")
        with TestClient(app) as client:
            client.get("/team/activity", params={"since": "2026-08-01"})

        called_params = mock_client.driver.execute_query.call_args.kwargs["params"]
        assert called_params["since"] == "2026-08-01"
        assert called_params["until"] is None


def test_activity_until_only_reaches_cypher_query():
    """?until=<date> with no `since` still reaches the Cypher query params
    (as since=None, until="...") — the asymmetric `IS NULL OR` clause must
    not require both to be set."""
    mock_server = MagicMock(spec=Server)

    async def _bypass_role(request=None):
        return "test-principal"

    with patch("memex.mcp_server.team.require_role", return_value=_bypass_role), patch(
        "memex.mcp_server.team.TelemetryDB"
    ) as mock_telemetry_cls, patch(
        "memex.mcp_server.team.get_graph_client", new=AsyncMock()
    ) as mock_get_client:
        mock_telemetry_cls.return_value.get_stats.return_value = {"by_agent": []}
        mock_client = AsyncMock()
        mock_client.driver.execute_query = AsyncMock(return_value=MagicMock(records=[]))
        mock_get_client.return_value = mock_client

        app = create_app(mock_server, "/fake/repo")
        with TestClient(app) as client:
            client.get("/team/activity", params={"until": "2026-08-10"})

        called_params = mock_client.driver.execute_query.call_args.kwargs["params"]
        assert called_params["since"] is None
        assert called_params["until"] == "2026-08-10"
