"""Unit tests for ``memex.graph.cluster``.

Every test here runs without Neo4j — the orchestrator
:func:`run_cluster_pass` accepts explicit ``module_paths`` and
``module_decision_texts`` so the network of pure helpers can be
exercised end-to-end against a synthetic repo on disk.

Test names mirror ``docs/PLAN-v0.3.0.md`` Phase 6 where applicable plus
additional unit-level cases for the pure helpers.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from memex.graph.cluster import (
    CallEdge,
    ClusterAssignment,
    MEMEX_SEED,
    _fallback_name_from_paths,
    _normalise_module_path,
    _parent_dir,
    _to_slash_module,
    aggregate_symbol_calls,
    apply_user_overrides,
    build_hybrid_graph,
    cluster_modules,
    name_clusters_by_tfidf,
    pin_cluster_names,
    run_cluster_pass,
)


# ---------------------------------------------------------------------------
# Path helpers
# ---------------------------------------------------------------------------


def test_normalise_module_path_handles_windows_and_dot_prefix() -> None:
    assert _normalise_module_path("memex/graph/cluster.py") == "memex/graph/cluster.py"
    assert _normalise_module_path(r"memex\graph\cluster.py") == "memex/graph/cluster.py"
    assert _normalise_module_path("./memex/graph/cluster.py") == "memex/graph/cluster.py"


def test_parent_dir_handles_top_level() -> None:
    assert _parent_dir("memex/graph/cluster.py") == "memex/graph"
    assert _parent_dir("README.md") == ""


def test_to_slash_module_converts_dotted_python_path() -> None:
    assert _to_slash_module("memex.watcher.handlers") == "memex/watcher/handlers.py"
    assert _to_slash_module("memex/watcher/handlers.py") == "memex/watcher/handlers.py"
    # already-extensioned file paths pass through
    assert _to_slash_module("memex/graph/cluster.py") == "memex/graph/cluster.py"


# ---------------------------------------------------------------------------
# aggregate_symbol_calls
# ---------------------------------------------------------------------------


def test_aggregate_symbol_calls_collapses_directed_pairs_and_drops_self_loops() -> None:
    records = [
        {"caller_file": "a/x.py", "callee_file": "b/y.py"},
        {"caller_file": "a/x.py", "callee_file": "b/y.py"},
        {"caller_file": "b/y.py", "callee_file": "a/x.py"},   # reverse direction
        {"caller_file": "a/x.py", "callee_file": "a/x.py"},   # self-loop, dropped
        {"caller_file": "c/z.py", "callee_file": "d/w.py"},
    ]
    out = sorted(aggregate_symbol_calls(records), key=lambda e: (e.from_module, e.to_module))
    assert out == [
        CallEdge(from_module="a/x.py", to_module="b/y.py", calls=3),
        CallEdge(from_module="c/z.py", to_module="d/w.py", calls=1),
    ]


def test_aggregate_symbol_calls_skips_missing_endpoints() -> None:
    records = [
        {"caller_file": "a/x.py"},  # no callee
        {"callee_file": "b/y.py"},  # no caller
        {"caller_file": "a/x.py", "callee_file": "b/y.py"},
    ]
    out = aggregate_symbol_calls(records)
    assert len(out) == 1
    assert out[0].calls == 1


# ---------------------------------------------------------------------------
# build_hybrid_graph
# ---------------------------------------------------------------------------


def test_build_hybrid_graph_dir_colocation_edges_weight_one() -> None:
    modules = ["pkg/a.py", "pkg/b.py", "pkg/c.py", "other/x.py"]
    g = build_hybrid_graph(modules)
    assert g.has_edge("pkg/a.py", "pkg/b.py")
    assert g["pkg/a.py"]["pkg/b.py"]["weight"] == 1.0
    # other/x.py has no siblings → no edges
    assert g.degree("other/x.py") == 0


def test_build_hybrid_graph_import_edges_weight_two_and_dotted_resolves() -> None:
    modules = ["memex/watcher/handlers.py", "memex/graph/writer.py"]
    imports = [("memex.watcher.handlers", "memex.graph.writer")]
    g = build_hybrid_graph(modules, imports)
    assert g.has_edge("memex/watcher/handlers.py", "memex/graph/writer.py")
    # No dir co-location (different parents), import weight only
    assert g["memex/watcher/handlers.py"]["memex/graph/writer.py"]["weight"] == 2.0


def test_build_hybrid_graph_call_edges_use_log1p_weight() -> None:
    import math
    modules = ["a/x.py", "b/y.py"]
    calls = [CallEdge("a/x.py", "b/y.py", 7)]
    g = build_hybrid_graph(modules, [], calls)
    assert g["a/x.py"]["b/y.py"]["weight"] == pytest.approx(math.log1p(7))


def test_build_hybrid_graph_accumulates_weights_across_sources() -> None:
    import math
    modules = ["pkg/a.py", "pkg/b.py"]
    imports = [("pkg.a", "pkg.b")]
    calls = [CallEdge("pkg/a.py", "pkg/b.py", 3)]
    g = build_hybrid_graph(modules, imports, calls)
    # dir(1) + import(2) + call(log1p(3))
    expected = 1.0 + 2.0 + math.log1p(3)
    assert g["pkg/a.py"]["pkg/b.py"]["weight"] == pytest.approx(expected)


def test_build_hybrid_graph_ignores_blank_and_external_modules() -> None:
    modules = ["pkg/a.py", "", None, "pkg/b.py"]
    imports = [("pkg.a", "external.pkg"), ("pkg.a", "pkg.b")]
    g = build_hybrid_graph(modules, imports)
    assert "external/pkg.py" not in g.nodes
    assert g.has_edge("pkg/a.py", "pkg/b.py")


# ---------------------------------------------------------------------------
# cluster_modules (Leiden)
# ---------------------------------------------------------------------------


def test_cluster_modules_empty_graph_returns_empty() -> None:
    g = build_hybrid_graph([])
    assert cluster_modules(g) == {}


def test_cluster_modules_no_edges_returns_singleton_clusters() -> None:
    g = build_hybrid_graph(["a/x.py", "b/y.py", "c/z.py"])
    out = cluster_modules(g)
    # Three singletons; ids are negative so they don't collide with Leiden's
    assert len(out) == 3
    assert all(len(members) == 1 for members in out.values())
    assert all(cid < 0 for cid in out)


def test_cluster_modules_two_dense_groups_split_cleanly() -> None:
    # Group A: a/*  cross-imports + dir co-location
    # Group B: b/*  cross-imports + dir co-location
    modules = [
        "a/x.py", "a/y.py", "a/z.py",
        "b/x.py", "b/y.py", "b/z.py",
    ]
    imports = [
        ("a.x", "a.y"), ("a.y", "a.z"), ("a.z", "a.x"),
        ("b.x", "b.y"), ("b.y", "b.z"), ("b.z", "b.x"),
    ]
    g = build_hybrid_graph(modules, imports)
    out = cluster_modules(g, seed=MEMEX_SEED)
    # Should yield two clusters; each cluster is fully within one parent dir
    parent_dirs_per_cluster = [
        {m.split("/")[0] for m in members} for members in out.values()
    ]
    assert all(len(parents) == 1 for parents in parent_dirs_per_cluster), (
        f"clusters crossed parent dirs: {parent_dirs_per_cluster}"
    )


def test_cluster_modules_is_deterministic_with_fixed_seed() -> None:
    modules = ["a/x.py", "a/y.py", "b/x.py", "b/y.py"]
    imports = [("a.x", "a.y"), ("b.x", "b.y")]
    g1 = build_hybrid_graph(modules, imports)
    g2 = build_hybrid_graph(modules, imports)
    out1 = {frozenset(members) for members in cluster_modules(g1).values()}
    out2 = {frozenset(members) for members in cluster_modules(g2).values()}
    assert out1 == out2


# ---------------------------------------------------------------------------
# name_clusters_by_tfidf
# ---------------------------------------------------------------------------


def test_name_clusters_by_tfidf_picks_distinguishing_terms() -> None:
    clusters = {
        1: {"watcher/handlers.py", "watcher/router.py"},
        2: {"graph/writer.py", "graph/queries.py"},
    }
    texts = {
        "watcher/handlers.py": [
            "Corroborate decisions when commit matches affected files",
            "Watcher debounces filesystem events before extraction",
        ],
        "watcher/router.py": [
            "Route events to handlers based on path matchers",
        ],
        "graph/writer.py": [
            "Write decision nodes with confidence anchored at base",
            "Cypher SET pattern targets the episode uuid",
        ],
        "graph/queries.py": [
            "Cypher queries filter expired edges and respect repo scope",
        ],
    }
    names = name_clusters_by_tfidf(clusters, texts)
    # Cluster 1 should weight watcher/events terms; cluster 2 should weight cypher/graph terms.
    # We don't pin exact tokens (TF-IDF could pick any of several), only that the names differ
    # and that neither falls back to "cluster-N".
    assert names[1] != names[2]
    assert not names[1].startswith("cluster-")
    assert not names[2].startswith("cluster-")


def test_name_clusters_by_tfidf_falls_back_to_path_prefix_when_no_decisions() -> None:
    clusters = {1: {"memex/watcher/handlers.py", "memex/watcher/router.py"}}
    names = name_clusters_by_tfidf(clusters, {})
    assert names[1] == "watcher"


def test_name_clusters_by_tfidf_falls_back_on_single_top_level_path() -> None:
    clusters = {1: {"README.md"}}
    names = name_clusters_by_tfidf(clusters, {})
    # Top-level module → only one path component, falls through to it
    assert names[1] == "README.md" or names[1] == "cluster-1"


def test_fallback_name_uses_second_level_when_repo_prefix_dominates() -> None:
    members = {"memex/watcher/x.py", "memex/watcher/y.py"}
    # Top-level "memex" is too coarse; second-level "watcher" is the
    # distinguishing component.
    assert _fallback_name_from_paths(members, 7) == "watcher"


# ---------------------------------------------------------------------------
# pin_cluster_names
# ---------------------------------------------------------------------------


def test_pin_cluster_names_inherits_when_jaccard_above_threshold() -> None:
    new_clusters = {1: {"a.py", "b.py", "c.py"}}
    prior_clusters = {"watcher": {"a.py", "b.py", "c.py", "d.py"}}  # J = 3/4 = 0.75
    candidate_names = {1: "foo-bar"}
    out = pin_cluster_names(new_clusters, prior_clusters, candidate_names)
    assert out[1] == "watcher"


def test_pin_cluster_names_keeps_candidate_below_threshold() -> None:
    new_clusters = {1: {"a.py", "b.py", "c.py"}}
    prior_clusters = {"watcher": {"a.py", "x.py", "y.py", "z.py"}}  # J = 1/6 ≈ 0.17
    candidate_names = {1: "foo-bar"}
    out = pin_cluster_names(new_clusters, prior_clusters, candidate_names)
    assert out[1] == "foo-bar"


def test_pin_cluster_names_does_not_reuse_a_prior_twice() -> None:
    new_clusters = {
        1: {"a.py", "b.py", "c.py"},  # perfect Jaccard with prior "watcher"
        2: {"a.py", "b.py"},          # J = 2/3 with prior "watcher"
    }
    prior_clusters = {"watcher": {"a.py", "b.py", "c.py"}}
    candidate_names = {1: "tfidf-1", 2: "tfidf-2"}
    out = pin_cluster_names(new_clusters, prior_clusters, candidate_names)
    # Cluster 1 has higher Jaccard → wins the prior name
    assert out[1] == "watcher"
    # Cluster 2 keeps its TF-IDF candidate
    assert out[2] == "tfidf-2"


# ---------------------------------------------------------------------------
# apply_user_overrides
# ---------------------------------------------------------------------------


def test_apply_user_overrides_moves_module_into_named_bucket() -> None:
    names = {1: "alpha", 2: "beta"}
    members = {1: {"foo.py", "bar.py"}, 2: {"baz.py"}}
    overrides = {"foo.py": "beta"}  # move foo.py from alpha → beta

    new_names, new_members = apply_user_overrides(names, members, overrides)

    assert new_members[1] == {"bar.py"}
    assert new_members[2] == {"baz.py", "foo.py"}
    assert new_names[2] == "beta"


def test_apply_user_overrides_creates_new_bucket_for_unknown_target() -> None:
    names = {1: "alpha"}
    members = {1: {"foo.py", "bar.py"}}
    overrides = {"foo.py": "gamma"}  # gamma doesn't exist yet

    new_names, new_members = apply_user_overrides(names, members, overrides)

    # foo.py moved to a fresh cluster named "gamma"
    assert any(name == "gamma" for name in new_names.values())
    gamma_id = next(cid for cid, name in new_names.items() if name == "gamma")
    assert new_members[gamma_id] == {"foo.py"}


def test_apply_user_overrides_drops_emptied_buckets() -> None:
    names = {1: "alpha"}
    members = {1: {"foo.py"}}
    overrides = {"foo.py": "beta"}

    new_names, new_members = apply_user_overrides(names, members, overrides)

    # cluster 1 was emptied → both names and members should not contain it
    assert 1 not in new_members
    assert 1 not in new_names


def test_apply_user_overrides_no_op_when_no_overrides() -> None:
    names = {1: "alpha"}
    members = {1: {"foo.py"}}
    out_names, out_members = apply_user_overrides(names, members, {})
    assert out_names == names
    assert out_members == members


# ---------------------------------------------------------------------------
# run_cluster_pass orchestrator (no Neo4j)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_cluster_pass_on_synthetic_repo(tmp_path: Path) -> None:
    # Build a tiny synthetic repo with two import-coupled clusters
    (tmp_path / "watcher").mkdir()
    (tmp_path / "graph").mkdir()
    (tmp_path / "watcher" / "handlers.py").write_text(
        "from watcher.router import dispatch\n", encoding="utf-8"
    )
    (tmp_path / "watcher" / "router.py").write_text(
        "def dispatch(): pass\n", encoding="utf-8"
    )
    (tmp_path / "graph" / "writer.py").write_text(
        "from graph.queries import run\n", encoding="utf-8"
    )
    (tmp_path / "graph" / "queries.py").write_text(
        "def run(): pass\n", encoding="utf-8"
    )

    modules = [
        "watcher/handlers.py",
        "watcher/router.py",
        "graph/writer.py",
        "graph/queries.py",
    ]
    decision_texts = {
        "watcher/handlers.py": ["Watcher dispatches filesystem events"],
        "graph/writer.py": ["Writer persists decisions through cypher"],
    }

    assignments = await run_cluster_pass(
        repo_root=tmp_path,
        module_paths=modules,
        module_decision_texts=decision_texts,
    )

    assert assignments, "expected at least one cluster"
    # Every input module ends up in exactly one cluster
    placed = {m for a in assignments for m in a.members}
    assert placed == set(modules)
    # Each cluster sits in a single top-level dir
    for a in assignments:
        parents = {m.split("/")[0] for m in a.members}
        assert len(parents) == 1, f"cluster {a.name} crossed parent dirs: {parents}"
    # ClusterAssignment shape sanity
    assert all(isinstance(a, ClusterAssignment) for a in assignments)


@pytest.mark.asyncio
async def test_run_cluster_pass_applies_user_overrides(tmp_path: Path) -> None:
    (tmp_path / ".memex").mkdir()
    (tmp_path / ".memex" / "clusters.yaml").write_text(
        "watcher/handlers.py: pinned-bucket\n", encoding="utf-8"
    )

    modules = ["watcher/handlers.py", "watcher/router.py", "graph/writer.py"]
    assignments = await run_cluster_pass(
        repo_root=tmp_path, module_paths=modules
    )

    pinned = [a for a in assignments if a.name == "pinned-bucket"]
    assert len(pinned) == 1
    assert "watcher/handlers.py" in pinned[0].members
    assert pinned[0].pinned_by_user is True


@pytest.mark.asyncio
async def test_run_cluster_pass_empty_module_list_returns_empty(tmp_path: Path) -> None:
    out = await run_cluster_pass(repo_root=tmp_path, module_paths=[])
    assert out == []


@pytest.mark.asyncio
async def test_run_cluster_pass_is_deterministic(tmp_path: Path) -> None:
    modules = ["a/x.py", "a/y.py", "b/x.py", "b/y.py"]
    one = await run_cluster_pass(repo_root=tmp_path, module_paths=modules)
    two = await run_cluster_pass(repo_root=tmp_path, module_paths=modules)
    one_sig = sorted((a.name, frozenset(a.members)) for a in one)
    two_sig = sorted((a.name, frozenset(a.members)) for a in two)
    assert one_sig == two_sig
