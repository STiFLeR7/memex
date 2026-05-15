import asyncio
import logging
import os
import sys
from importlib.metadata import version as get_version, PackageNotFoundError
from pathlib import Path
from typing import List, Dict, Any

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
from memex.mcp_server.tools_write import record_decision, record_problem, resolve_problem, invalidate_edge

logger = logging.getLogger(__name__)

# Attempt to get version from pyproject.toml via importlib
try:
    __version__ = get_version("memex-mcp")
except PackageNotFoundError:
    __version__ = "0.2.0"

class ConfigError(Exception):
    """Raised when server configuration is invalid."""
    pass

class MemexStartupError(Exception):
    """Raised when the server fails to connect to backends during startup."""
    pass

async def handle_list_tools() -> list[Tool]:
    """
    Returns the list of 10 tools.
    """
    return [
        Tool(
            name="get_project_context",
            description="Returns a compressed briefing of the project as a Markdown string: active modules, recent decisions, and open problems.",
            inputSchema={
                "type": "object",
                "properties": {
                    "scope": {
                        "type": "string",
                        "description": "Optional relative path to filter the briefing (e.g. 'src/auth')."
                    },
                    "repo": {
                        "type": "string",
                        "description": "Optional absolute path to the repository to scope results."
                    }
                }
            }
        ),
        Tool(
            name="get_symbol_context",
            description="Returns detailed information about a specific function or class as a Markdown string including callers/callees.",
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
                    },
                    "repo": {
                        "type": "string",
                        "description": "Optional absolute path to the repository to scope results."
                    }
                },
                "required": ["symbol_name"]
            }
        ),
        Tool(
            name="get_recent_decisions",
            description="Returns architectural and technical decisions from the past N days as a Markdown string.",
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
                    },
                    "repo": {
                        "type": "string",
                        "description": "Optional absolute path to the repository to scope results."
                    }
                }
            }
        ),
        Tool(
            name="get_open_problems",
            description="Returns currently open technical problems and TODOs sorted by severity as a Markdown string.",
            inputSchema={
                "type": "object",
                "properties": {
                    "module": {
                        "type": "string",
                        "description": "Optional relative path to filter problems by module."
                    },
                    "repo": {
                        "type": "string",
                        "description": "Optional absolute path to the repository to scope results."
                    }
                }
            }
        ),
        Tool(
            name="search_context",
            description="Semantic + keyword + graph traversal search across all node types. Use for broad discovery. Returns a Markdown string.",
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
                    },
                    "repo": {
                        "type": "string",
                        "description": "Optional absolute path to the repository to scope results."
                    }
                },
                "required": ["query"]
            }
        ),
        Tool(
            name="get_stale_context",
            description="Returns relationships that have decayed in confidence and may be outdated as a Markdown string.",
            inputSchema={
                "type": "object",
                "properties": {
                    "threshold": {
                        "type": "number",
                        "description": "Confidence threshold below which edges are considered stale (0.0-1.0, default: 0.5).",
                        "default": 0.5
                    },
                    "repo": {
                        "type": "string",
                        "description": "Optional absolute path to the repository to scope results."
                    }
                }
            }
        ),
        Tool(
            name="record_decision",
            description="Creates a Decision node in the graph. Call this when making or discovering architectural choices. Returns a status string.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The decision text (min 10 chars)."
                    },
                    "module": {
                        "type": "string",
                        "description": "Optional relative path to the affected module."
                    },
                    "symbol": {
                        "type": "string",
                        "description": "Optional name of the affected symbol."
                    },
                    "rationale": {
                        "type": "string",
                        "description": "Optional reasoning behind the decision."
                    },
                    "repo": {
                        "type": "string",
                        "description": "Optional absolute path to the repository."
                    }
                },
                "required": ["text"]
            }
        ),
        Tool(
            name="record_problem",
            description="Creates a Problem node in the graph. Call this when discovering bugs or technical debt. Returns a status string.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The problem description (min 10 chars)."
                    },
                    "module": {
                        "type": "string",
                        "description": "Optional relative path to the affected module."
                    },
                    "severity": {
                        "type": "string",
                        "description": "Problem severity: critical, high, medium, low (default: medium).",
                        "enum": ["critical", "high", "medium", "low"]
                    },
                    "repo": {
                        "type": "string",
                        "description": "Optional absolute path to the repository."
                    }
                },
                "required": ["text"]
            }
        ),
        Tool(
            name="resolve_problem",
            description="Marks a Problem as closed and records the resolution. Returns a status string.",
            inputSchema={
                "type": "object",
                "properties": {
                    "problem_id": {
                        "type": "string",
                        "description": "The unique ID or name of the problem node."
                    },
                    "resolution_text": {
                        "type": "string",
                        "description": "Explanation of how the problem was resolved (min 10 chars)."
                    },
                    "repo": {
                        "type": "string",
                        "description": "Optional absolute path to the repository."
                    }
                },
                "required": ["problem_id", "resolution_text"]
            }
        ),
        Tool(
            name="invalidate_edge",
            description="Explicitly invalidates a graph edge when it is discovered to be stale or incorrect. Returns a status string.",
            inputSchema={
                "type": "object",
                "properties": {
                    "edge_id": {
                        "type": "string",
                        "description": "The unique ID of the edge to invalidate."
                    },
                    "reason": {
                        "type": "string",
                        "description": "The reason for invalidating this relationship."
                    },
                    "repo": {
                        "type": "string",
                        "description": "Optional absolute path to the repository."
                    }
                },
                "required": ["edge_id", "reason"]
            }
        )
    ]

