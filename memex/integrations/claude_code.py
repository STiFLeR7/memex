"""Claude Code host adapter: a measured deny-and-reconsider gate.

S01 measured the installed client rather than trusting hook names. The findings
that shape this module:

* ``PreToolUse`` is synchronous and ``permissionDecision:"deny"`` stops the
  pending mutation before it touches disk. That is the gate. Returning ``allow``
  with ``additionalContext`` would let the stale write land first, so this
  adapter never uses that path for a material correction.
* ``deny`` is honored even under ``bypassPermissions``, so the gate does not
  depend on permission policy. This module therefore never edits permissions,
  never auto-approves and never rewrites tool arguments.
* A hook that overruns its configured ``timeout`` does **not** block: the write
  proceeds. So the adapter keeps a hard deadline strictly inside the hook
  timeout and, when it cannot finish, records a fail-open advisory instead of
  pretending it gated anything.
* Parallel edit calls were serialized by the host, each with its own
  check/execute pair. A check therefore certifies its own declared target at its
  own moment. Nothing here protects a file from a writer that arrives after the
  check returns.
* The model verified a correction it could disprove and overrode it. Corrections
  must be true, which is why every denial is backed by a real hash comparison
  against what memex actually delivered to this session.

A hook payload is not an authenticator: anything able to run this entry point
could put a principal in stdin. Authentication comes from a capability file in
the repository's Git common directory, and the payload's ``cwd`` must resolve to
the registered worktree.
"""
import json
import os
from pathlib import Path
import sys

from memex.integrations.host_adapter import (  # noqa: F401 - stable public surface
    DELIVERY_MARKER, MAX_DELIVERY_CONFIRMATIONS, MAX_EVIDENCE_RECORDS, AdapterCapability,
    HookResponse, HostAdapter, hook_main, normalize_target, read_capability, write_capability,
)
from memex.runtime.views import discover_repository

HOOK_CLIENT = Path(__file__).resolve().parent.parent / "hook_client.py"

ADAPTER_VERSION = "claude-code.v2"
HARNESS = "claude_code"

#: Tools that declare the files they mutate. Everything else is opaque.
DECLARED_EDIT_TOOLS = {
    "Edit": "file_path",
    "Write": "file_path",
    "MultiEdit": "file_path",
    "NotebookEdit": "notebook_path",
}

#: The host caps injected context at 10,000 characters.
HOST_CONTEXT_LIMIT = 10_000

#: The record that proves injected context entered the session, measured on
#: Claude Code 2.1.289: the client writes an `attachment` of this type holding
#: the text it actually rendered into the conversation.
INSERTION_ATTACHMENT = "hook_additional_context"

CAPABILITY_RELATIVE = Path("memex") / "adapters" / "claude-code.json"


# --------------------------------------------------------------------------- #
# Trusted local registration
# --------------------------------------------------------------------------- #

def capability_path(registration) -> Path:
    return Path(registration.common_dir) / CAPABILITY_RELATIVE


def register(root, principal_id: str, *, denied_paths=()) -> AdapterCapability:
    """Authorize one principal for this repository's Claude Code adapter."""
    return write_capability(discover_repository(root), CAPABILITY_RELATIVE, version=ADAPTER_VERSION,
                            principal_id=principal_id, denied_paths=denied_paths)


def load_capability(registration) -> AdapterCapability:
    return read_capability(registration, CAPABILITY_RELATIVE, "Claude Code")


def declared_targets(tool_name: str, tool_input: dict) -> tuple[str, ...] | None:
    """Return declared raw target paths, or None for an opaque action."""
    field = DECLARED_EDIT_TOOLS.get(tool_name)
    if field is None:
        return None
    raw = (tool_input or {}).get(field)
    if not isinstance(raw, str) or not raw:
        return ()
    return (raw,)


