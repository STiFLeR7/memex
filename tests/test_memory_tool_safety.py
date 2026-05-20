"""Tests for ``memex.memory_tool.safety``.

Covers the four path-traversal vectors enumerated in ARCHITECTURE
§11.5 safety guards:

* literal ``..`` segments
* URL-encoded ``../`` (``%2e%2e%2f`` / ``%252e``)
* symlink-escape via ``_validate_no_symlink_escape``
* missing ``/memories`` prefix
"""

from __future__ import annotations

import os
import platform

import pytest

from memex.memory_tool.safety import (
    PathTraversalError,
    validate_memory_path,
    validate_no_symlink_escape,
)


# ---------------------------------------------------------------------------
# validate_memory_path
# ---------------------------------------------------------------------------


def test_validate_memory_path_strips_trailing_slash():
    """Audit B4 — the docstring promises 'no trailing slash unless root', and
    `_route` depends on it: `/memories/scratch` is a protected root, but
    `/memories/scratch/` (trailing slash) must NOT slip past as a deletable
    `scratch` zone. Canonicalize at the source."""
    assert validate_memory_path("/memories/scratch/") == "/memories/scratch"
    assert validate_memory_path("/memories/repos/") == "/memories/repos"
    assert validate_memory_path("/memories/") == "/memories"
    # No-trailing-slash inputs are unchanged.
    assert validate_memory_path("/memories/scratch") == "/memories/scratch"
    # Interior content is preserved.
    assert validate_memory_path("/memories/scratch/sess/f.txt") == "/memories/scratch/sess/f.txt"


def test_path_traversal_dotdot_is_rejected():
    with pytest.raises(PathTraversalError) as exc:
        validate_memory_path("/memories/../etc/passwd")
    assert "parent-directory" in str(exc.value).lower() or "escape" in str(
        exc.value
    ).lower()


def test_path_traversal_inline_dotdot_segment_is_rejected():
    with pytest.raises(PathTraversalError):
        validate_memory_path("/memories/scratch/../../etc/passwd")


def test_path_traversal_url_encoded_is_rejected():
    with pytest.raises(PathTraversalError):
        validate_memory_path("/memories/%2e%2e/etc/passwd")


def test_path_traversal_double_url_encoded_is_rejected():
    # Defensive: %252e%252e is a layered encoding of %2e%2e (i.e. ..).
    with pytest.raises(PathTraversalError):
        validate_memory_path("/memories/%252e%252e/etc/passwd")


def test_path_must_start_with_memories():
    with pytest.raises(PathTraversalError) as exc:
        validate_memory_path("/etc/passwd")
    assert "/memories" in str(exc.value)


def test_path_backslash_is_rejected():
    """Backslash is a Windows-style traversal vector; reject regardless
    of platform so the same path that fails on Windows fails on Linux."""
    with pytest.raises(PathTraversalError):
        validate_memory_path("/memories\\..\\etc")


def test_path_nul_byte_is_rejected():
    with pytest.raises(PathTraversalError):
        validate_memory_path("/memories/scratch/file\x00.md")


def test_path_must_be_a_string():
    with pytest.raises(PathTraversalError):
        validate_memory_path(123)  # type: ignore[arg-type]


def test_valid_paths_pass_through():
    """Sanity: well-formed paths must survive validation."""
    for p in [
        "/memories",
        "/memories/scratch",
        "/memories/scratch/session-1/notes.md",
        "/memories/repos/myrepo/graph/decisions/use-neo4j.md",
    ]:
        assert validate_memory_path(p) == p


# ---------------------------------------------------------------------------
# validate_no_symlink_escape
# ---------------------------------------------------------------------------


@pytest.mark.skipif(
    platform.system() == "Windows",
    reason="os.symlink on Windows requires admin/dev mode — out of scope",
)
def test_path_traversal_symlink_escape_is_rejected(tmp_path):
    """A symlink inside the memory root that points outside the root
    must be rejected."""
    memory_root = tmp_path / "memories"
    memory_root.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    sneaky = memory_root / "sneaky"
    os.symlink(str(outside), str(sneaky))

    with pytest.raises(PathTraversalError):
        validate_no_symlink_escape(sneaky / "loot", memory_root)


def test_validate_no_symlink_escape_passes_normal_paths(tmp_path):
    memory_root = tmp_path / "memories"
    memory_root.mkdir()
    target = memory_root / "scratch" / "session" / "notes.md"
    # Target need not exist; the function walks up to find a resolvable
    # ancestor and verifies that ancestor is inside the root.
    validate_no_symlink_escape(target, memory_root)
