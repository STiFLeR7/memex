#!/usr/bin/env bash
# memex-server container entrypoint.
#
# Owns exactly two things:
#   1. A defensive, idempotent first-boot check that provisions an admin
#      bearer key if one doesn't already exist in the (persisted) principal
#      registry.
#   2. The final `exec memex serve --transport http ...` that becomes PID 1.
#
# Deliberately does NOT implement any Neo4j-readiness polling/sleep loop --
# that's Docker Compose's job via `depends_on: condition: service_healthy`
# (wired in plan 06-02), not the entrypoint's. See 06-RESEARCH.md
# Pattern 2 / Pitfall 2 for why Neo4j's own credential (NEO4J_AUTH) is a
# separate, pre-`up` concern (docker/bootstrap-team-env.sh) from this
# in-container admin-key provisioning, which is genuine app runtime logic.
set -euo pipefail

# `memex keys add <name> --role {viewer,contributor,admin}` is the current
# CLI surface (memex/cli.py) -- Phase 02 (RBAC/team auth) has landed. The
# plain-name fallback below is kept as defensive dead code in case this
# image is ever built against an older/rolled-back CLI that predates
# --role; it costs nothing and never runs against today's CLI.
provision_admin_key() {
  if memex keys list 2>/dev/null | grep -qE '^admin(\s|$)'; then
    echo "Admin key already provisioned -- skipping first-boot key creation."
    return 0
  fi

  echo "No admin key found -- provisioning one (first boot only)."

  if memex keys add admin --role admin >/tmp/keys-add.out 2>/tmp/keys-add.err; then
    cat /tmp/keys-add.out
    echo "Provisioned admin key with role support"
  else
    echo "memex keys add admin --role admin failed: $(tail -n1 /tmp/keys-add.err 2>/dev/null) -- falling back to plain 'memex keys add admin'"
    if memex keys add admin >/tmp/keys-add.out 2>/tmp/keys-add.err; then
      cat /tmp/keys-add.out
      echo "Provisioned admin key (role support not yet available -- Phase 02 pending)"
    else
      echo "WARNING: failed to provision an admin key: $(tail -n1 /tmp/keys-add.err 2>/dev/null) -- continuing startup without one. Provision manually via 'docker compose exec memex-server memex keys add admin --role admin'." >&2
    fi
  fi
  rm -f /tmp/keys-add.out /tmp/keys-add.err
}

# Never gate server startup on successful key provisioning -- the
# entrypoint's job is to attempt it, not to crash-loop the container if it
# fails (e.g. registry file not yet writable).
provision_admin_key || echo "WARNING: admin key provisioning step failed unexpectedly -- continuing startup." >&2

# The key itself is shown only once (in the log line above) -- operators
# must capture it via `docker compose logs memex-server` (documented in
# plan 06-02's docs task).
exec memex serve --transport http --host 0.0.0.0 --port 8000
