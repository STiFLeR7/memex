# Self-hosted team deployment

This document covers `docker/docker-compose.team.yml` — a shared, network-isolated,
auth-on-by-default deployment for a team: one Neo4j instance and one memex-server
instance, reachable by everyone on the team through memex-server's HTTP API only.

It is a **separate, additive** file. It does not replace, modify, or depend on
`docker/docker-compose.yml`, which remains the existing single-developer manual-install
flow documented in the main [README](../README.md).

## 1. Bootstrap secrets (one time, before first `up`)

```bash
bash docker/bootstrap-team-env.sh
```

This generates `docker/.env` with a random `NEO4J_PASSWORD` (via the same
`secrets.token_hex()` primitive memex's own bearer-key generation uses) and a
placeholder `GEMINI_API_KEY=` line. It is idempotent — running it again when
`docker/.env` already exists leaves the file untouched and prints a message saying so.

**Edit `docker/.env` and fill in `GEMINI_API_KEY`** before the next step. Nothing else
in the file needs to be edited (`NEO4J_USER`/`NEO4J_PASSWORD` are already populated).

`docker/.env` is already covered by the repo's `.gitignore` (`.env` pattern) — never
commit it.

## 2. Bring the stack up

```bash
docker compose -f docker/docker-compose.team.yml up -d
```

This builds and starts two services:

- **`neo4j`** — internal-only. It has no `ports:` mapping at all, so its Bolt (7687)
  and HTTP (7474) interfaces are **not reachable from the host machine** — only from
  `memex-server`, over Compose's internal DNS (`bolt://neo4j:7687`). This is the
  deployment's compensating control for Neo4j Community Edition having no native
  per-user RBAC: the only way to reach the graph directly is by being inside the
  Compose network already.
- **`memex-server`** — the only service with a published port (`8000:8000`). This is
  the sole sanctioned entry point into the stack for team members.

`memex-server` waits for Neo4j's healthcheck (an HTTP probe against `:7474`, not the
`neo4j status` CLI command, which is documented as unreliable inside Docker) before it
starts, via `depends_on: condition: service_healthy`.

If `docker/.env` is missing or incomplete, `up` fails immediately with a clear error
naming the missing variable (e.g. `NEO4J_PASSWORD`) instead of starting with a silent
default or crash-looping.

## 3. Capture the initial admin key

On first boot, `memex-server`'s entrypoint provisions an initial `admin`-role bearer
key (`mx_...`) if one doesn't already exist, and logs it once to container stdout:

```bash
docker compose -f docker/docker-compose.team.yml logs memex-server
```

Copy the printed `mx_...` key and distribute it to whoever is administering the
deployment. **Rotate this key if your log aggregation is more broadly readable than
your own terminal** — e.g. if container logs are shipped to a centrally-accessible log
platform, treat the one-time stdout print as exposed the moment it's shipped, and issue
a fresh key via `memex keys add` / `memex keys revoke` once the deployment is reachable.

## 4. Shutting down

```bash
docker compose -f docker/docker-compose.team.yml down
```

Without `-v`, this is always safe for principal/role state: `principal_registry` is
its own named volume, separate from `neo4j_data`/`neo4j_logs`, specifically so that
`down` (no flag) — and any future graph-only reset — can never touch the team's
access-control registry.

### Warning: `down -v` still destroys `principal_registry`

**`docker compose -f docker/docker-compose.team.yml down -v` (WITH the `-v` flag)
removes ALL of this project's named volumes indiscriminately — including
`principal_registry`, not just `neo4j_data`/`neo4j_logs`.**

The volume separation in this deployment prevents an *accidental* graph-only reset
(e.g. an operator running plain `down` intending only to reset the graph) from
incidentally wiping the team's access list. It does **not** make the explicit `-v`
flag itself safe — `-v` is a deliberate "delete everything" operation and will delete
the principal/role registry along with the graph data every time it's used. Only use
`-v` when you genuinely intend to discard the entire deployment's state, including
every team member's provisioned access.

## Deferred to a fast-follow

This phase ships `build:` context only — `docker-compose.team.yml` builds
`memex-server` from local source on the operator's own machine
(`docker/Dockerfile.server`). **No `publish-docker` CI job exists yet, and no
registry-hosted `memex-server` image is published anywhere** (verified:
`.github/workflows/publish.yml` today only has `test`, `publish-pypi`, `publish-npm`,
and `publish-registry` jobs — no Docker build/push step).

This is a known, intentional deferral, not an oversight. A future fast-follow can add a
CI job that builds and pushes a per-release-tagged `memex-server` image to a registry
(e.g. GitHub Container Registry), letting operators `docker compose pull` instead of
building locally. Until then, every operator building this stack builds the image
themselves via the `build:` context already declared in
`docker/docker-compose.team.yml` — this works standalone and requires no registry.

## Related

- [`docker/docker-compose.team.yml`](./docker-compose.team.yml) — the compose file this
  document describes.
- [`docker/bootstrap-team-env.sh`](./bootstrap-team-env.sh) — the pre-`up` secret
  bootstrap script.
- [`docker/smoke-test.sh`](./smoke-test.sh) — runnable verification of this
  document's network-isolation and fail-fast claims.
- Main [README](../README.md) — single-developer manual-install flow
  (`docker/docker-compose.yml`), unaffected by this document.
