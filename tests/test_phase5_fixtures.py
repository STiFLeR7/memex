"""W16 fixtures: every history discriminates, every change lands as specified, nothing leaks.

Real Git and the real checks runner; no model and no graph.
"""
import json
import pathlib

import pytest

from memex.evaluation import fixtures
from memex.evaluation.fixtures import (CONFIRMATORY_TEMPLATES, DEV_HISTORIES, DEV_TEMPLATES, MECHANISMS, admit,
                                       after_files, apply_change, before_files, confirmatory_histories, gold,
                                       materialize, run_checks, stale)

ALL = DEV_HISTORIES + confirmatory_histories(tuple(MECHANISMS))


def test_development_and_confirmatory_sets_are_disjoint_and_cover_every_mechanism():
    assert not set(DEV_TEMPLATES) & set(CONFIRMATORY_TEMPLATES)
    assert {h.template for h in DEV_HISTORIES} == set(DEV_TEMPLATES)
    assert {h.mechanism for h in DEV_HISTORIES} == set(MECHANISMS)
    assert len(DEV_HISTORIES) == 12 and len({h.history_id for h in ALL}) == len(ALL)
    assert {h.fixture for h in DEV_HISTORIES} >= {"F01", "F02", "F03", "F04", "F05", "F06", "F08", "F09", "F13"}


@pytest.mark.parametrize("history", ALL, ids=lambda h: f"{h.history_id}-{h.template}-{h.mechanism}")
def test_every_history_discriminates(history):
    admission = admit(history)
    assert admission.admitted, admission.reason
    if history.affected:
        assert admission.probe, "an affected history needs a stale probe"
        # The stale patch is a correct solution of the original world.
        assert not fixtures.failed_checks(history, run_checks(history, before_files(history), stale(history)))
    else:
        assert gold(history) == stale(history)


@pytest.mark.parametrize("mechanism", sorted(MECHANISMS))
def test_each_change_lands_exactly_as_specified(tmp_path, mechanism):
    history = next(h for h in ALL if h.mechanism == mechanism)
    made = materialize(history, tmp_path)
    repo = pathlib.Path(made["repo"])
    before = {p: (repo / p).read_text(encoding="utf-8") for p in before_files(history)}
    assert before == before_files(history)
    apply_change(repo, made["plan"], uri=None)  # the graph half of `superseded` needs Neo4j; tested natively
    expected = after_files(history)
    for path, content in expected.items():
        assert (repo / path).read_text(encoding="utf-8") == content, path
    if mechanism == "worktree":
        other = pathlib.Path(made["plan"]["actions"][0]["path"])
        assert (other / history.spec.dep).read_text(encoding="utf-8") == history.spec.dep_new
    if mechanism in ("merged", "checkout"):
        assert fixtures.git(repo, "status", "--porcelain") == "", "the change arrived as committed Git history"


@pytest.mark.parametrize("history", ALL, ids=lambda h: h.history_id)
def test_nothing_the_agent_can_read_reveals_the_change_or_the_answer(tmp_path, history):
    made = materialize(history, tmp_path)
    repo = pathlib.Path(made["repo"])
    readable = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                         for p in repo.rglob("*") if p.is_file() and ".git" not in p.parts)
    t = history.spec
    assert "check_" not in readable, "hidden check names are in the repository"
    assert t.gold_new.strip() not in readable and t.gold_old.strip() not in readable
    if history.affected and history.mechanism not in ("merged", "checkout"):
        changed = {p: c for p, c in after_files(history).items() if before_files(history).get(p) != c}
        for content in changed.values():
            assert content not in readable, "post-change content is visible before the change"


def test_change_plans_are_not_stored_readably(tmp_path):
    from tests.phase5_trial_hooks import decode_plan, encode_plan
    plan = materialize(DEV_HISTORIES[0], tmp_path)["plan"]
    encoded = encode_plan(plan)
    assert DEV_HISTORIES[0].spec.dep_new not in encoded and decode_plan(encoded) == json.loads(json.dumps(plan))