# --------------------------------------------------------------------------- #
# Insertion evidence
# --------------------------------------------------------------------------- #
#
# Measured on Claude Code 2.1.289, a `SessionStart` hook that returns
# `additionalContext` produces two records: an `attachment` of type
# `hook_success` holding the hook process's raw stdout, exit code and duration,
# and an `attachment` of type `hook_additional_context` holding the text the
# client rendered into the conversation. Only the second is insertion. The first
# is written whether or not the client used the output, and is written for a
# failed hook too. A denied `PreToolUse` reaches the model as a `tool_result`
# part with `is_error` true, carrying the denied call's `tool_use_id`.
#
# Everything else -- hook stdout at any exit code, hook system messages, prose
# quoting a marker, records belonging to another session, truncated or malformed
# lines -- is insufficient evidence.

def transcript_roots() -> tuple[Path, ...]:
    """Directories the installed client keeps session transcripts under.

    Measured: with `CLAUDE_CONFIG_DIR` set, the client writes
    `<CLAUDE_CONFIG_DIR>/projects/<slug>/<session_id>.jsonl`; unset, it uses
    `~/.claude` as that config home.
    """
    roots = []
    configured = os.getenv("CLAUDE_CONFIG_DIR")
    if configured:
        roots.append(Path(configured))
    roots.append(Path.home() / ".claude")
    return tuple(root / "projects" for root in roots)


def authorized_transcript(transcript_path, native_session_id: str) -> Path | None:
    """This session's own transcript file, or None.

    `transcript_path` arrives in the hook payload, so it is a claim rather than a
    fact. The supported boundary is one file per session inside the client's
    project tree, named for that session. A path naming another session, or
    sitting outside that tree, is read as no evidence at all rather than as
    somebody else's evidence.
    """
    if not transcript_path or not native_session_id:
        return None
    try:
        candidate = Path(transcript_path).resolve()
    except (OSError, ValueError):
        return None
    if candidate.suffix != ".jsonl" or candidate.stem != native_session_id:
        return None
    for root in transcript_roots():
        try:
            if candidate.is_relative_to(root.resolve()):
                return candidate
        except (OSError, ValueError):
            continue
    return None


def _texts(value, out: list, depth: int = 0) -> None:
    """Collect the rendered strings of a record field, bounded."""
    if depth > 4 or len(out) >= 64:
        return
    if isinstance(value, str):
        out.append(value)
    elif isinstance(value, list):
        for entry in value[:64]:
            _texts(entry, out, depth + 1)
    elif isinstance(value, dict):
        for key in ("text", "content"):
            if key in value:
                _texts(value[key], out, depth + 1)


def insertion_evidence(blob: str, native_session_id: str) -> list[tuple[str, str | None, str]]:
    """Parse a transcript tail into `(evidence_kind, tool_use_id, inserted_text)`.

    Only the two measured insertion shapes qualify, and only for the intended
    session. The text is returned for one completeness check and not retained.
    """
    evidence: list[tuple[str, str | None, str]] = []
    if not blob or not native_session_id:
        return evidence
    seen = 0
    # A tail can begin mid-record; such a line fails to parse and is skipped.
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
        if not isinstance(record, dict) or record.get("sessionId") != native_session_id:
            continue

        attachment = record.get("attachment")
        if isinstance(attachment, dict):
            if (attachment.get("type") == INSERTION_ATTACHMENT
                    and attachment.get("hookEvent") == "SessionStart"):
                texts: list[str] = []
                _texts(attachment.get("content"), texts)
                evidence.extend(("additional_context", None, text) for text in texts)
            continue  # Any other attachment, `hook_success` included, is diagnostic.

        if record.get("type") != "user":
            continue
        message = record.get("message")
        if not isinstance(message, dict):
            continue
        parts = message.get("content")
        if not isinstance(parts, list):
            continue
        for part in parts[:64]:
            if not isinstance(part, dict) or part.get("type") != "tool_result":
                continue
            if part.get("is_error") is not True:
                continue  # A successful result did not carry a denial reason.
            texts = []
            _texts(part.get("content"), texts)
            tool_use_id = part.get("tool_use_id")
            tool_use_id = tool_use_id if isinstance(tool_use_id, str) else None
            evidence.append(("tool_denial", tool_use_id, "\n".join(texts)))
    return evidence


# --------------------------------------------------------------------------- #
# Adapter
# --------------------------------------------------------------------------- #

