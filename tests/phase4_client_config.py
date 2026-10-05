"""Track, and remove, only the client configuration entries a native run creates.

Native runs use the client configurations already authenticated on the
machine. Two entries are known to appear for a fixture directory: Codex
persists `[projects.'<dir>'] trust_level = "trusted"` in its `config.toml`
(S02-22), and Claude Code may record a `projects` entry in `~/.claude.json`.

usage:
  python tests/phase4_client_config.py snapshot <snapshot.json>
  python tests/phase4_client_config.py cleanup <snapshot.json> <basetemp> [--apply]

`cleanup` removes an entry only if it was absent from the snapshot *and* names
a directory under `basetemp`, the run's own pytest base directory. Everything
else -- earlier entries, the user's settings, concurrent changes by the
user's own clients -- is kept. Without `--apply` it is a dry run. No
credential file is read.

Concurrency. The clients take no lock memex could share, so neither an
advisory lock nor "compare a hash, then replace the file" can stop one of
them writing between cleanup's read and its write. Cleanup therefore opens
the file with **no sharing at all** (Windows share mode 0): while that handle
is open, no other process can open, read, write, replace or delete the file.
The removal is computed from the bytes read through that handle, and written
back through the same handle, so nothing written by anyone else can be
overwritten. A client that tries to write meanwhile gets a sharing violation
and retries or fails on its own terms; the hold lasts milliseconds. If the
file cannot be opened exclusively within a bounded time, or on a platform
without mandatory exclusion, nothing is written and the cleanup is reported
pending (exit status 3).

Interruption. Replacing the file from a temporary copy would need the
exclusive handle closed first, reopening the race, so the write happens in
place, ordered so the file is always complete: the new content, which is
never longer than the old, is written over the old at the old length, padded
with trailing newlines, in one write and flushed; only then is the padding
truncated. A stop between the two leaves valid content with extra trailing
newlines, which later cleanups leave as they are.

Formatting. `config.toml` is edited as bytes, so line endings and every byte
outside the removed blocks stay as they were. A block is removed only if its
header is the client's own single-quoted form and its body is exactly
`trust_level = "trusted"`; anything else is preserved and reported pending.
The result must parse to the original minus exactly the removed projects.
`~/.claude.json` is rewritten only if re-serializing it reproduces its bytes
exactly, so its layout is kept; otherwise it is left alone and reported
pending.
"""
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
import os
import pathlib
import re
import sys
import time
import tomllib

HOME = pathlib.Path.home()
CODEX_CONFIG = pathlib.Path(os.getenv("CODEX_HOME") or HOME / ".codex") / "config.toml"
CLAUDE_STATE = HOME / ".claude.json"
WATCHED = {  # reported if changed; never edited here
    "codex hooks.json": CODEX_CONFIG.with_name("hooks.json"),
    "claude settings.json": HOME / ".claude" / "settings.json",
}
HEADER = re.compile(rb"^\[projects\.'([^']+)'\]\s*$")
EXCLUSIVE_TIMEOUT = 5.0
PENDING = 3


class Pending(Exception):
    """Cleanup could not proceed safely; the file was left unchanged."""


@dataclass
class Outcome:
    result: bytes
    removed: list = field(default_factory=list)
    preserved: list = field(default_factory=list)  # this run's entries that could not be removed safely


def digest(path: pathlib.Path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def codex_projects(raw: bytes) -> list[str]:
    return list(tomllib.loads(raw.decode("utf-8")).get("projects", {})) if raw else []


def claude_projects(raw: bytes) -> list[str]:
    return list(json.loads(raw).get("projects", {})) if raw else []


def read(path: pathlib.Path) -> bytes:
    return path.read_bytes() if path.exists() else b""


def norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path.replace("/", os.sep)))


def under(path: str, base: str) -> bool:
    path, base = norm(path), norm(base)
    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


def snapshot(target: pathlib.Path) -> None:
    codex, claude = read(CODEX_CONFIG), read(CLAUDE_STATE)
    target.write_text(json.dumps({
        "codex_projects": codex_projects(codex), "codex_sha256": hashlib.sha256(codex).hexdigest(),
        "claude_projects": claude_projects(claude),
        "watched": {name: digest(path) for name, path in WATCHED.items()},
    }, indent=1))
    print(f"snapshot: {len(codex_projects(codex))} codex projects, {len(claude_projects(claude))} claude projects")


# -- the removals, as pure functions of the bytes on disk --------------------- #

def strip_codex(raw: bytes, doomed: set[str]) -> Outcome:
    """Remove the trust blocks for `doomed` that have exactly the client's own form."""
    lines = raw.splitlines(keepends=True)
    keep, blocks, removed, index = [], [], [], 0
    while index < len(lines):
        match = HEADER.match(lines[index])
        name = match.group(1).decode("utf-8") if match else None
        if name in doomed:
            end = index + 1
            while end < len(lines) and not lines[end].startswith(b"["):
                end += 1
            if b"".join(lines[index + 1:end]).strip() == b'trust_level = "trusted"':
                blocks.append(b"".join(lines[index:end]))
                removed.append(name)
                index = end
                continue
        keep.append(lines[index])
        index += 1
    result = b"".join(keep)
    rebuilt = raw
    for block in blocks:
        rebuilt = rebuilt.replace(block, b"", 1)
    before, after = tomllib.loads(raw.decode("utf-8")), tomllib.loads(result.decode("utf-8"))
    if rebuilt != result or after != _without(before, removed):
        raise Pending("config.toml: the removal would change more than this run's blocks")
    return Outcome(result, removed, sorted(doomed - set(removed)))


