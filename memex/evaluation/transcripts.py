"""Confirmed insertions: the memex text a client actually put into an agent session.

Protocol amendment A4 counts a correction only when its insertion into the
intended session is confirmed. The confirmation is the client's own record of
that session (Claude Code's transcript, Codex's rollout), read the same way for
every arm. A hook that ran, or text a hook emitted, is not confirmation.

Each insertion is {"at": epoch seconds, "kind": "context" | "tool_result", "text": str}:
`context` is hook context added to the session (SessionStart, PostToolUse);
`tool_result` is text returned in place of a tool's result, which is how a
denied action's reason reaches the agent.
"""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

MARKER = re.compile(r"memex(?: engineering context| notes for| warning|:| \(fresh retrieval\))")
CLAUDE_HOME = Path.home() / ".claude"
CODEX_HOME = Path.home() / ".codex"


def _epoch(stamp: str | None) -> float | None:
    if not stamp:
        return None
    return datetime.fromisoformat(stamp.replace("Z", "+00:00")).timestamp()


def _texts(content) -> list[str]:
    if isinstance(content, str):
        return [content]
    out = []
    for part in content or []:
        if isinstance(part, str):
            out.append(part)
        elif isinstance(part, dict):
            out += _texts(part.get("text") if "text" in part else part.get("content"))
    return out


def _keep(at, kind, texts) -> list[dict]:
    text = "\n".join(t for t in texts if t)
    return [{"at": at, "kind": kind, "text": text}] if at is not None and MARKER.search(text) else []


def claude_insertions(path: Path) -> list[dict]:
    found = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        at = _epoch(entry.get("timestamp"))
        attachment = entry.get("attachment")
        if isinstance(attachment, dict) and attachment.get("type") == "hook_additional_context":
            found += _keep(at, "context", _texts(attachment.get("content")))
        elif entry.get("type") == "user":
            for block in (entry.get("message") or {}).get("content") or []:
                if isinstance(block, dict) and block.get("type") == "tool_result":
                    found += _keep(at, "tool_result", _texts(block.get("content")))
    return found


def codex_insertions(path: Path) -> list[dict]:
    found = []
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        payload = entry.get("payload") or {}
        if entry.get("type") != "response_item":
            continue
        at = _epoch(entry.get("timestamp"))
        if payload.get("type") == "message" and payload.get("role") in ("developer", "user"):
            found += _keep(at, "context", _texts(payload.get("content")))
        elif str(payload.get("type", "")).endswith("_output"):
            output = payload.get("output")
            texts = _texts(output) if not isinstance(output, str) else [output]
            # Nested tool output arrives JSON-escaped inside the outer output.
            found += _keep(at, "tool_result", [t.replace("\\n", "\n").replace('\\"', '"') for t in texts])
    return found


def locate(host: str, session_id: str | None) -> Path | None:
    """The client's own record of `session_id`, if it exists."""
    if not session_id:
        return None
    if host == "claude":
        matches = sorted((CLAUDE_HOME / "projects").glob(f"*/{session_id}.jsonl"))
    else:
        matches = sorted((CODEX_HOME / "sessions").rglob(f"rollout-*{session_id}.jsonl"))
    return matches[0] if matches else None


def insertions(host: str, session_id: str | None) -> dict:
    path = locate(host, session_id)
    if path is None:
        return {"transcript": None, "insertions": []}
    read = claude_insertions if host == "claude" else codex_insertions
    return {"transcript": str(path), "insertions": read(path)}