async def handle_call_tool(name: str, arguments: dict) -> list[TextContent | ImageContent | EmbeddedResource]:
    """
    Handle tool calls with argument validation and type coercion.
    """
    try:
        repo = str(arguments.get("repo")) if arguments.get("repo") else None
        
        if name == "get_project_context":
            scope = str(arguments.get("scope")) if arguments.get("scope") else None
            result = await get_project_context(scope, repo=repo)
            return [TextContent(type="text", text=result)]
        elif name == "get_symbol_context":
            symbol_name = str(arguments.get("symbol_name", ""))
            file = str(arguments.get("file")) if arguments.get("file") else None
            if not symbol_name:
                return [TextContent(type="text", text="Error: 'symbol_name' is required.")]
            result = await get_symbol_context(symbol_name, file, repo=repo)
            return [TextContent(type="text", text=result)]
        elif name == "get_recent_decisions":
            try:
                days = int(arguments.get("days", 30))
            except (ValueError, TypeError):
                days = 30
            module = str(arguments.get("module")) if arguments.get("module") else None
            result = await get_recent_decisions(days, module, repo=repo)
            return [TextContent(type="text", text=result)]
        elif name == "get_open_problems":
            module = str(arguments.get("module")) if arguments.get("module") else None
            result = await get_open_problems(module, repo=repo)
            return [TextContent(type="text", text=result)]
        elif name == "search_context":
            query = str(arguments.get("query", ""))
            try:
                top_k = int(arguments.get("top_k", 8))
            except (ValueError, TypeError):
                top_k = 8
            result = await search_context(query, top_k, repo=repo)
            return [TextContent(type="text", text=result)]
        elif name == "get_stale_context":
            try:
                threshold = float(arguments.get("threshold", 0.5))
            except (ValueError, TypeError):
                threshold = 0.5
            result = await get_stale_context(threshold, repo=repo)
            return [TextContent(type="text", text=result)]
        elif name == "record_decision":
            text = str(arguments.get("text", ""))
            module = str(arguments.get("module")) if arguments.get("module") else None
            symbol = str(arguments.get("symbol")) if arguments.get("symbol") else None
            rationale = str(arguments.get("rationale")) if arguments.get("rationale") else None
            result = await record_decision(text, module, symbol, rationale, repo=repo)
            return [TextContent(type="text", text=result)]
        elif name == "record_problem":
            text = str(arguments.get("text", ""))
            module = str(arguments.get("module")) if arguments.get("module") else None
            severity = str(arguments.get("severity", "medium"))
            result = await record_problem(text, module, severity, repo=repo)
            return [TextContent(type="text", text=result)]
        elif name == "resolve_problem":
            problem_id = str(arguments.get("problem_id", ""))
            resolution_text = str(arguments.get("resolution_text", ""))
            result = await resolve_problem(problem_id, resolution_text, repo=repo)
            return [TextContent(type="text", text=result)]
        elif name == "invalidate_edge":
            edge_id = str(arguments.get("edge_id", ""))
            reason = str(arguments.get("reason", ""))
            result = await invalidate_edge(edge_id, reason, repo=repo)
            return [TextContent(type="text", text=result)]
        return [TextContent(type="text", text=f"Tool {name} not found")]
    except Exception as e:
        logger.error("Internal error calling tool %s", name, exc_info=True)
        return [TextContent(type="text", text=f"Internal Server Error: {str(e)}")]

