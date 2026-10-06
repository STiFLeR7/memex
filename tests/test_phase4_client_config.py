"""Client-configuration cleanup never loses a concurrent change.

The defect this pins: cleanup read config.toml, prepared the removal of this
run's fixture trust entries, then wrote that earlier content back, so a
setting another client changed in between was lost. Every case runs on
temporary configuration files. A concurrent client is a real thread doing
its own unsynchronized read-modify-write, started at a chosen point inside
cleanup: before cleanup takes exclusive access, or while it holds it.
"""
import json
import os
import pathlib
import subprocess
import sys
import threading
import time
import tomllib

import pytest

from tests import phase4_client_config as cfg

# The cleanup writes only under Windows mandatory file exclusion; elsewhere it
# refuses and reports "pending" (see test_without_mandatory_exclusion_nothing_is_written).
WINDOWS_ONLY = pytest.mark.skipif(os.name != "nt", reason="cleanup writes only under Windows mandatory file exclusion")

PENDING = 3


class World:
    def __init__(self, tmp_path, newline=b"\n"):
        self.base = tmp_path / "run"
        self.base.mkdir()
        self.codex = tmp_path / "codex" / "config.toml"
        self.codex.parent.mkdir()
        self.claude = tmp_path / ".claude.json"
        self.snap = tmp_path / "snapshot.json"
        self.nl = newline
        self.mine = [str(self.base / f"t{i}" / "repo").lower() for i in range(2)]
        self.write_codex(b'model = "old"\napproval_policy = "on-request"\n\n'
                         b"[projects.'c:\\existing\\one']\ntrust_level = \"trusted\"\n\n"
                         b'[mcp_servers.tool]\ncommand = "tool"\n')
        self.claude.write_bytes(json.dumps({"theme": "old", "projects": {"C:/existing/one": {"allowedTools": []}}},
                                           indent=2).encode())

    def write_codex(self, text: bytes):
        self.codex.write_bytes(text.replace(b"\n", self.nl))

    def bind(self, monkeypatch):
        monkeypatch.setattr(cfg, "CODEX_CONFIG", self.codex)
        monkeypatch.setattr(cfg, "CLAUDE_STATE", self.claude)
        monkeypatch.setattr(cfg, "WATCHED", {})

    def run_adds_entries(self, foreign=True):
        """What a native run leaves: one trust block per fixture directory, plus a
        project the user's own client created meanwhile, which is not this run's."""
        blocks = b"".join(b"\n[projects.'" + p.encode() + b"']\ntrust_level = \"trusted\"\n" for p in self.mine)
        if foreign:
            blocks += b"\n[projects.'c:\\elsewhere\\new']\ntrust_level = \"trusted\"\n"
        self.codex.write_bytes(self.codex.read_bytes() + blocks.replace(b"\n", self.nl))
        data = json.loads(self.claude.read_bytes())
        for p in self.mine:
            data["projects"][p.replace("\\", "/")] = {"allowedTools": []}
        if foreign:
            data["projects"]["C:/elsewhere/new"] = {"allowedTools": []}
        self.claude.write_bytes(json.dumps(data, indent=2).encode())

    def cleanup(self):
        try:
            return cfg.cleanup(self.snap, str(self.base), True)
        except SystemExit as exc:  # the unfixed helper refuses by exiting
            return exc.code

    def codex_projects(self):
        return set(tomllib.loads(self.codex.read_bytes().decode()).get("projects", {}))

    def claude_projects(self):
        return set(json.loads(self.claude.read_bytes())["projects"])


@pytest.fixture
def world(tmp_path, monkeypatch):
    w = World(tmp_path)
    w.bind(monkeypatch)
    cfg.snapshot(w.snap)
    return w


class Client(threading.Thread):
    """Another client's own read-modify-write, retried until the file can be opened."""

    def __init__(self, path, edit):
        super().__init__(daemon=True)
        self.path, self.edit = path, edit
        self.attempted, self.blocked, self.done = threading.Event(), 0, False

    def run(self):
        deadline = time.time() + 20
        while time.time() < deadline:
            try:
                with open(self.path, "r+b") as stream:
                    data = self.edit(stream.read())
                    stream.seek(0)
                    stream.write(data)
                    stream.truncate()
                self.done = True
                self.attempted.set()
                return
            except PermissionError:
                self.blocked += 1
                self.attempted.set()
                time.sleep(0.01)


