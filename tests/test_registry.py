import pytest
import os
from pathlib import Path
from memex.watcher import registry

@pytest.fixture
def temp_registry(tmp_path):
    reg_path = tmp_path / "registry.json"
    old_path = registry.REGISTRY_PATH
    registry.REGISTRY_PATH = reg_path
    yield reg_path
    registry.REGISTRY_PATH = old_path

def test_validate_key_accepts_real_rejects_bogus(temp_registry):
    """Audit B6 — validate_key authorizes the HTTP transport. Must accept a
    real key and reject empty/wrong ones (now via constant-time compare)."""
    real = registry.add_key("ci")
    assert registry.validate_key(real) is True
    assert registry.validate_key("mx_deadbeef") is False
    assert registry.validate_key("") is False
    assert registry.validate_key(None) is False


def test_add_repository(temp_registry):
    repo_path = Path("/tmp/fake-repo").absolute()
    # On Windows absolute path starts with drive letter
    if os.name == 'nt':
        repo_path = Path("C:/fake-repo").absolute()
    
    registry.add_repository(str(repo_path), "test-repo")
    
    repos = registry.get_repositories()
    assert len(repos) == 1
    assert repos[0].name == "test-repo"
    assert repos[0].path == str(repo_path)
    assert repos[0].active is True

def test_add_repository_default_name(temp_registry):
    repo_path = Path("/tmp/fake-repo").absolute()
    if os.name == 'nt':
        repo_path = Path("C:/fake-repo").absolute()

    registry.add_repository(str(repo_path))
    
    repos = registry.get_repositories()
    assert len(repos) == 1
    assert repos[0].name == "fake-repo"

def test_remove_repository(temp_registry):
    repo_path = Path("/tmp/fake-repo").absolute()
    if os.name == 'nt':
        repo_path = Path("C:/fake-repo").absolute()

    registry.add_repository(str(repo_path))
    assert len(registry.get_repositories()) == 1
    
    registry.remove_repository(str(repo_path))
    assert len(registry.get_repositories()) == 0

def test_get_active_repositories(temp_registry):
    repo1 = Path("/tmp/repo1").absolute()
    repo2 = Path("/tmp/repo2").absolute()
    if os.name == 'nt':
        repo1 = Path("C:/repo1").absolute()
        repo2 = Path("C:/repo2").absolute()

    registry.add_repository(str(repo1), "repo1")
    registry.add_repository(str(repo2), "repo2")
    
    registry.toggle_repository_active(str(repo1), False)
    
    active = registry.get_active_repositories()
    assert len(active) == 1
    assert active[0].name == "repo2"

def test_add_key(temp_registry):
    key1 = registry.add_key("test-client")
    keys = registry.get_keys()
    assert len(keys) == 1
    assert keys[0]["name"] == "test-client"
    assert keys[0]["key"] == key1
    assert key1.startswith("mx_")
    
    key2 = registry.add_key("test-client")
    keys = registry.get_keys()
    assert len(keys) == 1
    assert keys[0]["key"] == key2
    assert key2 != key1
