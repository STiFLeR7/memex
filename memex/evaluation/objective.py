"""Small objective checks for live engineering-task evaluations."""

from __future__ import annotations

from pathlib import Path
from typing import Mapping, Sequence


def verify_file_state(
    repo: str | Path,
    *,
    required_files: Sequence[str] = (),
    expected_text: Mapping[str, Sequence[str]] | None = None,
    forbidden_text: Mapping[str, Sequence[str]] | None = None,
) -> tuple[bool, list[str]]:
    """Verify observable repository state without trusting agent prose."""
    root = Path(repo)
    failures: list[str] = []
    for relative in required_files:
        if not (root / relative).is_file():
            failures.append(f"missing_file:{relative}")
    for relative, needles in (expected_text or {}).items():
        path = root / relative
        if not path.is_file():
            failures.append(f"missing_file:{relative}")
            continue
        content = path.read_text(encoding="utf-8")
        for needle in needles:
            if needle not in content:
                failures.append(f"missing_text:{relative}:{needle}")
    for relative, needles in (forbidden_text or {}).items():
        path = root / relative
        if not path.is_file():
            continue
        content = path.read_text(encoding="utf-8")
        for needle in needles:
            if needle in content:
                failures.append(f"forbidden_text:{relative}:{needle}")
    return not failures, failures
