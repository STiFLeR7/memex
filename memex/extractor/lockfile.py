"""Lockfile + manifest parsers → Dependency nodes + IMPORTS edges.

Ships with v0.3.0 Phase 6 (ARCHITECTURE-v0.3.0 §10). Two public entry
points:

* :func:`extract_dependencies` parses lockfiles / manifests across four
  ecosystems and returns :class:`~memex.graph.schema.Dependency` Pydantic
  instances. This is the *external* package layer (third-party deps).
* :func:`extract_module_imports` walks source files in the repo and emits
  ``(from_module, to_module, metadata)`` tuples for ``IMPORTS`` edges.
  This is the *internal* graph used by Phase 6's hybrid-Leiden cluster
  assignment (deferred to dev2). It also runs from the watcher when a
  lockfile changes, so Dependency nodes stay fresh.

Scope notes
-----------

For external dependency parsing, the goal is producing well-typed
:class:`Dependency` records — name + version + ecosystem. Lockfile
formats are heterogeneous (``requirements.txt`` has no version pinning
guarantee, ``go.sum`` has multiple lines per module). We intentionally
choose pragmatic parsing over exhaustive correctness; tests cover the
common-case shapes.

For module-import edges, only Python is fully implemented via the stdlib
``ast`` module (which is more reliable than tree-sitter for this narrow
job and avoids a tree-sitter language switch). Node / Cargo / Go source
import resolution is significant work and deferred: the dependency
extraction half still ships for those ecosystems.
"""

from __future__ import annotations

import ast
import asyncio
import json
import logging
import re
from datetime import datetime, UTC
from pathlib import Path
from typing import Any, Iterable

from memex.graph.schema import Dependency

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Ecosystem-specific dependency parsers
# ---------------------------------------------------------------------------


_REQUIREMENT_LINE = re.compile(
    r"""
    ^\s*
    (?P<name>[A-Za-z0-9_.\-]+)
    (?:\s*\[[^\]]+\])?               # extras: foo[bar]
    \s*
    (?:
        (?P<op>==|>=|<=|~=|!=|>|<|===)
        \s*
        (?P<version>[^\s;#]+)
    )?
    """,
    re.VERBOSE,
)


def _parse_requirements_txt(text: str) -> list[tuple[str, str]]:
    """Parse a requirements.txt-style file into ``[(name, version)]`` tuples.

    Skips blank lines, comments, ``-r`` includes, and editable installs.
    Returns ``"*"`` for the version when no pin is present.
    """
    out: list[tuple[str, str]] = []
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("-") or line.startswith("git+") or line.startswith("http"):
            # -r includes, -e editable installs, direct URLs: skip
            continue
        # Strip env markers / inline comments
        line = line.split(";", 1)[0].strip()
        line = line.split("#", 1)[0].strip()
        if not line:
            continue
        m = _REQUIREMENT_LINE.match(line)
        if not m:
            continue
        name = m.group("name")
        version = m.group("version") or "*"
        out.append((name, version))
    return out


def _parse_pyproject_toml(text: str) -> list[tuple[str, str]]:
    """Extract dependency names + versions from a ``pyproject.toml`` blob.

    Reads PEP 621 ``[project.dependencies]`` and
    ``[project.optional-dependencies.*]``. Poetry's ``[tool.poetry.dependencies]``
    is also recognised as a fallback (different version-spec syntax).
    """
    try:
        import tomllib  # Python 3.11+
    except ModuleNotFoundError:  # pragma: no cover - covered by 3.11+ requirement
        import tomli as tomllib  # type: ignore[no-redef]

    try:
        data = tomllib.loads(text)
    except Exception as exc:
        logger.warning("pyproject.toml: parse failed: %s", exc)
        return []

    out: list[tuple[str, str]] = []

    project = data.get("project") or {}
    for spec in project.get("dependencies", []) or []:
        if not isinstance(spec, str):
            continue
        m = _REQUIREMENT_LINE.match(spec)
        if m:
            out.append((m.group("name"), m.group("version") or "*"))

    optional = project.get("optional-dependencies") or {}
    for group_specs in optional.values():
        for spec in group_specs or []:
            if not isinstance(spec, str):
                continue
            m = _REQUIREMENT_LINE.match(spec)
            if m:
                out.append((m.group("name"), m.group("version") or "*"))

    # Poetry fallback
    poetry = ((data.get("tool") or {}).get("poetry") or {}).get("dependencies") or {}
    for name, spec in poetry.items():
        if name.lower() == "python":
            continue
        if isinstance(spec, str):
            version = spec.lstrip("^~>=<! ").strip() or "*"
        elif isinstance(spec, dict):
            version = str(spec.get("version", "*")).lstrip("^~>=<! ").strip() or "*"
        else:
            version = "*"
        out.append((name, version))

    return out


