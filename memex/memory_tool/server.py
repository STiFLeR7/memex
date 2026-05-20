"""``MemexAsyncMemoryTool`` — Anthropic memory-tool backend.

Subclasses ``BetaAsyncAbstractMemoryTool`` and implements the six file
CRUD ops (``view``, ``create``, ``str_replace``, ``insert``, ``delete``,
``rename``).

Two zones (see ARCHITECTURE-v0.3.0.md §11.5):

* ``/memories/repos/<slug>/graph/**`` — read-only projection of the
  live Neo4j graph (per-session snapshot). Writes return a structured
  error string redirecting Claude to ``record_decision`` or to the
  scratch zone.
* ``/memories/scratch/<session-id>/**`` — read-write, SQLite-backed.

Path-traversal protection mirrors the reference impl: ``..`` rejected,
URL-encoded variants rejected, backslashes rejected, raw paths must
start with ``/memories``. The undeletable roots
(``/memories``, ``/memories/repos``, ``/memories/repos/<slug>``,
``/memories/repos/<slug>/graph``) raise ``ToolError`` on ``delete``.

All return-string formats match the Anthropic reference impl verbatim
so the model receives the same conformance signal regardless of backend.
"""

from __future__ import annotations

import logging
from typing import Optional

from typing_extensions import override

from anthropic.lib.tools._beta_builtin_memory_tool import BetaAsyncAbstractMemoryTool
from anthropic.lib.tools._beta_functions import ToolError
from anthropic.types.beta import (
    BetaMemoryTool20250818CreateCommand,
    BetaMemoryTool20250818DeleteCommand,
    BetaMemoryTool20250818InsertCommand,
    BetaMemoryTool20250818RenameCommand,
    BetaMemoryTool20250818StrReplaceCommand,
    BetaMemoryTool20250818ViewCommand,
)

from memex.memory_tool.projection import GraphProjection
from memex.memory_tool.safety import PathTraversalError, validate_memory_path
from memex.memory_tool.scratch import (
    LINE_NUMBER_WIDTH,
    ScratchStore,
)

logger = logging.getLogger(__name__)


_GRAPH_PREFIX = "/memories/repos/"
_SCRATCH_PREFIX = "/memories/scratch"
_PROTECTED_ROOTS = {
    "/memories",
    "/memories/repos",
    "/memories/scratch",
}


def _graph_zone_redirect(path: str) -> str:
    """Return the structured helpful-error string for graph-zone writes.

    Per ARCHITECTURE §11.5: redirect to ``record_decision`` for canonical
    writes or to ``/memories/scratch/`` for free-form notes.
    """
    return (
        f"Error: The path {path} is read-only. "
        f"Use record_decision via the MCP server "
        f"(e.g. memex.record_decision(text=...)) for canonical writes, "
        f"or use /memories/scratch/<session-id>/ for free-form notes."
    )


def _is_graph_zone(path: str) -> bool:
    if not path.startswith(_GRAPH_PREFIX):
        return False
    rest = path[len(_GRAPH_PREFIX):]
    parts = rest.split("/")
    if len(parts) < 2:
        return False
    # parts[1] must be "graph" to be in the graph zone proper.
    return parts[1] == "graph"


def _is_scratch_zone(path: str) -> bool:
    if path == _SCRATCH_PREFIX:
        return True
    return path.startswith(_SCRATCH_PREFIX + "/")


