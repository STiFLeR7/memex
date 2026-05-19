"""Tests for ``memex.extractor.lockfile``."""

from __future__ import annotations

from pathlib import Path

import pytest

from memex.extractor.lockfile import (
    extract_dependencies,
    extract_module_imports,
    is_lockfile_path,
)
from memex.graph.schema import Dependency


# ---------------------------------------------------------------------------
# Dependency extraction
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_lockfile_extracts_python_imports(tmp_path: Path) -> None:
    (tmp_path / "requirements.txt").write_text(
        "fastapi==0.100.0\n"
        "uvicorn>=0.20\n"
        "# a comment\n"
        "neo4j\n"
        "-r other.txt\n"
        "git+https://github.com/foo/bar.git\n",
        encoding="utf-8",
    )
    (tmp_path / "pyproject.toml").write_text(
        '[project]\n'
        'name = "demo"\n'
        'version = "0.1.0"\n'
        'dependencies = [\n'
        '    "pydantic>=2.0",\n'
        '    "PyYAML>=6.0,<7.0",\n'
        ']\n'
        '\n'
        '[project.optional-dependencies]\n'
        'dev = ["pytest>=8.0"]\n',
        encoding="utf-8",
    )

    deps = await extract_dependencies(tmp_path)

    by_name = {d.name: d for d in deps}
    assert "fastapi" in by_name
    assert by_name["fastapi"].version == "0.100.0"
    assert by_name["fastapi"].ecosystem == "pypi"
    assert "uvicorn" in by_name and by_name["uvicorn"].version == "0.20"
    assert "neo4j" in by_name and by_name["neo4j"].version == "*"
    assert "pydantic" in by_name and by_name["pydantic"].version == "2.0"
    assert "PyYAML" in by_name
    assert "pytest" in by_name and by_name["pytest"].version == "8.0"
    # All Dependency objects validate cleanly
    for d in deps:
        assert isinstance(d, Dependency)


@pytest.mark.asyncio
async def test_lockfile_extracts_npm_imports(tmp_path: Path) -> None:
    (tmp_path / "package.json").write_text(
        '{\n'
        '  "name": "demo-app",\n'
        '  "version": "1.0.0",\n'
        '  "dependencies": {\n'
        '    "react": "^18.2.0",\n'
        '    "lodash": "4.17.21"\n'
        '  },\n'
        '  "devDependencies": {\n'
        '    "typescript": "^5.0.0"\n'
        '  }\n'
        '}\n',
        encoding="utf-8",
    )
    (tmp_path / "package-lock.json").write_text(
        '{\n'
        '  "name": "demo-app",\n'
        '  "lockfileVersion": 3,\n'
        '  "packages": {\n'
        '    "": {"name": "demo-app", "version": "1.0.0"},\n'
        '    "node_modules/react": {"version": "18.2.0"},\n'
        '    "node_modules/lodash": {"version": "4.17.21"},\n'
        '    "node_modules/@scope/pkg": {"version": "0.1.0"}\n'
        '  }\n'
        '}\n',
        encoding="utf-8",
    )

    deps = await extract_dependencies(tmp_path)

    by_name = {d.name: d for d in deps}
    assert "react" in by_name
    # Concrete lock version wins over the "^" range
    assert by_name["react"].version == "18.2.0"
    assert by_name["react"].ecosystem == "npm"
    assert "lodash" in by_name and by_name["lodash"].version == "4.17.21"
    assert "typescript" in by_name
    assert "@scope/pkg" in by_name and by_name["@scope/pkg"].version == "0.1.0"


