"""Upgrading an accepted Phase 3 control plane to Phase 4's host-namespaced schema.

Phase 3 keyed adapter bindings by the native session ID alone. With a second
host, a Codex thread whose native ID equals a Claude session's would overwrite
that session's binding. Phase 4 rekeys bindings by `(harness, native_session_id)`
and adds host, binding and packet-fingerprint columns to the delivery ledger.

The prior schema here is the verbatim DDL from accepted commit `1390f15`. No
graph backend is needed: the engine is a stub carrying only the registration.
"""
import json
import pathlib
import sqlite3
import subprocess
import sys
import types

import pytest

from memex.integrations.claude_code import ClaudeCodeAdapter
from memex.integrations.claude_code import register as register_claude
from memex.integrations.codex import CodexAdapter
from memex.integrations.codex import register as register_codex
from memex.runtime.views import discover_repository

#: Verbatim from the accepted Phase 3 adapter (`1390f15`).
PHASE3_SCHEMA = """
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
        check_outcome TEXT, check_reason TEXT,
        PRIMARY KEY(task_id, attempt_id)
    );
    CREATE TABLE IF NOT EXISTS live_adapter_deliveries (
        task_id TEXT NOT NULL, sequence INTEGER NOT NULL,
        native_session_id TEXT NOT NULL, view_id TEXT NOT NULL,
        marker TEXT NOT NULL, kind TEXT NOT NULL, attempt_id TEXT,
        state TEXT NOT NULL, confirmations INTEGER NOT NULL DEFAULT 0,
        PRIMARY KEY(task_id, sequence)
    );
"""

NATIVE = "shared-native-id"
TASK = "task-phase3"


def make_repo(tmp_path, name="repo"):
    repo = tmp_path / name
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (repo / "api.py").write_text("def send(payload):\n    return payload\n")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    return repo


def stub(registration):
    return types.SimpleNamespace(indexer=types.SimpleNamespace(registration=registration))


@pytest.fixture
def phase3(tmp_path):
    """A registered repository whose control plane is an in-flight Phase 3 session."""
    repo = make_repo(tmp_path)
    registration = discover_repository(repo)
    claude_capability = register_claude(repo, "owner")
    codex_capability = register_codex(repo, "owner")
    db = sqlite3.connect(registration.runtime_path)
    with db:
        db.executescript(PHASE3_SCHEMA)
        db.execute("INSERT INTO live_adapter_sessions VALUES(?,?,?,?,1)",
                   (NATIVE, "cc-phase3", TASK, "token-phase3"))
        db.execute("INSERT INTO live_adapter_denials VALUES(?,?,?)", (TASK, "api.py", "toolu_root"))
        db.execute("INSERT INTO live_adapter_requests VALUES(?,?,?,?,?,?)",
                   (TASK, "toolu_root", "{}", json.dumps({"decision": "deny"}), "replan", "context_changed"))
        db.execute("INSERT INTO live_adapter_deliveries"
                   "(task_id,sequence,native_session_id,view_id,marker,kind,attempt_id,state)"
                   " VALUES(?,?,?,?,?,?,?, 'emitted')",
                   (TASK, 2, NATIVE, "view:x", "memex-delivery:0123456789abcdef", "correction", "toolu_root"))
    db.close()
    return repo, registration, claude_capability, codex_capability


def rows(path, table):
    db = sqlite3.connect(path)
    db.row_factory = sqlite3.Row
    try:
        return [dict(r) for r in db.execute(f"SELECT * FROM {table}")]
    finally:
        db.close()


def columns(path, table):
    db = sqlite3.connect(path)
    try:
        return [r[1] for r in db.execute(f"PRAGMA table_info({table})")]
    finally:
        db.close()


def test_the_phase3_key_lets_one_host_overwrite_another(phase3):
    """The reproduction, against the untouched Phase 3 table."""
    _, registration, _, _ = phase3
    db = sqlite3.connect(registration.runtime_path)
    with db:
        db.execute("INSERT OR REPLACE INTO live_adapter_sessions"
                   "(native_session_id,memex_session_id,task_id,continuation_token,context_retained)"
                   " VALUES(?,?,?,?,1)", (NATIVE, "cx-other", "task-codex", "token-codex"))
        survivors = db.execute("SELECT memex_session_id FROM live_adapter_sessions").fetchall()
    db.close()
    assert survivors == [("cx-other",)], "Phase 3's key really does collapse two hosts' bindings"


