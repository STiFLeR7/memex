"""Graph node → Markdown + YAML frontmatter + XML tag-block serialiser.

The format matches the example in ARCHITECTURE-v0.3.0.md §11.5:

```
---
id: dec_2026_05_neo4j
type: Decision
created: 2026-05-18
status: accepted
validated: true
tags: [storage, graph]
edges:
  - rejects: alt_postgres_jsonb
  - influences: mod_ingest
---
# Use Neo4j + Graphiti over Postgres JSONB

<context>
Memex needs typed relationships between decisions and modules…
</context>

<rationale>
Cypher gives us multi-hop queries…
</rationale>
```

Why Markdown+YAML over pure JSON (verbatim from ARCHITECTURE §11.5): `str_replace`
edits are unsafe on JSON. Markdown+YAML survives partial edits gracefully.
The XML tags give Claude structured anchors per Anthropic's prompt-engineering
guidance.
"""

from __future__ import annotations

import re
import unicodedata
from datetime import datetime
from typing import Any, Iterable, Mapping


# ---------------------------------------------------------------------------
# Slug helpers
# ---------------------------------------------------------------------------


def slugify(name: str, *, max_len: int = 64) -> str:
    """Convert an arbitrary string to a filesystem-safe slug.

    Anthropic's guidance treats filenames as signals to the model; we
    therefore use descriptive slugs rather than UUIDs. Lowercased,
    ASCII-only, hyphen-separated, truncated to ``max_len``.
    """
    if not name:
        return "unnamed"

    # Strip combining marks / accents via NFKD decomposition.
    normalised = unicodedata.normalize("NFKD", str(name))
    ascii_only = normalised.encode("ascii", "ignore").decode("ascii")
    lowered = ascii_only.lower()

    # Replace any run of non-[a-z0-9] with a single hyphen.
    slug = re.sub(r"[^a-z0-9]+", "-", lowered).strip("-")
    if not slug:
        return "unnamed"
    return slug[:max_len].rstrip("-") or "unnamed"


# ---------------------------------------------------------------------------
# YAML frontmatter helpers
# ---------------------------------------------------------------------------


