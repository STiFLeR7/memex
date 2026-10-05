"""Upgrading an existing Phase 3 control plane in place.

The hardening pass added `check_outcome`/`check_reason` to
`live_adapter_requests` and introduced `live_adapter_deliveries`, but declared
both through `CREATE TABLE IF NOT EXISTS`. A database written by the earlier
adapter therefore keeps its original three-column request table, and the first
replay lookup fails with `no such column: check_outcome`.

These tests pin the upgrade against the *exact* prior schema, taken from
`01da9f8`, rather than against a convenient approximation. No Neo4j is needed:
the migration is control-plane only, so the engine here is a stub that carries
nothing but the registration the adapter reads.
"""
import json
import sqlite3
import subprocess
import types

import pytest

from memex.context.live import ActionRequest, SessionIdentity
from memex.context.revision import RepositoryView
from memex.integrations.claude_code import ClaudeCodeAdapter, register
from memex.runtime.views import discover_repository

#: Verbatim DDL from the pre-hardening adapter. `live_adapter_deliveries` is
#: absent because it did not exist, which is the other half of the upgrade.
PRIOR_SCHEMA = """
    CREATE TABLE IF NOT EXISTS live_adapter_sessions (
        native_session_id TEXT PRIMARY KEY,
        memex_session_id TEXT NOT NULL,
        task_id TEXT NOT NULL,
        continuation_token TEXT,
        context_retained INTEGER NOT NULL DEFAULT 1
    );
    CREATE TABLE IF NOT EXISTS live_adapter_denials (
        task_id TEXT NOT NULL, target_key TEXT NOT NULL,
        attempt_id TEXT NOT NULL, PRIMARY KEY(task_id, target_key)
    );
    CREATE TABLE IF NOT EXISTS live_adapter_requests (
        task_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
        request TEXT NOT NULL, response TEXT,
        PRIMARY KEY(task_id, attempt_id)
    );
"""

TASK = "task-legacy"
ATTEMPT = "toolu_legacy"
NATIVE = "native-legacy"


def legacy_request(registration) -> ActionRequest:
    view = RepositoryView(repo_id=registration.repo_id, worktree_id=registration.worktree_id,
                          head_commit=None, content_generation=1, indexed_generation=1,
                          manifest_hash="sha256:" + "a" * 64)
    return ActionRequest(
        session=SessionIdentity(harness="claude_code", native_session_id=NATIVE,
                                memex_session_id="cc-legacy", principal_id="owner"),
        task_id=TASK, view=view, attempt_id=ATTEMPT, action_kind="Edit",
        targets=("api.py",), expected_hashes=(("api.py", "sha256:" + "b" * 64),),
        last_acknowledged=1, scope_complete=True, context_retained=True)


