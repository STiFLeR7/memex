"""Hierarchical Leiden cluster engine over a hybrid module-edge graph.

Phase 6 / Deliverable 1 (v0.3.1). Groups Modules into Clusters using
``graspologic.partition.hierarchical_leiden`` over a weighted graph
combining three edge sources:

- **Directory co-location** (weight 1): two modules share a parent dir
- **Symbol-call aggregation** (weight ``log(1 + calls)``): aggregate of
  CALLS edges between symbols whose ``file`` resolves to two different
  modules
- **Module-import** (weight 2): from
  :func:`memex.extractor.lockfile.extract_module_imports`

Cluster IDs are pinned across re-runs: if a new cluster's member set has
Jaccard similarity ≥ 0.5 with an existing Cluster node's, the new cluster
inherits the old name. Random seed is ``0xMEMEX`` (1366488157) so
re-clustering the same graph yields the same assignment.

After Leiden, user pins from ``.memex/clusters.yaml`` (via
:mod:`memex.graph.cluster_overrides`) win unconditionally — if a module
is pinned to a cluster name, that wins regardless of what Leiden produced.

Naming for unpinned clusters: top-3 TF-IDF tokens extracted from member
Decisions' text, joined with ``-``. If no Decisions exist yet (e.g. on
``memex init`` first pass), falls back to the most-common parent-directory
prefix of the cluster's members.

The orchestrator :func:`run_cluster_pass` is async and optionally takes a
graph client for prior-cluster lookup + call-count aggregation; the lower
helpers are pure functions so tests don't need Neo4j.
"""

from __future__ import annotations

import logging
import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Iterable, Optional

from memex.extractor.lockfile import extract_module_imports
from memex.graph.cluster_overrides import load_cluster_overrides

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: Deterministic seed for graspologic Leiden so re-runs are reproducible.
#: The handoff calls for ``0xMEMEX``; "MEMEX" isn't valid hex (X is out of
#: range), so we materialise it as the big-endian integer of the ASCII bytes
#: of "MEMEX" — same semantic intent, valid Python.
MEMEX_SEED = int.from_bytes(b"MEMEX", "big")  # 331875173720

#: Pinning threshold: new cluster inherits prior cluster's name if Jaccard
#: similarity of member sets is at or above this value.
JACCARD_PIN_THRESHOLD = 0.5

#: Edge weights per the handoff (DEV2-HANDOFF.md "Your six deliverables" §1).
DIR_COLOCATION_WEIGHT = 1.0
MODULE_IMPORT_WEIGHT = 2.0


def _call_edge_weight(calls: int) -> float:
    """``log(1 + calls)`` — saturating weight that rewards tight coupling
    without letting a hot symbol pair dominate the entire partition."""
    if calls <= 0:
        return 0.0
    return math.log1p(calls)


# ---------------------------------------------------------------------------
# Path / module helpers
# ---------------------------------------------------------------------------


def _normalise_module_path(path: str) -> str:
    """``foo\\bar\\baz.py`` and ``foo/bar/baz.py`` both → ``foo/bar/baz.py``.

    The repo stores module identifiers as POSIX-style relative paths
    (matches ``cluster_overrides.yaml`` examples). Windows watchers may
    emit backslashes; this guarantees a single canonical form.
    """
    return str(path).replace("\\", "/").lstrip("./")


def _parent_dir(module_path: str) -> str:
    """Parent directory of a normalised module path, or ``""`` for top-level."""
    norm = _normalise_module_path(module_path)
    if "/" not in norm:
        return ""
    return norm.rsplit("/", 1)[0]


def _file_to_module(file_path: str) -> str:
    """Map a Symbol.file path back to a module path.

    Symbol records store ``file`` as the *relative* path (e.g.
    ``memex/watcher/handlers.py``). For directory co-location and
    call-edge aggregation we treat the file path itself as the module
    identifier — :class:`memex.graph.schema.Module.path` uses the same
    convention. We strip Windows backslashes defensively.
    """
    return _normalise_module_path(file_path)