def _without(parsed: dict, removed) -> dict:
    out = dict(parsed)
    if removed:
        out["projects"] = {k: v for k, v in parsed["projects"].items() if k not in set(removed)}
        if not out["projects"]:
            del out["projects"]  # no project header left at all
    return out


def codex_outcome(raw: bytes, snap: dict, base: str) -> Outcome:
    doomed = {p for p in codex_projects(raw) if p not in snap["codex_projects"] and under(p, base)}
    return strip_codex(raw, doomed) if doomed else Outcome(raw)


def claude_outcome(raw: bytes, snap: dict, base: str) -> Outcome:
    if not raw:
        return Outcome(raw)
    data = json.loads(raw)
    doomed = [p for p in data.get("projects", {}) if p not in snap["claude_projects"] and under(p, base)]
    if not doomed:
        return Outcome(raw)
    if json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8") != raw:
        return Outcome(raw, [], doomed)  # its layout cannot be reproduced; leave it alone
    removable = [p for p in doomed if isinstance(data["projects"][p], dict)]
    for p in removable:
        del data["projects"][p]
    return Outcome(json.dumps(data, indent=2, ensure_ascii=False).encode("utf-8"), removable,
                   [p for p in doomed if p not in removable])


# -- exclusive, interruption-safe rewriting ----------------------------------- #

@contextmanager
def exclusive(path: pathlib.Path):
    """A read-write descriptor no other process can share until it closes."""
    if os.name != "nt":
        raise Pending("no mandatory file exclusion on this platform")
    import ctypes
    import msvcrt
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateFileW.restype = wintypes.HANDLE
    kernel32.CreateFileW.argtypes = (wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                     wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE)
    generic_read_write, open_existing, normal, share_none = 0xC0000000, 3, 0x80, 0
    deadline = time.monotonic() + EXCLUSIVE_TIMEOUT
    while True:
        handle = kernel32.CreateFileW(str(path), generic_read_write, share_none, None, open_existing, normal, None)
        if handle not in (None, wintypes.HANDLE(-1).value):
            break
        error = ctypes.get_last_error()
        if error in (32, 33) and time.monotonic() < deadline:  # sharing or lock violation: someone has it open
            time.sleep(0.05)
            continue
        raise Pending(f"{path.name}: could not be opened exclusively (Windows error {error})")
    descriptor = msvcrt.open_osfhandle(handle, os.O_RDWR | os.O_BINARY)
    try:
        yield descriptor
    finally:
        os.close(descriptor)


def _stop(point: str) -> None:
    """Test hook: stop the process at a named boundary of a named file's rewrite."""
    if os.getenv("MEMEX_CONFIG_CLEANUP_STOP") == point:
        os._exit(70)


def _read_all(descriptor) -> bytes:
    os.lseek(descriptor, 0, os.SEEK_SET)
    chunks = []
    while chunk := os.read(descriptor, 1 << 20):
        chunks.append(chunk)
    return b"".join(chunks)


def _rewrite(descriptor, old: bytes, new: bytes, label: str) -> None:
    assert len(new) <= len(old), "cleanup only removes"
    padded = new + b"\n" * (len(old) - len(new))
    _stop(f"{label}:before-write")
    os.lseek(descriptor, 0, os.SEEK_SET)
    written = 0
    while written < len(padded):
        written += os.write(descriptor, padded[written:])
    os.fsync(descriptor)
    _stop(f"{label}:after-write")
    os.ftruncate(descriptor, len(new))
    os.fsync(descriptor)


def clean(path: pathlib.Path, outcome_of, label: str, apply: bool) -> Outcome:
    """Preview from a plain read; apply only from bytes read under exclusive access."""
    preview = outcome_of(read(path))
    if not apply or not preview.removed:
        return preview
    with exclusive(path) as descriptor:
        current = _read_all(descriptor)
        outcome = outcome_of(current)  # recomputed from what is on disk now, under exclusion
        if outcome.removed:
            _rewrite(descriptor, current, outcome.result, label)
    return outcome


def cleanup(snap_path: pathlib.Path, base: str, apply: bool) -> int:
    snap = json.loads(pathlib.Path(snap_path).read_text())
    status = 0
    for label, path, outcome_of in (
            ("codex", CODEX_CONFIG, lambda raw: codex_outcome(raw, snap, base)),
            ("claude", CLAUDE_STATE, lambda raw: claude_outcome(raw, snap, base))):
        try:
            outcome = clean(path, outcome_of, label, apply)
        except (Pending, OSError) as exc:
            print(f"{label}: cleanup pending, file unchanged: {exc}")
            status = PENDING
            continue
        verb = "removed" if apply else "would remove"
        print(f"{label}: {verb} {len(outcome.removed)} of this run's entries")
        for p in outcome.removed:
            print(f"  {verb} {p}")
        for p in outcome.preserved:
            print(f"  pending (preserved, not in the client's own form) {p}")
        if outcome.preserved:
            status = PENDING
    for name, path in WATCHED.items():
        if digest(path) != snap["watched"].get(name):
            print(f"  note: {name} changed since the snapshot (not edited here)")
    return status


if __name__ == "__main__":
    command, *rest = sys.argv[1:]
    if command == "snapshot":
        snapshot(pathlib.Path(rest[0]))
    else:
        sys.exit(cleanup(pathlib.Path(rest[0]), rest[1], "--apply" in rest))
