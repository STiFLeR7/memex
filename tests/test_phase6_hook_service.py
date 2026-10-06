"""Phase 6 latency work: cheaper capture, parse and discovery caches, and the hook service."""
import asyncio
import json
import os
import pathlib
import subprocess
import sys
import time

import pytest

from memex.integrations import claude_code
from memex.runtime import parsing, views

CLIENT = pathlib.Path(claude_code.__file__).resolve().parent.parent / "hook_client.py"


def git(repo, *args):
    return subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c", "user.email=t@x", *args],
                          check=True, capture_output=True, text=True).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", "-b", "main", str(root)], check=True)
    (root / "a.py").write_text("def a():\n    return 1\n")
    return root


def head(root):
    return views._capture_once(views.discover_repository(root)).head_commit


def test_head_read_from_files_matches_git_in_every_ref_layout(repo, tmp_path):
    assert head(repo) is None                                     # unborn branch: Git says no HEAD
    git(repo, "add", ".")
    git(repo, "commit", "-qm", "one")
    assert head(repo) == git(repo, "rev-parse", "HEAD")           # loose ref
    git(repo, "pack-refs", "--all")
    assert head(repo) == git(repo, "rev-parse", "HEAD")           # packed ref
    git(repo, "commit", "-q", "--allow-empty", "-m", "two")
    git(repo, "checkout", "-q", "--detach")
    assert head(repo) == git(repo, "rev-parse", "HEAD")           # detached
    linked = tmp_path / "linked"
    git(repo, "worktree", "add", "-q", "-b", "side", str(linked))
    git(linked, "commit", "-q", "--allow-empty", "-m", "three")
    assert head(linked) == git(linked, "rev-parse", "HEAD") != git(repo, "rev-parse", "HEAD")


def test_a_single_listing_capture_is_rechecked_by_a_fresh_listing(repo):
    registration = views.discover_repository(repo)
    capture = views.capture_sources(registration, relist=False)
    assert set(capture.files) == {"a.py"} and views.unchanged_since(registration, capture)
    (repo / "new.py").write_text("x = 1\n")                       # a file the first listing never saw
    assert not views.unchanged_since(registration, capture)


def test_discovery_cache_is_dropped_when_the_identity_files_change(repo, monkeypatch):
    monkeypatch.setattr(views, "DISCOVERY_CACHE", {})
    first = views.discover_repository(repo)
    assert views.discover_repository(repo) is first
    (pathlib.Path(first.git_dir) / "memex" / "worktree-id").unlink()
    again = views.discover_repository(repo)
    assert again.worktree_id != first.worktree_id


def test_parsing_skips_content_it_has_already_parsed(monkeypatch):
    calls = []
    real = parsing._parse_in_pool

    async def counted(files):
        calls.append(sorted(files))
        return await real(files)
    monkeypatch.setattr(parsing, "_parse_in_pool", counted)
    monkeypatch.setattr(parsing, "_parsed", {})

    async def run():
        first = await parsing.parse_sources({"m.py": b"def f():\n    pass\n", "n.txt": b"x"})
        second = await parsing.parse_sources({"m.py": b"def f():\n    pass\n", "n.txt": b"y"})
        return first, second
    first, second = asyncio.run(run())
    assert calls == [["m.py", "n.txt"], ["n.txt"]]
    assert [s.path for s in second] == ["m.py", "n.txt"] and second[0] is first[0]


def test_install_replaces_the_one_shot_hook_and_uninstall_removes_both(tmp_path):
    settings = tmp_path / "settings.json"
    legacy = claude_code.legacy_hook_command("py.exe")
    settings.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": legacy}]}]}}))
    installed = claude_code.install_hooks(settings, python_executable="py.exe")
    commands = [h["command"] for g in installed["hooks"]["SessionStart"] for h in g["hooks"]]
    assert commands == [claude_code.hook_command("py.exe")] and "hook_client.py" in commands[0]
    settings.write_text(json.dumps({"hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": legacy}]}]}}))
    assert "hooks" not in claude_code.uninstall_hooks(settings, python_executable="py.exe")


@pytest.mark.integration
def test_the_client_falls_back_once_then_the_service_answers(tmp_path):
    uri = os.getenv("MEMEX_PHASE1_NEO4J_URI")
    if not uri:
        pytest.skip("isolated native Neo4j required")
    root = tmp_path / "repo"
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    (root / "api.py").write_text("def send(payload):\n    return payload\n")
    claude_code.register(root, "owner")
    home = tmp_path / "home"
    env = {**os.environ, "HOME": str(home), "USERPROFILE": str(home), "MEMEX_LIVE_NEO4J_URI": uri,
           "MEMEX_HOOKD_IDLE_SECONDS": "20", "PYTHONPATH": str(CLIENT.parent.parent)}
    event = {"hook_event_name": "SessionStart", "session_id": "s1", "cwd": str(root), "source": "startup"}

    def call(payload):
        out = subprocess.run([sys.executable, "-I", "-S", str(CLIENT), "memex.integrations.claude_code"],
                             input=json.dumps(payload).encode(), capture_output=True, env=env, timeout=120)
        return json.loads(out.stdout)

    first = call(event)                                            # no service yet: one-shot
    assert "memex engineering context" in first["hookSpecificOutput"]["additionalContext"]
    services = home / ".memex" / "hookd"
    deadline = time.time() + 60
    while time.time() < deadline and not list(services.glob("*.json")):
        time.sleep(0.5)
    [address] = list(services.glob("*.json"))
    pid = json.loads(address.read_text())["pid"]
    second = call({**event, "session_id": "s2"})                   # answered by the service
    assert "memex engineering context" in second["hookSpecificOutput"]["additionalContext"]
    from memex.runtime.trace import TraceStore
    starts = [e for e in TraceStore(views.discover_repository(root).runtime_path).events()
              if e["event"] == "session_start"]
    assert len(starts) == 2
    # A request without the service's token gets no answer, and the client then fails open.
    address.write_text(json.dumps({**json.loads(address.read_text()), "token": "wrong"}))
    refused = call({**event, "session_id": "s3"})
    assert refused["systemMessage"].startswith("memex is unavailable")
    address.write_text(json.dumps({**json.loads(address.read_text()), "pid": -1}))  # hand over, so it exits
    deadline = time.time() + 120
    while time.time() < deadline and _alive(pid):
        time.sleep(1)
    assert not _alive(pid)


def _alive(pid: int) -> bool:
    if os.name == "nt":
        out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/NH"], capture_output=True, text=True).stdout
        return str(pid) in out
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False
