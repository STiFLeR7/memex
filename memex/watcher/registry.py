import hashlib
import hmac
import json
import os
import secrets
from pathlib import Path
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict
from memex.graph.schema import Principal, Repository

# Default registry path
DEFAULT_REGISTRY_DIR = Path.home() / ".memex"
DEFAULT_REGISTRY_PATH = DEFAULT_REGISTRY_DIR / "registry.json"

# Global variable that can be overridden for tests via env var
# or by directly modifying it in the module.
REGISTRY_PATH = Path(os.getenv("MEMEX_REGISTRY_PATH", str(DEFAULT_REGISTRY_PATH)))

class RegistrySchema(BaseModel):
    model_config = ConfigDict(extra="ignore")
    repositories: List[Repository] = []
    keys: List[dict] = []

def _load_registry() -> RegistrySchema:
    if not REGISTRY_PATH.exists():
        return RegistrySchema()
    try:
        with open(REGISTRY_PATH, "r") as f:
            data = json.load(f)
            return RegistrySchema.model_validate(data)
    except (json.JSONDecodeError, ValueError):
        return RegistrySchema()

def _save_registry(registry: RegistrySchema) -> None:
    REGISTRY_PATH.parent.mkdir(parents=True, exist_ok=True)
    temp_path = REGISTRY_PATH.with_suffix(".tmp")
    with open(temp_path, "w") as f:
        # Use mode='json' to ensure datetime objects are serialized
        json.dump(registry.model_dump(mode='json'), f, indent=2)
    temp_path.replace(REGISTRY_PATH)

def add_repository(path: str, name: Optional[str] = None, project_id: Optional[str] = None) -> None:
    """Adds a repo to the registry. If no name is provided, use the directory name.

    ``project_id`` (Phase 00 / NET-01) is persisted best-effort — resolved by
    the caller (typically `memex.config.resolve_project_id()`) and stored
    alongside the path-keyed entry. Re-running with a newly-resolvable
    ``project_id`` updates the existing entry in place (mirrors the
    existing ``name`` update-in-place behavior below).
    """
    path_obj = Path(path).resolve()
    if not name:
        name = path_obj.name

    registry = _load_registry()

    # Check if already exists
    for repo in registry.repositories:
        if repo.path == str(path_obj):
            repo.name = name
            if project_id is not None:
                repo.project_id = project_id
            _save_registry(registry)
            return

    new_repo = Repository(
        path=str(path_obj),
        name=name,
        added_at=datetime.now(),
        active=True,
        project_id=project_id,
    )
    registry.repositories.append(new_repo)
    _save_registry(registry)

def remove_repository(path: str) -> None:
    """Removes a repo from the registry."""
    path_obj = Path(path).resolve()
    registry = _load_registry()
    registry.repositories = [r for r in registry.repositories if r.path != str(path_obj)]
    _save_registry(registry)

def get_repositories() -> List[Repository]:
    """Returns a list of all registered repositories as Repository objects."""
    return _load_registry().repositories

def get_active_repositories() -> List[Repository]:
    """Returns only active ones."""
    return [r for r in _load_registry().repositories if r.active]

def toggle_repository_active(path: str, active: bool) -> None:
    """Enables/disables a repo in the registry."""
    path_obj = Path(path).resolve()
    registry = _load_registry()
    for repo in registry.repositories:
        if repo.path == str(path_obj):
            repo.active = active
            break
    _save_registry(registry)

def add_key(name: str, role: str = "admin", principal_id: Optional[str] = None) -> str:
    """Generates and adds a new mx_... key to the registry.

    ``role`` default is "admin" here (CLI-invocation default) to match
    today's v0.6.1 full-access behavior for anyone who runs `memex keys add`
    without `--role` — NOT to be confused with the `Principal` model's own
    field default used elsewhere (see 02-RESEARCH.md Common Pitfalls #1).

    The bearer secret is stored as a SHA-256 hash (`key_hash`) only — never
    plaintext (T-02-01). ``key_prefix`` (first 10 chars of the raw key, e.g.
    `mx_ab12cd34`) is retained, non-secret, for `keys list` display.
    """
    new_key = f"mx_{secrets.token_hex(16)}"
    key_hash = hashlib.sha256(new_key.encode()).hexdigest()

    registry = _load_registry()

    # Remove existing key with same name if any
    registry.keys = [k for k in registry.keys if k.get("name") != name]

    registry.keys.append({
        "name": name,
        "principal_id": principal_id or name,
        "role": role,
        "key_hash": key_hash,
        "key_prefix": new_key[:10],
        "created_at": datetime.now().isoformat(),
    })
    _save_registry(registry)
    return new_key  # plaintext returned ONCE to the operator, never stored

