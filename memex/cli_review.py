"""``memex review`` — terminal UI for validating watcher-synthesised decisions.

ARCHITECTURE §9 / Phase 8 spec:

- Order decisions **lowest-computed-confidence first** (Prodigy / Label Studio
  active-learning convention — maximises information gain per annotation).
- Do NOT display the LLM's numeric confidence next to the y/n prompt
  (ACL 2025 *Just Put a Human in the Loop?* — showing confidence biases
  annotators 81-87% toward "y").
- Keys: ``y`` validate, ``n`` delete, ``e`` edit, ``s`` skip, ``q`` quit.
- Non-blocking — empty queue exits gracefully.

Implementation note: ``rich.prompt.Prompt.ask`` is synchronous and blocks the
event loop. That is acceptable for a TUI; we wrap it in ``asyncio.to_thread``
so the surrounding async code (Neo4j driver) keeps working.
"""

from __future__ import annotations

import asyncio
import logging
import os
import subprocess
import tempfile
from datetime import datetime, timezone
from typing import Any

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt

from memex.graph.client import get_graph_client
from memex.graph.confidence import current_confidence

logger = logging.getLogger(__name__)

_VALID_KEYS = ["y", "n", "e", "s", "q"]


async def _fetch_pending_decisions(repo_root: str) -> list[dict[str, Any]]:
    """Return every Decision node that has ``validated=False`` and is not
    soft-excluded, ordered lowest-computed-confidence first."""
    client = await get_graph_client()
    query = """
    MATCH (d:Entity)
    WHERE (d.type = 'Decision' OR d.name CONTAINS 'Decision')
      AND coalesce(d.validated, false) = false
      AND coalesce(d.excluded, false) = false
    OPTIONAL MATCH (d)-[:MOTIVATES|RELATES_TO|MENTIONS]-(m:Entity)
    WHERE coalesce(m.type, '') = 'Module' OR m.name ENDS WITH '.py'
    WITH d, collect(DISTINCT m.name) AS modules
    RETURN
        coalesce(d.uuid, elementId(d)) AS id,
        d.name                          AS text,
        coalesce(d.summary, '')         AS rationale,
        coalesce(d.source_commit, '')   AS source_commit,
        coalesce(d.source, 'watcher')   AS source,
        coalesce(d.corroborated, false) AS corroborated,
        coalesce(d.validated, false)    AS validated,
        coalesce(d.base_confidence, d.confidence, 0.6) AS base_confidence,
        coalesce(d.last_reinforced_at, d.created_at)   AS last_reinforced_at,
        d.created_at                    AS created_at,
        modules                         AS modules
    """
    try:
        res = await client.driver.execute_query(query)
    except Exception:
        logger.error("Failed to fetch pending decisions", exc_info=True)
        return []

    rows: list[dict[str, Any]] = [dict(r.data()) for r in res.records]

    # Compute confidence in Python (matches confidence.py semantics exactly)
    # and sort ascending.
    for row in rows:
        row["_computed_confidence"] = current_confidence(row)
    rows.sort(key=lambda r: r["_computed_confidence"])
    return rows


async def _set_validated(decision_id: str) -> None:
    """Flip ``validated=True``, stamp ``validated_at`` + ``last_reinforced_at``."""
    client = await get_graph_client()
    now = datetime.now(timezone.utc)
    query = """
    MATCH (d:Entity)
    WHERE d.uuid = $id OR elementId(d) = $id
    SET d.validated          = true,
        d.validated_at       = $now,
        d.last_reinforced_at = $now,
        d.updated_at         = $now
    """
    try:
        await client.driver.execute_query(query, params={"id": decision_id, "now": now})
    except Exception:
        logger.error("Failed to validate decision %s", decision_id, exc_info=True)
        raise


async def _soft_delete(decision_id: str) -> None:
    """Soft-delete: set ``excluded=True`` and log an audit timestamp.
    A destructive ``DETACH DELETE`` is avoided so the audit trail survives."""
    client = await get_graph_client()
    now = datetime.now(timezone.utc)
    query = """
    MATCH (d:Entity)
    WHERE d.uuid = $id OR elementId(d) = $id
    SET d.excluded    = true,
        d.excluded_at = $now,
        d.updated_at  = $now
    """
    try:
        await client.driver.execute_query(query, params={"id": decision_id, "now": now})
    except Exception:
        logger.error("Failed to soft-delete decision %s", decision_id, exc_info=True)
        raise


async def _update_text(decision_id: str, new_text: str) -> None:
    """Update the decision text (after ``$EDITOR`` round-trip).
    Lifts ``last_reinforced_at`` since the human just engaged with the node."""
    client = await get_graph_client()
    now = datetime.now(timezone.utc)
    query = """
    MATCH (d:Entity)
    WHERE d.uuid = $id OR elementId(d) = $id
    SET d.name               = $text,
        d.last_reinforced_at = $now,
        d.updated_at         = $now
    """
    try:
        await client.driver.execute_query(
            query, params={"id": decision_id, "text": new_text, "now": now}
        )
    except Exception:
        logger.error("Failed to update decision text for %s", decision_id, exc_info=True)
        raise


