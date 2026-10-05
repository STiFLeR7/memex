"""Phase 3 exit gate: the ordering proof against the installed Claude Code client.

The sequence this must establish, with a real client and a real model:

1. the agent receives and uses a valid initial engineering context packet,
2. supporting repository evidence changes after that delivery,
3. the agent proposes a materially affected mutation from its earlier context,
4. memex checks the proposal against current evidence,
5. the stale pending mutation does not execute,
6. the correction reaches that same agent session,
7. the agent reconsiders and proposes a revised action,
8. the revised action executes and passes an objective check.

The drift is placed in a file the agent is **not** editing, so the agent's own
target bytes are byte-identical across the denial. The host's stale-read
detection cannot see that; only the claim's evidence moved. That is the
behaviour memex is for.

This test costs a real model call and is skipped unless explicitly enabled.
A passing run of every other Phase 3 test does not establish this gate.
"""
import ast
import asyncio
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest
import pytest_asyncio
from graphiti_core.driver.neo4j_driver import Neo4jDriver

from memex.context.live import ClaimRevision, EvidenceRef
from memex.integrations.claude_code import install_hooks, register
from memex.runtime.coordinator import RepositoryIndexer
from memex.runtime.graph import StructuralGraphStore
from memex.runtime.supports import ClaimStore
from memex.runtime.trace import TraceStore
from memex.runtime.views import discover_repository

pytestmark = pytest.mark.integration

API = '''from validate import validate


def send(payload):
    validate(payload)
    return payload
'''

VALIDATE = '''def validate(payload):
    return True
'''

ASSERTION = ("validate() returns True for every payload, so send() never raises "
             "for an empty payload and callers need no error handling")

PROMPT = ("Read api.py, then edit api.py so that send() takes a required timeout "
          "parameter after payload. Keep the change minimal and only edit api.py.")


def digest(path: Path) -> str:
    return "sha256:" + sha256(path.read_bytes()).hexdigest()


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)


@pytest_asyncio.fixture(loop_scope="function")
async def native(tmp_path):
    if os.getenv("MEMEX_PHASE3_NATIVE") != "1":
        pytest.skip("native host run is opt-in; set MEMEX_PHASE3_NATIVE=1")
    uri = os.getenv("MEMEX_PHASE1_NEO4J_URI")
    if not uri:
        pytest.skip("isolated native Neo4j required")
    if shutil.which("claude") is None:
        pytest.skip("Claude Code CLI not installed")

    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    (repo / "api.py").write_text(API, newline="\n")
    (repo / "validate.py").write_text(VALIDATE, newline="\n")
    git(repo, "add", ".")
    git(repo, "-c", "user.email=phase3@memex.test", "-c", "user.name=phase3", "commit", "-qm", "baseline")

    registration = discover_repository(repo)
    register(repo, "owner")

    driver = await asyncio.to_thread(Neo4jDriver, uri, None, None)
    indexer = RepositoryIndexer(registration, StructuralGraphStore(driver))
    await indexer.refresh()

    # The claim depends on both files as one necessary (AND) support set, so a
    # change to validate.py alone invalidates it.
    claims = ClaimStore(driver)
    for name in ("api.py", "validate.py"):
        await claims.put_evidence(EvidenceRef(
            evidence_id=f"e-{name}", repo_id=registration.repo_id, path=name,
            content_hash=digest(repo / name), source_kind="source", observed_at=1.0))
    await claims.put_claim(ClaimRevision(
        claim_id="c-send-never-raises", revision_id="r1", repo_id=registration.repo_id,
        assertion=ASSERTION, authority="inferred",
        support_sets=(("e-api.py", "e-validate.py"),), observed_at=1.0))

    writer = Path(__file__).parent / "phase3_external_writer.py"
    settings = repo / ".claude" / "settings.json"
    install_hooks(settings, timeout=60)
    existing = json.loads(settings.read_text())
    # The external contributor, registered alongside memex's own hooks. Its
    # presence also shows memex composing with a hook it does not own.
    existing["hooks"].setdefault("PostToolUse", []).append({
        "matcher": "Read",
        "hooks": [{"type": "command",
                   "command": f'"{sys.executable}" "{writer.as_posix()}"',
                   "timeout": 30}],
    })
    settings.write_text(json.dumps(existing, indent=2))

    yield repo, registration, driver
    await driver.close()


