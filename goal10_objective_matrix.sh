#!/usr/bin/env bash
set -u
# The shared CREDS file is the normal source. The legacy credential export
# file is also supported because it contains the provider keys used by this
# benchmark. Values are never printed. Filtered lines are sourced so headers
# in the export file cannot become shell commands.
set -a
if [ -f /mnt/d/memex/CREDS.txt ]; then
  source <(sed -e 's/\r$//' -n -e '/^[A-Za-z_][A-Za-z0-9_]*=/p' /mnt/d/memex/CREDS.txt)
fi
source <(find /mnt/d/memex -maxdepth 1 -type f -name 'NVIDIA_NIM_API_KEY=*' -exec sed -e 's/\r$//' -n -e '/^[A-Za-z_][A-Za-z0-9_]*=/p' {} \;)
set +a
export NVIDIA_API_KEY="$NVIDIA_NIM_API_KEY"
export NVIDIA_BASE_URL="$NVIDIA_NIM_BASE_URL"
# Hermes inference is independently selectable from memex's embedding/LLM
# backend. OpenRouter is preferred when configured and uses its free router.
if [ -n "${GOAL10_LLM_PROVIDER:-}" ]; then
  :
elif [ -n "${OPENROUTER_API_KEY:-}" ]; then
  export GOAL10_LLM_PROVIDER=openrouter
elif [ -n "${GROQ_API_KEY:-}" ]; then
  export GOAL10_LLM_PROVIDER=groq
else
  export GOAL10_LLM_PROVIDER=nvidia
fi
if [ "$GOAL10_LLM_PROVIDER" = "openrouter" ]; then
  export OPENROUTER_BASE_URL="${OPENROUTER_BASE_URL:-https://openrouter.ai/api/v1}"
  export GOAL10_MODEL="${GOAL10_MODEL:-stealth/ox-alpha}"
elif [ "$GOAL10_LLM_PROVIDER" = "groq" ]; then
  export GROQ_BASE_URL="${GROQ_BASE_URL:-https://api.groq.com/openai/v1}"
  export GOAL10_MODEL="${GOAL10_MODEL:-llama-3.3-70b-versatile}"
else
  export GOAL10_MODEL="${GOAL10_MODEL:-nvidia/llama-3.3-nemotron-super-49b-v1.5}"
fi
export MEMEX_LLM_PROVIDER=nvidia
export MEMEX_LLM_API_KEY="$NVIDIA_NIM_API_KEY"
export MEMEX_LLM_BASE_URL="$NVIDIA_NIM_BASE_URL"
export MEMEX_LLM_MODEL="${MEMEX_LLM_MODEL:-nvidia/llama-3.3-nemotron-super-49b-v1.5}"
export MEMEX_EMBEDDING_MODEL=nvidia/nemotron-3-embed-1b
export MEMEX_EMBEDDING_DIM=2048
neo4j_auth=$(docker inspect -f '{{range .Config.Env}}{{println .}}{{end}}' memex-neo4j | awk -F= '$1=="NEO4J_AUTH"{print $2; exit}')
export NEO4J_USER="${neo4j_auth%%/*}"
export NEO4J_PASSWORD="${neo4j_auth#*/}"
export NEO4J_URI=bolt://127.0.0.1:7687
export NEO4J_DATABASE=neo4j
if [ "${1:-}" != "" ]; then
  export GOAL10_CASES="$1"
fi
if [ "${2:-}" != "" ]; then
  export GOAL10_HERMES_TIMEOUT="$2"
fi
exec /mnt/d/memex/.goal10-hermes-venv/bin/python /mnt/d/memex/goal10_objective_matrix.py