class MemexAsyncMemoryTool(BetaAsyncAbstractMemoryTool):
    """memex backend for Anthropic's ``memory_20250818`` tool surface.

    Construction is cheap (no Neo4j connection until the first graph-zone
    ``view``). Per the per-session snapshot semantics, instantiate one
    instance per session.
    """

    def __init__(
        self,
        repo_root: str,
        *,
        scratch_db_path: Optional[str] = None,
        projection: Optional[GraphProjection] = None,
        scratch: Optional[ScratchStore] = None,
        caller: str = "memory_tool",
    ) -> None:
        super().__init__()
        self.repo_root = repo_root
        self._projection = projection or GraphProjection(repo_root=repo_root)
        self._scratch = scratch or ScratchStore(
            repo_root=repo_root,
            db_path=scratch_db_path,
            caller=caller,
        )

    # ------------------------------------------------------------------
    # Path routing
    # ------------------------------------------------------------------

    def _route(self, path: str) -> str:
        """Validate path traversal + classify zone.

        Returns one of ``"graph"``, ``"scratch"``, ``"root"`` (for
        navigational paths like ``/memories``, ``/memories/repos``,
        ``/memories/repos/<slug>``). Raises ``ToolError`` for invalid
        paths.
        """
        try:
            canonical = validate_memory_path(path)
        except PathTraversalError as err:
            raise ToolError(str(err)) from err

        if canonical == "/memories":
            return "root"
        if canonical == "/memories/scratch":
            # The /memories/scratch directory itself is protected — only
            # /memories/scratch/<session-id>/... is writable. This matches
            # ARCHITECTURE §11.5: the four roots are undeletable.
            return "root"
        if canonical.startswith("/memories/scratch/"):
            return "scratch"
        if canonical == "/memories/repos":
            return "root"
        if canonical.startswith("/memories/repos/"):
            rest = canonical[len("/memories/repos/"):]
            parts = rest.split("/")
            if len(parts) == 1:
                # /memories/repos/<slug>
                return "root"
            # /memories/repos/<slug>/graph[/...]
            if parts[1] == "graph":
                return "graph"
            return "root"
        raise ToolError(
            f"Path {path} is outside the memex zone layout. "
            "Use /memories/repos/<slug>/graph/... or /memories/scratch/<session-id>/..."
        )

    # ------------------------------------------------------------------
    # View
    # ------------------------------------------------------------------

    @override
    async def view(
        self, command: BetaMemoryTool20250818ViewCommand
    ) -> str:
        zone = self._route(command.path)

        # Per-session graph snapshot: pin on the very first ``view``,
        # regardless of which zone it targets. This guarantees a
        # consistent timestamp for any subsequent graph-zone read in
        # the same session (ARCHITECTURE §11.5).
        self._projection._ensure_snapshot()

        if zone == "root":
            return await self._view_navigational(command.path)

        if zone == "graph":
            return await self._view_graph(command.path)

        # scratch
        view_range: Optional[tuple[int, int]] = None
        if command.view_range and len(command.view_range) == 2:
            view_range = (command.view_range[0], command.view_range[1])
        return self._scratch.view(command.path, view_range)

    async def _view_navigational(self, path: str) -> str:
        """Render the ``/memories``, ``/memories/repos``,
        ``/memories/repos/<slug>``, ``/memories/repos/<slug>/graph``
        and category listings as a ``du -h``-style directory."""
        path = path.rstrip("/")
        if path == "":
            path = "/memories"

        if path == "/memories":
            entries = [("repos", True), ("scratch", True)]
            return self._format_dir_listing(path, entries)

        if path == "/memories/repos":
            repos = await self._projection.list_repos()
            entries = [(s, True) for s in repos]
            return self._format_dir_listing(path, entries)

        # /memories/repos/<slug>
        parts = path[len("/memories/"):].split("/")
        if len(parts) == 2 and parts[0] == "repos":
            entries = [("graph", True)]
            return self._format_dir_listing(path, entries)

        # /memories/repos/<slug>/graph
        if len(parts) == 3 and parts[0] == "repos" and parts[2] == "graph":
            children = await self._projection.list_directory(
                "/".join(parts[:3])
            )
            return self._format_dir_listing(path, children)

        # /memories/repos/<slug>/graph/<category>
        if len(parts) == 4 and parts[0] == "repos" and parts[2] == "graph":
            children = await self._projection.list_directory(
                "/".join(parts[:4])
            )
            return self._format_dir_listing(path, children)

        raise ToolError(
            f"The path {path} does not exist. Please provide a valid path."
        )

    async def _view_graph(self, path: str) -> str:
        """View something in the graph zone — either a directory listing
        below ``/memories/repos/<slug>/graph`` or a file render."""
        # If the path ends with .md we treat it as a file.
        rel = path[len("/memories/"):]
        if path.endswith(".md") or path.endswith("recent.md"):
            try:
                body = await self._projection.read_node_file(rel)
            except FileNotFoundError as err:
                raise ToolError(
                    f"The path {path} does not exist. Please provide a valid path."
                ) from err
            return self._format_file_view(path, body)

        # Otherwise it's a directory.
        children = await self._projection.list_directory(rel)
        return self._format_dir_listing(path, children)

    # ------------------------------------------------------------------
    # Mutating ops
    # ------------------------------------------------------------------

    @override
    async def create(
        self, command: BetaMemoryTool20250818CreateCommand
    ) -> str:
        zone = self._route(command.path)
        if zone in ("graph", "root"):
            return _graph_zone_redirect(command.path)
        return self._scratch.create(command.path, command.file_text)

    @override
    async def str_replace(
        self, command: BetaMemoryTool20250818StrReplaceCommand
    ) -> str:
        zone = self._route(command.path)
        if zone in ("graph", "root"):
            return _graph_zone_redirect(command.path)
        return self._scratch.str_replace(
            command.path, command.old_str, command.new_str
        )

    @override
    async def insert(
        self, command: BetaMemoryTool20250818InsertCommand
    ) -> str:
        zone = self._route(command.path)
        if zone in ("graph", "root"):
            return _graph_zone_redirect(command.path)
        return self._scratch.insert(
            command.path, command.insert_line, command.insert_text
        )

    @override
    async def delete(
        self, command: BetaMemoryTool20250818DeleteCommand
    ) -> str:
        # First: the four hard-protected roots, regardless of zone.
        # _route canonicalizes the path (incl. trailing-slash normalization,
        # audit B4), so a trailing slash can't dodge the protected-root check.
        canonical = self._route(command.path)

        if canonical == "root":
            raise ToolError(
                f"Cannot delete the protected directory {command.path}. "
                "The /memories, /memories/repos, /memories/repos/<slug>, "
                "and /memories/repos/<slug>/graph directories are undeletable."
            )

        if canonical == "graph":
            return _graph_zone_redirect(command.path)

        return self._scratch.delete(command.path)

    @override
    async def rename(
        self, command: BetaMemoryTool20250818RenameCommand
    ) -> str:
        old_zone = self._route(command.old_path)
        new_zone = self._route(command.new_path)
        if old_zone != "scratch" or new_zone != "scratch":
            return _graph_zone_redirect(command.old_path)
        return self._scratch.rename(command.old_path, command.new_path)

    # ------------------------------------------------------------------
    # Formatting helpers (mirror Anthropic reference impl verbatim)
    # ------------------------------------------------------------------

    @staticmethod
    def _format_dir_listing(
        path: str, entries: list[tuple[str, bool]]
    ) -> str:
        """Render a virtual directory in the Anthropic reference format."""
        header = (
            f"Here're the files and directories up to 2 levels deep in "
            f"{path}, excluding hidden items:"
        )
        # Virtual directories have no real on-disk size; use 0B for both
        # the parent line and the children. Real scratch files come
        # through ``ScratchStore`` which sizes them properly.
        lines = [f"0B\t{path}"]
        for name, is_dir in sorted(entries, key=lambda x: x[0]):
            suffix = "/" if is_dir else ""
            lines.append(
                f"0B\t{path.rstrip('/')}/{name}{suffix}"
            )
        return f"{header}\n" + "\n".join(lines)

    @staticmethod
    def _format_file_view(path: str, content: str) -> str:
        lines = content.split("\n")
        numbered = [
            f"{str(i + 1).rjust(LINE_NUMBER_WIDTH)}\t{line}"
            for i, line in enumerate(lines)
        ]
        return (
            f"Here's the content of {path} with line numbers:\n"
            + "\n".join(numbered)
        )


