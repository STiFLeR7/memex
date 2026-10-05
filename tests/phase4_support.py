"""Shared machinery for the Phase 4 native runs: an app-server client and fixtures.

The app-server client is a measurement harness, not an adapter. It never
approves anything: any approval request the server raises is declined and
recorded, so nothing in a native run is permitted by the harness that the
user's own policy would not have permitted.
"""
import json
import os
import pathlib
import queue
import shutil
import subprocess
import sys
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
CHECKOUT = HERE.parent
FIXTURE_HOOKS = HERE / "phase4_fixture_hooks.py"

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

CODEX_MODEL = os.getenv("MEMEX_PHASE4_CODEX_MODEL", "gpt-6-sol")


def git(repo, *args: str) -> str:
    return subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True,
                          text=True).stdout.strip()


def digest(path: pathlib.Path) -> str:
    import hashlib
    return "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()


def fixture_repo(path: pathlib.Path) -> pathlib.Path:
    path.mkdir(parents=True)
    git(path.parent, "init", "-q", str(path))
    (path / "api.py").write_text(API, newline="\n")
    (path / "validate.py").write_text(VALIDATE, newline="\n")
    (path / "contract_check.py").write_text(CONTRACT, newline="\n")
    git(path, "add", ".")
    git(path, "-c", "user.email=phase4@memex.test", "-c", "user.name=phase4", "commit", "-qm", "baseline")
    return path


def script_launcher(directory: pathlib.Path, name: str, script: pathlib.Path) -> pathlib.Path:
    """A `.cmd`/`.sh` wrapper, because S02 measured quoted interpreter paths failing."""
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        target = directory / f"{name}.cmd"
        target.write_text(f'@echo off\r\n"{sys.executable}" "{script}" %*\r\n', encoding="utf-8")
    else:
        target = directory / f"{name}.sh"
        target.write_text(f"#!/bin/sh\nexec '{sys.executable}' '{script}' \"$@\"\n", encoding="utf-8")
        target.chmod(0o700)
    return target


def fixture_command(launcher: pathlib.Path, *args) -> str:
    return " ".join([launcher.as_posix(), *[str(a).replace("\\", "/") for a in args]])


# --------------------------------------------------------------------------- #
# Objective contract
# --------------------------------------------------------------------------- #

def apply_patch_text(patch: str, original: dict[str, str]) -> dict[str, str]:
    """Reconstruct the files an `apply_patch` body would have written.

    Supports the Add/Update/Delete headers the model produced in these runs.
    Raises if a hunk does not apply, so a reconstruction never guesses.
    """
    files = dict(original)
    current, mode, hunks = None, None, []

    def flush():
        if current is None:
            return
        if mode == "add":
            files[current] = "\n".join(line[1:] for line in hunks if line.startswith("+")) + "\n"
        elif mode == "delete":
            files.pop(current, None)
        elif mode == "update":
            text = files[current]
            for hunk in _split_hunks(hunks):
                old = "\n".join(line[1:] for line in hunk if line[:1] in (" ", "-"))
                new = "\n".join(line[1:] for line in hunk if line[:1] in (" ", "+"))
                if old not in text:
                    raise AssertionError(f"hunk does not apply to {current}: {old!r}")
                text = text.replace(old, new, 1)
            files[current] = text

    for line in patch.splitlines():
        for header, kind in (("*** Add File: ", "add"), ("*** Update File: ", "update"),
                             ("*** Delete File: ", "delete")):
            if line.startswith(header):
                flush()
                current, mode, hunks = pathlib.PurePath(line[len(header):].strip()).name, kind, []
                break
        else:
            if line.startswith("*** "):
                continue
            hunks.append(line)
    flush()
    return files


def _split_hunks(lines):
    hunk = []
    for line in lines:
        if line.startswith("@@"):
            if hunk:
                yield hunk
            hunk = []
        elif line[:1] in (" ", "-", "+"):
            hunk.append(line)
        elif line == "":
            hunk.append(" ")
    if hunk:
        yield hunk


def proposed_api(tool: str, tool_input, original: str) -> str:
    """Reconstruct api.py as a denied proposal would have written it."""
    if tool == "apply_patch":
        command = tool_input.get("command") if isinstance(tool_input, dict) else tool_input
        return apply_patch_text(command, {"api.py": original})["api.py"]
    if tool == "Write":
        return tool_input["content"]
    if tool == "Edit":
        old, new = tool_input["old_string"], tool_input["new_string"]
        assert old in original, "the denied edit did not apply to the pre-denial file"
        return original.replace(old, new) if tool_input.get("replace_all") else original.replace(old, new, 1)
    if tool == "MultiEdit":
        text = original
        for edit in tool_input["edits"]:
            text = text.replace(edit["old_string"], edit["new_string"],
                                -1 if edit.get("replace_all") else 1)
        return text
    raise AssertionError(f"unexpected mutation tool: {tool}")


def run_contract(repo: pathlib.Path, api_source: str, scratch: pathlib.Path, label: str):
    """Evaluate one candidate implementation in an isolated copy."""
    sandbox = scratch / f"contract-{label}"
    if sandbox.exists():
        shutil.rmtree(sandbox)
    sandbox.mkdir(parents=True)
    for name in ("validate.py", "contract_check.py"):
        shutil.copyfile(repo / name, sandbox / name)
    (sandbox / "api.py").write_text(api_source, newline="\n")
    return subprocess.run([sys.executable, "contract_check.py"], cwd=sandbox,
                          capture_output=True, text=True, timeout=60)


