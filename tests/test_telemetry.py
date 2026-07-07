import os
import sqlite3
import pytest
from pathlib import Path
from unittest.mock import AsyncMock, patch, MagicMock
from fastapi.testclient import TestClient

from memex.graph.telemetry import (
    TelemetryDB,
    estimate_naive_tokens,
    detect_agent,
    record_tool_call,
    get_telemetry_db_path,
)
from memex.mcp_server.http import create_app
from memex.cli import run_stats_command

@pytest.fixture
def temp_db_path(tmp_path):
    """Fixture returning a temporary telemetry database path."""
    db_file = tmp_path / "subfolder" / "telemetry.db"
    return db_file

def test_telemetry_db_created_on_first_call(temp_db_path):
    """Verifies that the telemetry database and its folder are created and set to WAL mode on initialization."""
    assert not temp_db_path.exists()
    
    db = TelemetryDB(db_path=temp_db_path)
    
    assert temp_db_path.exists()
    
    # Verify WAL mode is set
    with db.get_connection() as conn:
        cursor = conn.cursor()
        cursor.execute("PRAGMA journal_mode;")
        mode = cursor.fetchone()[0]
        assert mode.lower() == "wal"

def test_naive_tokens_estimated_correctly_for_module_scope(tmp_path):
    """Tests naive token estimation for scoped and unscoped tools, and OS error safety."""
    repo_root = str(tmp_path)
    
    # 1. Create a dummy file with size 400 bytes (should be 100 tokens)
    dummy_file = tmp_path / "src" / "test.py"
    dummy_file.parent.mkdir(parents=True, exist_ok=True)
    dummy_file.write_text("A" * 400)
    
    # Test project context (sum of file sizes // 4)
    tokens = estimate_naive_tokens("get_project_context", 10, repo_root, ["src/test.py"])
    assert tokens == 100
    
    # Test symbol context
    tokens_symbol = estimate_naive_tokens("get_symbol_context", 15, repo_root, ["src/test.py"])
    assert tokens_symbol == 100
    
    # Test open problems with module
    tokens_problems = estimate_naive_tokens("get_open_problems", 20, repo_root, ["src/test.py"])
    assert tokens_problems == 100
    
    # 2. Empty module files
    assert estimate_naive_tokens("get_project_context", 10, repo_root, []) is None
    
    # 3. OSError fallback test
    # If file doesn't exist, it should fall back safely without raising
    tokens_missing = estimate_naive_tokens("get_symbol_context", 10, repo_root, ["src/nonexistent.py"])
    # 10 tokens returned * fallback multiplier 8 = 80
    assert tokens_missing == 80
    
    # 4. Unscoped tool multipliers
    assert estimate_naive_tokens("search_context", 100) == 1200
    assert estimate_naive_tokens("get_recent_decisions", 100) == 500
    assert estimate_naive_tokens("get_stale_context", 100) == 300
    assert estimate_naive_tokens("get_open_problems", 100, repo_root=None) == 400

def test_agent_detection(monkeypatch):
    """Verifies precedence of agent detection: direct -> MCP request_ctx -> env vars -> unknown."""
    # 1. Direct argument
    assert detect_agent("test-agent") == "test-agent"
    
    # 2. Context variable
    mock_ctx = MagicMock()
    mock_ctx.session.client_params.clientInfo.name = "mcp-client-name"
    
    # ContextVar mock setup
    mock_request_ctx = MagicMock()
    mock_request_ctx.get.return_value = mock_ctx
    
    with patch("mcp.server.lowlevel.server.request_ctx", mock_request_ctx):
        assert detect_agent() == "mcp-client-name"
        
    # 3. Env var sniffing
    monkeypatch.setenv("CLAUDE_CODE", "1")
    assert detect_agent() == "claude-code"
    
    monkeypatch.delenv("CLAUDE_CODE")
    monkeypatch.setenv("GEMINI_CLI", "1")
    assert detect_agent() == "gemini-cli"
    
    # 4. Unknown fallback
    monkeypatch.delenv("GEMINI_CLI")
    assert detect_agent() == "unknown"