# ---------------------------------------------------------------------------
# CLI entrypoint — wired up via memex/cli.py ``memory-tool serve``
# ---------------------------------------------------------------------------


async def run_memory_tool_serve(
    repo_root: str,
    transport: str = "stdio",
    port: int = 7464,
    listen_public: bool = False,
) -> None:
    """Serve the memex memory-tool backend.

    * ``transport="stdio"`` — print a one-line readiness banner and idle.
      The in-process import surface (``from memex.memory_tool import
      MemexAsyncMemoryTool``) is what most Python clients should use;
      this stdio entrypoint exists so ``memex memory-tool serve`` does
      something sensible on its own (acts as a long-lived health-checked
      reservation that can be killed cleanly).
    * ``transport="http"`` — start the FastAPI shim (see ``http.py``).
    """
    if transport == "http":
        from memex.memory_tool.http import run_http_memory_tool_server

        await run_http_memory_tool_server(
            repo_root=repo_root, port=port, listen_public=listen_public
        )
        return

    # stdio: print readiness, keep the process alive.
    import asyncio
    import sys

    sys.stderr.write(
        f"memex memory-tool ready: repo_root={repo_root} "
        f"(import MemexAsyncMemoryTool in-process; "
        f"this stdio shim idles until terminated)\n"
    )
    sys.stderr.flush()
    try:
        # Idle forever; signals terminate normally.
        while True:
            await asyncio.sleep(3600)
    except asyncio.CancelledError:
        return
