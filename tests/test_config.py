import os
import pytest
from memex.config import canonical_repo_path


def test_canonical_repo_path_equivalent_forms(tmp_path):
    """Equivalent spellings of the same directory must canonicalize to one
    string, so the watcher (write) and MCP server (read) agree on repo_path
    regardless of how `--repo` was spelled. Audit B1."""
    d = tmp_path / "repo"
    d.mkdir()
    base = canonical_repo_path(str(d))

    assert canonical_repo_path(str(d) + os.sep) == base          # trailing sep
    assert canonical_repo_path(str(d / ".")) == base             # dot segment
    assert canonical_repo_path(str(d / "sub" / "..")) == base    # parent segment
    assert canonical_repo_path(base) == base                     # idempotent
    assert "\\" not in base                                       # posix separators


def test_canonical_repo_path_windows_case_insensitive():
    """Windows filesystems are case-insensitive — drive/case differences must
    not split repo_path."""
    if os.name != "nt":
        pytest.skip("windows-only")
    assert canonical_repo_path("C:/Foo/Bar") == canonical_repo_path("c:/foo/bar")


def test_canonical_repo_path_handles_none_and_empty():
    """Defensive: never raise on degenerate input."""
    assert canonical_repo_path("") == ""
    assert canonical_repo_path(None) is None
