import os
import subprocess
import pytest
from unittest.mock import patch
from memex.config import canonical_repo_path, normalize_git_remote_url, resolve_project_id, Config


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


# ---------------------------------------------------------------------------
# normalize_git_remote_url() — Task 1 (NET-01)
# ---------------------------------------------------------------------------


def test_normalize_git_remote_scp_shorthand():
    assert normalize_git_remote_url("git@github.com:org/repo.git") == "github.com/org/repo"


def test_normalize_git_remote_https():
    assert normalize_git_remote_url("https://github.com/org/repo.git") == "github.com/org/repo"


def test_normalize_git_remote_https_trailing_slash_no_git_suffix():
    assert normalize_git_remote_url("https://github.com/org/repo/") == "github.com/org/repo"


def test_normalize_git_remote_lowercases_host_preserves_path_case():
    assert normalize_git_remote_url("https://GitHub.com/Org/Repo.git") == "github.com/Org/Repo"


def test_normalize_git_remote_self_hosted_custom_port():
    assert (
        normalize_git_remote_url("ssh://git@gitlab.example.com:2222/team/proj.git")
        == "gitlab.example.com/team/proj"
    )


def test_normalize_git_remote_scp_shorthand_self_hosted():
    assert (
        normalize_git_remote_url("git@gitlab.example.com:team/proj.git")
        == "gitlab.example.com/team/proj"
    )


def test_normalize_git_remote_local_bare_repo_path_returns_none(tmp_path):
    """Pitfall 1 — a local bare-repo path that exists on disk must never be
    mistaken for SCP shorthand, even though it superficially resembles
    `host:path` (e.g. a Windows drive letter before the colon)."""
    bare_repo = tmp_path / "shared.git"
    bare_repo.mkdir()
    assert normalize_git_remote_url(str(bare_repo)) is None


def test_normalize_git_remote_degenerate_inputs():
    assert normalize_git_remote_url("") is None
    assert normalize_git_remote_url(None) is None
    assert normalize_git_remote_url("not a url") is None


# ---------------------------------------------------------------------------
# resolve_project_id() — Task 2 (NET-01) — three-step fallback chain
# ---------------------------------------------------------------------------


def test_resolve_project_id_prefers_git_remote_over_file(tmp_path):
    (tmp_path / ".git").mkdir()
    memex_dir = tmp_path / ".memex"
    memex_dir.mkdir()
    (memex_dir / "project_id").write_text("acme-widgets-team", encoding="utf-8")

    with patch(
        "memex.config.subprocess.check_output",
        return_value=b"git@github.com:acme/widgets.git",
    ):
        assert resolve_project_id(str(tmp_path)) == "github.com/acme/widgets"


def test_resolve_project_id_falls_back_to_file_when_no_remote(tmp_path):
    memex_dir = tmp_path / ".memex"
    memex_dir.mkdir()
    (memex_dir / "project_id").write_text("acme-widgets-team", encoding="utf-8")

    with patch(
        "memex.config.subprocess.check_output",
        side_effect=subprocess.CalledProcessError(128, "git"),
    ):
        assert resolve_project_id(str(tmp_path)) == "acme-widgets-team"


def test_resolve_project_id_returns_none_when_git_missing_and_no_file(tmp_path):
    with patch(
        "memex.config.subprocess.check_output",
        side_effect=FileNotFoundError("git not found"),
    ):
        assert resolve_project_id(str(tmp_path)) is None


def test_resolve_project_id_empty_file_falls_through_to_none(tmp_path):
    memex_dir = tmp_path / ".memex"
    memex_dir.mkdir()
    (memex_dir / "project_id").write_text("   ", encoding="utf-8")

    with patch(
        "memex.config.subprocess.check_output",
        side_effect=subprocess.CalledProcessError(128, "git"),
    ):
        assert resolve_project_id(str(tmp_path)) is None


# ---------------------------------------------------------------------------
# Governance-report scheduling config (Phase 04 / NET-16)
# ---------------------------------------------------------------------------


def test_config_report_scheduling_defaults():
    """report_hour/report_day_of_week default per the documented spec, and
    report_hour must differ from decay_hour by default (regression guard for
    research Pitfall 2 — the two jobs must not collide on the same hour)."""
    cfg = Config(
        neo4j_uri="x", neo4j_user="x", neo4j_password="x", gemini_api_key="x"
    )
    assert cfg.report_hour == 3
    assert cfg.report_day_of_week == "mon"
    assert cfg.report_hour != cfg.decay_hour