# ---------------------------------------------------------------------------
# Hybrid edge graph construction (pure)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CallEdge:
    """An aggregated symbol-call edge between two modules.

    Produced by :func:`aggregate_symbol_calls` from a list of CALLS edges
    in the graph. ``calls`` is the count of distinct symbol-pair calls,
    not the runtime invocation count.
    """

    from_module: str
    to_module: str
    calls: int


def aggregate_symbol_calls(
    call_records: Iterable[dict],
) -> list[CallEdge]:
    """Collapse ``[{caller_file, callee_file}, ...]`` into
    ``[CallEdge(from, to, calls), ...]``.

    Self-loops (caller and callee in same file) are dropped — they don't
    contribute to inter-module clustering. Direction is canonicalised
    (lexicographically smaller module first) so the resulting list has
    one entry per unordered pair.
    """
    counts: Counter[tuple[str, str]] = Counter()
    for rec in call_records:
        caller = rec.get("caller_file") or rec.get("from") or rec.get("from_file")
        callee = rec.get("callee_file") or rec.get("to") or rec.get("to_file")
        if not caller or not callee:
            continue
        a = _file_to_module(caller)
        b = _file_to_module(callee)
        if a == b:
            continue
        key = (a, b) if a < b else (b, a)
        counts[key] += 1
    return [CallEdge(a, b, n) for (a, b), n in counts.items()]


def build_hybrid_graph(
    modules: Iterable[str],
    imports: Iterable[tuple[str, str]] = (),
    call_edges: Iterable[CallEdge] = (),
) -> Any:
    """Construct the weighted ``networkx.Graph`` used by Leiden.

    Args:
        modules: Module identifiers (relative paths). One node per entry.
        imports: ``(from_module, to_module)`` pairs from
            :func:`memex.extractor.lockfile.extract_module_imports`.
            Module identifiers here are dotted Python paths
            (``memex.watcher.handlers``); we accept both dotted and
            slash forms and normalise to the slash form used elsewhere.
        call_edges: Output of :func:`aggregate_symbol_calls`.

    Returns:
        A ``networkx.Graph`` where every edge has a ``weight`` attribute
        equal to the sum of contributing edge sources.
    """
    # Local import — networkx is a graspologic transitive dep, but we
    # don't want module-import-time failure if the cluster extra is not
    # installed. This module IS the cluster extra; if we get this far
    # the import is safe, but keeping it local matches the pattern of
    # other heavy optional deps in the codebase.
    import networkx as nx  # noqa: PLC0415

    g: Any = nx.Graph()

    # Canonicalise + deduplicate module list. Empty / blank entries are
    # dropped defensively so a malformed Module node doesn't blow up
    # Leiden with a degenerate node.
    canonical: list[str] = []
    seen: set[str] = set()
    for m in modules:
        if not m:
            continue
        norm = _normalise_module_path(str(m))
        if norm in seen:
            continue
        seen.add(norm)
        canonical.append(norm)
        g.add_node(norm)

    # 1. Directory co-location (weight 1 per pair)
    by_dir: dict[str, list[str]] = defaultdict(list)
    for m in canonical:
        parent = _parent_dir(m)
        # Skip top-level modules (no parent dir) — they contribute no
        # co-location signal. A solo module at repo root would otherwise
        # have a degree-zero island, which Leiden handles fine but the
        # cluster name would be meaningless.
        if parent:
            by_dir[parent].append(m)

    for siblings in by_dir.values():
        if len(siblings) < 2:
            continue
        for i, a in enumerate(siblings):
            for b in siblings[i + 1 :]:
                _accumulate_weight(g, a, b, DIR_COLOCATION_WEIGHT)

    # 2. Module imports (weight 2 per directed pair, undirected sum)
    for src, dst in imports:
        if not src or not dst:
            continue
        a = _to_slash_module(src)
        b = _to_slash_module(dst)
        if a == b or a not in seen or b not in seen:
            continue
        _accumulate_weight(g, a, b, MODULE_IMPORT_WEIGHT)

    # 3. Symbol-call aggregation (weight log(1 + calls))
    for ce in call_edges:
        if ce.from_module == ce.to_module:
            continue
        if ce.from_module not in seen or ce.to_module not in seen:
            continue
        _accumulate_weight(
            g,
            ce.from_module,
            ce.to_module,
            _call_edge_weight(ce.calls),
        )

    return g