# --------------------------------------------------------------------------- #
# Codex app-server client
# --------------------------------------------------------------------------- #

def codex_command() -> list[str]:
    """How to start codex without the npm `.CMD` shim on Windows.

    The shim re-parses arguments through cmd.exe, which does not understand the
    `\\"` escapes CreateProcess quoting produces, so a `|` inside a hook matcher
    becomes a pipe. Running the package's own entry script avoids that parse.
    """
    found = shutil.which("codex")
    if os.name == "nt" and found and found.lower().endswith((".cmd", ".bat")):
        script = pathlib.Path(found).parent / "node_modules" / "@openai" / "codex" / "bin" / "codex.js"
        node = shutil.which("node")
        if script.exists() and node:
            return [node, str(script)]
    return [found or "codex"]


class AppServer:
    """A JSON-RPC client over the app-server's stdio transport."""

    def __init__(self, flags: list[str], *, env=None, log_path: pathlib.Path | None = None):
        self.proc = subprocess.Popen([*codex_command(), "app-server", *flags],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                     text=True, encoding="utf-8", errors="replace", bufsize=1,
                                     env=env or dict(os.environ))
        self.inbox: queue.Queue = queue.Queue()
        self.messages: list[dict] = []
        self.declined: list[dict] = []
        self.next_id = 0
        self.log_path = log_path
        self._send_lock = threading.Lock()
        threading.Thread(target=self._read, daemon=True).start()
        self.stderr: list[str] = []
        threading.Thread(target=lambda: [self.stderr.append(x) for x in self.proc.stderr], daemon=True).start()
        self.request("initialize", {"clientInfo": {"name": "memex-phase4", "version": "0.9.0"}})
        self.send({"method": "initialized"})

    def _read(self):
        for line in self.proc.stdout:
            try:
                message = json.loads(line)
            except json.JSONDecodeError:
                continue
            if "id" in message and "method" in message:
                # Never approve on the user's behalf.
                self.declined.append({"method": message["method"]})
                self.send({"id": message["id"], "result": {"decision": "decline"}})
                continue
            self.messages.append(message)
            self.inbox.put(message)

    def send(self, message: dict):
        with self._send_lock:
            self.proc.stdin.write(json.dumps(message) + "\n")
            self.proc.stdin.flush()

    def request(self, method: str, params: dict, timeout: float = 180):
        with self._send_lock:
            self.next_id += 1
            rid = self.next_id
        self.send({"id": rid, "method": method, "params": params})
        deadline = time.time() + timeout
        while time.time() < deadline:
            for message in list(self.messages):
                if message.get("id") == rid and "method" not in message:
                    self.messages.remove(message)
                    if "error" in message:
                        raise RuntimeError(f"{method}: {message['error']}")
                    return message.get("result")
            time.sleep(0.05)
        raise TimeoutError(f"{method}; exit={self.proc.poll()}; stderr tail: {''.join(self.stderr)[-1500:]}")

    def start_thread(self, cwd: pathlib.Path, *, model: str = CODEX_MODEL) -> str:
        # `bypass_hook_trust` is a per-thread override: it trusts this fixture's
        # session-flag hooks for this thread only and persists nothing.
        result = self.request("thread/start", {"cwd": str(cwd), "model": model, "sandbox": "workspace-write",
                                               "config": {"bypass_hook_trust": True}})
        return result["thread"]["id"]

    def start_turn(self, thread_id: str, text: str) -> str:
        result = self.request("turn/start", {"threadId": thread_id, "input": [{"type": "text", "text": text}]})
        return result["turn"]["id"]

    def wait_turn(self, thread_id: str, turn_id: str | None = None, timeout: float = 900) -> dict:
        deadline = time.time() + timeout
        while time.time() < deadline:
            for message in list(self.messages):
                params = message.get("params") or {}
                if message.get("method") == "turn/completed" and params.get("threadId") == thread_id \
                        and (turn_id is None or params.get("turn", {}).get("id") == turn_id):
                    return params["turn"]
            time.sleep(0.2)
        raise TimeoutError("turn did not complete")

    def rollout(self, thread_id: str) -> pathlib.Path:
        return pathlib.Path(self.request("thread/read", {"threadId": thread_id})["thread"]["path"])

    def close(self):
        try:
            self.proc.stdin.close()
            self.proc.wait(timeout=15)
        except Exception:  # noqa: BLE001 - teardown
            self.proc.kill()
        if self.log_path:
            self.log_path.write_text("\n".join(json.dumps(m) for m in self.messages), encoding="utf-8")


def rollout_records(path: pathlib.Path) -> list[dict]:
    records = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def rollout_texts_after(records: list[dict], index: int, kinds=("function_call", "custom_tool_call")) -> list[str]:
    """Inputs of tool calls the model made after record `index`, for read checks."""
    out = []
    for record in records[index + 1:]:
        payload = record.get("payload") or {}
        if record.get("type") == "response_item" and payload.get("type") in kinds:
            out.append(json.dumps(payload.get("arguments") or payload.get("input") or ""))
    return out


def denial_index(records: list[dict], marker: str) -> int | None:
    for index, record in enumerate(records):
        payload = record.get("payload") or {}
        if record.get("type") == "response_item" and payload.get("type") in (
                "function_call_output", "custom_tool_call_output") and marker in json.dumps(payload):
            return index
    return None


def proposals(state: pathlib.Path) -> list[dict]:
    path = state / "proposals.jsonl"
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
