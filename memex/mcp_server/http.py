import logging
import secrets
from typing import Optional, List, Dict

from fastapi import FastAPI, Request, Depends, HTTPException, status
from fastapi.responses import JSONResponse
from mcp.server import Server
from mcp.server.sse import SseServerTransport
import uvicorn

from memex.watcher.registry import validate_key

logger = logging.getLogger(__name__)

async def verify_auth_token(token: str) -> bool:
    """
    Validates the Bearer token against the registry.
    """
    if not token:
        return False
    return validate_key(token)

def create_app(server: Server, repo_root: str):
    """
    Creates the FastAPI application for memex.
    """
    app = FastAPI(
        title="memex MCP Server",
        description=f"Serving context for {repo_root}",
        version="0.2.0"
    )
    
    sse = SseServerTransport("/mcp/messages")

    @app.get("/health")
    async def health_check():
        return {"status": "ok", "repo": repo_root}

    # Custom ASGI app for MCP to handle raw send/receive
    async def mcp_asgi_app(scope, receive, send):
        if scope["type"] != "http":
            return

        logger.info(f"MCP ASGI request: {scope['method']} {scope['path']}")
        request = Request(scope, receive)
        auth_header = request.headers.get("Authorization")
        token = None
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.replace("Bearer ", "")
        
        if not await verify_auth_token(token):
            response = JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid Authorization header"}
            )
            await response(scope, receive, send)
            return

        if scope["path"].endswith("/sse") and scope["method"] == "GET":
            try:
                async with sse.connect_sse(scope, receive, send) as (read_stream, write_stream):
                    await server.run(
                        read_stream,
                        write_stream,
                        server.create_initialization_options()
                    )
            except Exception as e:
                logger.error(f"SSE Error: {e}", exc_info=True)
        elif scope["path"].endswith("/messages") and scope["method"] == "POST":
            try:
                await sse.handle_post_message(scope, receive, send)
            except Exception as e:
                logger.error(f"POST Message Error: {e}", exc_info=True)
        else:
            response = JSONResponse(status_code=404, content={"detail": "Not Found"})
            await response(scope, receive, send)

    app.mount("/mcp", mcp_asgi_app)

    return app

async def run_http_server(server: Server, repo_root: str, host: str = "0.0.0.0", port: int = 8000):
    """
    Runs the FastAPI app using uvicorn.
    """
    app = create_app(server, repo_root)
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server_uvicorn = uvicorn.Server(config)
    logger.info("Starting memex MCP HTTP server on %s:%s", host, port)
    logger.info("MCP SSE endpoint: http://%s:%s/mcp/sse", host, port)
    await server_uvicorn.serve()