def list_keys() -> List[dict]:
    """Returns all named keys with secret material redacted.

    New-format records (no plaintext `key` field) display `key_prefix`
    instead. Legacy records (pre-Phase-02, plaintext `key`) fall back to a
    truncated form of that plaintext field so `keys list` never raises or
    silently drops them.
    """
    keys = _load_registry().keys
    result = []
    for k in keys:
        key_prefix = k.get("key_prefix")
        if not key_prefix:
            legacy_key = k.get("key")
            key_prefix = (legacy_key[:10] + "...") if legacy_key else None
        result.append({
            "name": k.get("name"),
            "principal_id": k.get("principal_id") or k.get("name"),
            "role": k.get("role") or "admin",
            "key_prefix": key_prefix,
            "created_at": k.get("created_at"),
        })
    return result

def revoke_key(name: str) -> bool:
    """Removes a key by name. Returns True if found and removed."""
    registry = _load_registry()
    original_len = len(registry.keys)
    registry.keys = [k for k in registry.keys if k.get("name") != name]
    if len(registry.keys) < original_len:
        _save_registry(registry)
        return True
    return False

def _match_key_record(token: str) -> Optional[dict]:
    """Scans every stored key record for a match against ``token``.

    Checks both the SHA-256 hash (new-format records, `key_hash`) and a
    direct compare against a legacy plaintext `key` field, using
    `hmac.compare_digest` for both and never short-circuiting on first
    match — preserves the existing B6 timing-safety property (validation
    time must not leak which, or how many, records matched). Shared by
    `validate_key()` and `resolve_principal()` so both stay consistent.
    """
    token_hash = hashlib.sha256(str(token).encode()).hexdigest()
    keys = _load_registry().keys
    match = None
    for k in keys:
        stored_hash = k.get("key_hash") or ""
        stored_plain = k.get("key") or ""
        hash_match = hmac.compare_digest(stored_hash, token_hash)
        plain_match = hmac.compare_digest(str(stored_plain), str(token))
        if hash_match or plain_match:
            match = k
    return match

def validate_key(key: str) -> bool:
    """Checks if a key exists in the registry.

    Hashes the presented key and compares against `key_hash` (new-format
    records) OR falls back to a direct compare against the legacy plaintext
    `key` field — both via `hmac.compare_digest`, scanning every record
    without short-circuiting (B6 timing-safety, preserved).
    """
    if not key:
        return False
    return _match_key_record(key) is not None

async def resolve_principal(token: str) -> Optional[Principal]:
    """Resolves a bearer token to a `Principal`, or `None` if invalid.

    This is the abstraction boundary consumed by untrusted remote HTTP
    callers (Plan 02-02) — whatever storage backs it can change without
    touching any call site. `async def` because future storage backends may
    need I/O (today's implementation reads the local registry file only, no
    actual `await` needed beyond the function being a coroutine).
    """
    if not token:
        return None
    match = _match_key_record(token)
    if match is None:
        return None

    # Pitfall 1 (02-RESEARCH.md): explicit presence check, NOT
    # `match.get("role", "contributor")` — a legacy key record (pre-Phase-02,
    # no `role` field at all) must resolve to "admin" (the migration
    # sentinel, T-02-04), which is semantically different from the
    # `Principal` model's own creation-time default of "contributor" used
    # for records where `role` IS present but empty/null.
    if "role" not in match:
        role = "admin"
    else:
        role = match.get("role") or "admin"

    return Principal(
        principal_id=match.get("principal_id") or match.get("name"),
        display_name=match.get("name"),
        role=role,
        active=True,
    )

def get_keys() -> List[dict]:
    """Returns all keys from the registry (raw records, for internal use)."""
    return _load_registry().keys