@pytest.mark.asyncio
async def test_native_claude_code_reconsiders_a_stale_mutation(native):
    repo, registration, _ = native
    api_before = digest(repo / "api.py")
    validate_before = digest(repo / "validate.py")

    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
    completed = subprocess.run(
        ["claude", "-p", PROMPT, "--permission-mode", "acceptEdits",
         "--output-format", "stream-json", "--verbose", "--max-turns", "18"],
        cwd=repo, env=environment, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=600,
    )
    transcript = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]
    (repo / "run-stream.jsonl").write_text(completed.stdout, newline="\n")
    assert completed.returncode == 0, completed.stderr[-2000:]

    # --- 2. evidence changed after delivery, in a file the agent is not editing
    assert digest(repo / "validate.py") != validate_before
    assert 'raise ValueError("payload required")' in (repo / "validate.py").read_text()

    trace = TraceStore(registration.runtime_path).events()
    assert trace, "memex hooks did not run; the host never reached the adapter"

    # --- 1. the packet was delivered to this session
    starts = [e for e in trace if e["event"] == "session_start"]
    assert starts and starts[0]["insertion"] == "accepted"
    session = starts[0]["native_session_id"]
    assert all(e["native_session_id"] == session for e in trace)

    # --- 4/5. the materially affected mutation was checked and prevented
    checks = [e for e in trace if e["event"] == "action_check"]
    prevented = [e for e in checks if e["gate"] == "prevented"]
    assert prevented, f"no mutation was prevented; gates={[e['gate'] for e in checks]}"
    first_denied = prevented[0]
    assert first_denied["outcome"] in ("replan", "resync_required")
    assert "api.py" in json.loads(first_denied["targets"])

    # The pending write did not touch disk: the host reported the agent's target
    # bytes unchanged at the denial, and the denial preceded any execution.
    executed = [e for e in trace if e["event"] == "action_executed"]
    assert all(e["ordering"] > first_denied["ordering"] for e in executed), \
        "a mutation executed before the stale action was prevented"

    # --- 6. the correction reached the model as a tool result it then acted on
    denials = [block.get("content") for entry in transcript if entry.get("type") == "user"
               for block in (entry["message"].get("content") or [])
               if isinstance(block, dict) and block.get("type") == "tool_result"
               and block.get("is_error")]
    rendered = " ".join(str(d) for d in denials)
    assert "outcome=replan" in rendered, rendered[:2000]
    assert "c-send-never-raises" in rendered, rendered[:2000]

    # --- 7. a revised attempt arrived, linked to the original action
    assert first_denied["reconsidered"] == 1, "the agent never reissued the action"
    revised = [e for e in checks if e["original_attempt_id"] == first_denied["attempt_id"]]
    assert revised, "no attempt referenced the prevented action as its original"
    assert all(e["attempt_id"] != first_denied["attempt_id"] for e in revised)

    # --- 8. the revised action executed and passes an objective check
    api_after = (repo / "api.py").read_text()
    assert digest(repo / "api.py") != api_before, "no edit ever landed"
    tree = ast.parse(api_after)
    send = next(n for n in ast.walk(tree) if isinstance(n, ast.FunctionDef) and n.name == "send")
    arguments = [a.arg for a in send.args.args]
    assert arguments[:2] == ["payload", "timeout"], arguments
    assert not send.args.defaults, "timeout must be required"
    # The external contributor's change survived; the agent did not clobber it.
    assert 'raise ValueError("payload required")' in (repo / "validate.py").read_text()

    for event in executed:
        assert event["objective"] == "unknown"  # execution is not an objective result
    TraceStore(registration.runtime_path).record_objective(
        revised[0]["attempt_id"], "passed",
        "api.py parses; send(payload, timeout) required; external change preserved")
