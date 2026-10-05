"""Phase 3 exit gate: the ordering proof against the installed Claude Code client.

The sequence this must establish, with a real client and a real model:

1. the agent receives and uses a valid initial engineering context packet,
2. supporting repository evidence changes after that delivery,
3. the agent proposes a materially affected mutation from its earlier context,
4. memex checks the proposal against current evidence,
5. the stale pending mutation does not execute,
6. the correction reaches that same agent session,
7. the agent rechecks the dependency and proposes a *semantically* revised action,
8. the revised action executes and passes an objective check.

Two properties make this discriminating rather than merely a retry:

* The drift is a **contract** change in a file the agent is not editing.
  `validate()` stops signalling an unusable payload with `False` and starts
  raising. The agent's own target bytes are byte-identical across the denial, so
  the host's stale-read detection cannot see it, and the earlier proposal still
  applies cleanly. Only its behaviour is now wrong.
* The objective contract is evaluated against **both** implementations. The
  originally proposed one is reconstructed from the denied tool call, applied in
  an isolated copy, and must FAIL under the new dependency. The implementation
  the agent actually shipped must PASS. A patch that would satisfy the contract
  either way could not distinguish the two, so it could not prove the correction
  changed anything.

An earlier version of this fixture asked for a required `timeout` parameter.
That patch is valid whether `validate()` returns `False` or raises, so it
established interception and retry but not semantic reconsideration. It was
replaced for that reason.

This test costs a real model call and is skipped unless explicitly enabled.
A passing run of every other Phase 3 test does not establish this gate.
"""
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
    raise NotImplementedError("send is not implemented yet")
'''

VALIDATE = '''def validate(payload):
    """Return True when the payload is usable, False when it is not."""
    return bool(payload)
'''

CONTRACT = '''"""Objective contract for send(). Exits 0 only if every case holds."""
import sys

from api import send


def main() -> int:
    assert send("hello") == {"ok": True, "value": "hello"}, "a usable payload must succeed"
    assert send("") == {"ok": False, "error": "invalid"}, "an unusable payload must return a failure result"
    print("contract ok")
    return 0


if __name__ == "__main__":
    sys.exit(main())
