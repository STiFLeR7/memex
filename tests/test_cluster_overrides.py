"""Tests for ``memex.graph.cluster_overrides``."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from memex.graph.cluster_overrides import load_cluster_overrides


def test_clusters_yaml_overrides_module_to_cluster_mapping(tmp_path: Path) -> None:
    memex_dir = tmp_path / ".memex"
    memex_dir.mkdir()
    (memex_dir / "clusters.yaml").write_text(
        "memex/watcher/handlers.py: watcher\n"
        "memex/watcher/event_router.py: watcher\n"
        "memex/mcp_server/queries.py: mcp\n",
        encoding="utf-8",
    )

    overrides = load_cluster_overrides(tmp_path)

    assert overrides == {
        "memex/watcher/handlers.py": "watcher",
        "memex/watcher/event_router.py": "watcher",
        "memex/mcp_server/queries.py": "mcp",
    }


def test_missing_clusters_yaml_returns_empty_dict(tmp_path: Path) -> None:
    # No .memex directory at all
    assert load_cluster_overrides(tmp_path) == {}

    # .memex exists but no clusters.yaml
    (tmp_path / ".memex").mkdir()
    assert load_cluster_overrides(tmp_path) == {}


def test_malformed_yaml_returns_empty_with_warning(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    memex_dir = tmp_path / ".memex"
    memex_dir.mkdir()
    # Genuinely unparseable YAML: dangling colon + bad indentation
    (memex_dir / "clusters.yaml").write_text(
        "this is not: : : valid: yaml:\n  - [unbalanced\n",
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="memex.graph.cluster_overrides"):
        result = load_cluster_overrides(tmp_path)

    assert result == {}
    assert any("clusters.yaml" in rec.message for rec in caplog.records)


def test_nested_clusters_yaml_form_is_supported(tmp_path: Path) -> None:
    memex_dir = tmp_path / ".memex"
    memex_dir.mkdir()
    (memex_dir / "clusters.yaml").write_text(
        "clusters:\n"
        "  watcher:\n"
        "    - memex/watcher/handlers.py\n"
        "    - memex/watcher/event_router.py\n"
        "  mcp:\n"
        "    - memex/mcp_server/queries.py\n",
        encoding="utf-8",
    )

    overrides = load_cluster_overrides(tmp_path)

    assert overrides == {
        "memex/watcher/handlers.py": "watcher",
        "memex/watcher/event_router.py": "watcher",
        "memex/mcp_server/queries.py": "mcp",
    }


def test_non_mapping_top_level_returns_empty(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    memex_dir = tmp_path / ".memex"
    memex_dir.mkdir()
    (memex_dir / "clusters.yaml").write_text(
        "- this\n- is\n- a\n- list\n",
        encoding="utf-8",
    )

    with caplog.at_level(logging.WARNING, logger="memex.graph.cluster_overrides"):
        result = load_cluster_overrides(tmp_path)

    assert result == {}
