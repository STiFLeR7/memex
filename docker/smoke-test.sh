#!/usr/bin/env bash
# Smoke test for the self-hosted team deployment (docker/docker-compose.team.yml).
#
# Runnable codification of the three checks from
# .planning/phases/06-deployment/06-RESEARCH.md's "Phase Requirements -> Test
# Map" table, so NET-21/NET-22/NET-23 are independently verifiable, not just
# documented:
#
#   1. NET-21 -- `memex-server` builds successfully.
#   2. NET-22 -- `docker compose up` on a fresh machine (no docker/.env) fails
#      fast with a clear, actionable NEO4J_PASSWORD error instead of a silent
#      misconfiguration or crash loop.
#   3. NET-23 -- Neo4j's HTTP port (7474) is unreachable from the host while
#      memex-server's published port (8000) is reachable and healthy.
#
# This script NEVER calls `down -v`. `-v` removes ALL of the compose
# project's named volumes indiscriminately, including `principal_registry`
# -- exactly the destructive footgun this phase's volume-separation work
# exists to prevent. See docker/TEAM-DEPLOY.md for the full warning.
#
# Usage: bash docker/smoke-test.sh   (run from anywhere; paths are resolved
#                                      relative to this script's location)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR/.."

COMPOSE_FILE="docker/docker-compose.team.yml"
ENV_FILE="docker/.env"
ENV_BACKUP=""

PASS_COUNT=0
FAIL_COUNT=0
declare -a RESULTS=()

record_result() {
  local check="$1"
  local status="$2"
  RESULTS+=("${check}: ${status}")
  if [ "$status" = "PASS" ]; then
    PASS_COUNT=$((PASS_COUNT + 1))
  else
    FAIL_COUNT=$((FAIL_COUNT + 1))
  fi
}

cleanup() {
  # Always restore a real operator .env if this script's NET-22 check backed
  # one up, and always tear the stack down WITHOUT -v so principal_registry
  # and neo4j_data survive for the next run.
  if [ -n "$ENV_BACKUP" ] && [ -f "$ENV_BACKUP" ]; then
    mv -f "$ENV_BACKUP" "$ENV_FILE"
    echo "Restored original docker/.env from backup."
  fi
  docker compose -f "$COMPOSE_FILE" down >/dev/null 2>&1 || true
}
trap cleanup EXIT

echo "=== memex team deployment smoke test ==="
echo

# ---------------------------------------------------------------------------
# Check 1 (NET-21): memex-server builds successfully.
# ---------------------------------------------------------------------------
echo "[1/3] NET-21: building memex-server..."
if docker compose -f "$COMPOSE_FILE" build memex-server; then
  record_result "NET-21 (build memex-server)" "PASS"
else
  record_result "NET-21 (build memex-server)" "FAIL"
fi
echo

# ---------------------------------------------------------------------------
# Check 2 (NET-22): missing docker/.env fails fast with a clear error.
# Back up and restore any existing docker/.env -- never destroy an
# operator's real one.
# ---------------------------------------------------------------------------
echo "[2/3] NET-22: verifying fail-fast behavior with no docker/.env present..."
if [ -f "$ENV_FILE" ]; then
  ENV_BACKUP="$(mktemp)"
  cp "$ENV_FILE" "$ENV_BACKUP"
  rm -f "$ENV_FILE"
fi

UP_OUTPUT="$(docker compose -f "$COMPOSE_FILE" up 2>&1 || true)"

if [ -n "$ENV_BACKUP" ]; then
  mv -f "$ENV_BACKUP" "$ENV_FILE"
  ENV_BACKUP=""
  echo "Restored original docker/.env from backup."
fi

if echo "$UP_OUTPUT" | grep -q "NEO4J_PASSWORD"; then
  record_result "NET-22 (fail-fast on missing .env)" "PASS"
else
  record_result "NET-22 (fail-fast on missing .env)" "FAIL"
  echo "Expected 'up' output to mention NEO4J_PASSWORD, got:"
  echo "$UP_OUTPUT"
fi
echo

# ---------------------------------------------------------------------------
# Check 3 (NET-23): Neo4j HTTP port unreachable from host, memex-server
# reachable and healthy.
# ---------------------------------------------------------------------------
echo "[3/3] NET-23: verifying network isolation with the stack up..."
if [ ! -f "$ENV_FILE" ]; then
  echo "docker/.env is required for this check -- run docker/bootstrap-team-env.sh first."
  record_result "NET-23 (port isolation)" "FAIL"
elif ! docker compose -f "$COMPOSE_FILE" up -d; then
  # Guarded explicitly (not left to `set -e`) so a failed `up -d` (e.g. the
  # Docker daemon being unreachable, or a build failure) still lets this
  # script reach its own summary/exit-code logic instead of aborting
  # mid-script with no PASS/FAIL report at all.
  record_result "NET-23 (port isolation)" "FAIL"
  echo "docker compose up -d failed -- see output above."
else
  NEO4J_UNREACHABLE=false
  if ! curl -f --max-time 3 http://localhost:7474 >/dev/null 2>&1; then
    NEO4J_UNREACHABLE=true
  fi

  SERVER_REACHABLE=false
  # Give memex-server a few retries to finish booting behind neo4j's healthcheck.
  for _ in $(seq 1 10); do
    if curl -f --max-time 3 http://localhost:8000/health >/dev/null 2>&1; then
      SERVER_REACHABLE=true
      break
    fi
    sleep 3
  done

  if [ "$NEO4J_UNREACHABLE" = true ] && [ "$SERVER_REACHABLE" = true ]; then
    record_result "NET-23 (port isolation)" "PASS"
  else
    record_result "NET-23 (port isolation)" "FAIL"
    echo "Neo4j unreachable from host: $NEO4J_UNREACHABLE (expected true)"
    echo "memex-server reachable at /health: $SERVER_REACHABLE (expected true)"
  fi

  # Deliberately WITHOUT -v -- never destroy principal_registry/neo4j_data.
  docker compose -f "$COMPOSE_FILE" down || true
fi
echo

# ---------------------------------------------------------------------------
# Summary
# ---------------------------------------------------------------------------
echo "=== Summary ==="
for result in "${RESULTS[@]}"; do
  echo "  $result"
done
echo
echo "Passed: $PASS_COUNT / $((PASS_COUNT + FAIL_COUNT))"

if [ "$FAIL_COUNT" -gt 0 ]; then
  echo "RESULT: FAIL"
  exit 1
fi

echo "RESULT: PASS"
