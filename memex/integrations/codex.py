"""Codex host adapter: the same deny-and-reconsider gate, on measured Codex semantics.

S02 measured codex-cli 0.157.1 rather than assuming Claude's behavior carries
over. The findings that shape this module:

* Hooks do not run under `codex exec`. They run in **app-server** threads, the
  protocol the IDE extension and desktop app use, so that is the supported mode.
* `PreToolUse` fires for `apply_patch` and for shell commands as `Bash`, and
  `permissionDecision:"deny"` blocks the pending patch with the target bytes
  unchanged. The denial reaches the model as a tool output reading
  `Command blocked by PreToolUse hook: <reason>`, and the model re-reads and
  reissues.
* One `apply_patch` call can add, update, delete and move several files, all in
  one `PreToolUse`. Every header in the patch is a declared target.
* A hook that times out or exits non-zero fails open. Hooks run concurrently.
* Hook processes receive a filtered environment, so backend settings come from
  the capability file rather than environment variables.
* Insertion is recorded in the rollout named by `transcript_path`: injected
  context as a `developer` message tagged `hooks.additional_context`, a denial
  as a tool-call output, each carrying the turn's `turn_id`. The nested
  `tool_use_id` of a code-mode `apply_patch` never appears there, so a
  correction is bound to its turn instead.
* An oversized `additionalContext` is middle-truncated with both ends kept. The
  base adapter therefore confirms only a complete packet, and the packet cap
  here stays well below the measured spill threshold.
"""
import json
import os
from pathlib import Path
import re
import sys

from memex.integrations.host_adapter import (
    DELIVERY_MARKER, MAX_EVIDENCE_RECORDS, AdapterCapability, HostAdapter, hook_main,
    read_capability, write_capability,
)
from memex.runtime.views import discover_repository

ADAPTER_VERSION = "codex.v1"
HARNESS = "codex"

#: Codex spills `additionalContext` above a token threshold (2,500 by default)
#: and keeps only the head and tail. Hash-heavy packets tokenize densely, so
#: the cap stays far enough below it that an intact packet fits.
HOST_CONTEXT_LIMIT = 4_000

CAPABILITY_RELATIVE = Path("memex") / "adapters" / "codex.json"

#: The rollout's own tag on injected hook context.
CONTEXT_ITEM_KIND = "hooks.additional_context"

#: How Codex prefixes a denial reason in the tool output the model receives.
DENIAL_PREFIX = "PreToolUse hook"

#: Codex's own marker for a context item it truncated.
TRUNCATION_WARNING = "Warning: truncated output"

PATCH_HEADER = re.compile(r"^\*\*\* (?:Add File|Update File|Delete File|Move to): (.+?)\s*$", re.MULTILINE)


def capability_path(registration) -> Path:
    return Path(registration.common_dir) / CAPABILITY_RELATIVE


def register(root, principal_id: str, *, denied_paths=(), backend: dict | None = None) -> AdapterCapability:
    """Authorize one principal for this repository's Codex adapter.

    `backend` carries non-secret connection settings, because Codex does not pass
    environment variables through to hook processes.
    """
    return write_capability(discover_repository(root), CAPABILITY_RELATIVE, version=ADAPTER_VERSION,
                            principal_id=principal_id, denied_paths=denied_paths, backend=backend)


def load_capability(registration) -> AdapterCapability:
    return read_capability(registration, CAPABILITY_RELATIVE, "Codex")


def patch_targets(patch: str) -> tuple[str, ...]:
    """Every path an `apply_patch` body adds, updates, deletes or moves to."""
    return tuple(dict.fromkeys(match.strip() for match in PATCH_HEADER.findall(patch or "")))


def declared_targets(tool_name: str, tool_input) -> tuple[str, ...] | None:
    """Return declared raw target paths, or None for an opaque action.

    A patch whose headers cannot be read is treated as opaque rather than as an
    action with no targets: certifying "nothing" would be certifying a guess.
    """
    if tool_name != "apply_patch":
        return None
    command = tool_input.get("command") if isinstance(tool_input, dict) else tool_input
    if not isinstance(command, str):
        return None
    targets = patch_targets(command)
    return targets or None


# --------------------------------------------------------------------------- #
# Insertion evidence
# --------------------------------------------------------------------------- #

def rollout_roots() -> tuple[Path, ...]:
    """Directories the installed client writes session rollouts under."""
    roots = []
    configured = os.getenv("CODEX_HOME")
    if configured:
        roots.append(Path(configured))
    roots.append(Path.home() / ".codex")
    return tuple(root / "sessions" for root in roots)


def _first_record(path: Path) -> dict | None:
    try:
        with open(path, "rb") as stream:
            line = stream.readline(4_000_000)
        record = json.loads(line.decode("utf-8", "replace"))
    except (OSError, ValueError):
        return None
    return record if isinstance(record, dict) else None


