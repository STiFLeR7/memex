import json
import os
from pathlib import Path
from typing import List, Optional
from datetime import datetime
from pydantic import BaseModel, ConfigDict
from memex.graph.schema import Repository

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

def add_repository(path: str, name: Optional[str] = None) -> None:
    """Adds a repo to the registry. If no name is provided, use the directory name."""
    path_obj = Path(path).resolve()
    if not name:
        name = path_obj.name
    
    registry = _load_registry()
    
    # Check if already exists
    for repo in registry.repositories:
        if repo.path == str(path_obj):
            repo.name = name
            _save_registry(registry)
            return

    new_repo = Repository(
        path=str(path_obj),
        name=name,
        added_at=datetime.now(),
        active=True
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

def add_key(name: str) -> str:
    """Generates and adds a new mx_... key to the registry."""
    import secrets
    new_key = f"mx_{secrets.token_hex(16)}"
    
    registry = _load_registry()
    
    # Remove existing key with same name if any
    registry.keys = [k for k in registry.keys if k.get("name") != name]
    
    registry.keys.append({
        "name": name, 
        "key": new_key,
        "created_at": datetime.now().isoformat()
    })
    _save_registry(registry)
    return new_key

def list_keys() -> List[dict]:
    """Returns all named keys (truncated)."""
    keys = _load_registry().keys
    # Return a copy with keys truncated for security
    return [
        {
            "name": k.get("name"),
            "key": k.get("key")[:7] + "..." if k.get("key") else None,
            "created_at": k.get("created_at")
        }
        for k in keys
    ]

def revoke_key(name: str) -> bool:
    """Removes a key by name. Returns True if found and removed."""
    registry = _load_registry()
    original_len = len(registry.keys)
    registry.keys = [k for k in registry.keys if k.get("name") != name]
    if len(registry.keys) < original_len:
        _save_registry(registry)
        return True
    return False

def validate_key(key: str) -> bool:
    """Checks if a key exists in the registry."""
    if not key:
        return False
    import secrets
    keys = _load_registry().keys
    # Constant-time compare against every stored key without short-circuiting,
    # so validation time doesn't leak which (or how many) prefixes matched (B6).
    found = False
    for k in keys:
        stored = k.get("key") or ""
        if secrets.compare_digest(str(stored), str(key)):
            found = True
    return found

def get_keys() -> List[dict]:
    """Returns all keys from the registry (full keys)."""
    return _load_registry().keys
