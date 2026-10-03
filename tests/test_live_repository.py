"""Real Git/SQLite acceptance tests for the Phase 1 control plane."""
from pathlib import Path
import subprocess
import os

import pytest

from memex.runtime.views import discover_repository, capture_sources
from memex.runtime.journal import ChangeJournal
from memex.runtime.indexing import extract_structure


def git(path, *args):
    return subprocess.check_output(["git", "-C", str(path), *args], text=True).strip()


@pytest.fixture
def repository(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    git(root, "init", "-q")
    git(root, "config", "user.name", "Phase 1 test")
    git(root, "config", "user.email", "phase1@example.invalid")
    (root / "api.py").write_text("def api():\n    return 1\n", encoding="utf-8")
    git(root, "add", "api.py")
    git(root, "commit", "-qm", "initial")
    return root


def test_linked_worktrees_share_repo_but_not_view(repository, tmp_path):
    linked = tmp_path / "linked"
    git(repository, "worktree", "add", "-qb", "other", str(linked))
    a, b = discover_repository(repository), discover_repository(linked)
    assert a.repo_id == b.repo_id
    assert a.worktree_id != b.worktree_id
    assert discover_repository(repository / "." ) == a
    assert Path(b.git_dir).is_dir() and (linked / ".git").is_file()


def test_unborn_and_detached_registration(repository, tmp_path):
    empty = tmp_path / "empty"
    empty.mkdir()
    git(empty, "init", "-q")
    assert capture_sources(discover_repository(empty)).head_commit is None
    git(repository, "checkout", "--detach", "-q")
    assert capture_sources(discover_repository(repository)).head_commit


def test_manifest_tracks_body_deletion_revert_and_excludes_credentials(repository):
    reg = discover_repository(repository)
    original = capture_sources(reg)
    (repository / "api.py").write_text("def api():\n    return 2\n", encoding="utf-8")
    assert capture_sources(reg).manifest_hash != original.manifest_hash
    git(repository, "restore", "api.py")
    assert capture_sources(reg).manifest_hash == original.manifest_hash
    (repository / ".env").write_text("NOT_FOR_CAPTURE=x", encoding="utf-8")
    (repository / "CREDS.txt").write_text("NOT_FOR_CAPTURE=x", encoding="utf-8")
    assert capture_sources(reg).manifest_hash == original.manifest_hash
    (repository / "api.py").unlink()
    assert "api.py" not in capture_sources(reg).files


def test_journal_reopens_pending_and_never_advances_before_ack(repository, tmp_path):
    reg = discover_repository(repository)
    path = tmp_path / "journal.sqlite"
    journal = ChangeJournal(path)
    first = journal.observe(reg, capture_sources(reg))
    assert first.indexed_generation == 0
    assert len(ChangeJournal(path).pending(reg.worktree_id)) == 1
    journal.acknowledge(first.view_id)
    assert journal.current(reg.worktree_id).indexed_generation == first.content_generation
    assert journal.pending(reg.worktree_id) == []
    journal.acknowledge(first.view_id)
    assert journal.observe(reg, capture_sources(reg)).view_id == first.view_id


def test_journal_newer_generation_cannot_be_regressed(repository, tmp_path):
    reg = discover_repository(repository)
    journal = ChangeJournal(tmp_path / "journal.sqlite")
    first = journal.observe(reg, capture_sources(reg))
    (repository / "api.py").write_text("def api():\n    return 3\n", encoding="utf-8")
    second = journal.observe(reg, capture_sources(reg))
    journal.acknowledge(second.view_id)
    journal.acknowledge(first.view_id)
    assert journal.current(reg.worktree_id).content_generation == second.content_generation
    assert journal.current(reg.worktree_id).indexed_generation == second.content_generation


def test_structure_distinguishes_empty_invalid_and_unsupported():
    assert extract_structure("empty.py", b"# empty\n").coverage == "complete"
    invalid = extract_structure("broken.py", b"def broken(\n")
    assert invalid.coverage == "parse_error"
    assert extract_structure("client.ts", b"export {};\n").coverage == "unsupported"
    changed = extract_structure("client.py", b"import os\ndef send():\n    return api()\n")
    assert changed.imports == ("os",)
    assert {(c.caller, c.callee) for c in changed.calls} == {("send", "api")}
    assert extract_structure("client.py", b"def send():\n    return 1\n").calls == ()


def test_symbols_use_qualified_identity():
    structure = extract_structure("client.py", b"class A:\n    def run(self):\n        return api()\nclass B:\n    def run(self):\n        return 1\n")
    assert {s.qualified_name for s in structure.symbols} >= {"A.run", "B.run"}
    assert structure.calls[0].caller == "A.run"


@pytest.mark.skipif(os.name != "nt", reason="Windows case-insensitive path aliases")
def test_case_aliases_preserve_repository_identity(repository):
    first = discover_repository(repository)
    alias = discover_repository(str(repository).upper())
    assert (alias.repo_id, alias.worktree_id) == (first.repo_id, first.worktree_id)


def test_source_capture_rejects_escape_and_oversized_file(repository, tmp_path):
    reg = discover_repository(repository)
    outside = tmp_path / "outside.py"
    outside.write_text("DO_NOT_READ = True\n")
    link = repository / "escape.py"
    try:
        link.symlink_to(outside)
    except OSError:
        pytest.skip("OS does not permit test symlinks")
    with pytest.raises(ValueError, match="escapes"):
        capture_sources(reg)
    link.unlink()
    (repository / "oversized.py").write_bytes(b"#" * 5_000_001)
    with pytest.raises(ValueError, match="budget"):
        capture_sources(reg)
