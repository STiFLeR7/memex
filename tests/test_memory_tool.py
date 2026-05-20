"""Tests for the memex memory-tool adapter (Move 1).

The tests cover both zones (projection + scratch) of
``MemexAsyncMemoryTool``. Projection tests use a fake graph client so
they never touch a live Neo4j; scratch tests use ``tmp_path`` for a
disposable SQLite DB.

Format assertions match Anthropic's reference impl verbatim (line
numbers right-justified to width 6, header strings exact). Mismatches
would surface to Claude as a regression even if our own tests passed,
so we mirror them on purpose.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest

from anthropic.lib.tools._beta_functions import ToolError
from anthropic.types.beta import (
    BetaMemoryTool20250818CreateCommand,
    BetaMemoryTool20250818DeleteCommand,
    BetaMemoryTool20250818InsertCommand,
    BetaMemoryTool20250818RenameCommand,
    BetaMemoryTool20250818StrReplaceCommand,
    BetaMemoryTool20250818ViewCommand,
)

from memex.memory_tool import MemexAsyncMemoryTool
from memex.memory_tool.projection import GraphProjection
from memex.memory_tool.scratch import LINE_NUMBER_WIDTH, ScratchStore
from memex.memory_tool.server import _graph_zone_redirect


# ---------------------------------------------------------------------------
# Fake graph client
# ---------------------------------------------------------------------------


class _FakeRecord:
    def __init__(self, data: dict):
        self._data = data

    def data(self) -> dict:
        return self._data


class _FakeResult:
    def __init__(self, rows: list[dict]):
        self.records = [_FakeRecord(r) for r in rows]


class _FakeDriver:
    """Stand-in for ``neo4j.AsyncDriver.execute_query``.

    Tests inject a callable ``responder(cypher, params) -> list[dict]``.
    """

    def __init__(self, responder):
        self.responder = responder
        self.calls: list[tuple[str, dict]] = []

    async def execute_query(self, cypher: str, params=None):
        params = params or {}
        self.calls.append((cypher, params))
        return _FakeResult(self.responder(cypher, params))


class _FakeClient:
    def __init__(self, responder):
        self.driver = _FakeDriver(responder)


def _make_tool(
    tmp_path,
    rows_by_type: Optional[dict[str, list[dict]]] = None,
    *,
    repo_root: Optional[str] = None,
) -> MemexAsyncMemoryTool:
    rows_by_type = rows_by_type or {}
    repo_root = repo_root or str(tmp_path / "repo")
    import os

    os.makedirs(repo_root, exist_ok=True)

    def responder(cypher: str, params: dict) -> list[dict]:
        # Repo discovery query.
        if "DISTINCT n.repo_path AS repo_path" in cypher:
            seen = set()
            for rows in rows_by_type.values():
                for r in rows:
                    if r.get("repo_path"):
                        seen.add(r["repo_path"])
            return [{"repo_path": rp} for rp in sorted(seen)]
        # Category listing.
        if "RETURN coalesce(n.text, n.name, n.path) AS title" in cypher:
            type_label = params.get("type")
            return [
                {
                    "title": r.get("text") or r.get("name") or "untitled",
                    "id": r.get("id") or r.get("uuid") or "x",
                    "repo_path": r.get("repo_path"),
                }
                for r in rows_by_type.get(type_label, [])
            ]
        # Single-node lookup.
        if "RETURN n{" in cypher and "AS node" in cypher:
            type_label = params.get("type")
            out = []
            for r in rows_by_type.get(type_label, []):
                node = dict(r)
                node.setdefault("id", r.get("id") or "x")
                node.setdefault(
                    "title",
                    r.get("text") or r.get("name") or "untitled",
                )
                out.append({"node": node})
            return out
        # Recent index.
        if "RETURN n.type AS type" in cypher:
            out = []
            for type_label, rows in rows_by_type.items():
                if type_label not in ("Decision", "Problem"):
                    continue
                for r in rows:
                    out.append(
                        {
                            "type": type_label,
                            "title": r.get("text") or r.get("name") or "untitled",
                            "created_at": r.get("created_at"),
                            "repo_path": r.get("repo_path"),
                        }
                    )
            return out
        return []

    async def client_factory() -> Any:
        return _FakeClient(responder)

    projection = GraphProjection(repo_root, client_factory=client_factory)
    scratch = ScratchStore(
        repo_root=repo_root,
        db_path=str(tmp_path / "scratch.db"),
    )
    return MemexAsyncMemoryTool(
        repo_root=repo_root, projection=projection, scratch=scratch
    )


# ---------------------------------------------------------------------------
# Inheritance smoke
# ---------------------------------------------------------------------------


def test_inherits_from_abstract_base():
    """MRO must contain ``BetaAsyncAbstractMemoryTool`` so the
    Anthropic SDK runner accepts the tool."""
    from anthropic.lib.tools._beta_builtin_memory_tool import (
        BetaAsyncAbstractMemoryTool,
    )

    assert BetaAsyncAbstractMemoryTool in MemexAsyncMemoryTool.__mro__


def test_to_dict_emits_memory_20250818(tmp_path):
    tool = _make_tool(tmp_path)
    payload = tool.to_dict()
    assert payload["type"] == "memory_20250818"
    assert payload["name"] == "memory"


# ---------------------------------------------------------------------------
# Navigational view tests (root, repos, etc.)
# ---------------------------------------------------------------------------


async def test_view_root_lists_repos(tmp_path):
    tool = _make_tool(
        tmp_path,
        rows_by_type={
            "Decision": [
                {
                    "text": "Use Neo4j over Postgres",
                    "repo_path": "/abs/path/to/memex",
                }
            ]
        },
    )
    cmd = BetaMemoryTool20250818ViewCommand.model_construct(
        command="view", path="/memories", view_range=None
    )
    out = await tool.view(cmd)
    assert "repos/" in out
    assert "scratch/" in out
    assert "Here're the files and directories up to 2 levels deep" in out


async def test_view_repos_lists_known_repo_slugs(tmp_path):
    tool = _make_tool(
        tmp_path,
        rows_by_type={
            "Decision": [
                {"text": "X", "repo_path": "/some/where/myproj"},
            ]
        },
    )
    cmd = BetaMemoryTool20250818ViewCommand.model_construct(
        command="view", path="/memories/repos", view_range=None
    )
    out = await tool.view(cmd)
    assert "myproj" in out


# ---------------------------------------------------------------------------
# Graph-zone reads
# ---------------------------------------------------------------------------


async def test_view_graph_decision_renders_markdown_with_frontmatter(tmp_path):
    repo_root = str(tmp_path / "myrepo")
    tool = _make_tool(
        tmp_path,
        rows_by_type={
            "Decision": [
                {
                    "text": "Use Neo4j + Graphiti over Postgres JSONB",
                    "rationale": "Multi-hop Cypher",
                    "scope": "global",
                    "validated": True,
                    "base_confidence": 0.6,
                    "repo_path": repo_root,
                    "uuid": "dec_1",
                }
            ]
        },
        repo_root=repo_root,
    )
    from pathlib import Path

    slug = Path(repo_root).resolve().name
    # Slugify is lowercase ASCII; the test repo name is already safe.
    from memex.memory_tool.serializer import slugify

    repo_slug = slugify(slug)
    file_slug = slugify("Use Neo4j + Graphiti over Postgres JSONB")
    cmd = BetaMemoryTool20250818ViewCommand.model_construct(
        command="view",
        path=f"/memories/repos/{repo_slug}/graph/decisions/{file_slug}.md",
        view_range=None,
    )
    out = await tool.view(cmd)
    assert "Here's the content of" in out
    assert "with line numbers:" in out
    # Frontmatter fences must render.
    assert "---" in out
    assert "type: Decision" in out
    # Heading must render.
    assert "Use Neo4j + Graphiti over Postgres JSONB" in out
    # Rationale XML block.
    assert "<rationale>" in out
    assert "Multi-hop Cypher" in out


# ---------------------------------------------------------------------------
# Format tests (line numbers / dir listing)
# ---------------------------------------------------------------------------


async def test_view_uses_line_numbered_format(tmp_path):
    """Anthropic reference impl uses right-justified line numbers padded
    to width 6 (``len(str(999999))``) followed by a tab. Match exactly."""
    tool = _make_tool(tmp_path)
    # Use scratch which actually stores arbitrary content.
    cmd_create = BetaMemoryTool20250818CreateCommand.model_construct(
        command="create",
        path="/memories/scratch/session-1/notes.md",
        file_text="line A\nline B\nline C",
    )
    await tool.create(cmd_create)

    cmd_view = BetaMemoryTool20250818ViewCommand.model_construct(
        command="view",
        path="/memories/scratch/session-1/notes.md",
        view_range=None,
    )
    out = await tool.view(cmd_view)
    expected_first = f"{str(1).rjust(LINE_NUMBER_WIDTH)}\tline A"
    expected_second = f"{str(2).rjust(LINE_NUMBER_WIDTH)}\tline B"
    expected_third = f"{str(3).rjust(LINE_NUMBER_WIDTH)}\tline C"
    assert expected_first in out
    assert expected_second in out
    assert expected_third in out
    # Sanity: width = 6 means "     1" not "1".
    assert "     1\tline A" in out


async def test_view_directory_uses_anthropic_size_format(tmp_path):
    """Reference impl uses ``_format_file_size`` (B/K/M/G, no decimal
    when whole), not ``du -h``. The exact header text is verbatim."""
    tool = _make_tool(tmp_path)
    cmd_create = BetaMemoryTool20250818CreateCommand.model_construct(
        command="create",
        path="/memories/scratch/session-1/a.md",
        file_text="hello",
    )
    await tool.create(cmd_create)

    cmd_view = BetaMemoryTool20250818ViewCommand.model_construct(
        command="view",
        path="/memories/scratch/session-1",
        view_range=None,
    )
    out = await tool.view(cmd_view)
    # Header verbatim per reference impl.
    assert "Here're the files and directories up to 2 levels deep in" in out
    # Listing rows use TAB separator, B/K/M/G units.
    assert "\t/memories/scratch/session-1/a.md" in out
    # First line should be a size-prefixed row for the parent path.
    first_line = out.splitlines()[1]
    assert "\t" in first_line


# ---------------------------------------------------------------------------
# Graph-zone write redirects
# ---------------------------------------------------------------------------


async def test_create_in_graph_zone_returns_redirect_error(tmp_path):
    tool = _make_tool(tmp_path)
    cmd = BetaMemoryTool20250818CreateCommand.model_construct(
        command="create",
        path="/memories/repos/x/graph/decisions/new.md",
        file_text="content",
    )
    out = await tool.create(cmd)
    assert out.startswith("Error: The path")
    assert "read-only" in out
    assert "record_decision" in out
    assert "/memories/scratch/" in out


async def test_str_replace_in_graph_zone_returns_redirect_error(tmp_path):
    tool = _make_tool(tmp_path)
    cmd = BetaMemoryTool20250818StrReplaceCommand.model_construct(
        command="str_replace",
        path="/memories/repos/x/graph/decisions/old.md",
        old_str="a",
        new_str="b",
    )
    out = await tool.str_replace(cmd)
    assert "read-only" in out


async def test_delete_in_graph_zone_returns_redirect_error(tmp_path):
    tool = _make_tool(tmp_path)
    cmd = BetaMemoryTool20250818DeleteCommand.model_construct(
        command="delete",
        path="/memories/repos/x/graph/decisions/old.md",
    )
    out = await tool.delete(cmd)
    assert "read-only" in out


# ---------------------------------------------------------------------------
# Scratch zone CRUD
# ---------------------------------------------------------------------------


async def test_create_in_scratch_zone_persists_to_sqlite(tmp_path):
    tool = _make_tool(tmp_path)
    cmd = BetaMemoryTool20250818CreateCommand.model_construct(
        command="create",
        path="/memories/scratch/sess-1/hello.md",
        file_text="hello world",
    )
    out = await tool.create(cmd)
    assert out == "File created successfully at: /memories/scratch/sess-1/hello.md"

    # Round-trip via view.
    cmd_view = BetaMemoryTool20250818ViewCommand.model_construct(
        command="view",
        path="/memories/scratch/sess-1/hello.md",
        view_range=None,
    )
    out_view = await tool.view(cmd_view)
    assert "hello world" in out_view


async def test_create_duplicate_in_scratch_raises(tmp_path):
    tool = _make_tool(tmp_path)
    cmd = BetaMemoryTool20250818CreateCommand.model_construct(
        command="create",
        path="/memories/scratch/sess-1/a.md",
        file_text="a",
    )
    await tool.create(cmd)
    with pytest.raises(ToolError) as exc:
        await tool.create(cmd)
    assert "already exists" in str(exc.value)


async def test_str_replace_unique_text_succeeds(tmp_path):
    tool = _make_tool(tmp_path)
    await tool.create(
        BetaMemoryTool20250818CreateCommand.model_construct(
            command="create",
            path="/memories/scratch/s/notes.md",
            file_text="alpha\nbeta\ngamma",
        )
    )
    out = await tool.str_replace(
        BetaMemoryTool20250818StrReplaceCommand.model_construct(
            command="str_replace",
            path="/memories/scratch/s/notes.md",
            old_str="beta",
            new_str="BETA",
        )
    )
    assert "The memory file has been edited" in out
    assert "BETA" in out


async def test_str_replace_nonunique_returns_helpful_error(tmp_path):
    """Anthropic-spec error format: lists matching line numbers
    verbatim and reminds the agent to make ``old_str`` unique."""
    tool = _make_tool(tmp_path)
    await tool.create(
        BetaMemoryTool20250818CreateCommand.model_construct(
            command="create",
            path="/memories/scratch/s/notes.md",
            file_text="x\nx\nx",
        )
    )
    with pytest.raises(ToolError) as exc:
        await tool.str_replace(
            BetaMemoryTool20250818StrReplaceCommand.model_construct(
                command="str_replace",
                path="/memories/scratch/s/notes.md",
                old_str="x",
                new_str="y",
            )
        )
    msg = str(exc.value)
    assert "Multiple occurrences of old_str" in msg
    assert "in lines: 1, 2, 3" in msg
    assert "Please ensure it is unique" in msg


async def test_str_replace_missing_text_returns_verbatim_error(tmp_path):
    tool = _make_tool(tmp_path)
    await tool.create(
        BetaMemoryTool20250818CreateCommand.model_construct(
            command="create",
            path="/memories/scratch/s/notes.md",
            file_text="hello",
        )
    )
    with pytest.raises(ToolError) as exc:
        await tool.str_replace(
            BetaMemoryTool20250818StrReplaceCommand.model_construct(
                command="str_replace",
                path="/memories/scratch/s/notes.md",
                old_str="absent",
                new_str="X",
            )
        )
    assert "did not appear verbatim" in str(exc.value)


async def test_insert_at_line_n_adds_to_scratch_file(tmp_path):
    tool = _make_tool(tmp_path)
    await tool.create(
        BetaMemoryTool20250818CreateCommand.model_construct(
            command="create",
            path="/memories/scratch/s/notes.md",
            file_text="line1\nline2\nline3",
        )
    )
    out = await tool.insert(
        BetaMemoryTool20250818InsertCommand.model_construct(
            command="insert",
            path="/memories/scratch/s/notes.md",
            insert_line=1,
            insert_text="INSERTED",
        )
    )
    assert "has been edited" in out

    out_view = await tool.view(
        BetaMemoryTool20250818ViewCommand.model_construct(
            command="view",
            path="/memories/scratch/s/notes.md",
            view_range=None,
        )
    )
    # Order after insert_line=1: line1, INSERTED, line2, line3.
    assert "INSERTED" in out_view


async def test_delete_scratch_file_succeeds(tmp_path):
    tool = _make_tool(tmp_path)
    await tool.create(
        BetaMemoryTool20250818CreateCommand.model_construct(
            command="create",
            path="/memories/scratch/s/a.md",
            file_text="a",
        )
    )
    out = await tool.delete(
        BetaMemoryTool20250818DeleteCommand.model_construct(
            command="delete", path="/memories/scratch/s/a.md"
        )
    )
    assert out == "Successfully deleted /memories/scratch/s/a.md"

    # Now the file is gone — view should raise.
    with pytest.raises(ToolError):
        await tool.view(
            BetaMemoryTool20250818ViewCommand.model_construct(
                command="view", path="/memories/scratch/s/a.md", view_range=None
            )
        )


async def test_delete_protected_path_returns_error(tmp_path):
    tool = _make_tool(tmp_path)
    for protected in [
        "/memories",
        "/memories/repos",
        "/memories/scratch",
    ]:
        with pytest.raises(ToolError) as exc:
            await tool.delete(
                BetaMemoryTool20250818DeleteCommand.model_construct(
                    command="delete", path=protected
                )
            )
        msg = str(exc.value)
        assert "Cannot delete the protected directory" in msg

    # /memories/repos/<slug> is also protected (root zone).
    with pytest.raises(ToolError):
        await tool.delete(
            BetaMemoryTool20250818DeleteCommand.model_construct(
                command="delete", path="/memories/repos/myrepo"
            )
        )


async def test_delete_protected_path_trailing_slash_still_protected(tmp_path):
    """Audit B4 — a trailing slash must not flip a protected root into a
    deletable zone (e.g. `/memories/scratch/` previously routed to 'scratch')."""
    tool = _make_tool(tmp_path)
    for protected in [
        "/memories/",
        "/memories/repos/",
        "/memories/scratch/",
    ]:
        with pytest.raises(ToolError) as exc:
            await tool.delete(
                BetaMemoryTool20250818DeleteCommand.model_construct(
                    command="delete", path=protected
                )
            )
        assert "Cannot delete the protected directory" in str(exc.value)


async def test_rename_within_scratch_succeeds(tmp_path):
    tool = _make_tool(tmp_path)
    await tool.create(
        BetaMemoryTool20250818CreateCommand.model_construct(
            command="create",
            path="/memories/scratch/s/old.md",
            file_text="hi",
        )
    )
    out = await tool.rename(
        BetaMemoryTool20250818RenameCommand.model_construct(
            command="rename",
            old_path="/memories/scratch/s/old.md",
            new_path="/memories/scratch/s/new.md",
        )
    )
    assert "Successfully renamed" in out

    # Old gone, new present.
    with pytest.raises(ToolError):
        await tool.view(
            BetaMemoryTool20250818ViewCommand.model_construct(
                command="view",
                path="/memories/scratch/s/old.md",
                view_range=None,
            )
        )
    out_view = await tool.view(
        BetaMemoryTool20250818ViewCommand.model_construct(
            command="view",
            path="/memories/scratch/s/new.md",
            view_range=None,
        )
    )
    assert "hi" in out_view


async def test_rename_across_zones_returns_redirect_error(tmp_path):
    tool = _make_tool(tmp_path)
    out = await tool.rename(
        BetaMemoryTool20250818RenameCommand.model_construct(
            command="rename",
            old_path="/memories/scratch/s/old.md",
            new_path="/memories/repos/x/graph/decisions/new.md",
        )
    )
    assert "read-only" in out


# ---------------------------------------------------------------------------
# Consistency model
# ---------------------------------------------------------------------------


async def test_per_session_snapshot_pinned_on_first_view(tmp_path):
    tool = _make_tool(tmp_path)
    assert tool._projection.snapshot_at is None
    await tool.view(
        BetaMemoryTool20250818ViewCommand.model_construct(
            command="view", path="/memories", view_range=None
        )
    )
    snap1 = tool._projection.snapshot_at
    assert snap1 is not None

    # Second view does not re-pin.
    await tool.view(
        BetaMemoryTool20250818ViewCommand.model_construct(
            command="view", path="/memories/repos", view_range=None
        )
    )
    assert tool._projection.snapshot_at == snap1


async def test_repo_prefix_isolates_multi_repo_views(tmp_path):
    """A node belonging to repo ``alpha`` must not surface under the
    ``beta`` slug. This is the multi-tenancy invariant of §11.5."""
    tool = _make_tool(
        tmp_path,
        rows_by_type={
            "Decision": [
                {"text": "A-decision", "repo_path": "/abs/alpha"},
                {"text": "B-decision", "repo_path": "/abs/beta"},
            ]
        },
    )
    out_alpha = await tool.view(
        BetaMemoryTool20250818ViewCommand.model_construct(
            command="view",
            path="/memories/repos/alpha/graph/decisions",
            view_range=None,
        )
    )
    out_beta = await tool.view(
        BetaMemoryTool20250818ViewCommand.model_construct(
            command="view",
            path="/memories/repos/beta/graph/decisions",
            view_range=None,
        )
    )
    assert "a-decision.md" in out_alpha
    assert "b-decision.md" not in out_alpha
    assert "b-decision.md" in out_beta
    assert "a-decision.md" not in out_beta


# ---------------------------------------------------------------------------
# Byte cap + audit
# ---------------------------------------------------------------------------


async def test_byte_cap_enforced_at_256k(tmp_path):
    tool = _make_tool(tmp_path)
    big = "x" * (256 * 1024 + 1)
    with pytest.raises(ToolError) as exc:
        await tool.create(
            BetaMemoryTool20250818CreateCommand.model_construct(
                command="create",
                path="/memories/scratch/s/big.md",
                file_text=big,
            )
        )
    assert "256 KB" in str(exc.value)


async def test_audit_log_records_writes(tmp_path):
    tool = _make_tool(tmp_path)
    await tool.create(
        BetaMemoryTool20250818CreateCommand.model_construct(
            command="create",
            path="/memories/scratch/s/a.md",
            file_text="alpha",
        )
    )
    await tool.str_replace(
        BetaMemoryTool20250818StrReplaceCommand.model_construct(
            command="str_replace",
            path="/memories/scratch/s/a.md",
            old_str="alpha",
            new_str="beta",
        )
    )
    log = tool._scratch.list_audit_log()
    ops = [row["op"] for row in log]
    assert "create" in ops
    assert "str_replace" in ops
    for row in log:
        assert row["caller"] == "memory_tool"


# ---------------------------------------------------------------------------
# Redirect helper exposed
# ---------------------------------------------------------------------------


def test_graph_zone_redirect_message_is_actionable():
    msg = _graph_zone_redirect("/memories/repos/x/graph/decisions/foo.md")
    assert "record_decision" in msg
    assert "/memories/scratch/" in msg