def test_stats_endpoint_returns_json_and_requires_auth(temp_db_path):
    """Verifies stats endpoint behavior: 401 unauthorized, and 200 with stats schema when authenticated."""
    # Create the test server app
    mock_server = MagicMock()
    app = create_app(mock_server, "/fake/repo")
    client = TestClient(app)
    
    # 1. Missing Authorization header
    response = client.get("/stats?days=30")
    assert response.status_code == 401
    assert "detail" in response.json()
    
    # 2. Invalid Bearer Token
    with patch("memex.mcp_server.http.validate_key", return_value=False):
        response = client.get("/stats?days=30", headers={"Authorization": "Bearer bad-token"})
        assert response.status_code == 401
        
    # 3. Success / stats schema check
    # Patch registry validation and TelemetryDB to point to our test database
    with patch("memex.mcp_server.http.validate_key", return_value=True):
        with patch("memex.graph.telemetry.get_telemetry_db_path", return_value=temp_db_path):
            with patch("memex.graph.stats.get_graph_client") as mock_graph_client:
                # Mock Neo4j query for validation health
                mock_driver = MagicMock()
                mock_driver.execute_query = AsyncMock(return_value=MagicMock(records=[
                    MagicMock(data=lambda: {
                        "validated": 5,
                        "unvalidated": 2,
                        "corroborated": 3,
                        "last_validated_at": "2026-05-26T10:00:00Z"
                    })
                ]))
                mock_graph_client.return_value = MagicMock(driver=mock_driver)
                
                # Pre-seed telemetry data
                db = TelemetryDB(db_path=temp_db_path)
                db.record_call("get_project_context", "/fake/repo", "claude-code", 200, 1000)
                db.record_call("search_context", "/fake/repo", "gemini-cli", 50, None)
                
                response = client.get("/stats?days=30", headers={"Authorization": "Bearer good-token"})
                assert response.status_code == 200
                data = response.json()
                
                assert "today" in data
                assert "lifetime" in data
                assert data["lifetime"]["tool_calls"] == 2
                assert data["lifetime"]["tokens_returned"] == 250
                assert data["lifetime"]["tokens_naive"] == 1000
                assert data["lifetime"]["tokens_saved"] == 800
                assert data["lifetime"]["reduction_pct"] == 80.0
                
                # Check breakdowns
                assert len(data["top_tools"]) == 2
                assert data["top_tools"][0]["tool_name"] == "get_project_context"
                assert data["top_tools"][0]["calls"] == 1
                assert data["top_tools"][0]["tokens_saved"] == 800
                
                # Check validation health
                assert "validation_health" in data
                assert data["validation_health"]["validated"] == 5
                assert data["validation_health"]["unvalidated"] == 2
                assert data["validation_health"]["corroborated"] == 3
                assert data["validation_health"]["last_review_days_ago"] is not None

