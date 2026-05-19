"""FastAPI shim around ``MemexAsyncMemoryTool``.

Lets non-Python Anthropic SDK clients (TypeScript, Go, anything calling
the Anthropic API) use memex as their memory-tool backend. The shim is
thin: every endpoint maps 1:1 to a method on
``MemexAsyncMemoryTool``.

Bearer-auth pattern is inspired by ``memex/mcp_server/http.py`` but
intentionally simpler: a single key stored at ``.memex/memory_tool.key``,
generated on first run, printed once to stderr. This matches the
``mcp.http_key_path`` convention in config.yaml.
"""

from __future__ import annotations

import logging
import os
import secrets
import sys
from pathlib import Path
from typing import Any, Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel
import uvicorn

from anthropic.lib.tools._beta_functions import ToolError
from anthropic.types.beta import (
    BetaMemoryTool20250818CreateCommand,
    BetaMemoryTool20250818DeleteCommand,
    BetaMemoryTool20250818InsertCommand,
    BetaMemoryTool20250818RenameCommand,
    BetaMemoryTool20250818StrReplaceCommand,
    BetaMemoryTool20250818ViewCommand,
)

from memex.memory_tool.server import MemexAsyncMemoryTool

logger = logging.getLogger(__name__)


def _ensure_key(repo_root: str) -> str:
    """Read or generate the bearer key at ``.memex/memory_tool.key``."""
    key_path = Path(repo_root).resolve() / ".memex" / "memory_tool.key"
    key_path.parent.mkdir(parents=True, exist_ok=True)
    if key_path.exists():
        existing = key_path.read_text(encoding="utf-8").strip()
        if existing:
            return existing
    key = "memex-mt-" + secrets.token_urlsafe(32)
    key_path.write_text(key + "\n", encoding="utf-8")
    # Restrict the key file to owner read/write (POSIX) — silent no-op on
    # Windows where chmod's POSIX bits don't map. Matches the secret-key
    # hygiene the Anthropic reference impl uses for the memory file mode.
    try:
        os.chmod(key_path, 0o600)
    except (OSError, NotImplementedError):
        logger.debug("could not chmod bearer key file (likely non-POSIX); continuing")
    sys.stderr.write(
        f"[memex memory-tool] generated new bearer key at {key_path}\n"
        f"[memex memory-tool] key: {key}\n"
        f"[memex memory-tool] use Authorization: Bearer <key> for every request\n"
    )
    sys.stderr.flush()
    return key


# ---------------------------------------------------------------------------
# Request schemas (mirror the Anthropic command payloads)
# ---------------------------------------------------------------------------


class ViewRequest(BaseModel):
    path: str
    view_range: Optional[list[int]] = None


class CreateRequest(BaseModel):
    path: str
    file_text: str


class StrReplaceRequest(BaseModel):
    path: str
    old_str: str
    new_str: str


class InsertRequest(BaseModel):
    path: str
    insert_line: int
    insert_text: str


class DeleteRequest(BaseModel):
    path: str


class RenameRequest(BaseModel):
    old_path: str
    new_path: str


class MemoryToolResponse(BaseModel):
    is_error: bool
    content: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _verify_bearer(authorization: Optional[str], expected_key: str) -> None:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    token = authorization[len("Bearer "):]
    if not secrets.compare_digest(token, expected_key):
        raise HTTPException(status_code=401, detail="Invalid bearer token")


async def _invoke(coro) -> MemoryToolResponse:
    try:
        result = await coro
    except ToolError as err:
        # ToolError carries either a str message or a content-block list.
        if isinstance(err.content, str):
            return MemoryToolResponse(is_error=True, content=err.content)
        return MemoryToolResponse(is_error=True, content=str(err))
    return MemoryToolResponse(is_error=False, content=result)


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------


