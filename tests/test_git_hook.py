import pytest
import json
import subprocess
from unittest.mock import patch
from memex.watcher import git_hook

def test_install_hooks_writes_post_commit_script(tmp_path):
    # Mock .git/hooks directory
    hooks_dir = tmp_path / ".git" / "hooks"
    hooks_dir.mkdir(parents=True)
    
    git_hook.install_hooks(str(tmp_path))
    
    post_commit = hooks_dir / "post-commit"
    assert post_commit.exists()
    assert "memex" in post_commit.read_text()

def test_install_hooks_is_idempotent(tmp_path):
    hooks_dir = tmp_path / ".git" / "hooks"
    hooks_dir.mkdir(parents=True)
    
    git_hook.install_hooks(str(tmp_path))
    content1 = (hooks_dir / "post-commit").read_text()
    
    git_hook.install_hooks(str(tmp_path))
    content2 = (hooks_dir / "post-commit").read_text()
    
    assert content1 == content2

def test_hook_script_uses_lf_line_endings(tmp_path):
    hooks_dir = tmp_path / ".git" / "hooks"
    hooks_dir.mkdir(parents=True)
    
    git_hook.install_hooks(str(tmp_path))
    
    with open(hooks_dir / "post-commit", "rb") as f:
        content = f.read()
        assert b"\r\n" not in content

@patch("subprocess.check_output")
def test_emit_commit_event_writes_sidecar_json(mock_subproc, tmp_path):
    # Mock git commands
    mock_subproc.side_effect = [
        b"abc123sha", # rev-parse
        b"Commit message", # log
        b"+ diff content", # diff
        b"file1.py\nfile2.py" # diff-tree
    ]
    
    git_hook.emit_commit_event(str(tmp_path))
    
    pending_file = tmp_path / ".memex" / "pending_commit.json"
    assert pending_file.exists()
    
    with open(pending_file, "r") as f:
        data = json.load(f)
        assert data["sha"] == "abc123sha"
        assert "file1.py" in data["files_changed"]

def test_emit_commit_event_no_memex_dir(tmp_path):
    # .memex should be created if not exists
    with patch("subprocess.check_output", return_value=b"sha"):
        git_hook.emit_commit_event(str(tmp_path))
        assert (tmp_path / ".memex").exists()

@patch("subprocess.check_output", side_effect=Exception("Git fail"))
def test_emit_commit_event_git_error(mock_subproc, tmp_path):
    # Should not crash
    git_hook.emit_commit_event(str(tmp_path))
    assert not (tmp_path / ".memex" / "pending_commit.json").exists()

@patch("subprocess.check_output")
def test_emit_commit_event_initial_commit(mock_subproc, tmp_path):
    # Mock git commands to simulate initial commit (HEAD exists, but HEAD~1 fails)
    def side_effect(cmd, cwd=None):
        if "HEAD~1" in cmd:
            raise subprocess.CalledProcessError(1, cmd)
        if "rev-parse" in cmd: return b"sha123"
        if "log" in cmd: return b"initial commit"
        if "show" in cmd: return b"initial diff content"
        return b"file1.py"
        
    mock_subproc.side_effect = side_effect
    git_hook.emit_commit_event(str(tmp_path))
    
    pending_file = tmp_path / ".memex" / "pending_commit.json"
    assert pending_file.exists()
    assert "initial diff content" in pending_file.read_text()

def test_install_hooks_not_git_repo(tmp_path):
    with pytest.raises(ValueError) as exc:
        git_hook.install_hooks(str(tmp_path))
    assert "Not a git repository" in str(exc.value)
