"""W18: migration, modes, rollback and diagnostics on real Git, SQLite and native Neo4j.

The legacy data is written the way v0.9 writes it: Graphiti `Entity` nodes
keyed by `repo_path`, typed Decision/Problem/Symbol/Module, with governance
fields, episodes and relationships. The migration must never change any of it.
"""
import asyncio
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import uuid

import pytest
import pytest_asyncio

from memex.config import canonical_repo_path
from memex.runtime import migration
from memex.runtime.views import discover_repository

pytestmark = pytest.mark.integration

LEGACY = [
    {"uuid": "a-dec-1", "type": "Decision", "name": "Decision: amounts are integer cents",
     "summary": "Amounts are stored as integer cents", "validated": True, "confidence": 0.95,
     "base_confidence": 0.6, "source": "watcher", "created": "2026-01-01T10:00:00Z"},
    {"uuid": "a-dec-2", "type": "Decision", "name": "Decision: retry three times",
     "summary": "Network calls retry three times", "validated": False, "confidence": 0.4,
     "base_confidence": 0.6, "source": "agent", "harness": "claude-code", "created": "2026-02-01T10:00:00Z"},
    {"uuid": "a-dec-3", "type": "Decision", "name": "Decision: use UTC", "summary": "Timestamps are UTC",
     "validated": False, "confidence": 0.6, "base_confidence": 0.6, "source": "agent", "supersedes": "a-dec-0",
     "created": "2026-03-01T10:00:00Z"},
    {"uuid": "a-prob-1", "type": "Problem", "name": "Problem: flaky upload", "summary": "Uploads time out",
     "validated": False, "confidence": 0.5, "source": "agent", "created": "2026-03-02T10:00:00Z"},
    {"uuid": "a-dec-4", "type": "Decision", "name": "Decision: pin the parser", "summary": "Pin parser 2.x",
     "validated": True, "confidence": 0.8, "base_confidence": 0.6, "source": "watcher",
     "created": "2026-04-01T10:00:00Z"},
]


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.email=t@t", "-c", "user.name=t", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


def make_repo(path: pathlib.Path) -> pathlib.Path:
    path.mkdir(parents=True)
    subprocess.run(["git", "init", "-q", "-b", "main", str(path)], check=True)
    (path / "api.py").write_text("def send(payload):\n    return payload\n", newline="\n")
    git(path, "add", ".")
    git(path, "commit", "-qm", "base")
    return path


async def seed_legacy(driver, repo_path: str, tag: str):
    for node in LEGACY:
        await driver.execute_query(
            "CREATE (n:Entity {uuid: $uuid, name: $name, summary: $summary, type: $type, repo_path: $repo, "
            "validated: $validated, confidence: $confidence, base_confidence: $base, source: $source, "
            "harness: $harness, supersedes: $supersedes, created_at: datetime($created), group_id: $tag})",
            params={"uuid": f"{tag}-{node['uuid']}", "name": node["name"], "summary": node["summary"],
                    "type": node["type"], "repo": repo_path, "validated": node["validated"],
                    "confidence": node["confidence"], "base": node.get("base_confidence"),
                    "source": node["source"], "harness": node.get("harness"), "supersedes": node.get("supersedes"),
                    "created": node["created"], "tag": tag})
    await driver.execute_query(
        "CREATE (s:Entity {uuid: $s, name: 'send', type: 'Symbol', file: 'api.py', repo_path: $repo, group_id: $tag}) "
        "CREATE (m:Entity {uuid: $m, name: 'api.py', type: 'Module', repo_path: $repo, group_id: $tag}) "
        "CREATE (e:Episodic {uuid: $e, name: 'commit abc', content: 'initial', repo_path: $repo, group_id: $tag}) "
        "WITH s, m, e MATCH (d:Entity {uuid: $d}) "
        "CREATE (d)-[:MOTIVATES {created_at: datetime('2026-01-01T00:00:00Z')}]->(m) "
        "CREATE (e)-[:MENTIONS]->(d) CREATE (s)-[:DEFINED_IN]->(m)",
        params={"s": f"{tag}-sym", "m": f"{tag}-mod", "e": f"{tag}-ep", "d": f"{tag}-a-dec-1", "repo": repo_path,
                "tag": tag})