def create_app(
    repo_root: str,
    *,
    tool: Optional[MemexAsyncMemoryTool] = None,
    bearer_key: Optional[str] = None,
) -> FastAPI:
    """Build the FastAPI app.

    ``tool`` is exposed for tests so they can inject a custom
    ``MemexAsyncMemoryTool`` (e.g. one with a tmp scratch DB and a fake
    projection client).
    """
    app = FastAPI(
        title="memex memory-tool",
        description=f"Anthropic memory_20250818 backend for {repo_root}",
        version="0.3.0",
    )

    if tool is None:
        tool = MemexAsyncMemoryTool(repo_root=repo_root)
    if bearer_key is None:
        bearer_key = _ensure_key(repo_root)

    app.state.tool = tool
    app.state.bearer_key = bearer_key

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"status": "ok", "repo_root": repo_root}

    @app.post("/memory/view", response_model=MemoryToolResponse)
    async def view_endpoint(
        req: ViewRequest, authorization: Optional[str] = Header(None)
    ) -> MemoryToolResponse:
        _verify_bearer(authorization, app.state.bearer_key)
        cmd = BetaMemoryTool20250818ViewCommand.model_construct(
            command="view", path=req.path, view_range=req.view_range
        )
        return await _invoke(app.state.tool.view(cmd))

    @app.post("/memory/create", response_model=MemoryToolResponse)
    async def create_endpoint(
        req: CreateRequest, authorization: Optional[str] = Header(None)
    ) -> MemoryToolResponse:
        _verify_bearer(authorization, app.state.bearer_key)
        cmd = BetaMemoryTool20250818CreateCommand.model_construct(
            command="create", path=req.path, file_text=req.file_text
        )
        return await _invoke(app.state.tool.create(cmd))

    @app.post("/memory/str_replace", response_model=MemoryToolResponse)
    async def str_replace_endpoint(
        req: StrReplaceRequest, authorization: Optional[str] = Header(None)
    ) -> MemoryToolResponse:
        _verify_bearer(authorization, app.state.bearer_key)
        cmd = BetaMemoryTool20250818StrReplaceCommand.model_construct(
            command="str_replace",
            path=req.path,
            old_str=req.old_str,
            new_str=req.new_str,
        )
        return await _invoke(app.state.tool.str_replace(cmd))

    @app.post("/memory/insert", response_model=MemoryToolResponse)
    async def insert_endpoint(
        req: InsertRequest, authorization: Optional[str] = Header(None)
    ) -> MemoryToolResponse:
        _verify_bearer(authorization, app.state.bearer_key)
        cmd = BetaMemoryTool20250818InsertCommand.model_construct(
            command="insert",
            path=req.path,
            insert_line=req.insert_line,
            insert_text=req.insert_text,
        )
        return await _invoke(app.state.tool.insert(cmd))

    @app.post("/memory/delete", response_model=MemoryToolResponse)
    async def delete_endpoint(
        req: DeleteRequest, authorization: Optional[str] = Header(None)
    ) -> MemoryToolResponse:
        _verify_bearer(authorization, app.state.bearer_key)
        cmd = BetaMemoryTool20250818DeleteCommand.model_construct(
            command="delete", path=req.path
        )
        return await _invoke(app.state.tool.delete(cmd))

    @app.post("/memory/rename", response_model=MemoryToolResponse)
    async def rename_endpoint(
        req: RenameRequest, authorization: Optional[str] = Header(None)
    ) -> MemoryToolResponse:
        _verify_bearer(authorization, app.state.bearer_key)
        cmd = BetaMemoryTool20250818RenameCommand.model_construct(
            command="rename", old_path=req.old_path, new_path=req.new_path
        )
        return await _invoke(app.state.tool.rename(cmd))

    return app


async def run_http_memory_tool_server(
    repo_root: str,
    host: str = "127.0.0.1",
    port: int = 7464,
    listen_public: bool = False,
) -> None:
    """Run the FastAPI shim.

    Default bind is 127.0.0.1 to limit exposure on shared / multi-tenant
    machines; pass ``listen_public=True`` (or ``--listen-public`` from the
    CLI) to bind 0.0.0.0 when running against a non-local agent.
    """
    if listen_public:
        host = "0.0.0.0"
    app = create_app(repo_root)
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server = uvicorn.Server(config)
    logger.info(
        "Starting memex memory-tool HTTP shim on %s:%s for %s",
        host,
        port,
        repo_root,
    )
    await server.serve()
