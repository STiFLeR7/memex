import os
import pytest
from pathlib import Path

# Force MEMEX_REGISTRY_PATH to a non-existent temp file during import
# to prevent tests from interacting with the user's live registry.
os.environ["MEMEX_REGISTRY_PATH"] = str(Path(__file__).parent / "test_registry_tmp.json")

@pytest.fixture(scope="session", autouse=True)
def clean_registry_path(tmp_path_factory):
    temp_file = tmp_path_factory.mktemp("registry") / "registry.json"
    from memex.watcher import registry
    registry.REGISTRY_PATH = temp_file
    yield