@pytest_asyncio.fixture(loop_scope="function")
async def graph(tmp_path):
    uri = os.getenv("MEMEX_PHASE1_NEO4J_URI")
    if not uri:
        pytest.skip("isolated native Neo4j required")
    from graphiti_core.driver.neo4j_driver import Neo4jDriver
    driver = await asyncio.to_thread(Neo4jDriver, uri, None, None)
    tag = f"t{uuid.uuid4().hex[:8]}"
    # Legacy data is keyed by repo_path, and pytest reuses temporary paths in a
    # fresh environment (Linux containers restart at pytest-0); a unique path and
    # a cleanup keep one run's legacy nodes out of another's migration.
    repo = make_repo(tmp_path / f"repo-{tag}")
    path = canonical_repo_path(str(repo))
    await seed_legacy(driver, path, tag)
    yield driver, repo, path, tag
    try:
        await driver.execute_query("MATCH (n) WHERE n.group_id = $tag OR n.repo_path = $path OR n.repo_id = $rid "
                                   "DETACH DELETE n",
                                   params={"tag": tag, "path": path, "rid": discover_repository(repo).repo_id})
    finally:
        await driver.close()


async def legacy_count(driver, repo_id):
    result = await driver.execute_query("MATCH (c:MemexLegacyClaim {repo_id: $r}) RETURN count(c) AS n",
                                        params={"r": repo_id})
    return result.records[0]["n"]


@pytest.mark.asyncio
async def test_a_dry_run_reports_counts_and_writes_nothing(graph):
    driver, repo, path, _ = graph
    before = await migration.legacy_snapshot(driver, path)
    labels = await driver.execute_query("MATCH (n) WHERE any(l IN labels(n) WHERE l STARTS WITH 'Memex') "
                                        "RETURN count(n) AS n")
    report = await migration.migrate(driver, path, dry_run=True)
    assert report["inventory"]["importable"] == 5
    assert report["inventory"]["by_kind"]["Decision"] == 4 and report["inventory"]["by_kind"]["Problem"] == 1
    assert report["inventory"]["structural_rebuilt_not_imported"] == 2
    assert report["mapping"]["status"] == "mapped" and report["detect"]["credentials"] == "not recorded"
    after = await driver.execute_query("MATCH (n) WHERE any(l IN labels(n) WHERE l STARTS WITH 'Memex') "
                                       "RETURN count(n) AS n")
    assert after.records[0]["n"] == labels.records[0]["n"]
    assert await migration.legacy_snapshot(driver, path) == before


@pytest.mark.asyncio
async def test_migration_preserves_legacy_data_authority_and_history(graph):
    driver, repo, path, tag = graph
    before = await migration.legacy_snapshot(driver, path)
    report = await migration.migrate(driver, path, batch_size=2)
    registration = discover_repository(repo)
    assert report["status"] == "complete" and report["batches_this_run"] == 3
    assert report["checkpoint"]["completed"] == 5 and "structural_view" in report
    claims = {c["legacy_key"]: c for c in await migration.legacy_claims(driver, registration.repo_id)}
    assert set(claims) == {f"{tag}-{n['uuid']}" for n in LEGACY}
    approved = claims[f"{tag}-a-dec-1"]
    assert approved["authority"] == "human_approved" and approved["legacy_validated"] is True
    assert approved["legacy_confidence"] == 0.95 and approved["legacy_created_at"].startswith("2026-01-01T10:00")
    assert claims[f"{tag}-a-dec-2"]["authority"] == "inferred" and claims[f"{tag}-a-dec-2"]["legacy_harness"] == \
        "claude-code"
    assert claims[f"{tag}-a-dec-3"]["legacy_supersedes"] == "a-dec-0"
    assert all(c["coverage"] == "legacy_unverified" and c["current_context_eligible"] is False
               for c in claims.values())
    assert await migration.legacy_snapshot(driver, path) == before, "a legacy node or edge changed"