def authorized_transcript(transcript_path, native_session_id: str) -> Path | None:
    """This thread's own rollout, or None.

    Measured layout: `<CODEX_HOME>/sessions/YYYY/MM/DD/rollout-<time>-<thread>.jsonl`,
    whose first record is `session_meta` naming that thread. The payload's path
    is a claim, so all three are checked: inside the sessions tree, named for
    this thread, and self-identifying as this thread.
    """
    if not transcript_path or not native_session_id:
        return None
    try:
        candidate = Path(transcript_path).resolve()
    except (OSError, ValueError):
        return None
    if candidate.suffix != ".jsonl" or not candidate.name.startswith("rollout-") \
            or not candidate.stem.endswith("-" + native_session_id):
        return None
    if not any(_inside(candidate, root) for root in rollout_roots()):
        return None
    meta = _first_record(candidate)
    payload = (meta or {}).get("payload")
    if not meta or meta.get("type") != "session_meta" or not isinstance(payload, dict) \
            or payload.get("id") != native_session_id:
        return None
    return candidate


def _inside(candidate: Path, root: Path) -> bool:
    try:
        return candidate.is_relative_to(root.resolve())
    except (OSError, ValueError):
        return False


def _text_of(value) -> str:
    """Concatenate a content field's text parts, bounded."""
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts = []
        for entry in value[:64]:
            if isinstance(entry, dict) and isinstance(entry.get("text"), str):
                parts.append(entry["text"])
            elif isinstance(entry, str):
                parts.append(entry)
        return "\n".join(parts)
    return ""


def insertion_evidence(blob: str, native_session_id: str) -> list[tuple[str, str | None, str]]:
    """Parse a rollout tail into `(evidence_kind, turn_id, inserted_text)`.

    Only `response_item` records count: those are the items in the model-visible
    history. A `developer` message tagged as hook context is an injected
    snapshot; a tool-call output carrying the denial prefix is a delivered
    correction. App-server notifications, agent or user prose, reasoning and the
    hook's own output entries are not insertions and are never read as such.
    """
    evidence: list[tuple[str, str | None, str]] = []
    if not blob or not native_session_id:
        return evidence
    seen = 0
    for line in blob.splitlines():
        if DELIVERY_MARKER not in line:
            continue  # Cheap prefilter; the decision below is still structural.
        if seen >= MAX_EVIDENCE_RECORDS:
            break
        seen += 1
        try:
            record = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            continue
        if not isinstance(record, dict) or record.get("type") != "response_item":
            continue
        payload = record.get("payload")
        if not isinstance(payload, dict):
            continue
        meta = payload.get("internal_chat_message_metadata_passthrough")
        meta = meta if isinstance(meta, dict) else {}
        turn_id = meta.get("turn_id") if isinstance(meta.get("turn_id"), str) else None
        kinds = meta.get("content_item_kinds") if isinstance(meta.get("content_item_kinds"), list) else []

        if payload.get("type") == "message" and payload.get("role") == "developer" \
                and CONTEXT_ITEM_KIND in kinds:
            evidence.append(("additional_context", turn_id, _text_of(payload.get("content"))))
        elif payload.get("type") in ("function_call_output", "custom_tool_call_output"):
            text = _text_of(payload.get("output"))
            if DENIAL_PREFIX in text:
                evidence.append(("tool_denial", turn_id, text))
    return evidence


# --------------------------------------------------------------------------- #
# Adapter
# --------------------------------------------------------------------------- #

class CodexAdapter(HostAdapter):
    """The Codex host: `apply_patch` targets, rollout records, turn-bound corrections."""

    HARNESS = HARNESS
    SESSION_PREFIX = "cx-"
    ADAPTER_VERSION = ADAPTER_VERSION
    CONTEXT_LIMIT = HOST_CONTEXT_LIMIT

    def declared_targets(self, tool_name, tool_input):
        return declared_targets(tool_name, tool_input)

    def authorized_transcript(self, transcript_path, native_session_id):
        return authorized_transcript(transcript_path, native_session_id)

    def insertion_evidence(self, blob, native_session_id):
        return insertion_evidence(blob, native_session_id)

    def correction_binding(self, payload, attempt_id):
        # The model-visible denial record carries the turn, not the nested call.
        turn = payload.get("turn_id")
        return turn if isinstance(turn, str) and turn else None


# --------------------------------------------------------------------------- #
# Hook configuration
# --------------------------------------------------------------------------- #
#
# S02 measured three installation constraints. A quoted interpreter path with
# arguments fails on Windows, so the hook command is a launcher script path.
# Hook processes get a filtered environment, so the launcher sets PYTHONPATH
# itself. And hooks only run once trusted: memex never writes that trust; the
# user grants it through Codex's own `/hooks` review, and fixtures grant it for
# one thread through the app-server's `bypass_hook_trust` thread override.