class ClaudeCodeAdapter(HostAdapter):
    """The Claude Code host: Edit/Write/MultiEdit/NotebookEdit, transcript records."""

    HARNESS = HARNESS
    SESSION_PREFIX = "cc-"
    ADAPTER_VERSION = ADAPTER_VERSION
    CONTEXT_LIMIT = HOST_CONTEXT_LIMIT

    def declared_targets(self, tool_name, tool_input):
        return declared_targets(tool_name, tool_input)

    def authorized_transcript(self, transcript_path, native_session_id):
        return authorized_transcript(transcript_path, native_session_id)

    def insertion_evidence(self, blob, native_session_id):
        return insertion_evidence(blob, native_session_id)


# --------------------------------------------------------------------------- #
# Hook installation that preserves existing configuration
# --------------------------------------------------------------------------- #

def hook_command(python_executable: str | None = None) -> str:
    """The hook client, which hands events to the long-lived hook service (`memex.hookd`)."""
    executable = python_executable or sys.executable
    return f'"{executable}" -I -S "{HOOK_CLIENT}" memex.integrations.claude_code'


def legacy_hook_command(python_executable: str | None = None) -> str:
    """The one-shot command installed before the hook service existed."""
    executable = python_executable or sys.executable
    return f'"{executable}" -m memex.integrations.claude_code'


def hook_settings(*, timeout: int = 20, python_executable: str | None = None) -> dict:
    """The hook entries memex needs, for review before installation."""
    entry = {"type": "command", "command": hook_command(python_executable), "timeout": timeout}
    return {
        "SessionStart": [{"hooks": [entry]}],
        "PreToolUse": [{"matcher": "Edit|Write|MultiEdit|NotebookEdit|Bash|mcp__memex_guard__guard_write",
                        "hooks": [entry]}],
        "PostToolUse": [{"matcher": "Edit|Write|MultiEdit|NotebookEdit|mcp__memex_guard__guard_write",
                         "hooks": [entry]}],
        "SessionEnd": [{"hooks": [entry]}],
    }


def _without(hooks: dict, commands: set[str]) -> None:
    """Drop entries running any of `commands`, and groups or events left empty."""
    for event in list(hooks):
        groups = []
        for group in hooks[event]:
            remaining = [h for h in group.get("hooks", []) if h.get("command") not in commands]
            if remaining:
                groups.append({**group, "hooks": remaining})
        if groups:
            hooks[event] = groups
        else:
            del hooks[event]


def install_hooks(settings_path, *, timeout: int = 20, python_executable: str | None = None) -> dict:
    """Compose memex hooks into a settings file, preserving everything else.

    Permission policy is never touched, existing hook entries are kept, and a
    repeated install does not duplicate memex entries. An earlier one-shot
    memex entry is replaced, so the two never both run.
    """
    path = Path(settings_path)
    settings = {}
    if path.exists():
        settings = json.loads(path.read_text(encoding="utf-8") or "{}")
    hooks = settings.setdefault("hooks", {})
    _without(hooks, {legacy_hook_command(python_executable)})
    command = hook_command(python_executable)
    for event, groups in hook_settings(timeout=timeout, python_executable=python_executable).items():
        existing = hooks.setdefault(event, [])
        if any(h.get("command") == command for group in existing for h in group.get("hooks", [])):
            continue
        existing.extend(groups)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return settings


def uninstall_hooks(settings_path, *, python_executable: str | None = None) -> dict:
    """Remove only memex entries, leaving other hooks and settings intact."""
    path = Path(settings_path)
    if not path.exists():
        return {}
    settings = json.loads(path.read_text(encoding="utf-8") or "{}")
    hooks = settings.get("hooks", {})
    _without(hooks, {hook_command(python_executable), legacy_hook_command(python_executable)})
    if not hooks:
        settings.pop("hooks", None)
    path.write_text(json.dumps(settings, indent=2), encoding="utf-8")
    return settings


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #

def main(argv=None) -> int:
    """Hook entry point. Never blocks the host on a memex failure."""
    return hook_main(ClaudeCodeAdapter, load_capability)


if __name__ == "__main__":
    sys.exit(main())