async def create_server(repo_root: str) -> Server:
    """
    Constructs the MCP Server instance, validates config, checks Neo4j,
    and registers all 10 tools - but never touches stdio.
    """
    # 1. Validate config
    try:
        config = get_config()
        config.repo_root = os.path.abspath(repo_root)
    except ValueError as e:
        logger.error("Configuration error: %s", e)
        raise ConfigError(str(e))

    # 2. Check Neo4j connectivity
    try:
        client = await get_graph_client()
        await client.driver.execute_query("RETURN 1")
    except Exception as e:
        logger.error("Failed to connect to Neo4j during startup: %s", e, exc_info=True)
        raise MemexStartupError(f"Neo4j connectivity check failed: {e}")
    
    server = Server("memex", version=__version__)
    server.list_tools()(handle_list_tools)
    server.call_tool()(handle_call_tool)

    return server

async def run_server(repo_root: str, transport: str = "stdio", host: str = "0.0.0.0", port: int = 8000):
    """
    Starts the MCP server using the specified transport(s).
    """
    try:
        # 1. Create server (validates config and checks Neo4j)
        server = await create_server(repo_root)
        
        config = get_config()
        logger.info("memex MCP server %s ready — repo: %s, neo4j: %s", __version__, config.repo_root, config.neo4j_uri)

        tasks = []
        
        if transport in ("stdio", "both"):
            async def run_stdio():
                logger.info("Starting stdio transport")
                async with stdio_server() as (read_stream, write_stream):
                    await server.run(
                        read_stream,
                        write_stream,
                        server.create_initialization_options()
                    )
            tasks.append(run_stdio())

        if transport in ("http", "both"):
            from memex.mcp_server.http import run_http_server
            logger.info("Starting HTTP transport on %s:%d", host, port)
            tasks.append(run_http_server(server, repo_root, host, port))

        if not tasks:
            logger.error("No transport specified")
            sys.exit(1)

        await asyncio.gather(*tasks)

    except (asyncio.CancelledError, KeyboardInterrupt):
        logger.info("memex MCP server stopping")
    except (ConfigError, MemexStartupError) as e:
        logger.error("Startup error: %s", e)
        sys.exit(1)
    except Exception as e:
        logger.error("MCP server runtime error: %s", e, exc_info=True)
        sys.exit(1)
    finally:
        logger.info("memex MCP server stopped")

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="memex MCP server")
    parser.add_argument("--repo", default=".", help="Path to the repository root")
    parser.add_argument("--transport", choices=["stdio", "http", "both"], default="stdio", help="Transport to use")
    parser.add_argument("--host", default="0.0.0.0", help="HTTP host")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port")
    
    args = parser.parse_args()
    
    # Configure logging to stderr for stdio transport compatibility
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    
    asyncio.run(run_server(args.repo, args.transport, args.host, args.port))