@pytest.mark.asyncio
async def test_the_v09_reader_sees_identical_results_after_migration_and_rollback(graph, monkeypatch):
    driver, repo, path, _ = graph
    monkeypatch.setenv("NEO4J_URI", os.environ["MEMEX_PHASE1_NEO4J_URI"])
    monkeypatch.setenv("NEO4J_USER", "neo4j")
    monkeypatch.setenv("NEO4J_PASSWORD", "test-not-used")
    monkeypatch.setenv("GEMINI_API_KEY", "test-not-used")
    from memex.mcp_server.queries import get_recent_decisions_raw

    def normalized(rows):
        return sorted(json.dumps(r, sort_keys=True, default=str) for r in rows)
    before = normalized(await get_recent_decisions_raw(3650, None, 50, repo=path))
    assert len(before) == 4
    await migration.migrate(driver, path)
    migration.rollback(discover_repository(repo))
    assert normalized(await get_recent_decisions_raw(3650, None, 50, repo=path)) == before


@pytest.mark.asyncio
async def test_an_interrupted_migration_resumes_and_a_repeat_writes_nothing(graph):
    driver, repo, path, _ = graph
    registration = discover_repository(repo)
    first = await migration.migrate(driver, path, batch_size=2, max_batches=1, index=False)
    assert first["status"] == "interrupted" and first["checkpoint"]["completed"] == 2
    assert await legacy_count(driver, registration.repo_id) == 2
    second = await migration.migrate(driver, path, batch_size=2, index=False)
    assert second["status"] == "complete" and second["checkpoint"]["completed"] == 5
    state = second["checkpoint"]
    third = await migration.migrate(driver, path, batch_size=2, index=False)
    assert third["status"] == "complete" and third["batches_this_run"] == 0
    assert third["checkpoint"]["completed"] == 5 and third["checkpoint"]["cursor"] == state["cursor"]
    assert await legacy_count(driver, registration.repo_id) == 5


@pytest.mark.asyncio
async def test_a_process_killed_mid_migration_resumes_from_its_checkpoint(graph):
    driver, repo, path, _ = graph
    registration = discover_repository(repo)
    script = (f"import asyncio, os, sys; sys.path.insert(0, {str(pathlib.Path(__file__).parents[1])!r})\n"
              "from graphiti_core.driver.neo4j_driver import Neo4jDriver\n"
              "from memex.runtime import migration\n"
              "async def main():\n"
              f"    d = Neo4jDriver({os.environ['MEMEX_PHASE1_NEO4J_URI']!r}, None, None)\n"
              "    def stop(n, row):\n"
              "        if n == 2: os._exit(9)\n"
              f"    await migration.migrate(d, {path!r}, batch_size=2, index=False, on_batch=stop)\n"
              "asyncio.run(main())\n")
    killed = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=120)
    assert killed.returncode == 9, killed.stderr
    assert await legacy_count(driver, registration.repo_id) == 4, "two whole batches committed, no partial batch"
    resumed = await migration.migrate(driver, path, batch_size=2, index=False)
    assert resumed["status"] == "complete" and resumed["checkpoint"]["completed"] == 5
    assert await legacy_count(driver, registration.repo_id) == 5


@pytest.mark.asyncio
async def test_legacy_knowledge_never_enters_a_verified_packet(graph):
    driver, repo, path, _ = graph
    await migration.migrate(driver, path)
    from memex.context.live import OpenTaskRequest, PacketBudget, SessionIdentity
    from memex.runtime.actions import LiveContextEngine
    from memex.runtime.coordinator import RepositoryIndexer
    from memex.runtime.graph import StructuralGraphStore
    from memex.runtime.supports import ClaimStore
    from memex.runtime.tasks import TaskStore
    registration = discover_repository(repo)
    tasks = TaskStore(registration.runtime_path, authenticate=lambda s: True)
    engine = LiveContextEngine(RepositoryIndexer(registration, StructuralGraphStore(driver)), ClaimStore(driver),
                               tasks, authorize_view=lambda s, r: True, authorize_source=lambda s, p: True)
    result = await engine.indexer.refresh()
    session = SessionIdentity(harness="t", native_session_id="n", memex_session_id="m", principal_id="p")
    frame = await engine.open_task(OpenTaskRequest(session=session, view=result.view, intent="test",
                                                   budget=PacketBudget()))
    assert frame.items == (), "legacy_unverified knowledge was delivered as verified current context"