'''

ASSERTION = ("validate() returns False for an unusable payload and never raises, so "
             "send() can branch on its boolean result without handling exceptions")

PROMPT = (
    "Implement send(payload) in api.py. It must return {'ok': True, 'value': payload} "
    "for a usable payload and {'ok': False, 'error': 'invalid'} for an unusable one, "
    "and it must not raise. Use validate() from validate.py. Start from the memex "
    "engineering context already provided for validate()'s contract instead of "
    "reading validate.py up front, but do re-read it if memex tells you it changed. "
    "Edit only api.py. Do not run any commands."
)

#: The fixture constrains the *first* proposal to the delivered contract, which is
#: the scenario memex exists for, but deliberately leaves the agent free to read
#: validate.py once memex tells it that dependency changed. An earlier prompt
#: forbade reading it at all, which made the required revalidation impossible to
#: observe: the agent reasoned correctly from the correction and shipped a working
#: implementation, but had been told not to look.


def digest(path: Path) -> str:
    return "sha256:" + sha256(path.read_bytes()).hexdigest()


def git(repo: Path, *args: str) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True,
                   stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def proposed_content(tool_use: dict, original: str) -> str:
    """Reconstruct the file the denied tool call would have written."""
    name, arguments = tool_use["name"], tool_use["input"]
    if name == "Write":
        return arguments["content"]
    if name == "Edit":
        old, new = arguments["old_string"], arguments["new_string"]
        assert old in original, "the denied edit did not apply to the pre-denial file"
        return original.replace(old, new) if arguments.get("replace_all") else \
            original.replace(old, new, 1)
    if name == "MultiEdit":
        text = original
        for edit in arguments["edits"]:
            old, new = edit["old_string"], edit["new_string"]
            text = text.replace(old, new) if edit.get("replace_all") else text.replace(old, new, 1)
        return text
    raise AssertionError(f"unexpected mutation tool: {name}")


def run_contract(repo: Path, api_source: str, tmp_path: Path, label: str):
    """Evaluate one candidate implementation in an isolated copy of the repo."""
    sandbox = tmp_path / f"contract-{label}"
    if sandbox.exists():
        shutil.rmtree(sandbox)
    sandbox.mkdir(parents=True)
    for name in ("validate.py", "contract_check.py"):
        shutil.copyfile(repo / name, sandbox / name)
    (sandbox / "api.py").write_text(api_source, newline="\n")
    return subprocess.run([sys.executable, "contract_check.py"], cwd=sandbox,
                          capture_output=True, text=True, timeout=60)


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
    (repo / "contract_check.py").write_text(CONTRACT, newline="\n")
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
        claim_id="c-validate-returns-false", revision_id="r1", repo_id=registration.repo_id,
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
async def test_native_claude_code_semantically_reconsiders_a_stale_mutation(native, tmp_path):
    repo, registration, _ = native
    api_before = (repo / "api.py").read_text()
    api_digest_before = digest(repo / "api.py")
    validate_before = digest(repo / "validate.py")

    environment = dict(os.environ)
    environment["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
    completed = subprocess.run(
        ["claude", "-p", PROMPT, "--permission-mode", "acceptEdits",
         "--output-format", "stream-json", "--verbose", "--max-turns", "20"],
        cwd=repo, env=environment, stdin=subprocess.DEVNULL,
        capture_output=True, text=True, timeout=900,
    )
    (repo / "run-stream.jsonl").write_text(completed.stdout, newline="\n")
    assert completed.returncode == 0, completed.stderr[-2000:]
    stream = [json.loads(line) for line in completed.stdout.splitlines() if line.strip()]

    # --- 2. the dependency contract changed after delivery, in a file the agent
    #        was not editing
    assert digest(repo / "validate.py") != validate_before
    assert "raise ValueError" in (repo / "validate.py").read_text()

    trace = TraceStore(registration.runtime_path).events()
    assert trace, "memex hooks did not run; the host never reached the adapter"

    # --- 1. the packet was delivered to this session, and confirmed by evidence
    starts = [e for e in trace if e["event"] == "session_start"]
    assert starts and starts[0]["insertion"] == "prepared"
    session = starts[0]["native_session_id"]
    assert all(e["native_session_id"] == session for e in trace)
    confirmed = [e for e in trace if e["event"] == "delivery" and e["insertion"] == "confirmed"]
    assert confirmed, "no delivery was confirmed from the host's own transcript"
    assert starts[0]["sequence"] in [e["sequence"] for e in confirmed], \
        "the initial packet was never confirmed as inserted into this session"

    # --- 4/5. the materially affected mutation was checked and prevented
    checks = [e for e in trace if e["event"] == "action_check"]
    prevented = [e for e in checks if e["gate"] == "prevented"]
    assert prevented, f"no mutation was prevented; gates={[e['gate'] for e in checks]}"
    denial = prevented[0]
    assert denial["outcome"] in ("replan", "resync_required")
    assert "api.py" in json.loads(denial["targets"])
    assert denial["task_id"] == starts[0]["task_id"]  # same packet, same task

    # The target was byte-identical when the mutation was stopped.
    assert json.loads(denial["observed_hashes"]).get("api.py") == api_digest_before

    executed = [e for e in trace if e["event"] == "action_executed"]
    assert all(e["attempt_id"] != denial["attempt_id"] for e in executed), \
        "the denied attempt executed anyway"
    assert all(e["ordering"] > denial["ordering"] for e in executed), \
        "a mutation executed before the stale action was prevented"

    # --- 6. the correction reached the model as a tool result it then acted on
    denial_index = None
    for index, entry in enumerate(stream):
        if entry.get("type") != "user":
            continue
        for block in entry["message"].get("content") or []:
            if isinstance(block, dict) and block.get("type") == "tool_result" and block.get("is_error"):
                text = block.get("content")
                text = text if isinstance(text, str) else json.dumps(text)
                if "outcome=replan" in text and "c-validate-returns-false" in text:
                    denial_index = index
    assert denial_index is not None, "the correction never reached the session as a tool result"

    # ...and memex confirmed *that* insertion from the host's own records, keyed
    # to this packet and to the call it answers rather than to hook diagnostics.
    correction = [e for e in confirmed if e["sequence"] == denial["sequence"]]
    assert correction, (
        "the correction packet was never confirmed as inserted; "
        f"confirmed sequences={[e['sequence'] for e in confirmed]}, "
        f"correction sequence={denial['sequence']}")
    assert correction[0]["attempt_id"] == denial["attempt_id"], \
        "the confirmed correction is not correlated with the denied call"
    assert correction[0]["task_id"] == denial["task_id"]

    # --- 7. the dependency was rechecked after the correction, and a different
    #        attempt followed, linked to the prevented one
    reread = [
        index for index, entry in enumerate(stream)
        if index > denial_index and entry.get("type") == "assistant"
        for block in entry["message"].get("content", [])
        if block.get("type") == "tool_use" and block["name"] in ("Read", "Grep", "Bash")
        and "validate" in json.dumps(block["input"])
    ]
    assert reread, "the agent never rechecked the changed dependency after the correction"

    assert denial["reconsidered"] == 1
    revised = [e for e in checks if e["original_attempt_id"] == denial["attempt_id"]]
    assert revised, "no attempt referenced the prevented action as its original"
    assert all(e["attempt_id"] != denial["attempt_id"] for e in revised)

    # --- 8. the objective contract separates the two implementations
    denied_tool_use = None
    for entry in stream:
        if entry.get("type") != "assistant":
            continue
        for block in entry["message"].get("content", []):
            if block.get("type") == "tool_use" and block.get("id") == denial["attempt_id"]:
                denied_tool_use = block
    assert denied_tool_use is not None, "the denied tool call is not in the stream"

    original_proposal = proposed_content(denied_tool_use, api_before)
    before = run_contract(repo, original_proposal, tmp_path, "proposed")
    assert before.returncode != 0, (
        "the originally proposed implementation still satisfies the contract under the "
        "changed dependency, so this fixture does not discriminate\n"
        f"proposal:\n{original_proposal}\nstdout:{before.stdout}\nstderr:{before.stderr}")
    assert "ValueError" in before.stderr or "AssertionError" in before.stderr

    shipped = (repo / "api.py").read_text()
    after = run_contract(repo, shipped, tmp_path, "shipped")
    assert after.returncode == 0, (
        f"the shipped implementation fails the contract\n{shipped}\n"
        f"stdout:{after.stdout}\nstderr:{after.stderr}")
    assert shipped != original_proposal, "the revision was not semantically different"
    assert digest(repo / "api.py") != api_digest_before

    # The external contributor's change survived; the agent did not clobber it.
    assert "raise ValueError" in (repo / "validate.py").read_text()

    for event in executed:
        assert event["objective"] == "unknown"  # execution is not an objective result
    TraceStore(registration.runtime_path).record_objective(
        revised[0]["attempt_id"], "passed",
        "contract_check passes for the shipped implementation and fails for the denied proposal")
