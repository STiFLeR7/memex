"""Manual cluster-override loader.

Reads `.memex/clusters.yaml` (per ARCHITECTURE-v0.3.0 §10) and returns a
flat mapping of ``{module_path: cluster_name}``. The hybrid-Leiden cluster
assignment in ``memex/graph/cluster.py`` (deferred to dev2) consults this
loader after Leiden runs; overrides win.

Until dev2 wires the override map into cluster.py, this module is consumed
only by tests. It is intentionally small, side-effect-free, and lenient on
errors so a malformed YAML never crashes the watcher.

Expected file format (flat mapping):

.. code-block:: yaml

    # .memex/clusters.yaml
    memex/watcher/handlers.py: watcher
    memex/watcher/event_router.py: watcher
    memex/mcp_server/queries.py: mcp
    memex/mcp_server/server.py: mcp

The YAML format is documented as an example only; dev2 may extend it
(e.g. nested ``clusters:`` mapping). Callers should treat the return value
as a flat ``dict[str, str]`` for now.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger(__name__)

CLUSTERS_YAML_PATH = ".memex/clusters.yaml"


def _coerce_to_flat_mapping(data: Any) -> dict[str, str]:
    """Normalise a parsed YAML document into ``{module_path: cluster_name}``.

    Supports two shapes:

    * Flat: ``{path: cluster_name, ...}``
    * Nested: ``{"clusters": {cluster_name: [path, path, ...]}, ...}``

    Unknown shapes return ``{}`` with a warning.
    """
    if data is None:
        return {}

    if not isinstance(data, dict):
        logger.warning(
            "clusters.yaml: top-level document must be a mapping, got %s",
            type(data).__name__,
        )
        return {}

    # Nested form: {"clusters": {name: [path, ...]}}
    nested = data.get("clusters")
    if isinstance(nested, dict):
        out: dict[str, str] = {}
        for cluster_name, members in nested.items():
            if not isinstance(members, list):
                logger.warning(
                    "clusters.yaml: cluster %r members must be a list, got %s",
                    cluster_name,
                    type(members).__name__,
                )
                continue
            for member in members:
                if isinstance(member, str):
                    out[member] = str(cluster_name)
        return out

    # Flat form: {path: cluster_name}
    flat: dict[str, str] = {}
    for k, v in data.items():
        if isinstance(k, str) and isinstance(v, (str, int, float)):
            flat[k] = str(v)
        else:
            logger.warning(
                "clusters.yaml: skipping non-string entry %r -> %r", k, v
            )
    return flat


def load_cluster_overrides(repo_root: str | Path) -> dict[str, str]:
    """Return the module→cluster override mapping from ``.memex/clusters.yaml``.

    Returns an empty dict if the file does not exist or cannot be parsed.
    Parse errors are logged at WARNING level — they never raise.
    """
    repo_path = Path(repo_root)
    yaml_path = repo_path / CLUSTERS_YAML_PATH

    if not yaml_path.exists():
        return {}

    try:
        text = yaml_path.read_text(encoding="utf-8")
    except OSError as exc:
        logger.warning("clusters.yaml: could not read %s: %s", yaml_path, exc)
        return {}

    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        logger.warning("clusters.yaml: malformed YAML at %s: %s", yaml_path, exc)
        return {}

    return _coerce_to_flat_mapping(data)