def _accumulate_weight(g: Any, a: str, b: str, w: float) -> None:
    """Add ``w`` to the existing edge weight, or create the edge with ``w``."""
    if w <= 0:
        return
    if g.has_edge(a, b):
        g[a][b]["weight"] = g[a][b].get("weight", 0.0) + w
    else:
        g.add_edge(a, b, weight=w)


def _to_slash_module(name: str) -> str:
    """``memex.watcher.handlers`` → ``memex/watcher/handlers.py`` if it looks
    like a dotted Python module; passthrough otherwise.

    The lockfile import extractor yields dotted Python names; the rest of
    the repo identifies modules by their file path. We can't know the
    extension at this layer, so we *guess* ``.py`` — this is fine because
    :func:`build_hybrid_graph` checks the result against the canonical
    module set and drops unmatched targets.
    """
    if "/" in name or "\\" in name:
        return _normalise_module_path(name)
    if "." in name and not name.endswith(".py"):
        return name.replace(".", "/") + ".py"
    return name


# ---------------------------------------------------------------------------
# Leiden partition (pure on the input graph)
# ---------------------------------------------------------------------------


def cluster_modules(
    graph: Any,
    *,
    seed: int = MEMEX_SEED,
    max_cluster_size: int = 12,
) -> dict[int, set[str]]:
    """Run hierarchical Leiden and return ``{leiden_id: {module, ...}}``.

    Uses the *top-level* (level 0) partition for cluster assignment. The
    hierarchical structure is retained inside graspologic's output but we
    don't surface it here — call sites can re-cluster at a finer level
    if needed.

    Empty or single-node graphs return ``{}`` — there's nothing to
    cluster. Nodes with degree zero stay isolated and are assigned
    singleton clusters with negative IDs (``-1``, ``-2``, ...) so they
    don't collide with Leiden's own cluster IDs.

    Args:
        graph: The output of :func:`build_hybrid_graph`.
        seed: Random seed for Leiden's local search. Default is
            ``MEMEX_SEED`` (``0xMEMEX``).
        max_cluster_size: Forwarded to graspologic. Above this size,
            graspologic will recurse into sub-clusters.
    """
    if graph.number_of_nodes() == 0:
        return {}

    # graspologic raises on graphs with no edges. Short-circuit by
    # assigning every isolated node a singleton cluster.
    if graph.number_of_edges() == 0:
        return {
            -(i + 1): {node} for i, node in enumerate(sorted(graph.nodes))
        }

    from graspologic.partition import hierarchical_leiden  # noqa: PLC0415

    partitions = hierarchical_leiden(
        graph,
        max_cluster_size=max_cluster_size,
        random_seed=seed,
    )

    # `hierarchical_leiden` returns a list of `HierarchicalCluster`
    # objects; each has `node`, `cluster`, `level`, `is_final_cluster`.
    # We collect the level-0 (top-level) assignment per node.
    by_id: dict[int, set[str]] = defaultdict(set)
    seen_nodes: set[str] = set()
    for entry in partitions:
        if getattr(entry, "level", 0) != 0:
            continue
        node = entry.node
        if node in seen_nodes:
            continue
        seen_nodes.add(node)
        by_id[entry.cluster].add(node)

    # Sweep up any nodes graspologic didn't return at level 0 (isolated
    # components Leiden can sometimes drop). Each becomes a singleton
    # with a unique negative id, matching the no-edge fast path above.
    next_iso_id = -1
    for node in graph.nodes:
        if node not in seen_nodes:
            by_id[next_iso_id] = {node}
            next_iso_id -= 1

    return dict(by_id)


# ---------------------------------------------------------------------------
# Cluster naming (pure)
# ---------------------------------------------------------------------------


# Match the conservative stop list used by handlers.corroborate_decisions
# so cluster names stay consistent with corroboration tokenisation.
_NAMING_STOPWORDS: frozenset[str] = frozenset({
    "this", "that", "with", "from", "here", "there", "what", "when",
    "where", "which", "while", "decision", "rationale", "scope",
    "about", "been", "being", "does", "done", "each", "have", "into",
    "just", "more", "most", "only", "some", "such", "than", "then",
    "they", "very", "were", "your", "should", "code", "file", "files",
    "function", "class", "method", "module", "modules", "system", "test",
    "tests", "would", "could", "will", "make", "made",
})