#: Event -> matcher. Shell is matched so it can be recorded as opaque, never gated.
CODEX_HOOK_EVENTS = {
    "SessionStart": None,
    "PreToolUse": "apply_patch|Bash",
    "PostToolUse": "apply_patch",
    "SessionEnd": None,
}


def write_launcher(registration, *, python_executable: str | None = None,
                   pythonpath: str | None = None) -> Path:
    """Write the hook launcher beside the capability file and return its path."""
    executable = python_executable or sys.executable
    directory = Path(registration.common_dir) / "memex" / "adapters"
    directory.mkdir(parents=True, exist_ok=True)
    if os.name == "nt":
        target = directory / "codex-hook.cmd"
        lines = ["@echo off"]
        if pythonpath:
            lines.append(f'set "PYTHONPATH={pythonpath}"')
        lines.append(f'"{executable}" -m memex.integrations.codex')
        target.write_text("\r\n".join(lines) + "\r\n", encoding="utf-8")
    else:
        target = directory / "codex-hook.sh"
        lines = ["#!/bin/sh"]
        if pythonpath:
            lines.append(f"export PYTHONPATH='{pythonpath}'")
        lines.append(f"exec '{executable}' -m memex.integrations.codex")
        target.write_text("\n".join(lines) + "\n", encoding="utf-8")
        target.chmod(0o700)
    return target


def hook_groups(launcher: Path, *, timeout: int = 30, session_end_timeout: int = 3) -> dict:
    """Codex `hooks` configuration for memex, for review before installation."""
    command = Path(launcher).as_posix()
    groups = {}
    for event, matcher in CODEX_HOOK_EVENTS.items():
        entry = {"type": "command", "command": command,
                 "timeout": session_end_timeout if event == "SessionEnd" else timeout}
        group = {"hooks": [entry]}
        if matcher:
            group["matcher"] = matcher
        groups[event] = [group]
    return groups


def session_flags(launcher: Path, *, timeout: int = 30, extra: dict | None = None) -> list[str]:
    """The same hooks as per-invocation `-c` overrides, for fixtures.

    Nothing is persisted: these live only for the app-server process they are
    passed to. `extra` composes other hook groups into the same events, the way
    an installed configuration would sit beside a user's own hooks.
    """
    events = hook_groups(launcher, timeout=timeout)
    for event, groups in (extra or {}).items():
        events.setdefault(event, []).extend(groups)
    flags = []
    for event, groups in events.items():
        rendered = []
        for group in groups:
            bodies = ",".join(f"{{type=\"command\",command='{entry['command']}',timeout={entry['timeout']}}}"
                              for entry in group["hooks"])
            matcher = f"matcher=\"{group['matcher']}\"," if "matcher" in group else ""
            rendered.append(f"{{{matcher}hooks=[{bodies}]}}")
        flags += ["-c", f"hooks.{event}=[{','.join(rendered)}]"]
    return flags


def install_hooks(hooks_path, launcher: Path, *, timeout: int = 30) -> dict:
    """Compose memex hooks into a Codex `hooks.json`, preserving everything else.

    Codex requires the `{"hooks": {...}}` wrapper (S02 measured a flat file being
    rejected). Existing entries are kept, a repeated install does not duplicate,
    and trust is left to the user's own review.
    """
    path = Path(hooks_path)
    document = json.loads(path.read_text(encoding="utf-8") or "{}") if path.exists() else {}
    hooks = document.setdefault("hooks", {})
    command = Path(launcher).as_posix()
    for event, groups in hook_groups(launcher, timeout=timeout).items():
        existing = hooks.setdefault(event, [])
        if any(h.get("command") == command for group in existing for h in group.get("hooks", [])):
            continue
        existing.extend(groups)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return document


def uninstall_hooks(hooks_path, launcher: Path) -> dict:
    """Remove only memex entries, leaving other hooks intact."""
    path = Path(hooks_path)
    if not path.exists():
        return {}
    document = json.loads(path.read_text(encoding="utf-8") or "{}")
    command = Path(launcher).as_posix()
    hooks = document.get("hooks", {})
    for event in list(hooks):
        groups = []
        for group in hooks[event]:
            remaining = [h for h in group.get("hooks", []) if h.get("command") != command]
            if remaining:
                groups.append({**group, "hooks": remaining})
        if groups:
            hooks[event] = groups
        else:
            del hooks[event]
    if not hooks:
        document.pop("hooks", None)
    path.write_text(json.dumps(document, indent=2), encoding="utf-8")
    return document


def main(argv=None) -> int:
    """Hook entry point. Never blocks the host on a memex failure."""
    return hook_main(CodexAdapter, load_capability)


if __name__ == "__main__":
    sys.exit(main())
