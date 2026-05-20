# Contributing to memex

Thanks for your interest in improving memex! This guide covers local setup,
testing, and how to get a change merged. By participating you agree to the
[Code of Conduct](CODE_OF_CONDUCT.md).

## What memex is

A daemon + MCP server that builds a bitemporal knowledge graph of your codebase
(modules, symbols, decisions, problems) and serves it to AI coding agents over
MCP. Core stack: **tree-sitter** (extraction) → **Gemini** (entity synthesis) →
**Neo4j** via **Graphiti** (storage) → **MCP** (serving).

## Prerequisites

- **Python ≥ 3.11**
- **[uv](https://docs.astral.sh/uv/)** for dependency management
- A reachable **Neo4j** instance (local Docker is fine)
- A **Google Gemini API key** (only needed for LLM-backed tools; pure-graph
  tools like `predict_impact` run without it)

## Local setup

```bash
git clone https://github.com/STiFLeR7/memex
cd memex

# Install all extras (pulls graspologic/hdbscan/scikit-learn for the cluster engine)
uv sync --all-extras

# Configure backends in a repo-local .env (gitignored — never commit it)
cat > .env <<'ENV'
NEO4J_URI=bolt://localhost:7687
NEO4J_USER=neo4j
NEO4J_PASSWORD=your-password
GEMINI_API_KEY=your-key
ENV

# Sanity check
uv run memex doctor
```

`.env` is gitignored and **must stay that way**. Never paste its contents into
an issue, PR, or log.

## Running tests

memex uses `pytest`. Integration tests that require a live Neo4j/Gemini backend
are marked with `@pytest.mark.integration` and are excluded by default.

```bash
# Fast suite (no backends required)
uv run pytest -m "not integration"

# Full suite (requires Neo4j + Gemini configured in .env)
uv run pytest
```

Please keep the non-integration suite green. Add tests for any behavior change —
bugs especially should land with a failing test first, then the fix.

## Linting

```bash
uv run ruff check .
```

Ruff is configured in `pyproject.toml` (rule sets `F` + `B`, line length 100).
`ruff check` must pass before a PR is merged.

## Pull request process

1. **Branch** off `master` (e.g. `fix/predict-impact-empty`, `feat/http-auth`).
2. **Keep changes focused** — one logical change per PR. No unrelated
   "while I'm here" refactors.
3. **Root-cause first.** For bug fixes, investigate and explain the cause in the
   PR description; don't patch symptoms.
4. **Tests + lint pass** locally (`pytest -m "not integration"` and
   `ruff check .`).
5. **Conventional commit** subject lines are preferred:
   `feat:`, `fix:`, `docs:`, `chore:`, `ci:`, `test:`, `refactor:`.
6. Open the PR against `master` with a clear description of *what* changed and
   *why*. Link any related issue.

Maintainers review regularly. CI (tests on push tags / PRs) must be green.

## Reporting bugs & requesting features

- **Bugs:** open a GitHub issue with the memex version, your platform, repro
  steps, and the observed vs. expected behavior. Include logs — with any
  secrets redacted.
- **Security issues:** do **not** open a public issue. Follow
  [SECURITY.md](SECURITY.md).
- **Features:** open an issue describing the use case before writing code, so we
  can align on the approach.

## Releases

Releases are tag-driven: pushing a `v*` tag runs the publish workflow
(PyPI + npm + MCP Registry). Maintainers cut releases; contributors don't need
to bump versions in their PRs.