_NAMING_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_]{2,}")


def _tokenise_for_naming(text: str) -> list[str]:
    """Lowercase ``text`` and split into significant alphanumeric tokens."""
    if not text:
        return []
    return [
        tok.lower()
        for tok in _NAMING_TOKEN_RE.findall(text)
        if tok.lower() not in _NAMING_STOPWORDS and len(tok) > 3
    ]


def name_clusters_by_tfidf(
    clusters: dict[int, set[str]],
    module_decision_texts: dict[str, list[str]],
) -> dict[int, str]:
    """Pick a 3-token name per cluster from the TF-IDF top of its Decisions.

    ``module_decision_texts`` maps each module path to the list of
    Decision text bodies attached to it (or whose ``modules`` list
    contains it). When a cluster has no Decisions yet, falls back to the
    most-common parent-directory prefix of its members
    (e.g. ``mcp_server`` for everything under ``memex/mcp_server/``).

    Names are returned slug-safe: lowercase tokens joined with ``-``, max
    3 tokens. Empty clusters yield ``"cluster-<id>"``.
    """
    if not clusters:
        return {}

    # Build a per-cluster bag-of-words from the Decisions attached to
    # any of its members.
    cluster_tokens: dict[int, list[str]] = {}
    for cid, members in clusters.items():
        tokens: list[str] = []
        for m in members:
            for text in module_decision_texts.get(m, ()):
                tokens.extend(_tokenise_for_naming(text))
        cluster_tokens[cid] = tokens

    # IDF: document frequency = number of clusters whose token bag
    # contains the term at least once.
    doc_freq: Counter[str] = Counter()
    for tokens in cluster_tokens.values():
        for term in set(tokens):
            doc_freq[term] += 1

    n_docs = max(1, len(clusters))
    names: dict[int, str] = {}

    for cid, tokens in cluster_tokens.items():
        if not tokens:
            # Fallback: most common parent dir component, single-token name.
            names[cid] = _fallback_name_from_paths(clusters[cid], cid)
            continue

        tf = Counter(tokens)
        scored: list[tuple[float, str]] = []
        for term, count in tf.items():
            idf = math.log(n_docs / (1 + doc_freq[term])) + 1.0
            scored.append((count * idf, term))

        scored.sort(key=lambda pair: (-pair[0], pair[1]))
        top = [term for _, term in scored[:3]]
        names[cid] = "-".join(top) if top else _fallback_name_from_paths(
            clusters[cid], cid
        )

    return names


def _fallback_name_from_paths(members: Iterable[str], cluster_id: int) -> str:
    """Pick the dominant parent-dir component as a deterministic name."""
    parents: Counter[str] = Counter()
    for m in members:
        parts = _normalise_module_path(m).split("/")
        if len(parts) >= 2:
            # Use the *second* level when there's a common top-level
            # ("memex/watcher/..." → "watcher"); top-level alone is too
            # coarse (everything would be named "memex").
            parents[parts[1]] += 1
        elif parts:
            parents[parts[0]] += 1
    if not parents:
        return f"cluster-{cluster_id}"
    most_common, _ = parents.most_common(1)[0]
    return most_common or f"cluster-{cluster_id}"


# ---------------------------------------------------------------------------
# Cluster ID pinning across re-runs (pure)
# ---------------------------------------------------------------------------


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    union = a | b
    if not union:
        return 0.0
    return len(a & b) / len(union)


