# v1 onboarding: setup, host recipes, demos and rollback

Status: written for the v1 release candidate, `1.0.0-rc.1`. Validated on Windows 11, with memex's test suites also validated on Linux (see [Platforms](#platforms)). Measured costs and limits are in [23_PHASE5_VERIFICATION.md](23_PHASE5_VERIFICATION.md).

## What v1 gives each host

| Capability | Claude Code 2.1.289 | Codex 0.157.1 |
| --- | --- | --- |
| **Initial context:** a working set delivered at session start, with delivery confirmed from the client's own transcript | Yes | Yes, in app-server (IDE/desktop) sessions |
| **Advisory delivery:** context without blocking | Yes, as an advisory reason when freshness is unknown | Yes, as above |
| **Action reconsideration:** an affected edit is held, a correction delivered, and the agent revises | Yes (`Edit`, `Write`, `MultiEdit`, `NotebookEdit`) | Yes (`apply_patch`). **Not under `codex exec`, which runs no hooks** |
| **Shell edits** | Opaque: reported, never certified | As Claude Code |
| **Guarded writes** (opt-in) | Cooperating writers through `guard_read`/`guard_write` only | As Claude Code |
| **Hook failure** | Fails open: the edit proceeds without a certificate | As Claude Code |

## Prerequisites