@pytest.mark.asyncio
async def test_read_tools_record_telemetry_on_every_call():
    """Verify that calling read tools triggers record_tool_call with the correct arguments."""
    from memex.mcp_server import tools_read
    
    # Mock Neo4j queries and formatting
    with patch("memex.mcp_server.tools_read.get_config") as mock_cfg, \
         patch("memex.mcp_server.tools_read.get_node_counts", new_callable=AsyncMock) as mock_counts, \
         patch("memex.mcp_server.tools_read.get_active_modules", new_callable=AsyncMock) as mock_modules, \
         patch("memex.mcp_server.tools_read.get_recent_decisions_raw", new_callable=AsyncMock) as mock_decisions, \
         patch("memex.mcp_server.tools_read.get_open_problems_raw", new_callable=AsyncMock) as mock_problems, \
         patch("memex.mcp_server.tools_read.get_stale_edges", new_callable=AsyncMock) as mock_stale, \
         patch("memex.mcp_server.tools_read.count_unvalidated_decisions", new_callable=AsyncMock) as mock_unval, \
         patch("memex.mcp_server.tools_read.get_cluster_level_context", new_callable=AsyncMock) as mock_cluster, \
         patch("memex.mcp_server.tools_read.format_project_context", return_value="dummy briefing") as mock_fmt, \
         patch("memex.graph.telemetry.record_tool_call", new_callable=AsyncMock) as mock_record:
             
        mock_cfg.return_value = MagicMock(repo_root="/fake/repo")
        mock_modules.return_value = [{"path": "src/main.py", "description": "", "symbols": 1}]
        
        res = await tools_read.get_project_context(repo="/fake/repo")
        assert res == "dummy briefing"
        
        # Verify that record_tool_call was called for get_project_context.
        # project_id resolves to None here since "/fake/repo" isn't a real
        # git repo and no explicit `project` was passed — best-effort
        # telemetry attribution (Phase 00 Plan 04 Task 3) never raises and
        # falls back to None rather than breaking the read tool.
        mock_record.assert_called_once_with(
            "get_project_context",
            len("dummy briefing") // 4,
            "/fake/repo",
            ["src/main.py"],
            project_id=None
        )

def test_fresh_telemetry_db_has_project_id_column(temp_db_path):
    """A fresh TelemetryDB creates a tool_calls table with a nullable
    project_id TEXT column (Phase 00 Plan 04 / NET-03)."""
    db = TelemetryDB(db_path=temp_db_path)

    with db.get_connection() as conn:
        cols = {row["name"]: row for row in conn.execute("PRAGMA table_info(tool_calls)").fetchall()}

    assert "project_id" in cols
    assert cols["project_id"]["notnull"] == 0


def test_preexisting_schema_migrates_project_id_column_idempotently(temp_db_path):
    """An existing tool_calls table created by a pre-project_id (v0.6.x)
    TelemetryDB gets the column added via a guarded ALTER TABLE, without
    losing existing rows, and running ensure_schema() twice does not raise."""
    temp_db_path.parent.mkdir(parents=True, exist_ok=True)

    # Simulate the OLD schema (no project_id column) with one pre-existing row.
    conn = sqlite3.connect(str(temp_db_path))
    conn.execute("""
        CREATE TABLE tool_calls (
            id               INTEGER PRIMARY KEY,
            tool_name        TEXT    NOT NULL,
            called_at        TEXT    NOT NULL,
            repo_path        TEXT    NOT NULL,
            agent            TEXT    NOT NULL DEFAULT 'unknown',
            tokens_returned  INTEGER NOT NULL,
            tokens_naive     INTEGER,
            tokens_saved     INTEGER
        );
    """)
    conn.execute(
        "INSERT INTO tool_calls (tool_name, called_at, repo_path, agent, tokens_returned, tokens_naive, tokens_saved) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("get_project_context", "2026-01-01T00:00:00+00:00", "/old/repo", "claude-code", 100, 500, 400)
    )
    conn.commit()
    conn.close()

    # Instantiating a new TelemetryDB against the same file should migrate it.
    db = TelemetryDB(db_path=temp_db_path)

    with db.get_connection() as conn2:
        cols = {row["name"] for row in conn2.execute("PRAGMA table_info(tool_calls)").fetchall()}
        assert "project_id" in cols

        rows = conn2.execute("SELECT * FROM tool_calls").fetchall()
        assert len(rows) == 1
        assert rows[0]["repo_path"] == "/old/repo"
        assert rows[0]["project_id"] is None

    # Running ensure_schema() again must not raise (idempotent).
    db.ensure_schema()


