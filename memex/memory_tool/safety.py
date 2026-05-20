"""Path-traversal guards for the memory-tool adapter.

Mirrors the protections from Anthropic's reference
``BetaAsyncLocalFilesystemMemoryTool`` (``_validate_no_symlink_escape``,
``_validate_path``) but exposes them as small, importable helpers so the
scratch zone and the projection zone can both reuse them without depending
on a real filesystem root.

The functions here are pure-logical: they check the textual / canonical
form of a path. Anchoring to an actual filesystem root happens in
``scratch.py`` (which has a real ``base_path``) and in ``server.py``
(which routes to the appropriate zone).
"""

from __future__ import annotations

import os
from pathlib import Path
from urllib.parse import unquote


class PathTraversalError(ValueError):
    """Raised when a memory-tool path attempts to escape ``/memories``."""


_MEMORY_ROOT = "/memories"


def validate_memory_path(path: str, root: str = _MEMORY_ROOT) -> str:
    """Validate that ``path`` lives under the virtual ``/memories`` root.

    Returns the canonicalised virtual path (forward-slash, no trailing slash
    unless it is the root itself). Raises ``PathTraversalError`` for any
    attempted escape.

    Rules (mirrors Anthropic's reference impl):
      * must start with ``/memories``
      * no ``..`` segments after URL-decoding
      * no backslashes (the memory-tool virtual filesystem uses forward
        slashes; backslashes are a common Windows-escape vector)
      * no NUL bytes
    """
    if not isinstance(path, str):
        raise PathTraversalError(f"Path must be a string, got {type(path).__name__}")

    if "\x00" in path:
        raise PathTraversalError("Path contains NUL byte")

    # Reject raw backslashes — Anthropic's protocol uses forward slashes
    # for the virtual /memories filesystem; a backslash is a common
    # Windows-style traversal vector.
    if "\\" in path:
        raise PathTraversalError(f"Path contains backslash: {path}")

    # URL-decode once so %2e%2e%2f surfaces as ../.
    decoded = unquote(path)

    if not decoded.startswith(root):
        raise PathTraversalError(
            f"Path must start with {root}, got: {path}"
        )

    # Reject `..` segments anywhere after decoding.
    # We split on '/' and check segment-by-segment so 'foo..bar' (which
    # is harmless) isn't rejected, while '/memories/../etc' is.
    segments = decoded.split("/")
    for seg in segments:
        if seg == "..":
            raise PathTraversalError(
                f"Path contains parent-directory segment: {path}"
            )

    # Also reject the encoded form even if unquote() didn't catch it
    # (defensive — unquote handles %2e%2e but be paranoid about layered
    # encoding like %252e%252e).
    lowered = path.lower()
    if "%2e%2e" in lowered or "%252e" in lowered:
        raise PathTraversalError(
            f"Path contains URL-encoded parent-directory segment: {path}"
        )

    # Canonicalize trailing slashes (audit B4). Without this, `/memories/scratch/`
    # would NOT match the protected-root check for `/memories/scratch` in
    # server._route and would fall through to the deletable 'scratch' zone — a
    # protection bypass. Never strip below the root itself.
    canonical = decoded.rstrip("/")
    if not canonical:
        canonical = root
    return canonical


def validate_no_symlink_escape(target_path: Path, memory_root: Path) -> None:
    """Walk up ``target_path`` and ensure no component's resolution lands
    outside ``memory_root``.

    This is a direct port of Anthropic's reference
    ``_validate_no_symlink_escape``. It tolerates non-existent leaves
    (we may be about to create them) but rejects any existing symlink
    whose target escapes the root.
    """
    resolved_root = memory_root.resolve()

    current = target_path
    while True:
        try:
            resolved = current.resolve()
            if resolved != resolved_root and not str(resolved).startswith(
                str(resolved_root) + os.sep
            ):
                raise PathTraversalError(
                    "Path would escape /memories directory via symlink"
                )
            return
        except (FileNotFoundError, OSError):
            parent = current.parent
            if parent == current or current == memory_root:
                return
            current = parent