@pytest.fixture
def legacy(tmp_path):
    """A registered repository whose control plane holds pre-hardening rows."""
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (repo / "api.py").write_text("def send(payload):\n    return payload\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)

    registration = discover_repository(repo)
    capability = register(repo, "owner")
    request = legacy_request(registration)

    db = sqlite3.connect(registration.runtime_path)
    with db:
        db.executescript(PRIOR_SCHEMA)
        db.execute("INSERT INTO live_adapter_sessions VALUES(?,?,?,?,1)",
                   (NATIVE, "cc-legacy", TASK, "token-legacy"))
        db.execute("INSERT INTO live_adapter_denials VALUES(?,?,?)",
                   (TASK, "api.py", "toolu_root"))
        db.execute("INSERT INTO live_adapter_requests(task_id,attempt_id,request,response) "
                   "VALUES(?,?,?,?)",
                   (TASK, ATTEMPT, request.model_dump_json(),
                    json.dumps({"decision": "deny", "reason": "memex: legacy correction",
                                "additional_context": "", "system_message": "",
                                "delivery_key": None})))
    db.close()

    engine = types.SimpleNamespace(indexer=types.SimpleNamespace(registration=registration))
    return engine, capability, registration


def columns(path, table) -> set[str]:
    db = sqlite3.connect(path)
    try:
        return {row[1] for row in db.execute(f"PRAGMA table_info({table})")}
    finally:
        db.close()


def test_the_legacy_schema_really_is_missing_the_verdict_columns(legacy):
    """Guards the premise: without this the rest of the file proves nothing."""
    _, _, registration = legacy
    assert columns(registration.runtime_path, "live_adapter_requests") == {
        "task_id", "attempt_id", "request", "response"}
    assert columns(registration.runtime_path, "live_adapter_deliveries") == set()


def test_replay_lookup_works_against_an_upgraded_database(legacy):
    """The reproduction: this raised `no such column: check_outcome`."""
    engine, capability, _ = legacy
    adapter = ClaudeCodeAdapter(engine, capability)
    request, response, verdict = adapter._stored_attempt(TASK, ATTEMPT)
    assert request is not None and request.attempt_id == ATTEMPT
    assert response is not None and response.decision == "deny"
    assert verdict is None, "a legacy row records no verdict, so it cannot vouch for itself"


def test_existing_rows_survive_the_upgrade(legacy):
    engine, capability, registration = legacy
    ClaudeCodeAdapter(engine, capability)

    db = sqlite3.connect(registration.runtime_path)
    db.row_factory = sqlite3.Row
    try:
        session = db.execute("SELECT * FROM live_adapter_sessions").fetchall()
        denial = db.execute("SELECT * FROM live_adapter_denials").fetchall()
        requests = db.execute("SELECT * FROM live_adapter_requests").fetchall()
    finally:
        db.close()

    assert [dict(r) for r in session] == [{
        "native_session_id": NATIVE, "memex_session_id": "cc-legacy", "task_id": TASK,
        "continuation_token": "token-legacy", "context_retained": 1}]
    assert [tuple(r) for r in denial] == [(TASK, "api.py", "toolu_root")]
    assert len(requests) == 1 and requests[0]["attempt_id"] == ATTEMPT
    assert json.loads(requests[0]["response"])["reason"] == "memex: legacy correction"
    assert requests[0]["check_outcome"] is None and requests[0]["check_reason"] is None
    assert "state" in columns(registration.runtime_path, "live_adapter_deliveries")


def test_a_legacy_response_cannot_vouch_for_itself_on_replay(legacy):
    """A cached response with no verdict must never be reused as-is.

    The replay path compares the core's current `(outcome, reason)` against the
    verdict the stored text was rendered for. A legacy row has none, so it can
    never compare equal, which forces the adapter back through the core for an
    authoritative answer instead of letting pre-upgrade text bypass expiry or a
    revoked authorization.
    """
    engine, capability, _ = legacy
    adapter = ClaudeCodeAdapter(engine, capability)
    _, _, verdict = adapter._stored_attempt(TASK, ATTEMPT)
    for outcome, reason in (("proceed", "checked_declared_scope"),
                            ("resync_required", "attempt_expired; submit a new action-attempt ID"),
                            ("replan", "context_changed_or_delivery_pending")):
        assert verdict != (outcome, reason)


def test_repeated_initialization_is_harmless(legacy):
    engine, capability, registration = legacy
    before = None
    for _ in range(4):
        ClaudeCodeAdapter(engine, capability)
        after = columns(registration.runtime_path, "live_adapter_requests")
        assert {"check_outcome", "check_reason"} <= after
        assert before is None or after == before
        before = after

    db = sqlite3.connect(registration.runtime_path)
    try:
        assert db.execute("SELECT count(*) FROM live_adapter_requests").fetchone()[0] == 1
    finally:
        db.close()


def test_concurrent_initialization_is_safe(legacy):
    """Several adapter processes may start against one repository at once.

    Two processes can both observe the column missing, so the loser of that race
    must treat `duplicate column name` as success rather than crashing the hook.
    """
    engine, capability, registration = legacy
    script = (
        "import sys, types\n"
        "sys.path.insert(0, %r)\n"
        "from memex.integrations.claude_code import ClaudeCodeAdapter, load_capability\n"
        "from memex.runtime.views import discover_repository\n"
        "reg = discover_repository(%r)\n"
        "cap = load_capability(reg)\n"
        "engine = types.SimpleNamespace(indexer=types.SimpleNamespace(registration=reg))\n"
        "ClaudeCodeAdapter(engine, cap)\n"
        "print('ok')\n"
    ) % (str(__import__("pathlib").Path(__file__).resolve().parents[1]), str(registration.root))

    workers = [subprocess.Popen([__import__("sys").executable, "-c", script],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
               for _ in range(6)]
    results = [w.communicate() for w in workers]
    for (out, err), worker in zip(results, workers, strict=True):
        assert worker.returncode == 0, err
        assert out.strip().endswith("ok")
    assert {"check_outcome", "check_reason"} <= columns(
        registration.runtime_path, "live_adapter_requests")


def test_a_fresh_database_still_gets_the_current_schema(tmp_path):
    repo = tmp_path / "fresh"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (repo / "api.py").write_text("x = 1\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    registration = discover_repository(repo)
    capability = register(repo, "owner")
    engine = types.SimpleNamespace(indexer=types.SimpleNamespace(registration=registration))

    ClaudeCodeAdapter(engine, capability)
    assert {"check_outcome", "check_reason"} <= columns(
        registration.runtime_path, "live_adapter_requests")
    assert adapter_tables(registration.runtime_path) >= {
        "live_adapter_sessions", "live_adapter_denials",
        "live_adapter_requests", "live_adapter_deliveries"}


def adapter_tables(path) -> set[str]:
    db = sqlite3.connect(path)
    try:
        return {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    finally:
        db.close()