def test_record_call_persists_project_id(temp_db_path):
    """record_call with project_id persists it; omitting it (or passing None)
    persists NULL — existing call sites unaffected."""
    db = TelemetryDB(db_path=temp_db_path)

    db.record_call("get_project_context", "/abs/repo", "claude-code", 100, 500, project_id="github.com/acme/widgets")
    db.record_call("search_context", "/abs/repo", "claude-code", 50, None)

    with db.get_connection() as conn:
        rows = {r["tool_name"]: r for r in conn.execute("SELECT * FROM tool_calls").fetchall()}

    assert rows["get_project_context"]["project_id"] == "github.com/acme/widgets"
    assert rows["search_context"]["project_id"] is None


def test_get_stats_dual_key_project_filter(temp_db_path):
    """get_stats(project=...) matches rows by project_id even when repo_path
    differs; get_stats without project is unchanged (repo_path-only)."""
    db = TelemetryDB(db_path=temp_db_path)

    # Row 1: repo_path matches, project_id matches.
    db.record_call("get_project_context", "/abs/repo", "claude-code", 100, 500, project_id="github.com/acme/widgets")
    # Row 2: different repo_path, but same project_id — should be included
    # when querying by project (dual-key), excluded when querying by repo only.
    db.record_call("search_context", "/other/repo", "claude-code", 50, 200, project_id="github.com/acme/widgets")
    # Row 3: same repo_path as row 1, no project_id at all — always included
    # for repo-path queries; also included for project queries via the
    # `project_id IS NULL AND repo_path = ?` fallback branch.
    db.record_call("get_open_problems", "/abs/repo", "claude-code", 10, 40)

    # Repo-path-only query (no project) — byte-for-byte unchanged behavior:
    # matches rows 1 and 3 only (repo_path = /abs/repo).
    stats_repo_only = db.get_stats(repo_path="/abs/repo", days=7)
    assert stats_repo_only["total_calls"] == 2

    # Project-scoped query — matches rows 1, 2 (project_id match) and row 3
    # (repo_path fallback for untagged rows) = 3 total.
    stats_project = db.get_stats(repo_path="/abs/repo", days=7, project="github.com/acme/widgets")
    assert stats_project["total_calls"] == 3


@pytest.mark.asyncio
async def test_record_tool_call_threads_project_id(temp_db_path):
    """The module-level record_tool_call() threads project_id through to
    TelemetryDB.record_call()."""
    with patch("memex.graph.telemetry.get_telemetry_db_path", return_value=temp_db_path):
        await record_tool_call("get_project_context", 100, repo_path="/abs/repo", project_id="github.com/acme/widgets")

    db = TelemetryDB(db_path=temp_db_path)
    with db.get_connection() as conn:
        row = conn.execute("SELECT * FROM tool_calls WHERE tool_name = 'get_project_context'").fetchone()

    assert row["project_id"] == "github.com/acme/widgets"


@pytest.mark.asyncio
async def test_stats_command_outputs_correct_totals(temp_db_path):
    """Checks the CLI memex stats formatting and execution flow."""
    # Seed data
    db = TelemetryDB(db_path=temp_db_path)
    db.record_call("get_project_context", "/fake/repo", "claude-code", 200, 1000)
    
    with patch("memex.graph.telemetry.get_telemetry_db_path", return_value=temp_db_path), \
         patch("memex.graph.stats.get_graph_client") as mock_graph_client:
             
        mock_driver = MagicMock()
        mock_driver.execute_query = AsyncMock(return_value=MagicMock(records=[
            MagicMock(data=lambda: {
                "validated": 1,
                "unvalidated": 2,
                "corroborated": 0,
                "last_validated_at": None
            })
        ]))
        mock_graph_client.return_value = MagicMock(driver=mock_driver)
        
        import io
        from contextlib import redirect_stdout
        f = io.StringIO()
        with redirect_stdout(f):
            await run_stats_command("/fake/repo", 30)
            
        text_block = f.getvalue().lower()
        
        assert "memex statistics" in text_block
        assert "today" in text_block
        assert "validated:" in text_block
