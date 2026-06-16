import logging
import asyncio
import json
import os

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from mcp.server import Server
from mcp.server.sse import SseServerTransport
import uvicorn

from memex.watcher.registry import validate_key
from memex.graph.client import get_graph_client
from memex.config import canonical_repo_path

logger = logging.getLogger(__name__)

_subscribers: list[asyncio.Queue] = []

async def broadcast_event(event_type: str, data: dict):
    """
    Broadcasts an event to all connected SSE clients.
    """
    for queue in list(_subscribers):
        try:
            await queue.put({"event": event_type, "data": data})
        except Exception as e:
            logger.warning(f"Failed to push to subscriber queue: {e}")

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
    
    transport_mode = os.environ.get("MEMEX_MCP_TRANSPORT", "streamable-http")

    if transport_mode == "sse":
        import warnings
        warnings.warn(
            "SSE transport is deprecated and will be removed in v0.6.0. "
            "Migrate to Streamable HTTP by removing MEMEX_MCP_TRANSPORT env var.",
            DeprecationWarning,
            stacklevel=1,
        )
        sse = SseServerTransport("/mcp/messages")
    else:
        from mcp.server.streamable_http import StreamableHTTPServerTransport
        transport = StreamableHTTPServerTransport(mcp_session_id=None)

        async def run_transport():
            try:
                async with transport.connect() as (read_stream, write_stream):
                    await server.run(
                        read_stream,
                        write_stream,
                        server.create_initialization_options()
                    )
            except Exception as e:
                logger.error(f"Error in Streamable HTTP transport run loop: {e}", exc_info=True)

        @app.on_event("startup")
        async def startup_event():
            asyncio.create_task(run_transport())

    @app.get("/health")
    async def health_check():
        # /health is unauthenticated — don't leak the absolute repo path (B5).
        try:
            client = await get_graph_client()
            await client.driver.execute_query("RETURN 1")
            return {"status": "ok"}
        except Exception as e:
            logger.warning(f"Health check failed (Neo4j connection issue): {e}")
            return JSONResponse(
                status_code=503,
                content={"status": "error", "detail": "Neo4j connection failed"}
            )

    @app.get("/graph")
    async def get_graph():
        client = await get_graph_client()
        canonical_repo = canonical_repo_path(repo_root)
        
        # Query nodes
        nodes_query = """
        MATCH (n:Entity)
        WHERE n.repo_path = $repo
        RETURN 
          elementId(n) as id,
          n.name as name,
          coalesce(n.type, '') as raw_type,
          coalesce(n.summary, n.description, '') as summary,
          coalesce(n.created_at, '') as created_at,
          coalesce(n.status, '') as status,
          coalesce(n.scope, '') as scope,
          coalesce(n.source_commit, '') as source_commit
        """
        
        # Query relationships
        edges_query = """
        MATCH (n1:Entity)-[r]->(n2:Entity)
        WHERE n1.repo_path = $repo 
          AND n2.repo_path = $repo
          AND r.expired_at IS NULL
          AND r.valid_until IS NULL
        RETURN 
          elementId(n1) as source,
          elementId(n2) as target,
          type(r) as type,
          coalesce(r.created_at, '') as created_at
        """
        
        try:
            nodes_res = await client.driver.execute_query(nodes_query, params={"repo": canonical_repo})
            edges_res = await client.driver.execute_query(edges_query, params={"repo": canonical_repo})
            
            nodes = []
            for record in nodes_res.records:
                data = record.data()
                name = data["name"]
                raw_type = data["raw_type"]
                
                # Determine classification
                if raw_type == 'Decision' or 'Decision' in name:
                    node_type = 'Decision'
                elif raw_type == 'Problem':
                    node_type = 'Problem'
                elif raw_type == 'Module' or any(name.endswith(ext) for ext in ['.py', '.js', '.ts', '.tsx', '.jsx', '.html', '.css', '.json']):
                    node_type = 'Module'
                else:
                    node_type = 'Symbol'
                    
                # Format timestamps/datetimes to string if they are datetime objects
                created_at_val = data["created_at"]
                if hasattr(created_at_val, "isoformat"):
                    created_at_val = created_at_val.isoformat()
                elif created_at_val and not isinstance(created_at_val, str):
                    created_at_val = str(created_at_val)
                    
                nodes.append({
                    "id": data["id"],
                    "name": name,
                    "type": node_type,
                    "summary": data["summary"],
                    "created_at": created_at_val,
                    "status": data["status"],
                    "scope": data["scope"],
                    "source_commit": data["source_commit"]
                })
                
            edges = []
            for record in edges_res.records:
                data = record.data()
                created_at_val = data["created_at"]
                if hasattr(created_at_val, "isoformat"):
                    created_at_val = created_at_val.isoformat()
                elif created_at_val and not isinstance(created_at_val, str):
                    created_at_val = str(created_at_val)
                    
                edges.append({
                    "source": data["source"],
                    "target": data["target"],
                    "type": data["type"],
                    "created_at": created_at_val
                })
                
            return {"nodes": nodes, "edges": edges}
        except Exception as e:
            logger.error(f"Failed to fetch graph data: {e}", exc_info=True)
            return JSONResponse(
                status_code=500,
                content={"detail": "Failed to fetch graph data"}
            )

    @app.get("/events")
    async def sse_endpoint(request: Request):
        async def event_generator():
            queue = asyncio.Queue()
            _subscribers.append(queue)
            try:
                # Yield initial connect ping
                yield "event: ping\ndata: {\"status\": \"connected\"}\n\n"
                
                while True:
                    try:
                        # Wait for a message with a 15.0s timeout
                        msg = await asyncio.wait_for(queue.get(), timeout=15.0)
                        event_type = msg.get("event", "message")
                        data_str = json.dumps(msg.get("data", {}))
                        yield f"event: {event_type}\ndata: {data_str}\n\n"
                    except asyncio.TimeoutError:
                        # Keep-alive
                        yield "event: ping\ndata: {\"status\": \"keep-alive\"}\n\n"
            except asyncio.CancelledError:
                pass
            finally:
                if queue in _subscribers:
                    _subscribers.remove(queue)
                    
        return StreamingResponse(event_generator(), media_type="text/event-stream")

    @app.post("/notify")
    async def post_notify():
        await broadcast_event("graph_updated", {})
        return {"status": "ok"}

    @app.get("/stats")
    async def get_stats_endpoint(request: Request, repo: str = None, days: int = 30):
        # 1. Authenticate using Bearer token
        auth_header = request.headers.get("Authorization")
        token = None
        if auth_header and auth_header.startswith("Bearer "):
            token = auth_header.removeprefix("Bearer ")
        
        if not await verify_auth_token(token):
            return JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid Authorization header"}
            )
            
        # 2. Get Stats from unified stats service
        try:
            from memex.graph.stats import get_stats_data
            path = repo or repo_root
            stats = await get_stats_data(path)
            return stats
        except Exception as e:
            logger.error(f"Failed to generate stats in /stats endpoint: {e}", exc_info=True)
            return JSONResponse(
                status_code=500,
                content={"detail": f"Failed to generate stats: {str(e)}"}
            )

    # Custom ASGI app for MCP to handle raw send/receive
    async def mcp_asgi_app(scope, receive, send):
        if scope["type"] != "http":
            return

        logger.info(f"MCP ASGI request: {scope['method']} {scope['path']}")
        request = Request(scope, receive)
        auth_header = request.headers.get("Authorization")
        token = None
        if auth_header and auth_header.startswith("Bearer "):
            # Strip only the leading scheme — a token containing 'Bearer '
            # as a substring must survive intact (B6).
            token = auth_header.removeprefix("Bearer ")
        
        if not await verify_auth_token(token):
            response = JSONResponse(
                status_code=401,
                content={"detail": "Missing or invalid Authorization header"}
            )
            await response(scope, receive, send)
            return

        if transport_mode == "sse":
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
        else:
            # Streamable HTTP transport
            await transport.handle_request(scope, receive, send)

    app.mount("/mcp", mcp_asgi_app)

    return app

