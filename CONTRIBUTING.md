# Contributing to memex

Thanks for your interest in improving memex! This guide covers local setup,
testing, and how to get a change merged. By participating you agree to the
[Code of Conduct](CODE_OF_CONDUCT.md).

## What memex is

A protocol-neutral engineering-context layer that builds a bitemporal
knowledge graph of a repository and serves bounded, provenance-aware context
to coding agents. The core stack is **tree-sitter** (extraction) → an
OpenAI-compatible or Gemini backend (synthesis/embeddings) → **Neo4j** via
**Graphiti** (storage) → **ContextPacket** selection → Hermes MemoryProvider
or MCP.

The v0.9 Hermes integration is read-only. Hermes owns personal memory, raw
session state, and execution state; memex owns repository engineering
knowledge, provenance, freshness, confidence, supersession, and task context.

## Prerequisites

- **Python ≥ 3.11**
- **[uv](https://docs.astral.sh/uv/)** for dependency management
- A reachable **Neo4j** instance (local Docker is fine)
- An LLM/embedding backend supported by your configuration. Gemini remains the
  default documented setup; NVIDIA NIM and other OpenAI-compatible endpoints
  can be configured with `MEMEX_LLM_PROVIDER`, `MEMEX_LLM_API_KEY`, and
  `MEMEX_LLM_BASE_URL`.
- Hermes is optional. Install it separately to use automatic provider prefetch;
  MCP remains available without Hermes.

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

For Hermes, enable the provider in its profile configuration:

```yaml
memory:
  provider: memex
plugins:
  memex:
    repo_path: /absolute/path/to/repository
    prefetch_timeout_seconds: 7
    max_items: 8
    max_chars: 12000
```

Prefetch is bounded and fail-open. The provider does not ingest Hermes
`state.db`, prompts, transcripts, tool results, or secrets. Use the MCP
`get_engineering_context` tool for explicit lookup when Hermes is unavailable.

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
(`.github/workflows/publish.yml`), which publishes **PyPI + npm + MCP Registry**.
Version bumps must update `pyproject.toml`, `npm/package.json`, `server.json`,
the lockfile, and the team Docker image tag; they must agree. The current
release line is `0.9.0`. Maintainers cut releases; contributors don't need to
bump versions in their PRs.

The three publish jobs are **idempotent** — a version already on PyPI/npm is
skipped rather than failing the run. So a maintainer can publish manually and
still let CI complete the MCP Registry listing, and re-runs are safe.

**Authentication (no npm 2FA/OTP in CI):**

- **npm → OIDC Trusted Publishing.** No `NPM_API` token; the job uses
  `id-token: write` and npm ≥ 11.5.1. **One-time setup on npmjs.com** (required
  before the npm job can publish): package `stifler-memex-mcp` → *Settings* →
  *Trusted Publisher* → GitHub Actions → org `STiFLeR7`, repo `memex`, workflow
  `publish.yml`, allowed action `npm publish`.
- **PyPI → token.** Repo secret `PYPI_API` (`__token__` / API token). Trusted
  publishing optional; the token path is already OTP-free.
- **MCP Registry → GitHub OIDC.** No stored secret; the workflow's OIDC identity
  (`STiFLeR7/memex`) authorizes the `io.github.stifler7/*` namespace.

To publish a release:

```bash
uv build
uv run twine check dist/*
cd npm && npm pack --dry-run
```

After the artifacts pass validation, create and push the matching tag. The
workflow publishes only from a tag or an explicit maintainer dispatch. PyPI
uses the `PYPI_API` repository secret; npm uses OIDC trusted publishing; the
MCP Registry uses GitHub OIDC. To re-register an already-published version,
run **Actions → Publish → Run workflow** (`workflow_dispatch`).