- Python 3.11 or newer and [uv](https://docs.astral.sh/uv/).
- Git.
- A reachable Neo4j 5 server you already run. Docker is not required, and memex does not change your datastore.
- The host client you use, logged in as usual. memex never asks for or stores host credentials.

## Setup against an existing Neo4j

```powershell
git clone https://github.com/STiFLeR7/memex; cd memex
git checkout v1.0.0-rc.1             # the v1 release candidate
uv sync --python 3.12
$env:NEO4J_URI='bolt://your-neo4j:7687'; $env:NEO4J_USER='neo4j'; $env:NEO4J_PASSWORD='...'
```

Or put `NEO4J_URI`, `NEO4J_USER` and `NEO4J_PASSWORD` in a `.env` file at the repository root, as v0.9 already supports. Then, in the repository you want memex to serve:

```powershell
uv run --project <memex checkout> memex v1 doctor          # what is set up, what is missing, what to do next
uv run --project <memex checkout> memex v1 migrate --dry-run
uv run --project <memex checkout> memex v1 migrate         # additive; legacy data is never modified
uv run --project <memex checkout> memex v1 legacy          # imported knowledge, marked legacy_unverified
```

The migration imports v0.9 decisions and problems as `legacy_unverified`, keeping approved authority, timestamps and confidence. That knowledge is available for historical lookup, but is never presented as verified current context, because its source bytes and applicable view cannot be reconstructed.

## Host recipes

Both recipes install hooks only into the checkout's own project configuration. Your global client settings are not edited.

### Claude Code

```powershell
memex v1 install claude        # registers you and adds hooks to .claude/settings.json, keeping other hooks
memex v1 mode shadow           # optional first week: record what would be corrected, change nothing
memex v1 mode live
```

Start Claude Code in the checkout as usual. Transcripts must stay under the client's config directory (`~/.claude`, or `CLAUDE_CONFIG_DIR`), because memex confirms delivery from them.

### Codex

```powershell
memex v1 install codex --neo4j-uri bolt://your-neo4j:7687   # adds hooks to .codex/hooks.json
```

Open Codex in the checkout (the IDE extension, desktop app or interactive client) and review memex's hooks with `/hooks`. Untrusted hooks do not run, and memex never grants trust itself.

Codex filters a hook's environment, so the graph URI is stored in the registration and the launcher carries `CODEX_HOME`. Credentials are not stored: an authenticated Neo4j must be reachable with the environment the launcher sets. `codex exec` runs no hooks, so it gets no context and no checks.

### Guarded writes (optional)

```powershell
memex v1 guarded on
```

In guarded mode both adapters deny native edits to supported files and direct the agent to the `memex_guard` MCP server (`python -m memex.integrations.guard_mcp --root <checkout> --label <host>`). Add it to the client's MCP configuration yourself. The guard protects only writes that go through it. Editors, shell writes, other tools and other machines bypass it, and their changes are detected at the next guarded write, not prevented.

## Demos

**Mutation-before-edit (native, real host and real graph).** This needs a reachable Neo4j and a logged-in client, and consumes one real model session of two turns:

```powershell
$env:MEMEX_PHASE1_NEO4J_URI='bolt://your-neo4j:7687'
uv run python tests/phase5_trials.py one D01 E claude --out demo-out     # or: one D01 E codex
```

The agent plans an implementation of `send()` from the delivered contract for `validate()`. Between its turns a teammate changes `validate()` to raise. The record in `demo-out/trials/D01-E-claude.json` shows:
- the delivery confirmed from the client's transcript;
- the change;
- every edit attempt and memex's decision on it;
- the hidden checks the final code passed.

A Codex run adds one project-trust entry to your Codex `config.toml` for the temporary directory. `tests/phase4_client_config.py` removes exactly those entries afterwards; see its usage text.

**Core contract (deterministic, no services).** For contributors without a graph server or a model:

```powershell
uv run python -m memex.evaluation.core_demo
```

It runs the real v1 core (Git capture, journal, parsing, verification, task store, action check) against in-memory doubles for Neo4j and the host. It is labeled a core-contract demo. It does not prove native delivery or real-graph behavior.

## Diagnostics

`memex v1 doctor` reports:
- the repository and worktree identity;
- the mode and guarded state;
- observed versus indexed generations;
- pending corrections;
- sessions that must resynchronize;
- the last failed insertion;
- exhausted reconsideration;
- interrupted guarded writes;
- each host's registration, hooks and adapter version.

Graph reachability is reported separately, and never as a freshness result. Messages say what to do, for example *"index is 2 generations behind; current actions are checked as unknown until it catches up."*

**Hook service.** Installed hooks run `memex/hook_client.py`. It hands each event to a local memex process that stays up between events, so a warm check skips interpreter start and graph connection. The first event after a quiet period starts that process and runs one-shot. The process listens on 127.0.0.1 only, answers only requests carrying the token in `~/.memex/hookd/`, and exits after 15 idle minutes (`MEMEX_HOOKD_IDLE_SECONDS`). Set `MEMEX_HOOKD=0` to run every event one-shot. Reinstalling the hooks replaces an earlier one-shot entry.

## Rollback

```powershell
memex v1 rollback      # mode off, guarded writes off, cooperating leases expired
memex v1 uninstall claude; memex v1 uninstall codex   # optional: remove project hooks too
```

Rollback keeps the journal, the repository mapping, evidence, migration records, the trace and any unresolved interrupted write. Legacy nodes were never modified, so v0.9 readers return exactly what they returned before migration. Running `memex v1 mode live` again forces every bound session to resynchronize before its next affected action.

## Platforms

| Platform | Status |
| --- | --- |
| Windows 11 (Python 3.12.13, Git for Windows) | Validated: all Phase 1–5 suites and native host runs |
| Linux | **Test suites validated** in Docker: Debian (python:3.12-slim) under Docker's WSL2 kernel, Python 3.12.15, Neo4j 5.26.31, commit `8c56d0c` with the Windows lockfile: broad suite 915 passed (11 platform skips), Phase 5 205 passed, mechanism 16/16 with isolation and authority passed, core demo as expected; the migration suite passed again in a fresh container. Claude Code and Codex were not run natively on Linux. The native host-client integration (hooks fired by a real client on Linux) is **not yet validated**. The client-config cleanup helper for trial machines writes only on Windows and reports `pending` elsewhere |
| macOS | Not validated |

## Known limitations

- **Fail-open by default.** A hook timeout or error lets the edit proceed on both hosts.
- **Guarded writes cover cooperating writers only.**
- **Interruption recovery is not power-loss protection.** Recovery from interrupted guarded writes and confirmations is proven against a process stopping, not against power loss.
- **Host insertion formats are undocumented.** Delivery confirmation depends on each client's undocumented transcript and rollout formats. If they change, memex redelivers rather than wrongly confirming.
- **Action checks are slow.** Each one starts a Python process, connects to the graph and re-captures sources. Measured latency is in 23.