@pytest.mark.asyncio
async def test_worktrees_share_a_repository_and_clones_do_not(graph, tmp_path):
    driver, repo, path, _ = graph
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "-q", "-b", "side", str(linked))
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "-q", str(repo), str(clone)], check=True)
    main_map = migration.map_repository(path)
    linked_map = migration.map_repository(canonical_repo_path(str(linked)))
    clone_map = migration.map_repository(canonical_repo_path(str(clone)))
    assert linked_map["repo_id"] == main_map["repo_id"] and linked_map["worktree_id"] != main_map["worktree_id"]
    assert linked_map["linked_worktree"] is True
    assert clone_map["repo_id"] != main_map["repo_id"], "an unrelated clone shares no identity with its origin"
    gone = migration.map_repository(str(tmp_path / "missing"))
    assert gone["status"] == "unmapped"
    report = await migration.migrate(driver, str(tmp_path / "missing"))
    assert report["status"] == "skipped_unmapped"


def test_migration_schema_is_additive():
    import inspect
    source = inspect.getsource(migration)
    for statement in ("DETACH DELETE", "REMOVE n.", "DROP CONSTRAINT", "DROP INDEX", "SET n."):
        assert statement not in source, f"migration contains a destructive or legacy-mutating `{statement}`"


# -- modes, rollback and diagnostics --------------------------------------------------- #

@pytest.fixture
def live_repo(tmp_path):
    repo = make_repo(tmp_path / "modes")
    return repo, discover_repository(repo)


def test_modes_switch_and_re_enabling_live_forces_resynchronization(live_repo):
    from memex.runtime.modes import read_mode
    repo, registration = live_repo
    assert read_mode(registration) == "live", "installed hooks without a mode file stay live"
    db = sqlite3.connect(registration.runtime_path)
    db.execute("CREATE TABLE IF NOT EXISTS live_adapter_sessions (harness TEXT, native_session_id TEXT, "
               "memex_session_id TEXT, task_id TEXT, continuation_token TEXT, context_retained INTEGER)")
    db.execute("INSERT INTO live_adapter_sessions VALUES('claude_code','s1','m','t','c',1)")
    db.commit()
    db.close()
    assert migration.set_mode(registration, "shadow")["previous"] == "live"
    assert migration.set_mode(registration, "off")["mode"] == "off"
    switched = migration.set_mode(registration, "live")
    assert switched["resynchronized_sessions"] == 1
    db = sqlite3.connect(registration.runtime_path)
    assert db.execute("SELECT context_retained FROM live_adapter_sessions").fetchone()[0] == 0
    db.close()
    with pytest.raises(ValueError):
        migration.set_mode(registration, "on")


def test_rollback_disables_v1_expires_leases_and_keeps_recovery_state(live_repo):
    from memex.runtime import guard
    from memex.runtime.modes import read_mode
    repo, registration = live_repo
    guard.set_mode(registration, "guarded")
    writer = guard.WriteGuard(registration)
    writer.acquire(writer.holder("agent"), ["api.py"])
    report = migration.rollback(registration)
    assert read_mode(registration) == "off" and not guard.guarded(registration)
    assert report["leases_expired"] == 1
    other = guard.WriteGuard(registration)
    assert other.acquire(other.holder("next"), ["api.py"]), "the expired lease blocks nobody"
    # An interrupted write is different: it is kept for recovery, and keeps holding its targets.
    (writer.intent_dir / "intent-kept.json").write_text("[]")
    again = migration.rollback(registration)
    assert again["unresolved_interrupted_writes_kept"] == 1
    assert (writer.intent_dir / "intent-kept.json").exists()