def pin_cluster_names(
    new_clusters: dict[int, set[str]],
    prior_clusters: dict[str, set[str]],
    candidate_names: dict[int, str],
    *,
    threshold: float = JACCARD_PIN_THRESHOLD,
) -> dict[int, str]:
    """Inherit prior cluster names when member sets overlap.

    For each new cluster, find the prior cluster with the highest Jaccard
    similarity. If similarity ≥ ``threshold``, the new cluster inherits
    that prior cluster's name. Otherwise it keeps its ``candidate_names``
    entry. Each prior name is consumed at most once — the better-matching
    new cluster wins on tie, by cluster id.

    Args:
        new_clusters: Output of :func:`cluster_modules`.
        prior_clusters: ``{name: {module, ...}}`` read from Neo4j.
        candidate_names: Output of :func:`name_clusters_by_tfidf`.
        threshold: Pinning threshold (default ``JACCARD_PIN_THRESHOLD``).

    Returns:
        ``{new_cluster_id: chosen_name}``.
    """
    # Score every (new_id, prior_name) pair.
    pairs: list[tuple[float, int, str]] = []
    for cid, members in new_clusters.items():
        for prior_name, prior_members in prior_clusters.items():
            j = _jaccard(members, prior_members)
            if j >= threshold:
                pairs.append((j, cid, prior_name))

    pairs.sort(key=lambda t: (-t[0], t[1], t[2]))

    pinned_by_id: dict[int, str] = {}
    used_priors: set[str] = set()
    for _, cid, prior_name in pairs:
        if cid in pinned_by_id:
            continue
        if prior_name in used_priors:
            continue
        pinned_by_id[cid] = prior_name
        used_priors.add(prior_name)

    return {
        cid: pinned_by_id.get(cid, candidate_names.get(cid, f"cluster-{cid}"))
        for cid in new_clusters
    }


# ---------------------------------------------------------------------------
# User pin overlay (pure)
# ---------------------------------------------------------------------------


def apply_user_overrides(
    cluster_names: dict[int, str],
    cluster_members: dict[int, set[str]],
    overrides: dict[str, str],
) -> tuple[dict[int, str], dict[int, set[str]]]:
    """Re-bucket modules into user-pinned cluster names.

    User overrides come from ``.memex/clusters.yaml``. A pinned module is
    *moved* from its Leiden bucket to a cluster bucket named exactly as
    the user requested — even if that means creating a fresh cluster
    that Leiden never produced.

    Returns a pair of dicts using the same ``int → name`` and
    ``int → {members}`` shape as the inputs, with negative IDs (starting
    at ``-1000`` and decrementing) assigned to user-only buckets so they
    can't collide with Leiden ids or the singleton-isolated ids from
    :func:`cluster_modules`.
    """
    if not overrides:
        return cluster_names, cluster_members

    # Find the inverse: module → leiden_id
    module_to_id: dict[str, int] = {}
    for cid, members in cluster_members.items():
        for m in members:
            module_to_id[_normalise_module_path(m)] = cid

    # Resolve override target names to (existing or new) cluster ids.
    name_to_id: dict[str, int] = {name: cid for cid, name in cluster_names.items()}
    new_members: dict[int, set[str]] = {cid: set(ms) for cid, ms in cluster_members.items()}
    new_names: dict[int, str] = dict(cluster_names)

    next_user_id = -1000

    for module_path, target_name in overrides.items():
        norm_path = _normalise_module_path(module_path)
        if not target_name:
            continue
        # Resolve / create the target bucket.
        if target_name in name_to_id:
            target_id = name_to_id[target_name]
        else:
            target_id = next_user_id
            next_user_id -= 1
            name_to_id[target_name] = target_id
            new_names[target_id] = target_name
            new_members[target_id] = set()

        # Remove the module from its current bucket (if any) and add to target.
        current_id = module_to_id.get(norm_path)
        if current_id is not None and current_id != target_id:
            new_members[current_id].discard(norm_path)
        new_members[target_id].add(norm_path)
        module_to_id[norm_path] = target_id

    # Drop now-empty buckets — keeps downstream callers from materialising
    # ghost Cluster nodes for clusters Leiden produced but the user
    # entirely re-pinned away.
    empty = [cid for cid, ms in new_members.items() if not ms]
    for cid in empty:
        del new_members[cid]
        new_names.pop(cid, None)

    return new_names, new_members


# ---------------------------------------------------------------------------
# Top-level orchestrator (async, hits Neo4j optionally)
# ---------------------------------------------------------------------------


@dataclass
class ClusterAssignment:
    """The result row produced by :func:`run_cluster_pass`.

    One per resulting cluster. Persistence (Deliverable 6) consumes this
    shape directly when writing Cluster + CONTAINS edges.
    """

    name: str
    members: set[str] = field(default_factory=set)
    leiden_id: Optional[int] = None
    pinned_from_prior: bool = False
    pinned_by_user: bool = False