def _format_yaml_scalar(value: Any) -> str:
    """Render a Python value as a YAML scalar, quoting only when needed."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if isinstance(value, datetime):
        # ISO-8601, drop timezone if naive
        return value.isoformat()
    s = str(value)
    # Quote if it contains YAML-significant characters.
    if any(ch in s for ch in (":", "#", "\n", "{", "}", "[", "]", "&", "*", "!", "|", ">", "'", '"', "%", "@", "`")):
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    if s.strip() != s or not s:
        escaped = s.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return s


def _format_yaml_list(items: Iterable[Any]) -> str:
    parts = [_format_yaml_scalar(it) for it in items]
    return "[" + ", ".join(parts) + "]"


def _render_frontmatter(fields: Mapping[str, Any]) -> str:
    """Render an ordered mapping as YAML frontmatter (between --- fences)."""
    lines = ["---"]
    for key, value in fields.items():
        if value is None:
            continue
        if isinstance(value, list):
            if not value:
                continue
            # Edges get a multi-line block-style list of mappings; everything
            # else is flow-style.
            if key == "edges" and value and isinstance(value[0], Mapping):
                lines.append(f"{key}:")
                for entry in value:
                    for ek, ev in entry.items():
                        lines.append(f"  - {ek}: {_format_yaml_scalar(ev)}")
            else:
                lines.append(f"{key}: {_format_yaml_list(value)}")
        else:
            lines.append(f"{key}: {_format_yaml_scalar(value)}")
    lines.append("---")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# XML-tag helpers
# ---------------------------------------------------------------------------


def _xml_block(tag: str, content: str | None) -> str | None:
    if content is None:
        return None
    text = str(content).strip()
    if not text:
        return None
    return f"<{tag}>\n{text}\n</{tag}>"


# ---------------------------------------------------------------------------
# Node serialisers
# ---------------------------------------------------------------------------


def decision_to_markdown(d: Mapping[str, Any]) -> str:
    """Render a Decision node dict as Markdown + YAML + XML tag blocks.

    Expected keys (all optional except ``text``):
      id, text, rationale, scope, source_commit, created_at,
      validated, validated_at, base_confidence, computed_confidence,
      tags (list[str]), edges (list[dict[str, str]]).
    """
    text = d.get("text") or d.get("name") or "(untitled decision)"
    frontmatter_fields = {
        "id": d.get("id") or d.get("uuid"),
        "type": "Decision",
        "created": d.get("created_at") or d.get("created"),
        "scope": d.get("scope"),
        "status": d.get("status"),
        "validated": d.get("validated"),
        "validated_at": d.get("validated_at"),
        "base_confidence": d.get("base_confidence"),
        "computed_confidence": d.get("computed_confidence"),
        "source": d.get("source"),
        "source_commit": d.get("source_commit"),
        "supersedes": d.get("supersedes"),
        "tags": d.get("tags"),
        "edges": d.get("edges"),
    }
    body_parts: list[str] = [
        _render_frontmatter(frontmatter_fields),
        f"# {text}",
    ]
    ctx = _xml_block("context", d.get("context") or d.get("summary"))
    if ctx:
        body_parts.append(ctx)
    rationale = _xml_block("rationale", d.get("rationale"))
    if rationale:
        body_parts.append(rationale)
    edges_block = _xml_block(
        "edges",
        d.get("edges_text"),
    )
    if edges_block:
        body_parts.append(edges_block)

    return "\n\n".join(body_parts) + "\n"


def problem_to_markdown(p: Mapping[str, Any]) -> str:
    """Render a Problem node dict as Markdown + YAML + XML tag blocks."""
    text = p.get("text") or p.get("name") or "(untitled problem)"
    frontmatter_fields = {
        "id": p.get("id") or p.get("uuid"),
        "type": "Problem",
        "severity": p.get("severity"),
        "status": p.get("status"),
        "created": p.get("created_at") or p.get("created"),
        "base_confidence": p.get("base_confidence"),
        "computed_confidence": p.get("computed_confidence"),
        "surfaced_by": p.get("surfaced_by"),
        "module": p.get("module"),
        "tags": p.get("tags"),
        "edges": p.get("edges"),
    }
    body_parts: list[str] = [
        _render_frontmatter(frontmatter_fields),
        f"# {text}",
    ]
    ctx = _xml_block("context", p.get("context") or p.get("summary"))
    if ctx:
        body_parts.append(ctx)
    repro = _xml_block("reproduction", p.get("reproduction"))
    if repro:
        body_parts.append(repro)
    impact = _xml_block("impact", p.get("impact"))
    if impact:
        body_parts.append(impact)
    return "\n\n".join(body_parts) + "\n"


def module_to_markdown(
    m: Mapping[str, Any],
    symbols: list[Mapping[str, Any]] | None = None,
    decisions: list[Mapping[str, Any]] | None = None,
) -> str:
    """Render a Module node + its symbols + linked decisions as Markdown."""
    name = m.get("path") or m.get("name") or "(unknown module)"
    frontmatter_fields = {
        "id": m.get("id") or m.get("uuid"),
        "type": "Module",
        "language": m.get("language"),
        "cluster": m.get("cluster_name"),
        "created": m.get("created_at") or m.get("created"),
        "access_count": m.get("access_count"),
        "last_reinforced_at": m.get("last_reinforced_at"),
        "tags": m.get("tags"),
    }
    body_parts: list[str] = [
        _render_frontmatter(frontmatter_fields),
        f"# {name}",
    ]
    description = _xml_block("description", m.get("description") or m.get("summary"))
    if description:
        body_parts.append(description)

    if symbols:
        lines = ["<symbols>"]
        for s in symbols:
            sym_name = s.get("name") or "(unnamed)"
            sym_sig = s.get("signature")
            if sym_sig:
                lines.append(f"- `{sym_name}` — `{sym_sig}`")
            else:
                lines.append(f"- `{sym_name}`")
        lines.append("</symbols>")
        body_parts.append("\n".join(lines))

    if decisions:
        lines = ["<decisions>"]
        for d in decisions:
            d_text = d.get("text") or d.get("name") or "(untitled decision)"
            d_id = d.get("id") or d.get("uuid") or ""
            if d_id:
                lines.append(f"- [{d_id}] {d_text}")
            else:
                lines.append(f"- {d_text}")
        lines.append("</decisions>")
        body_parts.append("\n".join(lines))

    return "\n\n".join(body_parts) + "\n"


def cluster_to_markdown(
    c: Mapping[str, Any],
    modules: list[Mapping[str, Any]] | None = None,
) -> str:
    """Render a Cluster node + its constituent modules as Markdown."""
    name = c.get("name") or "(unnamed cluster)"
    frontmatter_fields = {
        "id": c.get("id") or c.get("uuid"),
        "type": "Cluster",
        "repo_path": c.get("repo_path"),
        "module_count": c.get("module_count"),
        "created": c.get("created_at") or c.get("created"),
        "tags": c.get("tags"),
    }
    body_parts: list[str] = [
        _render_frontmatter(frontmatter_fields),
        f"# Cluster: {name}",
    ]
    description = _xml_block("description", c.get("description") or c.get("summary"))
    if description:
        body_parts.append(description)

    if modules:
        lines = ["<modules>"]
        for m in modules:
            mod_name = m.get("path") or m.get("name") or "(unnamed module)"
            lines.append(f"- {mod_name}")
        lines.append("</modules>")
        body_parts.append("\n".join(lines))

    return "\n\n".join(body_parts) + "\n"