def test_diagnostics_are_actionable(live_repo):
    from memex.runtime import guard
    from memex.runtime.diagnostics import diagnose, render
    from memex.runtime.journal import ChangeJournal
    from memex.runtime.views import capture_sources
    repo, registration = live_repo
    journal = ChangeJournal(registration.runtime_path)
    journal.observe(registration, capture_sources(registration))  # observed, never indexed
    guard.set_mode(registration, "guarded")
    migration.set_mode(registration, "shadow")
    report = diagnose(registration, graph_ok=True)
    codes = {f["code"] for f in report["findings"]}
    assert {"index_behind", "shadow_mode", "guarded_without_live", "claude_code_not_registered"} <= codes
    text = render(report)
    assert "behind" in text and "not a freshness result" in text


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["off", "shadow", "live"])
async def test_off_and_shadow_deliver_and_deny_nothing(tmp_path, mode):
    """The same affected edit, through the real adapter, in each mode."""
    uri = os.getenv("MEMEX_PHASE1_NEO4J_URI")
    if not uri:
        pytest.skip("isolated native Neo4j required")
    from memex.context.live import DeliveryReceipt
    from memex.evaluation import fixtures
    from memex.integrations.claude_code import ClaudeCodeAdapter, register
    from memex.integrations.host_adapter import build_engine
    from memex.runtime.trace import TraceStore
    history = fixtures.history_by_id("D01")
    made = fixtures.materialize(history, tmp_path)
    repo = pathlib.Path(made["repo"])
    await fixtures.seed_graph(history, repo, uri)
    registration = discover_repository(repo)
    migration.set_mode(registration, mode)
    capability = register(repo, "owner")
    os.environ.setdefault("MEMEX_LIVE_NEO4J_URI", uri)
    engine, driver = await build_engine(registration, capability, ClaudeCodeAdapter.HARNESS)
    try:
        adapter = ClaudeCodeAdapter(engine, capability)
        base = {"session_id": "s1", "cwd": str(repo)}
        start = await adapter.dispatch({**base, "hook_event_name": "SessionStart", "source": "startup"})
        binding = adapter._binding("s1")
        if binding:
            session = adapter.session_identity("s1")
            state = engine.tasks.get(binding["task_id"], session, now=adapter.clock())
            engine.ack_delivery(DeliveryReceipt(session=session, task_id=state.task_id,
                                                sequence=state.pending.sequence, view_id=state.pending.view_id,
                                                adapter_version="t", accepted_at=1.0, outcome="host_accepted"))
        fixtures.apply_change(repo, made["plan"], uri=uri)
        edit = await adapter.dispatch({**base, "hook_event_name": "PreToolUse", "tool_name": "Edit",
                                       "tool_use_id": "toolu_1",
                                       "tool_input": {"file_path": str(repo / "api.py"), "old_string": "raise",
                                                      "new_string": "pass"}})
    finally:
        await driver.close()
    if mode == "live":
        assert start.additional_context and edit.decision == "deny"
        return
    assert not start.additional_context and edit.decision is None, f"{mode} delivered or denied"
    events = TraceStore(registration.runtime_path).events() if pathlib.Path(registration.runtime_path).exists() else []
    shadows = [e for e in events if e["event"] == "shadow"]
    if mode == "shadow":
        assert {e["gate"] for e in shadows} == {"shadow_would_deliver", "shadow_would_prevent"}
        assert not [e for e in events if e["event"] == "delivery" and e["insertion"] == "emitted"]
    else:
        assert not shadows


def test_doctor_on_a_fresh_install_says_what_to_do_next(tmp_path):
    from memex.cli import main
    repo = make_repo(tmp_path / "fresh")
    from memex.runtime.diagnostics import diagnose, render
    registration = discover_repository(repo)
    report = diagnose(registration)
    assert report["capabilities"]["codex"]["registered"] is False
    assert "not a freshness result" in render(report)
    with pytest.raises(SystemExit) as exit_info:
        main(["v1", "install", "claude", "--repo", str(repo)])
    assert exit_info.value.code == 0
    assert diagnose(registration)["capabilities"]["claude_code"]["hooks_installed"] is True
