"""Durable local generation journal. Graph commit must precede acknowledgement."""
from contextlib import contextmanager
from pathlib import Path
import sqlite3

from memex.context.revision import RepositoryView


class ChangeJournal:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connection() as connection:
            connection.executescript("""
                CREATE TABLE IF NOT EXISTS worktrees (
                    worktree_id TEXT PRIMARY KEY, repo_id TEXT NOT NULL,
                    head_commit TEXT, manifest_hash TEXT NOT NULL,
                    content_generation INTEGER NOT NULL, indexed_generation INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS views (
                    view_id TEXT PRIMARY KEY, repo_id TEXT NOT NULL, worktree_id TEXT NOT NULL,
                    head_commit TEXT, manifest_hash TEXT NOT NULL,
                    generation INTEGER NOT NULL, status TEXT NOT NULL DEFAULT 'pending'
                );
            """)

    @contextmanager
    def _connection(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    @staticmethod
    def _view(row) -> RepositoryView:
        return RepositoryView(row["repo_id"], row["worktree_id"], row["head_commit"],
                              row["content_generation"], row["indexed_generation"], row["manifest_hash"])

    def observe(self, registration, capture) -> RepositoryView:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            previous = connection.execute("SELECT * FROM worktrees WHERE worktree_id=?", (registration.worktree_id,)).fetchone()
            if previous and (previous["head_commit"], previous["manifest_hash"]) == (capture.head_commit, capture.manifest_hash):
                return self._view(previous)
            generation = previous["content_generation"] + 1 if previous else 1
            indexed = previous["indexed_generation"] if previous else 0
            view = RepositoryView(registration.repo_id, registration.worktree_id,
                                  capture.head_commit, generation, indexed, capture.manifest_hash)
            connection.execute("INSERT OR REPLACE INTO worktrees VALUES (?,?,?,?,?,?)", (
                view.worktree_id, view.repo_id, view.head_commit, view.manifest_hash, generation, indexed,
            ))
            connection.execute("INSERT INTO views VALUES (?,?,?,?,?,?,'pending')", (
                view.view_id, view.repo_id, view.worktree_id, view.head_commit, view.manifest_hash, generation,
            ))
            return view

    def current(self, worktree_id: str) -> RepositoryView | None:
        with self._connection() as connection:
            row = connection.execute("SELECT * FROM worktrees WHERE worktree_id=?", (worktree_id,)).fetchone()
            return self._view(row) if row else None

    def pending(self, worktree_id: str) -> list[str]:
        with self._connection() as connection:
            return [row[0] for row in connection.execute(
                "SELECT view_id FROM views WHERE worktree_id=? AND status='pending' ORDER BY generation", (worktree_id,),
            )]

    def acknowledge(self, view_id: str) -> None:
        with self._connection() as connection:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute("SELECT * FROM views WHERE view_id=?", (view_id,)).fetchone()
            if row is None:
                raise ValueError("unknown journal view")
            connection.execute("UPDATE views SET status='complete' WHERE view_id=?", (view_id,))
            connection.execute("UPDATE views SET status='superseded' WHERE worktree_id=? AND generation<? AND status='pending'", (row["worktree_id"], row["generation"]))
            connection.execute("UPDATE worktrees SET indexed_generation=MAX(indexed_generation,?) WHERE worktree_id=?", (row["generation"], row["worktree_id"]))