def set_model(raw: bytes) -> bytes:
    assert b'model = "old"' in raw
    return raw.replace(b'model = "old"', b'model = "new"')


def set_theme(raw: bytes) -> bytes:
    data = json.loads(raw)
    data["theme"] = "new"
    return json.dumps(data, indent=2).encode()


def inject(monkeypatch, name, call, client):
    """Start `client` at the `call`-th call of cfg.<name>, i.e. inside cleanup."""
    original, count = getattr(cfg, name), [0]

    def wrapped(*args, **kwargs):
        count[0] += 1
        if count[0] == call:
            client.start()
            assert client.attempted.wait(10)
        return original(*args, **kwargs)
    monkeypatch.setattr(cfg, name, wrapped)


def json_loads_hook(monkeypatch, call, client):
    """The unfixed helper has no named JSON step; hook the module's JSON parsing of file bytes."""
    real, count = json, [0]

    class Shim:
        dumps = staticmethod(real.dumps)
        JSONDecodeError = real.JSONDecodeError

        @staticmethod
        def loads(raw, *args, **kwargs):
            if isinstance(raw, (bytes, bytearray)):
                count[0] += 1
                if count[0] == call:
                    client.start()
                    assert client.attempted.wait(10)
            return real.loads(raw, *args, **kwargs)
    monkeypatch.setattr(cfg, "json", Shim)


# --------------------------------------------------------------------------- #

@WINDOWS_ONLY
@pytest.mark.parametrize("call", [1, 2], ids=["before-exclusive-access", "during-exclusive-access"])
def test_a_concurrent_codex_setting_change_survives_cleanup(world, monkeypatch, call):
    world.run_adds_entries()
    client = Client(world.codex, set_model)
    inject(monkeypatch, "strip_codex", call, client)
    world.cleanup()
    assert client.ident is not None, "cleanup never reached the point under test"
    client.join(30)
    assert client.done, "the concurrent edit never got through"
    raw = world.codex.read_bytes()
    assert b'model = "new"' in raw, "a concurrent client's setting was lost"
    parsed = tomllib.loads(raw.decode())
    assert parsed["approval_policy"] == "on-request" and parsed["mcp_servers"]["tool"]["command"] == "tool"
    assert "c:\\existing\\one" in parsed["projects"] and "c:\\elsewhere\\new" in parsed["projects"]
    if call == 2:
        assert client.blocked, "while cleanup held the file, the other writer had to wait"


@WINDOWS_ONLY
@pytest.mark.parametrize("call", [1, 2], ids=["before-exclusive-access", "during-exclusive-access"])
def test_a_concurrent_claude_setting_change_survives_cleanup(world, monkeypatch, call):
    world.run_adds_entries()
    client = Client(world.claude, set_theme)
    json_loads_hook(monkeypatch, call, client)
    world.cleanup()
    assert client.ident is not None, "cleanup never reached the point under test"
    client.join(30)
    assert client.done
    data = json.loads(world.claude.read_bytes())
    assert data["theme"] == "new", "a concurrent client's setting was lost"
    assert {"C:/existing/one", "C:/elsewhere/new"} <= set(data["projects"])


@WINDOWS_ONLY
@pytest.mark.parametrize("newline", [b"\n", b"\r\n"], ids=["lf", "crlf"])
def test_only_this_runs_new_entries_go_and_every_other_byte_stays(tmp_path, monkeypatch, newline):
    w = World(tmp_path, newline)
    w.bind(monkeypatch)
    cfg.snapshot(w.snap)
    before = w.codex.read_bytes()
    w.run_adds_entries()
    assert w.cleanup() in (0, None)
    foreign = b"\n[projects.'c:\\elsewhere\\new']\ntrust_level = \"trusted\"\n".replace(b"\n", newline)
    assert w.codex.read_bytes() == before + foreign, "only this run's blocks were removed, byte for byte"
    assert w.claude_projects() == {"C:/existing/one", "C:/elsewhere/new"}
    assert json.loads(w.claude.read_bytes())["theme"] == "old"