async def run_cluster_pass(
    repo_root: str | Path,
    *,
    client: Any = None,
    seed: int = MEMEX_SEED,
    max_cluster_size: int = 12,
    module_paths: Optional[Iterable[str]] = None,
    module_decision_texts: Optional[dict[str, list[str]]] = None,
) -> list[ClusterAssignment]:
    """Cluster every Module under ``repo_root`` into named groups.

    Args:
        repo_root: Repo path; used to read ``.memex/clusters.yaml``
            overrides and to walk source for module-import edges.
        client: Optional Graphiti / Neo4j client. When provided, used to
            fetch (a) the current set of Module paths if ``module_paths``
            isn't given, (b) Decision texts for naming, (c) prior
            Cluster member sets for pinning, and (d) CALLS edges for
            symbol-call aggregation. When ``None``, callers MUST supply
            ``module_paths`` (and optionally ``module_decision_texts``);
            pinning and call-edge contributions are skipped.
        seed: Random seed for Leiden (default ``MEMEX_SEED``).
        max_cluster_size: graspologic recursion threshold.
        module_paths: Explicit module list, overriding the Neo4j query.
            Useful when the orchestrator already has the watcher-derived
            list in memory (e.g. ``memex init`` first pass).
        module_decision_texts: Explicit ``{module_path: [decision_text, ...]}``.
            Falls back to ``{}`` if not provided and ``client`` is
            ``None`` — naming then uses the path-prefix fallback.

    Returns:
        A list of :class:`ClusterAssignment`, one per non-empty cluster.
    """
    repo_path = Path(repo_root)

    # 1. Resolve module list.
    modules: list[str]
    if module_paths is not None:
        modules = [_normalise_module_path(m) for m in module_paths if m]
    elif client is not None:
        modules = await _fetch_modules_from_graph(client, repo_path)
    else:
        modules = []

    if not modules:
        logger.info("cluster: no modules found under %s; nothing to cluster", repo_path)
        return []

    # 2. Build hybrid edge sources.
    try:
        imports = await extract_module_imports(repo_path)
    except Exception:
        logger.warning("cluster: module-import extraction failed", exc_info=True)
        imports = []
    import_pairs: list[tuple[str, str]] = [(src, dst) for src, dst, _meta in imports]

    call_edges: list[CallEdge] = []
    if client is not None:
        try:
            call_records = await _fetch_call_edges_from_graph(client, repo_path)
            call_edges = aggregate_symbol_calls(call_records)
        except Exception:
            logger.warning("cluster: call-edge fetch failed; falling back to dir+import only", exc_info=True)

    # 3. Build graph + Leiden.
    graph = build_hybrid_graph(modules, import_pairs, call_edges)
    clusters = cluster_modules(graph, seed=seed, max_cluster_size=max_cluster_size)

    if not clusters:
        return []

    # 4. Name the clusters.
    decision_texts: dict[str, list[str]] = module_decision_texts or {}
    if client is not None and not decision_texts:
        try:
            decision_texts = await _fetch_module_decision_texts(client, repo_path)
        except Exception:
            logger.warning("cluster: decision-text fetch failed; falling back to path-prefix naming", exc_info=True)
            decision_texts = {}
    candidate_names = name_clusters_by_tfidf(clusters, decision_texts)

    # 5. Pin against prior clusters (if available).
    prior_clusters: dict[str, set[str]] = {}
    if client is not None:
        try:
            prior_clusters = await _fetch_prior_clusters(client, repo_path)
        except Exception:
            logger.warning("cluster: prior-cluster fetch failed; not pinning", exc_info=True)
    pinned_names = pin_cluster_names(clusters, prior_clusters, candidate_names)
    pinned_from_prior_ids = {
        cid for cid, name in pinned_names.items() if name in prior_clusters
    }

    # 6. Apply user pins.
    overrides = load_cluster_overrides(repo_path)
    final_names, final_members = apply_user_overrides(
        pinned_names, clusters, overrides
    )
    user_pinned_names = set(overrides.values())

    # 7. Build result rows.
    assignments: list[ClusterAssignment] = []
    for cid, members in final_members.items():
        if not members:
            continue
        name = final_names.get(cid, f"cluster-{cid}")
        assignments.append(
            ClusterAssignment(
                name=name,
                members=set(members),
                leiden_id=cid if cid >= 0 else None,
                pinned_from_prior=cid in pinned_from_prior_ids,
                pinned_by_user=name in user_pinned_names,
            )
        )

    # Deterministic ordering helps tests and PR diffs.
    assignments.sort(key=lambda a: a.name)
    return assignments