def _parse_package_json(text: str) -> list[tuple[str, str]]:
    """Extract dependencies from a ``package.json`` blob.

    Reads ``dependencies`` and ``devDependencies``. Version strings are
    returned verbatim (e.g. ``"^1.2.3"``); downstream consumers may strip
    npm range operators if needed.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("package.json: parse failed: %s", exc)
        return []

    out: list[tuple[str, str]] = []
    for key in ("dependencies", "devDependencies", "peerDependencies"):
        section = data.get(key) or {}
        if not isinstance(section, dict):
            continue
        for name, version in section.items():
            if isinstance(name, str) and isinstance(version, str):
                out.append((name, version))
    return out


def _parse_package_lock_json(text: str) -> list[tuple[str, str]]:
    """Extract resolved dependency versions from a ``package-lock.json`` blob.

    Lockfile v2/v3 places everything under ``packages``; v1 used
    ``dependencies``. We try both for robustness.
    """
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        logger.warning("package-lock.json: parse failed: %s", exc)
        return []

    out: list[tuple[str, str]] = []

    packages = data.get("packages")
    if isinstance(packages, dict):
        for path, info in packages.items():
            if not path or not isinstance(info, dict):
                continue
            # Path "" is the root package itself
            if path == "":
                continue
            # path like "node_modules/foo" or "node_modules/@scope/bar"
            name = info.get("name")
            if not name:
                parts = path.split("node_modules/")
                if len(parts) >= 2:
                    name = parts[-1]
            version = info.get("version") or "*"
            if isinstance(name, str):
                out.append((name, str(version)))
        if out:
            return out

    deps = data.get("dependencies")
    if isinstance(deps, dict):
        for name, info in deps.items():
            if isinstance(info, dict):
                version = info.get("version") or "*"
                out.append((name, str(version)))
    return out


def _parse_cargo_toml(text: str) -> list[tuple[str, str]]:
    """Extract crate dependencies from a ``Cargo.toml`` blob.

    Reads ``[dependencies]``, ``[dev-dependencies]`` and ``[build-dependencies]``.
    Supports both ``name = "1.0"`` and ``name = { version = "1.0" }`` shapes.
    """
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover
        import tomli as tomllib  # type: ignore[no-redef]

    try:
        data = tomllib.loads(text)
    except Exception as exc:
        logger.warning("Cargo.toml: parse failed: %s", exc)
        return []

    out: list[tuple[str, str]] = []
    for table in ("dependencies", "dev-dependencies", "build-dependencies"):
        section = data.get(table) or {}
        if not isinstance(section, dict):
            continue
        for name, spec in section.items():
            if isinstance(spec, str):
                version = spec
            elif isinstance(spec, dict):
                version = str(spec.get("version", "*"))
            else:
                version = "*"
            out.append((name, version))
    return out


def _parse_cargo_lock(text: str) -> list[tuple[str, str]]:
    """Extract resolved crate versions from a ``Cargo.lock`` blob.

    Cargo.lock is TOML with repeated ``[[package]]`` tables.
    """
    try:
        import tomllib
    except ModuleNotFoundError:  # pragma: no cover
        import tomli as tomllib  # type: ignore[no-redef]

    try:
        data = tomllib.loads(text)
    except Exception as exc:
        logger.warning("Cargo.lock: parse failed: %s", exc)
        return []

    out: list[tuple[str, str]] = []
    for pkg in data.get("package", []) or []:
        if not isinstance(pkg, dict):
            continue
        name = pkg.get("name")
        version = pkg.get("version") or "*"
        if isinstance(name, str):
            out.append((name, str(version)))
    return out


_GO_REQUIRE_LINE = re.compile(
    r"""
    ^\s*
    (?P<name>[^\s]+)
    \s+
    (?P<version>v[^\s]+)
    """,
    re.VERBOSE,
)


def _parse_go_mod(text: str) -> list[tuple[str, str]]:
    """Extract module dependencies from a ``go.mod`` blob.

    Handles both single-line ``require name version`` and block
    ``require ( ... )`` forms. Lines marked ``// indirect`` are still
    included — they're real transitive deps.
    """
    out: list[tuple[str, str]] = []
    in_block = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("//"):
            continue
        if line.startswith("require ("):
            in_block = True
            continue
        if in_block and line == ")":
            in_block = False
            continue
        if line.startswith("require "):
            line = line[len("require "):]
        elif not in_block:
            continue
        # Strip trailing "// indirect" comment
        line = line.split("//", 1)[0].strip()
        if not line:
            continue
        m = _GO_REQUIRE_LINE.match(line)
        if m:
            out.append((m.group("name"), m.group("version")))
    return out


def _parse_go_sum(text: str) -> list[tuple[str, str]]:
    """Extract module versions from a ``go.sum`` blob.

    go.sum has two lines per module (``module version hash`` and
    ``module version/go.mod hash``). We dedupe on (name, version).
    """
    seen: set[tuple[str, str]] = set()
    for raw in text.splitlines():
        parts = raw.strip().split()
        if len(parts) < 2:
            continue
        name = parts[0]
        version = parts[1]
        # Skip the "/go.mod" pseudo-version lines
        if version.endswith("/go.mod"):
            version = version[: -len("/go.mod")]
        seen.add((name, version))
    return sorted(seen)


# ---------------------------------------------------------------------------
# Public surface — async
# ---------------------------------------------------------------------------


# (filename → (ecosystem, parser, is_lockfile))
# ``is_lockfile=True`` records take precedence on (name, ecosystem) collisions
# because they hold the *resolved* version rather than a range specifier.
_LOCKFILE_PARSERS: dict[str, tuple[str, Any, bool]] = {
    "requirements.txt":  ("pypi",  _parse_requirements_txt,  False),
    "pyproject.toml":    ("pypi",  _parse_pyproject_toml,    False),
    "package.json":      ("npm",   _parse_package_json,      False),
    "package-lock.json": ("npm",   _parse_package_lock_json, True),
    "Cargo.toml":        ("cargo", _parse_cargo_toml,        False),
    "Cargo.lock":        ("cargo", _parse_cargo_lock,        True),
    "go.mod":            ("go",    _parse_go_mod,            False),
    "go.sum":            ("go",    _parse_go_sum,            True),
}


def _find_lockfiles(
    repo_root: Path,
) -> Iterable[tuple[Path, str, Any, bool]]:
    """Yield ``(path, ecosystem, parser, is_lockfile)`` for every recognised file."""
    for filename, (ecosystem, parser, is_lock) in _LOCKFILE_PARSERS.items():
        candidate = repo_root / filename
        if candidate.is_file():
            yield candidate, ecosystem, parser, is_lock


def _dedupe_dependencies(
    pairs: list[tuple[str, str, str, bool]],
) -> list[tuple[str, str, str]]:
    """Deduplicate ``(name, version, ecosystem, is_lockfile)`` tuples.

    Preference order on collision:

    1. Lockfile-sourced concrete versions beat manifest-sourced specs.
    2. Any concrete version beats the ``"*"`` placeholder.
    """
    by_key: dict[tuple[str, str], tuple[str, str, str, bool]] = {}
    for name, version, ecosystem, is_lock in pairs:
        key = (name, ecosystem)
        existing = by_key.get(key)
        if existing is None:
            by_key[key] = (name, version, ecosystem, is_lock)
            continue
        _, ex_version, _, ex_is_lock = existing
        # Lockfile data always wins
        if is_lock and not ex_is_lock:
            by_key[key] = (name, version, ecosystem, is_lock)
            continue
        if ex_is_lock and not is_lock:
            continue
        # Both are same tier: a concrete version beats "*"
        if ex_version == "*" and version != "*":
            by_key[key] = (name, version, ecosystem, is_lock)
    return [(n, v, e) for (n, v, e, _) in by_key.values()]


def _read_text(path: Path) -> str:
    try:
        return path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return path.read_text(encoding="utf-8", errors="replace")


def _extract_dependencies_sync(repo_root: Path) -> list[Dependency]:
    now = datetime.now(UTC)
    quads: list[tuple[str, str, str, bool]] = []
    for path, ecosystem, parser, is_lock in _find_lockfiles(repo_root):
        try:
            text = _read_text(path)
        except OSError as exc:
            logger.warning("lockfile: could not read %s: %s", path, exc)
            continue
        try:
            for name, version in parser(text):
                if not name:
                    continue
                quads.append((name, version, ecosystem, is_lock))
        except Exception:
            logger.exception("lockfile: parser failed for %s", path)

    deps: list[Dependency] = []
    for name, version, ecosystem in _dedupe_dependencies(quads):
        try:
            deps.append(
                Dependency(
                    name=name,
                    version=version,
                    ecosystem=ecosystem,
                    last_updated=now,
                )
            )
        except Exception:
            logger.exception(
                "lockfile: schema validation failed for %s %s (%s)",
                name,
                version,
                ecosystem,
            )
    return deps


async def extract_dependencies(repo_root: str | Path) -> list[Dependency]:
    """Parse every supported lockfile / manifest under ``repo_root``.

    Returns :class:`Dependency` instances suitable for writing to the graph.
    Runs the synchronous parsers inside ``asyncio.to_thread`` so the watcher
    event loop is never blocked on file IO.
    """
    repo_path = Path(repo_root)
    return await asyncio.to_thread(_extract_dependencies_sync, repo_path)


# ---------------------------------------------------------------------------
# Module-import edges (Python only — see scope notes at top of file)
# ---------------------------------------------------------------------------


def _python_files(repo_root: Path) -> Iterable[Path]:
    """Yield repo-relative ``.py`` files, skipping common vendored / build dirs."""
    skip_dirs = {
        ".git",
        ".venv",
        "venv",
        "env",
        "__pycache__",
        ".mypy_cache",
        ".pytest_cache",
        ".tox",
        "node_modules",
        "build",
        "dist",
        ".memex",
    }
    for path in repo_root.rglob("*.py"):
        if any(part in skip_dirs for part in path.relative_to(repo_root).parts):
            continue
        yield path


def _path_to_module(rel_path: Path) -> str:
    """``foo/bar/baz.py`` → ``foo.bar.baz``; ``foo/__init__.py`` → ``foo``."""
    parts = list(rel_path.with_suffix("").parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _collect_repo_modules(repo_root: Path) -> set[str]:
    """All importable Python modules inside ``repo_root``."""
    mods: set[str] = set()
    for f in _python_files(repo_root):
        mods.add(_path_to_module(f.relative_to(repo_root)))
    return mods


def _resolve_relative(from_module: str, level: int, name: str | None) -> str | None:
    """Resolve a ``from .x import y`` style import to an absolute module path.

    ``from_module`` is the dotted-path of the *file containing* the import
    (e.g. ``myapp.subpkg.main`` for ``myapp/subpkg/main.py``). For level=1
    we drop one trailing component (the file itself), for level=2 we drop
    two, and so on. ``name`` is the dotted suffix (may be ``None`` for
    ``from . import x``).
    """
    if level <= 0:
        return name
    pkg_parts = from_module.split(".")
    if level > len(pkg_parts):
        return None
    base = pkg_parts[: len(pkg_parts) - level]
    if name:
        base = base + name.split(".")
    if not base:
        return None
    return ".".join(base)


def _extract_python_imports_for_file(
    path: Path,
    rel_module: str,
    repo_modules: set[str],
) -> list[tuple[str, str, dict]]:
    """Yield ``(from_module, to_module, metadata)`` for in-repo imports."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        logger.debug("imports: could not read %s: %s", path, exc)
        return []

    try:
        tree = ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        logger.debug("imports: syntax error in %s: %s", path, exc)
        return []

    edges: list[tuple[str, str, dict]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                target = alias.name
                if _is_repo_module(target, repo_modules):
                    edges.append(
                        (rel_module, target, {"kind": "import"})
                    )
        elif isinstance(node, ast.ImportFrom):
            target = _resolve_relative(rel_module, node.level or 0, node.module)
            if target is None:
                continue
            # `from pkg import sub` may name a submodule rather than a symbol.
            # Prefer the more-specific submodule edge when *that exact candidate*
            # is a repo module; otherwise fall back to the package edge.
            matched_submodule = False
            for alias in node.names:
                candidate = f"{target}.{alias.name}" if target else alias.name
                if candidate in repo_modules:
                    edges.append(
                        (rel_module, candidate, {"kind": "from-import"})
                    )
                    matched_submodule = True
            if not matched_submodule and _is_repo_module(target, repo_modules):
                edges.append(
                    (rel_module, target, {"kind": "from-import"})
                )
    return edges


def _is_repo_module(name: str, repo_modules: set[str]) -> bool:
    """Match either an exact module or its parent package."""
    if name in repo_modules:
        return True
    # `import pkg.sub` may resolve to a package whose `__init__.py` is in repo_modules.
    parts = name.split(".")
    for i in range(len(parts), 0, -1):
        if ".".join(parts[:i]) in repo_modules:
            return True
    return False


def _extract_module_imports_sync(repo_root: Path) -> list[tuple[str, str, dict]]:
    repo_modules = _collect_repo_modules(repo_root)
    edges: list[tuple[str, str, dict]] = []
    for f in _python_files(repo_root):
        rel_module = _path_to_module(f.relative_to(repo_root))
        edges.extend(_extract_python_imports_for_file(f, rel_module, repo_modules))
    # Deduplicate (from, to) pairs while preserving the first metadata seen
    seen: dict[tuple[str, str], dict] = {}
    for src, dst, meta in edges:
        if src == dst:
            continue
        key = (src, dst)
        if key not in seen:
            seen[key] = meta
    return [(src, dst, meta) for (src, dst), meta in seen.items()]


async def extract_module_imports(
    repo_root: str | Path,
) -> list[tuple[str, str, dict]]:
    """Walk every ``.py`` file under ``repo_root`` and emit module IMPORTS edges.

    Returns ``(from_module, to_module, metadata)`` tuples where both endpoints
    resolve to modules that exist inside the repo. External-dependency edges
    are out of scope here — they are represented by :class:`Dependency` nodes
    produced by :func:`extract_dependencies`.

    Python is the only language implemented today; Node / Cargo / Go source
    walking is deferred (see scope notes at the top of this module).
    """
    repo_path = Path(repo_root)
    return await asyncio.to_thread(_extract_module_imports_sync, repo_path)


# ---------------------------------------------------------------------------
# Watcher integration helper
# ---------------------------------------------------------------------------


LOCKFILE_FILENAMES: frozenset[str] = frozenset(_LOCKFILE_PARSERS.keys())


def is_lockfile_path(path: str | Path) -> bool:
    """Return True if ``path``'s basename is a recognised lockfile / manifest."""
    return Path(path).name in LOCKFILE_FILENAMES
