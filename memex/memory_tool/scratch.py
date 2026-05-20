"""SQLite-backed read-write scratch zone for the memory tool.

Stores per-session free-form notes under
``/memories/scratch/<session-id>/...``. One SQLite database per memex
repo (``.memex/memory_tool_scratch.db``). Files are byte-capped at 256 KB
and must be UTF-8.

Every mutation appends to an ``audit_log`` row so the team can answer
"who wrote what, when" without scraping the SQLite file itself.

The reference Anthropic impl works on a real filesystem; we use SQLite
because (a) we want a single durable artifact per repo rather than
hundreds of tiny files in ``.memex/``, (b) it gives us atomic writes
plus the audit log without inventing a journaling format, and (c) it
sidesteps Windows umask/permission quirks that the reference impl
papers over with ``os.O_EXCL`` + ``0o600``.

All return strings match Anthropic's reference impl verbatim so the
model sees the same conformance signal regardless of backend.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from anthropic.lib.tools._beta_functions import ToolError

logger = logging.getLogger(__name__)


MAX_LINES = 999999
LINE_NUMBER_WIDTH = len(str(MAX_LINES))  # = 6
MAX_FILE_BYTES = 256 * 1024  # 256 KB


def _format_file_size(bytes_size: int) -> str:
    """Mirror of Anthropic reference ``_format_file_size`` (B/K/M/G)."""
    if bytes_size == 0:
        return "0B"
    k = 1024
    sizes = ["B", "K", "M", "G"]
    i = int(bytes_size.bit_length() - 1) // 10
    i = min(i, len(sizes) - 1)
    size = bytes_size / (k**i)
    if size == int(size):
        return f"{int(size)}{sizes[i]}"
    return f"{size:.1f}{sizes[i]}"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ScratchStore:
    """Per-repo SQLite-backed scratch zone.

    Schema:

    ```sql
    CREATE TABLE files (
        path TEXT PRIMARY KEY,
        content BLOB NOT NULL,
        size INTEGER NOT NULL,
        created_at TEXT NOT NULL,
        modified_at TEXT NOT NULL
    );
    CREATE TABLE audit_log (
        ts TEXT NOT NULL,
        op TEXT NOT NULL,
        path TEXT NOT NULL,
        caller TEXT
    );
    ```

    ``path`` is the virtual path *below* ``/memories/scratch/`` —
    e.g. ``"session-xyz/notes.md"``. The leading ``/memories/scratch/``
    is stripped before storage so renaming a session is a single UPDATE
    and listing is a single LIKE.
    """

    def __init__(
        self,
        repo_root: str,
        db_path: Optional[str] = None,
        *,
        max_bytes: int = MAX_FILE_BYTES,
        caller: str = "memory_tool",
    ) -> None:
        self.repo_root = Path(repo_root).resolve()
        self.max_bytes = max_bytes
        self.caller = caller
        if db_path is None:
            memex_dir = self.repo_root / ".memex"
            memex_dir.mkdir(parents=True, exist_ok=True)
            self.db_path = memex_dir / "memory_tool_scratch.db"
        else:
            self.db_path = Path(db_path)
            self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    # ------------------------------------------------------------------
    # Schema + connection
    # ------------------------------------------------------------------

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(str(self.db_path), timeout=5.0, isolation_level=None)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    def _init_schema(self) -> None:
        with self._connect() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS files (
                    path TEXT PRIMARY KEY,
                    content BLOB NOT NULL,
                    size INTEGER NOT NULL,
                    created_at TEXT NOT NULL,
                    modified_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_log (
                    ts TEXT NOT NULL,
                    op TEXT NOT NULL,
                    path TEXT NOT NULL,
                    caller TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_files_prefix ON files(path);
                """
            )

    # ------------------------------------------------------------------
    # Path helpers
    # ------------------------------------------------------------------

    SCRATCH_PREFIX = "/memories/scratch"

    @classmethod
    def _strip_prefix(cls, virtual_path: str) -> str:
        if not virtual_path.startswith(cls.SCRATCH_PREFIX):
            raise ToolError(
                f"Path {virtual_path} is not under {cls.SCRATCH_PREFIX}"
            )
        rest = virtual_path[len(cls.SCRATCH_PREFIX):].lstrip("/")
        return rest

    @classmethod
    def _is_directory_path(cls, virtual_path: str) -> bool:
        """A path is *directory-shaped* if its key prefix in ``files``
        matches multiple rows (or no rows exactly equal it)."""
        # Just structural: trailing slash counts as a directory request.
        return virtual_path.endswith("/")

    # ------------------------------------------------------------------
    # Audit
    # ------------------------------------------------------------------

    def _audit(self, conn: sqlite3.Connection, op: str, path: str) -> None:
        conn.execute(
            "INSERT INTO audit_log (ts, op, path, caller) VALUES (?, ?, ?, ?)",
            (_now_iso(), op, path, self.caller),
        )

    def list_audit_log(self) -> list[dict]:
        """Test/debug helper. Returns audit rows oldest-first."""
        with self._connect() as conn:
            cur = conn.execute(
                "SELECT ts, op, path, caller FROM audit_log ORDER BY ts ASC"
            )
            return [
                {"ts": r[0], "op": r[1], "path": r[2], "caller": r[3]}
                for r in cur.fetchall()
            ]

    # ------------------------------------------------------------------
    # Public ops
    # ------------------------------------------------------------------

    def view(
        self, virtual_path: str, view_range: Optional[tuple[int, int]] = None
    ) -> str:
        """Return either the line-numbered content of a file or a
        ``du -h``-style directory listing.

        Matches Anthropic's reference impl format exactly so the model
        sees the same conformance signal regardless of backend.
        """
        rel = self._strip_prefix(virtual_path)

        with self._connect() as conn:
            # File first.
            row = conn.execute(
                "SELECT content, size FROM files WHERE path = ?",
                (rel,),
            ).fetchone()

            if row is not None and not self._is_directory_path(virtual_path):
                content_bytes, _size = row
                try:
                    content = content_bytes.decode("utf-8")
                except UnicodeDecodeError as err:
                    raise ToolError(
                        f"The file {virtual_path} is not valid UTF-8."
                    ) from err
                return self._render_file_view(virtual_path, content, view_range)

            # Otherwise treat as directory listing.
            return self._render_directory_view(conn, virtual_path, rel)

    def _render_file_view(
        self, virtual_path: str, content: str, view_range: Optional[tuple[int, int]]
    ) -> str:
        lines = content.split("\n")
        if len(lines) > MAX_LINES:
            raise ToolError(
                f"File {virtual_path} exceeds maximum line limit of 999,999 lines."
            )
        display_lines = lines
        start_num = 1
        if view_range and len(view_range) == 2:
            start_line = max(1, view_range[0]) - 1
            end_line = len(lines) if view_range[1] == -1 else view_range[1]
            display_lines = lines[start_line:end_line]
            start_num = start_line + 1
        numbered = [
            f"{str(i + start_num).rjust(LINE_NUMBER_WIDTH)}\t{line}"
            for i, line in enumerate(display_lines)
        ]
        return (
            f"Here's the content of {virtual_path} with line numbers:\n"
            + "\n".join(numbered)
        )

    def _render_directory_view(
        self, conn: sqlite3.Connection, virtual_path: str, rel: str
    ) -> str:
        prefix = rel.rstrip("/")
        like = f"{prefix}/%" if prefix else "%"
        cur = conn.execute(
            "SELECT path, size FROM files WHERE path LIKE ? ORDER BY path ASC",
            (like,),
        )
        rows = cur.fetchall()

        if not rows:
            # Empty scratch is still a valid directory (could be brand new).
            if virtual_path.rstrip("/") in (
                "/memories/scratch",
                self.SCRATCH_PREFIX,
            ):
                header = (
                    f"Here're the files and directories up to 2 levels deep in "
                    f"{virtual_path}, excluding hidden items:"
                )
                return f"{header}\n0B\t{virtual_path}"
            raise ToolError(
                f"The path {virtual_path} does not exist. Please provide a valid path."
            )

        items: list[tuple[str, str]] = []
        seen_dirs: set[str] = set()
        total_size = 0

        for path, size in rows:
            total_size += size
            relpath = path[len(prefix) + 1:] if prefix else path
            parts = relpath.split("/")
            # Surface only first 2 levels per Anthropic's format.
            if len(parts) == 1:
                items.append((_format_file_size(size), relpath))
            elif len(parts) == 2:
                top = parts[0]
                if top not in seen_dirs:
                    seen_dirs.add(top)
                    # Sum size of all files under this dir at this scan level.
                    dir_size = sum(
                        s for p, s in rows if p.startswith(
                            (f"{prefix}/{top}/" if prefix else f"{top}/")
                        )
                    )
                    items.append((_format_file_size(dir_size), f"{top}/"))
                items.append((_format_file_size(size), relpath))
            else:
                # Deeper than 2 levels — still record the top-level dir.
                top = parts[0]
                if top not in seen_dirs:
                    seen_dirs.add(top)
                    dir_size = sum(
                        s for p, s in rows if p.startswith(
                            (f"{prefix}/{top}/" if prefix else f"{top}/")
                        )
                    )
                    items.append((_format_file_size(dir_size), f"{top}/"))

        header = (
            f"Here're the files and directories up to 2 levels deep in "
            f"{virtual_path}, excluding hidden items:"
        )
        # Dedupe (a dir can appear twice if we surfaced both it and a child).
        seen: set[tuple[str, str]] = set()
        unique_items: list[tuple[str, str]] = []
        for entry in items:
            if entry in seen:
                continue
            seen.add(entry)
            unique_items.append(entry)
        unique_items.sort(key=lambda x: x[1])

        lines = [f"{_format_file_size(total_size)}\t{virtual_path}"]
        lines.extend(
            f"{size}\t{virtual_path.rstrip('/')}/{path}"
            for size, path in unique_items
        )
        return f"{header}\n" + "\n".join(lines)

    # --------------------- mutating ops ------------------------------

    def create(self, virtual_path: str, file_text: str) -> str:
        rel = self._strip_prefix(virtual_path)
        if not rel:
            raise ToolError(f"Cannot create at scratch root: {virtual_path}")
        self._enforce_utf8_and_size(file_text, virtual_path)

        with self._connect() as conn:
            existing = conn.execute(
                "SELECT 1 FROM files WHERE path = ?", (rel,)
            ).fetchone()
            if existing is not None:
                raise ToolError(f"File {virtual_path} already exists")
            now = _now_iso()
            data = file_text.encode("utf-8")
            conn.execute(
                "INSERT INTO files (path, content, size, created_at, modified_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (rel, data, len(data), now, now),
            )
            self._audit(conn, "create", virtual_path)
        return f"File created successfully at: {virtual_path}"

    def str_replace(
        self, virtual_path: str, old_str: str, new_str: str
    ) -> str:
        rel = self._strip_prefix(virtual_path)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT content FROM files WHERE path = ?", (rel,)
            ).fetchone()
            if row is None:
                raise ToolError(
                    f"The path {virtual_path} does not exist. Please provide a valid path."
                )
            try:
                content = row[0].decode("utf-8")
            except UnicodeDecodeError as err:
                raise ToolError(
                    f"The file {virtual_path} is not valid UTF-8."
                ) from err

            count = content.count(old_str)
            if count == 0:
                raise ToolError(
                    f"No replacement was performed, old_str `{old_str}` did not appear verbatim in {virtual_path}."
                )
            if count > 1:
                matching_lines: list[int] = []
                start = 0
                while True:
                    pos = content.find(old_str, start)
                    if pos == -1:
                        break
                    matching_lines.append(content[:pos].count("\n") + 1)
                    start = pos + 1
                raise ToolError(
                    f"No replacement was performed. Multiple occurrences of old_str `{old_str}` in lines: "
                    f"{', '.join(map(str, matching_lines))}. Please ensure it is unique"
                )

            pos = content.find(old_str)
            changed_line_index = content[:pos].count("\n")
            new_content = content.replace(old_str, new_str)
            self._enforce_utf8_and_size(new_content, virtual_path)
            data = new_content.encode("utf-8")
            conn.execute(
                "UPDATE files SET content = ?, size = ?, modified_at = ? WHERE path = ?",
                (data, len(data), _now_iso(), rel),
            )
            self._audit(conn, "str_replace", virtual_path)

        new_lines = new_content.split("\n")
        context_start = max(0, changed_line_index - 2)
        context_end = min(len(new_lines), changed_line_index + 3)
        snippet = [
            f"{str(line_num).rjust(LINE_NUMBER_WIDTH)}\t{new_lines[line_num - 1]}"
            for line_num in range(context_start + 1, context_end + 1)
        ]
        return (
            "The memory file has been edited. Here is the snippet showing the change (with line numbers):\n"
            + "\n".join(snippet)
        )

    def insert(self, virtual_path: str, insert_line: int, insert_text: str) -> str:
        rel = self._strip_prefix(virtual_path)
        with self._connect() as conn:
            row = conn.execute(
                "SELECT content FROM files WHERE path = ?", (rel,)
            ).fetchone()
            if row is None:
                raise ToolError(
                    f"The path {virtual_path} does not exist. Please provide a valid path."
                )
            try:
                content = row[0].decode("utf-8")
            except UnicodeDecodeError as err:
                raise ToolError(
                    f"The file {virtual_path} is not valid UTF-8."
                ) from err
            lines = content.splitlines()
            if insert_line < 0 or insert_line > len(lines):
                raise ToolError(
                    f"Invalid `insert_line` parameter: {insert_line}. "
                    f"It should be within the range [0, {len(lines)}]."
                )
            lines.insert(insert_line, insert_text.rstrip("\n"))
            new_content = "\n".join(lines)
            if not new_content.endswith("\n"):
                new_content += "\n"
            self._enforce_utf8_and_size(new_content, virtual_path)
            data = new_content.encode("utf-8")
            conn.execute(
                "UPDATE files SET content = ?, size = ?, modified_at = ? WHERE path = ?",
                (data, len(data), _now_iso(), rel),
            )
            self._audit(conn, "insert", virtual_path)
        return f"The file {virtual_path} has been edited."

    def delete(self, virtual_path: str) -> str:
        rel = self._strip_prefix(virtual_path)
        with self._connect() as conn:
            # Try file delete first.
            cur = conn.execute("DELETE FROM files WHERE path = ?", (rel,))
            if cur.rowcount > 0:
                self._audit(conn, "delete", virtual_path)
                return f"Successfully deleted {virtual_path}"

            # Directory delete: remove everything under the prefix.
            prefix = rel.rstrip("/")
            like = f"{prefix}/%" if prefix else "%"
            cur = conn.execute("DELETE FROM files WHERE path LIKE ?", (like,))
            if cur.rowcount > 0:
                self._audit(conn, "delete", virtual_path)
                return f"Successfully deleted {virtual_path}"
        raise ToolError(f"The path {virtual_path} does not exist")

    def rename(self, old_virtual_path: str, new_virtual_path: str) -> str:
        old_rel = self._strip_prefix(old_virtual_path)
        new_rel = self._strip_prefix(new_virtual_path)
        if not new_rel:
            raise ToolError(
                f"Cannot rename to scratch root: {new_virtual_path}"
            )

        with self._connect() as conn:
            existing_dest = conn.execute(
                "SELECT 1 FROM files WHERE path = ?", (new_rel,)
            ).fetchone()
            if existing_dest is not None:
                raise ToolError(
                    f"The destination {new_virtual_path} already exists"
                )

            row = conn.execute(
                "SELECT 1 FROM files WHERE path = ?", (old_rel,)
            ).fetchone()
            if row is not None:
                conn.execute(
                    "UPDATE files SET path = ?, modified_at = ? WHERE path = ?",
                    (new_rel, _now_iso(), old_rel),
                )
                self._audit(conn, "rename", f"{old_virtual_path}->{new_virtual_path}")
                return (
                    f"Successfully renamed {old_virtual_path} to {new_virtual_path}"
                )

            # Directory rename.
            old_prefix = old_rel.rstrip("/")
            like = f"{old_prefix}/%"
            cur = conn.execute(
                "SELECT path FROM files WHERE path LIKE ?", (like,)
            )
            paths = [r[0] for r in cur.fetchall()]
            if not paths:
                raise ToolError(
                    f"The path {old_virtual_path} does not exist"
                )
            new_prefix = new_rel.rstrip("/")
            for p in paths:
                replaced = new_prefix + p[len(old_prefix):]
                # Reject if the destination would collide.
                clash = conn.execute(
                    "SELECT 1 FROM files WHERE path = ?", (replaced,)
                ).fetchone()
                if clash is not None:
                    raise ToolError(
                        f"The destination {new_virtual_path} already exists"
                    )
                conn.execute(
                    "UPDATE files SET path = ?, modified_at = ? WHERE path = ?",
                    (replaced, _now_iso(), p),
                )
            self._audit(conn, "rename", f"{old_virtual_path}->{new_virtual_path}")
            return (
                f"Successfully renamed {old_virtual_path} to {new_virtual_path}"
            )

    # ------------------------------------------------------------------
    # Internal
    # ------------------------------------------------------------------

    def _enforce_utf8_and_size(self, text: str, virtual_path: str) -> None:
        try:
            data = text.encode("utf-8")
        except UnicodeEncodeError as err:
            raise ToolError(
                f"File content for {virtual_path} must be UTF-8 encodable"
            ) from err
        if len(data) > self.max_bytes:
            raise ToolError(
                f"File {virtual_path} exceeds the 256 KB scratch byte cap "
                f"({len(data)} bytes > {self.max_bytes} bytes). "
                "Trim the content or split across multiple files."
            )
