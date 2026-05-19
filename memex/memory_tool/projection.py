"""Read-only Neo4j → memory-tool-file projection.

Pins a per-instance Graphiti timestamp on the first ``view`` call against
the graph zone so subsequent reads see a consistent snapshot (the watcher
may be mid-flight).

All queries filter ``WHERE expired_at IS NULL`` (the latent v0.2.0 bug
fix per ARCHITECTURE §1). When a snapshot is pinned, the filter becomes
``WHERE expired_at IS NULL OR expired_at > $snapshot``.

The projection is intentionally read-only — writes go through the MCP
``record_decision`` / ``record_problem`` verbs. See server.py for the
helpful-error string returned on graph-zone writes.

The projection module never opens the Neo4j driver itself; it delegates
to ``memex.graph.client.get_graph_client()``. Tests inject a fake client
via the ``client_factory`` constructor argument.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any, Callable, Awaitable, Optional

from memex.memory_tool import serializer

logger = logging.getLogger(__name__)


class GraphProjection:
    """Per-session read-only projection of the live Neo4j graph.

    A single instance corresponds to a single ``MemexAsyncMemoryTool``
    instance, i.e. a single Claude session. The snapshot pin happens
    lazily on the first ``view`` so a session that never reads from
    the graph zone never pays the bookkeeping cost.
    """

    def __init__(
        self,
        repo_root: str,
        *,
        client_factory: Optional[Callable[[], Awaitable[Any]]] = None,
    ) -> None:
        self._repo_root = repo_root
        self._snapshot_at: Optional[datetime] = None
        # Allow tests to inject a fake client without monkeypatching the
        # global singleton.
        if client_factory is None:
            from memex.graph.client import get_graph_client

            self._client_factory = get_graph_client
        else:
            self._client_factory = client_factory

    @property
    def snapshot_at(self) -> Optional[datetime]:
        return self._snapshot_at

    def _ensure_snapshot(self) -> datetime:
        """Lazy snapshot pin. Idempotent."""
        if self._snapshot_at is None:
            self._snapshot_at = datetime.now(timezone.utc)
        return self._snapshot_at

    # ------------------------------------------------------------------
    # Driver helper
    # ------------------------------------------------------------------

    async def _driver(self):
        client = await self._client_factory()
        return client.driver

    async def _run(self, cypher: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        driver = await self._driver()
        try:
            result = await driver.execute_query(cypher, params=params)
        except Exception as e:  # pragma: no cover - network/db failures
            logger.exception("projection query failed: %s", e)
            return []
        return [r.data() for r in result.records]

    # ------------------------------------------------------------------
    # Repo discovery
    # ------------------------------------------------------------------

    async def list_repos(self) -> list[str]:
        """Return repo slugs visible in the graph (distinct ``repo_path``).

        Slug = the directory-name component, slugified. We always include
        the current ``repo_root`` even if Neo4j has nothing for it yet so
        Claude can navigate into an empty repo zone.
        """
        self._ensure_snapshot()
        cypher = (
            "MATCH (n:Entity) "
            "WHERE n.repo_path IS NOT NULL "
            "RETURN DISTINCT n.repo_path AS repo_path"
        )
        rows = await self._run(cypher, {})
        from pathlib import PurePath

        slugs: set[str] = set()
        for row in rows:
            repo_path = row.get("repo_path")
            if not repo_path:
                continue
            name = PurePath(repo_path).name or repo_path
            slugs.add(serializer.slugify(name))
        # Always include the local repo_root so a fresh install isn't empty.
        from pathlib import Path as _Path

        local_name = _Path(self._repo_root).resolve().name or "repo"
        slugs.add(serializer.slugify(local_name))
        return sorted(slugs)

    # ------------------------------------------------------------------
    # Directory & file rendering for the memory-tool surface
    # ------------------------------------------------------------------

    async def list_directory(self, virtual_path: str) -> list[tuple[str, bool]]:
        """List children of a graph-zone virtual directory.

        Returns ``[(name, is_dir), ...]``. ``virtual_path`` is the path
        below ``/memories``: e.g. ``""`` (root), ``"repos"``,
        ``"repos/<slug>"``, ``"repos/<slug>/graph"``,
        ``"repos/<slug>/graph/decisions"``.
        """
        self._ensure_snapshot()
        parts = [p for p in virtual_path.strip("/").split("/") if p]

        if not parts or parts == [""]:
            # /memories/ → ['repos/', 'scratch/']
            return [("repos", True), ("scratch", True)]

        if parts == ["repos"]:
            repos = await self.list_repos()
            return [(s, True) for s in repos]

        if len(parts) == 2 and parts[0] == "repos":
            # /memories/repos/<slug>/ → ['graph/']
            return [("graph", True)]

        if len(parts) == 3 and parts[0] == "repos" and parts[2] == "graph":
            # /memories/repos/<slug>/graph/ → 4 category dirs + recent.md
            return [
                ("clusters", True),
                ("decisions", True),
                ("modules", True),
                ("problems", True),
                ("recent.md", False),
            ]

        if len(parts) == 4 and parts[0] == "repos" and parts[2] == "graph":
            slug = parts[1]
            category = parts[3]
            return await self._list_category(slug, category)

        return []

    async def _list_category(self, slug: str, category: str) -> list[tuple[str, bool]]:
        """List nodes of a particular category for a repo, as ``<slug>.md``."""
        type_label = {
            "decisions": "Decision",
            "problems": "Problem",
            "modules": "Module",
            "clusters": "Cluster",
        }.get(category)
        if type_label is None:
            return []

        cypher = (
            "MATCH (n:Entity) "
            "WHERE n.type = $type "
            "  AND (n.expired_at IS NULL OR n.expired_at > $snapshot) "
            "RETURN coalesce(n.text, n.name, n.path) AS title, "
            "       coalesce(n.uuid, elementId(n)) AS id, "
            "       n.repo_path AS repo_path "
            "ORDER BY n.created_at DESC "
            "LIMIT 200"
        )
        rows = await self._run(
            cypher,
            {"type": type_label, "snapshot": self._ensure_snapshot()},
        )
        out: list[tuple[str, bool]] = []
        for row in rows:
            if not self._repo_matches_slug(row.get("repo_path"), slug):
                continue
            title = row.get("title") or "untitled"
            filename = f"{serializer.slugify(title)}.md"
            out.append((filename, False))
        # Sort + dedupe — distinct titles slugify to the same filename;
        # we want a stable listing.
        seen: dict[str, bool] = {}
        for name, is_dir in out:
            seen[name] = is_dir
        return sorted(((n, d) for n, d in seen.items()), key=lambda x: x[0])

    def _repo_matches_slug(self, repo_path: Optional[str], slug: str) -> bool:
        if not repo_path:
            # If a node has no repo_path it's only included under the local slug.
            from pathlib import Path as _Path

            local_slug = serializer.slugify(
                _Path(self._repo_root).resolve().name or "repo"
            )
            return slug == local_slug
        from pathlib import PurePath

        name = PurePath(repo_path).name or repo_path
        return serializer.slugify(name) == slug

    # ------------------------------------------------------------------
    # File rendering
    # ------------------------------------------------------------------

    async def read_node_file(self, virtual_path: str) -> str:
        """Render a single node file at ``virtual_path``.

        ``virtual_path`` is ``repos/<slug>/graph/<category>/<file>.md``.
        Returns the rendered Markdown body.

        Raises ``FileNotFoundError`` if no matching node exists.
        """
        self._ensure_snapshot()
        parts = [p for p in virtual_path.strip("/").split("/") if p]

        # recent.md
        if (
            len(parts) == 4
            and parts[0] == "repos"
            and parts[2] == "graph"
            and parts[3] == "recent.md"
        ):
            return await self._render_recent(parts[1])

        if len(parts) != 5 or parts[0] != "repos" or parts[2] != "graph":
            raise FileNotFoundError(virtual_path)

        slug = parts[1]
        category = parts[3]
        filename = parts[4]

        if not filename.endswith(".md"):
            raise FileNotFoundError(virtual_path)

        target_slug = filename[: -len(".md")]
        type_label = {
            "decisions": "Decision",
            "problems": "Problem",
            "modules": "Module",
            "clusters": "Cluster",
        }.get(category)
        if type_label is None:
            raise FileNotFoundError(virtual_path)

        node = await self._find_node_by_slug(type_label, slug, target_slug)
        if node is None:
            raise FileNotFoundError(virtual_path)

        if category == "decisions":
            return serializer.decision_to_markdown(node)
        if category == "problems":
            return serializer.problem_to_markdown(node)
        if category == "modules":
            return serializer.module_to_markdown(node)
        if category == "clusters":
            return serializer.cluster_to_markdown(node)
        raise FileNotFoundError(virtual_path)

    async def _find_node_by_slug(
        self, type_label: str, repo_slug: str, target_slug: str
    ) -> Optional[dict[str, Any]]:
        cypher = (
            "MATCH (n:Entity) "
            "WHERE n.type = $type "
            "  AND (n.expired_at IS NULL OR n.expired_at > $snapshot) "
            "RETURN n{.*, id: coalesce(n.uuid, elementId(n)), title: coalesce(n.text, n.name, n.path), repo_path: n.repo_path} AS node "
            "LIMIT 500"
        )
        rows = await self._run(
            cypher,
            {"type": type_label, "snapshot": self._ensure_snapshot()},
        )
        for row in rows:
            node = row.get("node") or {}
            if not self._repo_matches_slug(node.get("repo_path"), repo_slug):
                continue
            title = node.get("title") or node.get("text") or node.get("name") or ""
            if serializer.slugify(title) == target_slug:
                return node
        return None

    async def _render_recent(self, repo_slug: str) -> str:
        """Render a top-level ``recent.md`` index for the repo's graph zone."""
        cypher = (
            "MATCH (n:Entity) "
            "WHERE n.type IN ['Decision', 'Problem'] "
            "  AND (n.expired_at IS NULL OR n.expired_at > $snapshot) "
            "RETURN n.type AS type, "
            "       coalesce(n.text, n.name, n.path) AS title, "
            "       n.created_at AS created_at, "
            "       n.repo_path AS repo_path "
            "ORDER BY n.created_at DESC "
            "LIMIT 25"
        )
        rows = await self._run(cypher, {"snapshot": self._ensure_snapshot()})

        lines = [
            "---",
            "type: RecentIndex",
            f"repo: {repo_slug}",
            f"generated_at: {self._ensure_snapshot().isoformat()}",
            "---",
            "# Recent decisions and problems",
            "",
        ]
        any_rendered = False
        for row in rows:
            if not self._repo_matches_slug(row.get("repo_path"), repo_slug):
                continue
            any_rendered = True
            category = "decisions" if row.get("type") == "Decision" else "problems"
            title = row.get("title") or "untitled"
            filename = f"{serializer.slugify(title)}.md"
            target = (
                f"/memories/repos/{repo_slug}/graph/{category}/{filename}"
            )
            lines.append(f"- [{row.get('type')}] {title} → `{target}`")
        if not any_rendered:
            lines.append("_(no recent entries for this repo)_")
        return "\n".join(lines) + "\n"
