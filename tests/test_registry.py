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

def test_add_repository_with_project_id(temp_registry):
    """Phase 00 (NET-01) — add_repository() persists a resolved project_id
    onto the registered Repository entry."""
    repo_path = Path("/tmp/fake-repo").absolute()
    if os.name == 'nt':
        repo_path = Path("C:/fake-repo").absolute()

    registry.add_repository(str(repo_path), "test-repo", project_id="acme/widgets")

    repos = registry.get_repositories()
    assert len(repos) == 1
    assert repos[0].project_id == "acme/widgets"


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
    """add_key() with no --role stores role='admin' (CLI-invocation default,
    matches today's implicit full-access behavior) and NEVER a plaintext
    `key` field — only `key_hash` (sha256 hex digest) + `key_prefix`."""
    key1 = registry.add_key("test-client")
    keys = registry.get_keys()
    assert len(keys) == 1
    assert keys[0]["name"] == "test-client"
    assert keys[0]["role"] == "admin"
    assert "key" not in keys[0]
    assert keys[0]["key_hash"] == __import__("hashlib").sha256(key1.encode()).hexdigest()
    assert keys[0]["key_prefix"] == key1[:10]
    assert key1.startswith("mx_")

    key2 = registry.add_key("test-client")
    keys = registry.get_keys()
    assert len(keys) == 1
    assert keys[0]["key_prefix"] == key2[:10]
    assert key2 != key1


def test_add_key_with_explicit_role_and_principal_id(temp_registry):
    key = registry.add_key("viewer-key", role="viewer", principal_id="alice")
    keys = registry.get_keys()
    assert keys[0]["role"] == "viewer"
    assert keys[0]["principal_id"] == "alice"
    assert key.startswith("mx_")


def test_add_key_principal_id_defaults_to_name(temp_registry):
    registry.add_key("bob")
    keys = registry.get_keys()
    assert keys[0]["principal_id"] == "bob"


@pytest.mark.asyncio
async def test_resolve_principal_returns_principal_for_valid_token(temp_registry):
    key = registry.add_key("carol", role="contributor")
    principal = await registry.resolve_principal(key)
    assert principal is not None
    assert principal.principal_id == "carol"
    assert principal.role == "contributor"


@pytest.mark.asyncio
async def test_resolve_principal_returns_none_for_garbage_token(temp_registry):
    registry.add_key("carol")
    principal = await registry.resolve_principal("mx_totally_bogus_token")
    assert principal is None


@pytest.mark.asyncio
async def test_resolve_principal_legacy_record_resolves_to_admin_sentinel(temp_registry):
    """A hand-constructed legacy record (v0.6.1 registry.json shape — no
    `role`, no `key_hash`) must resolve to role='admin', the migration
    sentinel — NOT the Principal model's own 'contributor' default
    (02-RESEARCH.md Pitfall 1 / NET-11)."""
    legacy_registry = registry._load_registry()
    legacy_registry.keys.append({
        "name": "old",
        "key": "mx_deadbeefdeadbeefdeadbeefdeadbeef",
        "created_at": "2025-01-01T00:00:00",
    })
    registry._save_registry(legacy_registry)

    principal = await registry.resolve_principal("mx_deadbeefdeadbeefdeadbeefdeadbeef")
    assert principal is not None
    assert principal.role == "admin"
    assert principal.principal_id == "old"


def test_validate_key_accepts_legacy_plaintext_and_new_hash_only_keys(temp_registry):
    """validate_key() must authenticate both a legacy plaintext-stored key
    AND a newly-created (hash-only) key."""
    new_key = registry.add_key("new-format")

    legacy_registry = registry._load_registry()
    legacy_registry.keys.append({
        "name": "legacy",
        "key": "mx_legacyplaintextkeyvalue0000000",
        "created_at": "2025-01-01T00:00:00",
    })
    registry._save_registry(legacy_registry)

    assert registry.validate_key(new_key) is True
    assert registry.validate_key("mx_legacyplaintextkeyvalue0000000") is True
    assert registry.validate_key("mx_not_a_real_key") is False


def test_list_keys_displays_key_prefix_for_new_format_records(temp_registry):
    """list_keys() no longer raises/returns None for new-format records
    that have no plaintext `key` field — it displays `key_prefix` instead."""
    key = registry.add_key("prefixed")
    listed = registry.list_keys()
    assert len(listed) == 1
    assert listed[0]["key_prefix"] == key[:10]
    assert "key" not in listed[0]
    assert listed[0]["role"] == "admin"
    assert listed[0]["principal_id"] == "prefixed"


def test_list_keys_falls_back_to_truncated_legacy_key(temp_registry):
    legacy_registry = registry._load_registry()
    legacy_registry.keys.append({
        "name": "legacy",
        "key": "mx_legacyplaintextkeyvalue0000000",
        "created_at": "2025-01-01T00:00:00",
    })
    registry._save_registry(legacy_registry)

    listed = registry.list_keys()
    assert listed[0]["key_prefix"] == "mx_legacyp..."
    assert listed[0]["role"] == "admin"