def test_existing_phase3_state_survives_the_upgrade(phase3):
    _, registration, claude_capability, _ = phase3
    adapter = ClaudeCodeAdapter(stub(registration), claude_capability)

    assert rows(registration.runtime_path, "live_adapter_sessions") == [{
        "harness": "claude_code", "native_session_id": NATIVE, "memex_session_id": "cc-phase3",
        "task_id": TASK, "continuation_token": "token-phase3", "context_retained": 1}]
    assert adapter._binding(NATIVE)["task_id"] == TASK, "the in-flight Claude session is still bound"
    assert rows(registration.runtime_path, "live_adapter_denials") == [
        {"task_id": TASK, "target_key": "api.py", "attempt_id": "toolu_root"}]
    request = rows(registration.runtime_path, "live_adapter_requests")[0]
    assert (request["attempt_id"], request["check_outcome"]) == ("toolu_root", "replan")

    delivery = adapter.delivery_states(NATIVE)
    assert len(delivery) == 1 and delivery[0]["state"] == "emitted"
    assert delivery[0]["harness"] == "claude_code"
    assert delivery[0]["packet_sha256"] is None, "a legacy row has no fingerprint, and is not given one"


def test_a_legacy_pending_packet_cannot_be_confirmed_without_its_fingerprint(phase3):
    """Preserved, still pending, and never acknowledged on marker evidence alone."""
    _, registration, claude_capability, _ = phase3
    adapter = ClaudeCodeAdapter(stub(registration), claude_capability)
    row = adapter.delivery_states(NATIVE)[0]
    evidence = [("tool_denial", "toolu_root", "memex: outcome=replan\n[memex-delivery:0123456789abcdef]")]
    assert adapter._insertion_status(row, evidence) == "truncated"
    assert adapter._insertion_status(row, []) is None


def test_after_upgrade_two_hosts_bind_the_same_native_id_independently(phase3):
    _, registration, claude_capability, codex_capability = phase3
    claude = ClaudeCodeAdapter(stub(registration), claude_capability)
    codex_adapter = CodexAdapter(stub(registration), codex_capability)
    codex_adapter._bind(NATIVE, codex_adapter.session_identity(NATIVE), "task-codex", "token-codex")

    assert claude._binding(NATIVE)["task_id"] == TASK
    assert codex_adapter._binding(NATIVE)["task_id"] == "task-codex"
    assert claude.delivery_states(NATIVE) and codex_adapter.delivery_states(NATIVE) == []


def test_repeated_initialization_is_harmless(phase3):
    _, registration, claude_capability, codex_capability = phase3
    before = None
    for adapter_cls, capability in [(ClaudeCodeAdapter, claude_capability), (CodexAdapter, codex_capability)] * 3:
        adapter_cls(stub(registration), capability)
        shape = {t: columns(registration.runtime_path, t)
                 for t in ("live_adapter_sessions", "live_adapter_deliveries", "live_adapter_requests")}
        assert before is None or shape == before
        before = shape
    assert len(rows(registration.runtime_path, "live_adapter_sessions")) == 1


def test_concurrent_initialization_from_both_hosts_is_safe(phase3):
    """Six processes, both hosts, one Phase 3 database: the rebuild happens once."""
    repo, registration, _, _ = phase3
    checkout = str(pathlib.Path(__file__).resolve().parents[1])
    script = (
        "import sys, types\n"
        f"sys.path.insert(0, {checkout!r})\n"
        "from memex.runtime.views import discover_repository\n"
        "from memex.integrations import claude_code, codex\n"
        f"reg = discover_repository({str(repo)!r})\n"
        "engine = types.SimpleNamespace(indexer=types.SimpleNamespace(registration=reg))\n"
        "module = claude_code if sys.argv[1] == 'claude' else codex\n"
        "cls = claude_code.ClaudeCodeAdapter if sys.argv[1] == 'claude' else codex.CodexAdapter\n"
        "cls(engine, module.load_capability(reg))\n"
        "print('ok')\n"
    )
    workers = [subprocess.Popen([sys.executable, "-c", script, host], stdout=subprocess.PIPE,
                                stderr=subprocess.PIPE, text=True)
               for host in ("claude", "codex") * 3]
    for worker in workers:
        out, err = worker.communicate(timeout=120)
        assert worker.returncode == 0, err
        assert out.strip().endswith("ok")
    assert rows(registration.runtime_path, "live_adapter_sessions")[0]["harness"] == "claude_code"
    assert "live_adapter_sessions_v2" not in {
        r["name"] for r in rows(registration.runtime_path, "sqlite_master")}


def test_a_fresh_database_gets_the_namespaced_schema(tmp_path):
    repo = make_repo(tmp_path, "fresh")
    registration = discover_repository(repo)
    CodexAdapter(stub(registration), register_codex(repo, "owner"))
    assert columns(registration.runtime_path, "live_adapter_sessions")[:2] == ["harness", "native_session_id"]
    assert {"harness", "binding", "packet_sha256", "packet_chars", "marker_offset"} <= set(
        columns(registration.runtime_path, "live_adapter_deliveries"))