async def run_http_server(server: Server, repo_root: str, host: str = "127.0.0.1", port: int = 8000):
    """
    Runs the FastAPI app using uvicorn.
    """
    app = create_app(server, repo_root)
    config = uvicorn.Config(app, host=host, port=port, log_level="info")
    server_uvicorn = uvicorn.Server(config)

    from pathlib import Path
    from memex.config import canonical_repo_path

    repo_canon = canonical_repo_path(repo_root)
    port_dir = Path(repo_canon) / ".memex"
    port_file = port_dir / "port"

    try:
        port_dir.mkdir(parents=True, exist_ok=True)
        port_file.write_text(str(port))
        logger.info("Saved active server port %d to %s", port, port_file)
    except Exception as e:
        logger.warning("Failed to save active server port to file: %s", e)

    try:
        logger.info("Starting memex MCP HTTP server on %s:%s", host, port)
        if os.environ.get("MEMEX_MCP_TRANSPORT") == "sse":
            logger.info("MCP SSE endpoint: http://%s:%s/mcp/sse", host, port)
        else:
            logger.info("MCP Streamable HTTP endpoint: http://%s:%s/mcp", host, port)
        await server_uvicorn.serve()
    finally:
        try:
            if port_file.exists():
                port_file.unlink()
                logger.info("Cleaned up active server port file: %s", port_file)
        except Exception as e:
            logger.warning("Failed to clean up active server port file: %s", e)
