"""Hook client: hand one hook event to the memex hook service, or run it directly.

Hosts start a new process for every hook. Starting memex itself there (imports,
graph connection, parser workers) cost seconds per event, so this file is all
that runs per event. It is standard library only and is run by path with
`python -I -S`, which keeps its start-up at the interpreter's own cost.

    python -I -S .../memex/hook_client.py <hook module> [arguments]

The service (`memex.hookd`) is keyed by the hook module and arguments, the
interpreter, the backend environment, and the memex sources, so changed code or
a different backend never reaches an old service. When no service answers, the
event runs the one-shot way (`python -m <hook module>`) and a service is started
for the events after it. `MEMEX_HOOKD=0` always runs one-shot.
"""
import json
import os
import socket
import sys
import time

try:
    from _sha2 import sha256  # The builtin, without loading OpenSSL.
except ImportError:
    from hashlib import sha256

PACKAGE = os.path.dirname(os.path.abspath(__file__))
#: What a service's answers depend on besides the payload. Values are hashed, never stored.
ENV_KEYS = ("MEMEX_LIVE_NEO4J_URI", "MEMEX_PHASE1_NEO4J_URI", "NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD",
            "PYTHONPATH", "CODEX_HOME", "CLAUDE_CONFIG_DIR", "MEMEX_HOOKD_IDLE_SECONDS")
SOURCE_DIRS = ("context", "evaluation", "integrations", "runtime")
#: Below every host's hook timeout, so a stuck service still yields a fail-open answer.
REPLY_SECONDS = 15
TIMED_MODULES = ("memex.evaluation.arms",)


def process_created() -> float | None:
    """This process's creation time (epoch seconds), so measured latency includes interpreter start."""
    if os.name != "nt":
        try:
            with open(f"/proc/{os.getpid()}/stat") as stream:
                start_ticks = int(stream.read().rsplit(")", 1)[1].split()[19])
            with open("/proc/uptime") as stream:
                uptime = float(stream.read().split()[0])
            return time.time() - uptime + start_ticks / os.sysconf("SC_CLK_TCK")
        except (OSError, ValueError, IndexError):
            return None
    import ctypes
    from ctypes import wintypes

    times = [wintypes.FILETIME() for _ in range(4)]
    kernel32 = ctypes.WinDLL("kernel32")
    kernel32.GetCurrentProcess.restype = wintypes.HANDLE
    kernel32.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    kernel32.GetProcessTimes.restype = wintypes.BOOL
    if not kernel32.GetProcessTimes(kernel32.GetCurrentProcess(), *[ctypes.byref(t) for t in times]):
        return None
    created = (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime
    return created / 1e7 - 11644473600


def service_key(argv: list[str]) -> str:
    digest = sha256(json.dumps([sys.executable, argv, [os.environ.get(k) for k in ENV_KEYS]]).encode())
    for directory in SOURCE_DIRS:
        for entry in sorted(os.scandir(os.path.join(PACKAGE, directory)), key=lambda e: e.name):
            if entry.name.endswith(".py"):
                stat = entry.stat()
                digest.update(f"{entry.name}:{stat.st_mtime_ns}:{stat.st_size};".encode())
    return digest.hexdigest()[:24]


def service_dir() -> str | None:
    """Under the user's home; None when a filtered hook environment hides it."""
    home = os.path.expanduser("~")
    return None if home.startswith("~") else os.path.join(home, ".memex", "hookd")


def fail_open(raw: bytes) -> str:
    try:
        event = json.loads(raw or b"{}").get("hook_event_name") or "PreToolUse"
    except (ValueError, AttributeError):
        event = "PreToolUse"
    return json.dumps({"hookSpecificOutput": {"hookEventName": event},
                       "systemMessage": "memex is unavailable; context freshness is unverified."}) + "\n"


def start_service(argv: list[str], key: str) -> None:
    """Start a service unless one is already starting. Never waits for it."""
    import subprocess

    directory = service_dir()
    starting = os.path.join(directory, f"{key}.starting")
    try:
        os.makedirs(directory, exist_ok=True)
        if os.path.exists(starting) and time.time() - os.stat(starting).st_mtime < 60:
            return
        with open(starting, "w", encoding="ascii") as marker:
            marker.write(str(os.getpid()))
        flags = {}
        if os.name == "nt":
            # Detached and without inherited handles: a host waiting on the hook's
            # pipes must not wait on the service too.
            flags["creationflags"] = (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                                      | subprocess.CREATE_NO_WINDOW | 0x01000000)  # CREATE_BREAKAWAY_FROM_JOB
        else:
            flags["start_new_session"] = True
        try:
            subprocess.Popen([sys.executable, "-m", "memex.hookd", key, *argv], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, **flags)
        except OSError:
            if os.name != "nt":
                raise
            flags["creationflags"] &= ~0x01000000  # The host's job forbids breakaway.
            subprocess.Popen([sys.executable, "-m", "memex.hookd", key, *argv], stdin=subprocess.DEVNULL,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, close_fds=True, **flags)
    except OSError:
        pass  # No service this time; the event itself still runs.


def direct(argv: list[str], raw: bytes, created: float | None) -> int:
    import subprocess

    env = dict(os.environ)
    if created is not None:
        env["MEMEX_HOOK_CREATED"] = repr(created)
    result = subprocess.run([sys.executable, "-m", *argv], input=raw, stdout=subprocess.PIPE, env=env)
    sys.stdout.buffer.write(result.stdout)
    sys.stdout.flush()
    return 0


def via_service(address: dict, raw: bytes, created: float | None) -> bool:
    """True once the service has answered; False if it could not be reached at all."""
    try:
        connection = socket.create_connection(("127.0.0.1", int(address["port"])), timeout=1)
    except (OSError, ValueError, KeyError):
        return False
    with connection:
        connection.settimeout(REPLY_SECONDS)
        stream = connection.makefile("rwb")
        try:
            stream.write(json.dumps({"token": address.get("token"), "payload": raw.decode("utf-8", "replace"),
                                     "created": created}).encode() + b"\n")
            stream.flush()
            line = stream.readline()
        except OSError:
            line = b""
        if not line:
            # Sent but unanswered: the service may have acted, so it is not re-run.
            sys.stdout.write(fail_open(raw))
            sys.stdout.flush()
            return True
        sys.stdout.write(json.loads(line)["stdout"])
        sys.stdout.flush()
        try:
            stream.write(json.dumps({"printed": time.time()}).encode() + b"\n")
            stream.flush()
        except OSError:
            pass  # The service then records the output as failed, never as emitted.
    return True


def main() -> int:
    argv = sys.argv[1:]
    raw = sys.stdin.buffer.read()
    created = process_created() if argv and argv[0] in TIMED_MODULES else None
    if not argv or os.environ.get("MEMEX_HOOKD") == "0" or service_dir() is None:
        return direct(argv, raw, created)
    key = service_key(argv)
    try:
        with open(os.path.join(service_dir(), f"{key}.json"), encoding="utf-8") as stream:
            address = json.load(stream)
    except (OSError, ValueError):
        address = None
    if address is not None and via_service(address, raw, created):
        return 0
    start_service(argv, key)
    return direct(argv, raw, created)


if __name__ == "__main__":
    sys.exit(main())
