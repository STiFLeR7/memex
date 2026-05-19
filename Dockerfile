# memex MCP server — Glama / MCP directory introspection image.
#
# This image starts the memex MCP server in introspection-only mode so MCP
# directories (e.g. glama.ai) can verify the server boots and enumerate its
# tools without a live Neo4j or Gemini backend. For real use, run via uv/pip
# with the documented env vars instead — see https://github.com/STiFLeR7/memex
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MEMEX_INTROSPECTION_ONLY=1

WORKDIR /app

# Install the published package + the optional cluster extra so all tool
# imports resolve cleanly during introspection.
RUN pip install --upgrade pip \
 && pip install "memex-mcp[cluster]"

# Glama mounts the user's repo at /repo for real use; we just need a path
# that exists during introspection.
RUN mkdir -p /repo

CMD ["memex", "serve", "--repo", "/repo", "--transport", "stdio"]