def _open_editor(initial_text: str) -> str:
    """Open ``$EDITOR`` (or ``$VISUAL``, falling back to ``notepad`` on Windows
    and ``vi`` elsewhere) on a temp file and return the edited contents.

    Mirrors the ``git commit --amend`` pattern. Synchronous on purpose —
    editors are interactive."""
    editor = os.environ.get("VISUAL") or os.environ.get("EDITOR")
    if not editor:
        editor = "notepad" if os.name == "nt" else "vi"

    fd, path = tempfile.mkstemp(prefix="memex-review-", suffix=".txt", text=True)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(initial_text)
        subprocess.run([editor, path], check=False)
        with open(path, "r", encoding="utf-8") as f:
            edited = f.read()
        return edited.strip()
    finally:
        try:
            os.unlink(path)
        except OSError:
            pass


def _render_panel(console: Console, idx: int, total: int, decision: dict[str, Any]) -> None:
    """Render one decision card. Confidence is deliberately NOT shown."""
    commit = decision.get("source_commit") or "(no commit)"
    short_sha = commit[:8] if commit else "(no commit)"
    text = decision.get("text") or "(no text)"
    modules = decision.get("modules") or []
    module_line = ", ".join(modules) if modules else "(no module)"
    source = decision.get("source") or "watcher"
    validated = bool(decision.get("validated"))
    corroborated = bool(decision.get("corroborated"))

    body_lines = [
        "[bold]Synthesised decision:[/bold]",
        f"  {text}",
        "",
        f"[dim]Module:[/dim]       {module_line}",
        f"[dim]Source:[/dim]       {source}",
        f"[dim]Validated:[/dim]    {'yes' if validated else 'no'}",
        f"[dim]Corroborated:[/dim] {'yes' if corroborated else 'no'}",
    ]
    rationale = decision.get("rationale")
    if rationale:
        body_lines.append("")
        body_lines.append(f"[dim]Rationale:[/dim] {rationale}")

    console.print(
        Panel(
            "\n".join(body_lines),
            title=f"[{idx}/{total}] decision from commit {short_sha}",
            border_style="cyan",
        )
    )


async def _prompt(console: Console) -> str:
    """Render the keystroke prompt. No confidence shown next to the choices.

    Wrapped in ``asyncio.to_thread`` because ``rich.prompt.Prompt.ask`` blocks
    on stdin."""
    def _ask() -> str:
        return Prompt.ask(
            "[y]validate  [n]delete  [e]edit  [s]skip  [q]quit",
            choices=_VALID_KEYS,
            default="s",
            show_choices=False,
            show_default=False,
        )

    return await asyncio.to_thread(_ask)


async def run_review_command(repo_root: str) -> None:
    """Open the ``memex review`` TUI.

    Exits gracefully on an empty queue. Lowest-computed-confidence-first ordering;
    confidence numbers are deliberately hidden from the prompt to avoid the
    annotation-anchoring bias documented in ACL 2025.
    """
    console = Console()
    try:
        decisions = await _fetch_pending_decisions(repo_root)
    except Exception:
        console.print("[red]Failed to fetch pending decisions from Neo4j.[/red]")
        logger.error("review: fetch failed", exc_info=True)
        return

    if not decisions:
        console.print("[green]No pending decisions to review. Graph is up to date.[/green]")
        return

    total = len(decisions)
    console.print(f"\n[bold]memex review[/bold] — {total} decision(s) pending validation\n")

    for idx, decision in enumerate(decisions, start=1):
        _render_panel(console, idx, total, decision)
        try:
            choice = await _prompt(console)
        except (KeyboardInterrupt, EOFError):
            console.print("\n[yellow]Interrupted — exiting review.[/yellow]")
            return

        decision_id = decision.get("id")
        if not decision_id:
            console.print("[red]Decision has no id; skipping.[/red]")
            continue

        if choice == "q":
            console.print("[yellow]Quitting review.[/yellow]")
            return

        if choice == "s":
            console.print("[dim]skipped.[/dim]\n")
            continue

        if choice == "y":
            try:
                await _set_validated(decision_id)
                console.print("[green]validated.[/green]\n")
            except Exception:
                console.print("[red]failed to validate — see log.[/red]\n")
            continue

        if choice == "n":
            try:
                await _soft_delete(decision_id)
                console.print("[yellow]excluded.[/yellow]\n")
            except Exception:
                console.print("[red]failed to exclude — see log.[/red]\n")
            continue

        if choice == "e":
            current_text = decision.get("text") or ""
            try:
                new_text = await asyncio.to_thread(_open_editor, current_text)
            except Exception:
                console.print("[red]failed to open editor — see log.[/red]\n")
                logger.error("review: editor failed", exc_info=True)
                continue
            if not new_text or new_text == current_text:
                console.print("[dim]no change.[/dim]\n")
                continue
            try:
                await _update_text(decision_id, new_text)
                console.print("[green]updated.[/green]\n")
            except Exception:
                console.print("[red]failed to update — see log.[/red]\n")
            continue

    console.print("\n[green]Review complete.[/green]")