# ---------------------------------------------------------------------------
# Graph reads (Neo4j) — used by run_cluster_pass when a client is provided
# ---------------------------------------------------------------------------


async def _fetch_modules_from_graph(client: Any, repo_path: Path) -> list[str]:
    """Return every Module.path in the graph for ``repo_path``."""
    query = """
    MATCH (m:Entity)
    WHERE (m.type = 'Module' OR m.name ENDS WITH '.py' OR m.name ENDS WITH '.js')
      AND ($repo IS NULL OR m.repo_path = $repo)
    RETURN DISTINCT m.name as path
    """
    res = await client.driver.execute_query(
        query, params={"repo": str(repo_path)}
    )
    return [r["path"] for r in res.records if r.get("path")]


async def _fetch_call_edges_from_graph(
    client: Any, repo_path: Path
) -> list[dict]:
    """Return raw CALLS edge records (caller_file, callee_file)."""
    query = """
    MATCH (s:Entity)-[r:CALLS]->(t:Entity)
    WHERE r.expired_at IS NULL
      AND ($repo IS NULL OR s.repo_path = $repo)
    RETURN coalesce(s.file, '') as caller_file,
           coalesce(t.file, '') as callee_file
    """
    res = await client.driver.execute_query(
        query, params={"repo": str(repo_path)}
    )
    return [r.data() for r in res.records]


async def _fetch_module_decision_texts(
    client: Any, repo_path: Path
) -> dict[str, list[str]]:
    """Return ``{module_path: [decision_text, ...]}`` for TF-IDF naming."""
    query = """
    MATCH (d:Entity)
    WHERE (d.type = 'Decision' OR d.name CONTAINS 'Decision')
      AND ($repo IS NULL OR d.repo_path = $repo)
      AND coalesce(d.excluded, false) = false
    OPTIONAL MATCH (d)-[r:MOTIVATES|RELATES_TO|MENTIONS]-(m:Entity)
    WHERE r.expired_at IS NULL
      AND (m.type = 'Module' OR m.name ENDS WITH '.py' OR m.name ENDS WITH '.js')
    WITH d, collect(DISTINCT m.name) as module_paths
    RETURN d.name as text, module_paths
    """
    res = await client.driver.execute_query(
        query, params={"repo": str(repo_path)}
    )
    out: dict[str, list[str]] = defaultdict(list)
    for record in res.records:
        text = record.get("text") or ""
        paths = record.get("module_paths") or []
        if not text:
            continue
        for p in paths:
            if p:
                out[_normalise_module_path(p)].append(text)
    return dict(out)


async def _fetch_prior_clusters(
    client: Any, repo_path: Path
) -> dict[str, set[str]]:
    """Return ``{cluster_name: {module_path, ...}}`` for the repo's
    existing Cluster nodes. Used for Jaccard-based name pinning."""
    query = """
    MATCH (c:Entity)
    WHERE c.type = 'Cluster'
      AND ($repo IS NULL OR c.repo_path = $repo)
    OPTIONAL MATCH (c)-[r:CONTAINS]->(m:Entity)
    WHERE r.expired_at IS NULL
      AND (m.type = 'Module' OR m.name ENDS WITH '.py' OR m.name ENDS WITH '.js')
    RETURN c.name as name, collect(DISTINCT m.name) as members
    """
    res = await client.driver.execute_query(
        query, params={"repo": str(repo_path)}
    )
    out: dict[str, set[str]] = {}
    for record in res.records:
        name = record.get("name")
        members = record.get("members") or []
        if not name:
            continue
        out[name] = {_normalise_module_path(m) for m in members if m}
    return out