@WINDOWS_ONLY
def test_repeated_cleanup_changes_nothing_more(world):
    world.run_adds_entries()
    world.cleanup()
    codex, claude = world.codex.read_bytes(), world.claude.read_bytes()
    stamps = (world.codex.stat().st_mtime_ns, world.claude.stat().st_mtime_ns)
    for _ in range(2):
        assert world.cleanup() in (0, None)
    assert (world.codex.read_bytes(), world.claude.read_bytes()) == (codex, claude)
    assert (world.codex.stat().st_mtime_ns, world.claude.stat().st_mtime_ns) == stamps, "nothing was rewritten"


@pytest.mark.skipif(os.name == "nt", reason="Windows has mandatory exclusion; covered by the tests above")
def test_without_mandatory_exclusion_nothing_is_written(world):
    world.run_adds_entries()
    codex, claude = world.codex.read_bytes(), world.claude.read_bytes()
    assert world.cleanup() == PENDING
    assert (world.codex.read_bytes(), world.claude.read_bytes()) == (codex, claude)


def test_unsupported_formatting_and_unexpected_contents_are_left_alone(world):
    odd, extra = world.mine
    world.codex.write_bytes(world.codex.read_bytes()
                            + b'\n[projects."' + odd.replace("\\", "\\\\").encode() + b'"]\ntrust_level = "trusted"\n'
                            + b"\n[projects.'" + extra.encode() + b"']\ntrust_level = \"trusted\"\nnote = \"kept\"\n")
    data = json.loads(world.claude.read_bytes())
    data["projects"][odd.replace("\\", "/")] = {"allowedTools": []}
    world.claude.write_bytes(json.dumps(data, indent=4).encode())  # not the client's own layout
    codex, claude = world.codex.read_bytes(), world.claude.read_bytes()
    assert world.cleanup() == PENDING, "what cannot be removed safely is reported as pending"
    assert world.codex.read_bytes() == codex and world.claude.read_bytes() == claude


def test_a_file_another_process_holds_is_left_unchanged_and_pending(world, monkeypatch):
    world.run_adds_entries(foreign=False)
    monkeypatch.setattr(cfg, "EXCLUSIVE_TIMEOUT", 0.3, raising=False)
    codex = world.codex.read_bytes()
    with open(world.codex, "rb"):  # another client has it open
        assert world.cleanup() == PENDING
    assert world.codex.read_bytes() == codex


@WINDOWS_ONLY
@pytest.mark.parametrize("stop", ["before-write", "after-write"])
def test_an_interrupted_cleanup_leaves_a_valid_file(world, stop):
    world.run_adds_entries(foreign=False)
    script = (f"import pathlib, sys; sys.path.insert(0, {str(pathlib.Path(__file__).parents[1])!r})\n"
              "from tests import phase4_client_config as cfg\n"
              f"cfg.CODEX_CONFIG = pathlib.Path({str(world.codex)!r})\n"
              f"cfg.CLAUDE_STATE = pathlib.Path({str(world.claude)!r})\n"
              "cfg.WATCHED = {}\n"
              f"cfg.cleanup(pathlib.Path({str(world.snap)!r}), {str(world.base)!r}, True)\n")
    process = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True,
                             env=dict(os.environ, MEMEX_CONFIG_CLEANUP_STOP=f"codex:{stop}"))
    assert process.returncode == 70, process.stderr  # stopped at the boundary under test
    parsed = tomllib.loads(world.codex.read_bytes().decode())  # always a complete, valid file
    assert parsed["model"] == "old" and "c:\\existing\\one" in parsed["projects"]
    left = set(world.mine) & set(parsed["projects"])
    assert left == (set(world.mine) if stop == "before-write" else set())
    world.cleanup()  # and a later cleanup finishes the job
    assert not set(world.mine) & world.codex_projects()
    clean = (b'model = "old"\napproval_policy = "on-request"\n\n'
             b"[projects.'c:\\existing\\one']\ntrust_level = \"trusted\"\n\n"
             b'[mcp_servers.tool]\ncommand = "tool"\n')
    raw = world.codex.read_bytes()
    assert raw.rstrip(b"\n") == clean.rstrip(b"\n"), "at most trailing newline padding differs"
