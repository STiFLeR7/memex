import asyncio
import logging
import os
import sys
from importlib.metadata import version as get_version, PackageNotFoundError
from pathlib import Path

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, ImageContent, EmbeddedResource

from memex.config import get_config
from memex.graph.client import get_graph_client
from memex.mcp_server.tools_read import (
    get_project_context,
    get_symbol_context,
    get_recent_decisions,
    get_open_problems,
    search_context,
    get_stale_context
)

logger = logging.getLogger(__name__)

# Attempt to get version from pyproject.toml via importlib
try:
    __version__ = get_version("memex")
except PackageNotFoundError:
    __version__ = "0.1.0"

# Initialize MCP server with name and version
app = Server("memex")

@app.list_tools()
async def list_tools() -> list[Tool]:
    """
    List available tools with their schemas.
    """
    return [
        Tool(
            name="get_project_context",
            description="Returns a compressed briefing of the project: active modules, recent decisions, and open problems.",
            inputSchema={
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "description": "Optional relative path to filter the briefing (e.g. 'src/auth')."
                    }
                }
            }
        ),
        Tool(
            name="get_symbol_context",
            description="Returns detailed information about a specific function or class including callers/callees.",
            inputSchema={
                "type": "object",
                "properties": {
                    "symbol_name": {
                        "type": "string",
                        "description": "The name of the function or class to look up."
                    },
                    "file": {
                        "type": "string",
                        "description": "Optional relative path to disambiguate symbols with the same name."
                    }
                },
                "required": ["symbol_name"]
            }
        ),
        Tool(
            name="get_recent_decisions",
            description="Returns architectural and technical decisions from the past N days.",
            inputSchema={
                "type": "object",
                "properties": {
                    "days": {
                        "type": "integer",
                        "description": "Number of days to look back (default: 30).",
                        "default": 30
                    },
                    "module": {
                        "type": "string",
                        "description": "Optional relative path to filter decisions by affected module."
                    }
                }
            }
        ),
        Tool(
            name="get_open_problems",
            description="Returns currently open technical problems and TODOs sorted by severity.",
            inputSchema={
                "type": "object",
                "properties": {
                    "module": {
                        "type": "string",
                        "description": "Optional relative path to filter problems by module."
                    }
                }
            }
        ),
        Tool(
            name="search_context",
            description="Semantic + keyword + graph traversal search across all node types. Use for broad discovery.",
            inputSchema={
                "type": "object",
                "properties": {
                    "query": {
                        "type": "string",
                        "description": "The search query."
                    },
                    "top_k": {
                        "type": "integer",
                        "description": "Maximum number of results (1-20, default: 8).",
                        "default": 8
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="get_stale_context",
            description="Returns relationships that have decayed in confidence and may be outdated.",
            inputSchema={
                "type": "object",
                "properties": {
                    "threshold": {
                        "type": "number",
                        "description": "Confidence threshold below which edges are considered stale (0.0-1.0, default: 0.5).",
                        "default": 0.5
                    }
                }
            }
        )
    ]

@app.call_tool()
async def call_tool(name: str, arguments: dict) -> list[TextContent | ImageContent | EmbeddedResource]:
    """
    Handle tool calls with argument validation and type coercion.
    """
    try:
        if name == "get_project_context":
            scope = str(arguments.get("scope")) if arguments.get("scope") else None
            result = await get_project_context(scope)
            return [TextContent(type="text", text=result)]
            
        elif name == "get_symbol_context":
            symbol_name = str(arguments.get("symbol_name", ""))
            file = str(arguments.get("file")) if arguments.get("file") else None
            if not symbol_name:
                return [TextContent(type="text", text="Error: 'symbol_name' is required.")]
            result = await get_symbol_context(symbol_name, file)
            return [TextContent(type="text", text=result)]
            
        elif name == "get_recent_decisions":
            try:
                days = int(arguments.get("days", 30))
            except (ValueError, TypeError):
                days = 30
            module = str(arguments.get("module")) if arguments.get("module") else None
            result = await get_recent_decisions(days, module)
            return [TextContent(type="text", text=result)]
            
        elif name == "get_open_problems":
            module = str(arguments.get("module")) if arguments.get("module") else None
            result = await get_open_problems(module)
            return [TextContent(type="text", text=result)]
            
        elif name == "search_context":
            query = str(arguments.get("query", ""))
            try:
                top_k = int(arguments.get("top_k", 8))
            except (ValueError, TypeError):
                top_k = 8
            result = await search_context(query, top_k)
            return [TextContent(type="text", text=result)]
            
        elif name == "get_stale_context":
            try:
                threshold = float(arguments.get("threshold", 0.5))
            except (ValueError, TypeError):
                threshold = 0.5
            result = await get_stale_context(threshold)
            return [TextContent(type="text", text=result)]
            
        return [TextContent(type="text", text=f"Tool {name} not found")]
        
    except Exception as e:
        logger.error("Internal error calling tool %s", name, exc_info=True)
        return [TextContent(type="text", text=f"Internal Server Error: {str(e)}")]

async def run_server(repo_root: str):
    """
    Starts the MCP server using stdio transport.
    """
    # 1. Validate config
    try:
        config = get_config()
        config.repo_root = os.path.abspath(repo_root)
    except ValueError as e:
        logger.error("Configuration error: %s", e)
        sys.exit(1)

    # 2. Check Neo4j connectivity
    try:
        client = await get_graph_client()
        await client.driver.execute_query("RETURN 1")
        logger.info("memex MCP server %s ready — repo: %s, neo4j: %s", __version__, config.repo_root, config.neo4j_uri)
    except Exception:
        logger.error("Failed to connect to Neo4j. Backend unavailable.", exc_info=True)
        sys.exit(1)

    # 3. Serve
    try:
        async with stdio_server() as (read_stream, write_stream):
            await app.run(
                read_stream,
                write_stream,
                app.create_initialization_options()
            )
    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("memex MCP server stopping")
    except Exception as e:
        logger.error("MCP server runtime error: %s", e, exc_info=True)
    finally:
        logger.info("memex MCP server stopped")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    repo = sys.argv[1] if len(sys.argv) > 1 else "."
    asyncio.run(run_server(repo))