@pytest.mark.asyncio
async def test_lockfile_extracts_cargo_imports(tmp_path: Path) -> None:
    (tmp_path / "Cargo.toml").write_text(
        '[package]\n'
        'name = "demo"\n'
        'version = "0.1.0"\n'
        '\n'
        '[dependencies]\n'
        'serde = "1.0"\n'
        'tokio = { version = "1.35", features = ["full"] }\n'
        '\n'
        '[dev-dependencies]\n'
        'criterion = "0.5"\n',
        encoding="utf-8",
    )
    (tmp_path / "Cargo.lock").write_text(
        'version = 3\n'
        '\n'
        '[[package]]\n'
        'name = "serde"\n'
        'version = "1.0.193"\n'
        '\n'
        '[[package]]\n'
        'name = "tokio"\n'
        'version = "1.35.1"\n',
        encoding="utf-8",
    )

    deps = await extract_dependencies(tmp_path)

    by_name = {d.name: d for d in deps}
    assert "serde" in by_name
    # Concrete lock version preferred over the Cargo.toml range
    assert by_name["serde"].version == "1.0.193"
    assert by_name["serde"].ecosystem == "cargo"
    assert "tokio" in by_name and by_name["tokio"].version == "1.35.1"
    assert "criterion" in by_name and by_name["criterion"].version == "0.5"


@pytest.mark.asyncio
async def test_lockfile_extracts_go_imports(tmp_path: Path) -> None:
    (tmp_path / "go.mod").write_text(
        "module example.com/demo\n"
        "\n"
        "go 1.22\n"
        "\n"
        "require (\n"
        "    github.com/spf13/cobra v1.8.0\n"
        "    github.com/stretchr/testify v1.9.0 // indirect\n"
        ")\n"
        "\n"
        "require golang.org/x/sync v0.5.0\n",
        encoding="utf-8",
    )
    (tmp_path / "go.sum").write_text(
        "github.com/spf13/cobra v1.8.0 h1:hash1\n"
        "github.com/spf13/cobra v1.8.0/go.mod h1:hash2\n"
        "github.com/stretchr/testify v1.9.0 h1:hash3\n"
        "github.com/stretchr/testify v1.9.0/go.mod h1:hash4\n"
        "golang.org/x/sync v0.5.0 h1:hash5\n"
        "golang.org/x/sync v0.5.0/go.mod h1:hash6\n",
        encoding="utf-8",
    )

    deps = await extract_dependencies(tmp_path)

    by_name = {d.name: d for d in deps}
    assert "github.com/spf13/cobra" in by_name
    assert by_name["github.com/spf13/cobra"].version == "v1.8.0"
    assert by_name["github.com/spf13/cobra"].ecosystem == "go"
    assert "github.com/stretchr/testify" in by_name
    assert "golang.org/x/sync" in by_name


# ---------------------------------------------------------------------------
# Module-import edges (Python source walk)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_module_imports_walks_python_sources(tmp_path: Path) -> None:
    pkg = tmp_path / "myapp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text("", encoding="utf-8")
    (pkg / "main.py").write_text(
        "from myapp import utils\n"
        "from myapp.helpers import greet\n"
        "import os\n",  # stdlib — should NOT appear
        encoding="utf-8",
    )
    (pkg / "utils.py").write_text("def helper():\n    return 42\n", encoding="utf-8")
    (pkg / "helpers.py").write_text("def greet():\n    return 'hi'\n", encoding="utf-8")

    edges = await extract_module_imports(tmp_path)
    edge_pairs = {(src, dst) for src, dst, _meta in edges}

    assert ("myapp.main", "myapp.utils") in edge_pairs
    assert ("myapp.main", "myapp.helpers") in edge_pairs
    # stdlib must not be emitted as an in-repo edge
    assert not any(dst == "os" for _src, dst in edge_pairs)


@pytest.mark.asyncio
async def test_is_lockfile_path_recognises_supported_filenames() -> None:
    assert is_lockfile_path("requirements.txt")
    assert is_lockfile_path("/abs/path/to/pyproject.toml")
    assert is_lockfile_path(Path("Cargo.lock"))
    assert is_lockfile_path("package-lock.json")
    assert not is_lockfile_path("README.md")
    assert not is_lockfile_path("memex/cli.py")
