from dataclasses import replace

import pytest

from memex.context.revision import RepositoryView


def make_view(**updates):
    fields = dict(
        repo_id="repo-1", worktree_id="worktree-1", head_commit="a" * 40,
        content_generation=2, indexed_generation=1,
        manifest_hash="sha256:" + "b" * 64,
    )
    fields.update(updates)
    return RepositoryView(**fields)


def test_index_progress_does_not_change_view_identity():
    before = make_view()
    after = replace(before, indexed_generation=2)
    assert before.view_id == after.view_id


def test_worktrees_have_distinct_view_identity():
    assert make_view().view_id != make_view(worktree_id="worktree-2").view_id


def test_changed_content_generation_has_new_identity():
    assert make_view().view_id != make_view(content_generation=3).view_id


def test_unborn_view():
    assert make_view(head_commit=None, content_generation=0,
                     indexed_generation=0).head_commit is None


@pytest.mark.parametrize("updates", [
    {"repo_id": ""}, {"worktree_id": ""},
    {"content_generation": -1}, {"indexed_generation": -1},
    {"indexed_generation": 3}, {"manifest_hash": "not-a-digest"},
])
def test_invalid_view_rejected(updates):
    with pytest.raises(ValueError):
        make_view(**updates)
