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
else -- including entries from earlier runs and concurrent changes made by the
user's own clients -- is left as it is and reported. Without `--apply` it is a
dry run. `config.toml` is edited as bytes, preserving line endings; the result
is verified to be the original minus exactly the removed blocks and to parse
with every other key unchanged. `~/.claude.json` is rewritten only if its
original bytes are reproduced exactly by re-serialization, so its format is
kept; otherwise its entries are reported, not touched. No credential file is
read.
"""
import hashlib
import json
import os
import pathlib
import re
import sys
import tomllib

HOME = pathlib.Path.home()
CODEX_CONFIG = pathlib.Path(os.getenv("CODEX_HOME") or HOME / ".codex") / "config.toml"
CLAUDE_STATE = HOME / ".claude.json"
WATCHED = {  # reported if changed; never edited here
    "codex hooks.json": CODEX_CONFIG.with_name("hooks.json"),
    "claude settings.json": HOME / ".claude" / "settings.json",
}
HEADER = re.compile(rb"^\[projects\.'([^']+)'\]\s*$")


def digest(path: pathlib.Path):
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else None


def codex_projects(raw: bytes) -> list[str]:
    return [m.group(1).decode() for line in raw.splitlines() if (m := HEADER.match(line))]


def claude_projects() -> list[str]:
    if not CLAUDE_STATE.exists():
        return []
    return list(json.loads(CLAUDE_STATE.read_text(encoding="utf-8")).get("projects", {}))


def norm(path: str) -> str:
    return os.path.normcase(os.path.normpath(path.replace("/", os.sep)))


def under(path: str, base: str) -> bool:
    path, base = norm(path), norm(base)
    return path == base or path.startswith(base.rstrip(os.sep) + os.sep)


def snapshot(target: pathlib.Path) -> None:
    raw = CODEX_CONFIG.read_bytes() if CODEX_CONFIG.exists() else b""
    target.write_text(json.dumps({
        "codex_projects": codex_projects(raw), "codex_sha256": hashlib.sha256(raw).hexdigest(),
        "claude_projects": claude_projects(),
        "watched": {name: digest(path) for name, path in WATCHED.items()},
    }, indent=1))
    print(f"snapshot: {len(codex_projects(raw))} codex projects, {len(claude_projects())} claude projects")


def strip_codex(raw: bytes, doomed: set[str]):
    lines = raw.splitlines(keepends=True)
    keep, removed, index = [], [], 0
    while index < len(lines):
        match = HEADER.match(lines[index])
        if match and match.group(1).decode() in doomed:
            block = [lines[index]]
            index += 1
            while index < len(lines) and not lines[index].startswith(b"["):
                block.append(lines[index])
                index += 1
            if b"".join(block[1:]).strip() != b'trust_level = "trusted"':
                raise SystemExit(f"refusing: unexpected content under {match.group(1).decode()}")
            removed.append(b"".join(block))
            continue
        keep.append(lines[index])
        index += 1
    result = b"".join(keep)
    rebuilt = raw
    for block in removed:
        rebuilt = rebuilt.replace(block, b"", 1)
    assert rebuilt == result
    before, after = tomllib.loads(raw.decode()), tomllib.loads(result.decode())
    assert {k: v for k, v in before.items() if k != "projects"} == {k: v for k, v in after.items() if k != "projects"}
    assert set(before.get("projects", {})) - set(after.get("projects", {})) == doomed
    return result


def cleanup(snap_path: pathlib.Path, base: str, apply: bool) -> int:
    snap = json.loads(snap_path.read_text())
    raw = CODEX_CONFIG.read_bytes() if CODEX_CONFIG.exists() else b""
    new_codex = [p for p in codex_projects(raw) if p not in snap["codex_projects"]]
    ours_codex = {p for p in new_codex if under(p, base)}
    new_claude = [p for p in claude_projects() if p not in snap["claude_projects"]]
    ours_claude = [p for p in new_claude if under(p, base)]
    print(f"codex: {len(new_codex)} new project entries, {len(ours_codex)} from this run")
    for p in sorted(ours_codex):
        print("  remove " + p)
    for p in sorted(set(new_codex) - ours_codex):
        print("  keep (not this run's) " + p)
    print(f"claude: {len(new_claude)} new project entries, {len(ours_claude)} from this run")
    for p in ours_claude:
        print("  remove " + p)
    for name, path in WATCHED.items():
        if digest(path) != snap["watched"][name]:
            print(f"  note: {name} changed since the snapshot (not edited here)")
    if not apply:
        return 0
    if ours_codex:
        CODEX_CONFIG.write_bytes(strip_codex(raw, ours_codex))
        assert not ours_codex & set(codex_projects(CODEX_CONFIG.read_bytes()))
        print(f"codex: removed {len(ours_codex)}")
    if ours_claude:
        original = CLAUDE_STATE.read_bytes()
        data = json.loads(original)
        if json.dumps(data, indent=2, ensure_ascii=False).encode() != original:
            print("claude: format not reproducible; entries reported, not removed")
        else:
            for p in ours_claude:
                data["projects"].pop(p)
            temporary = CLAUDE_STATE.with_name(".claude.json.memex-tmp")
            temporary.write_bytes(json.dumps(data, indent=2, ensure_ascii=False).encode())
            os.replace(temporary, CLAUDE_STATE)
            print(f"claude: removed {len(ours_claude)}")
    return 0


if __name__ == "__main__":
    command, *rest = sys.argv[1:]
    if command == "snapshot":
        snapshot(pathlib.Path(rest[0]))
    else:
        sys.exit(cleanup(pathlib.Path(rest[0]), rest[1], "--apply" in rest))
