import asyncio
import logging
import os
import sys
from importlib.metadata import version as get_version, PackageNotFoundError

from mcp.server import Server
from mcp.server.stdio import stdio_server
from mcp.types import Tool, TextContent, ImageContent, EmbeddedResource

from memex.config import get_config, canonical_repo_path
from memex.graph.client import get_graph_client
from memex.mcp_server.tools_read import (
    get_project_context,
    get_symbol_context,
    get_recent_decisions,
    get_open_problems,
    search_context,
    get_stale_context,
    # Phase 9
    explain_change,
    predict_impact,
    get_context_briefing,
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
    Returns the list of 12 tools (6 v0.1 read + 4 v0.1 write + 2 Phase 9 read).
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
            description="Creates a Decision node in the graph. Call this when making or discovering architectural choices. Returns a status string. Phase 9: pass corroborates=<id> to reinforce, supersedes=<id> to replace, or force=true to bypass duplicate detection.",
            inputSchema={
                "type": "object",
                "properties": {
                    "text": {
                        "type": "string",
                        "description": "The decision text (min 10 chars). Not required when only corroborating."
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
                    },
                    "corroborates": {
                        "type": "string",
                        "description": "Phase 9: id of an existing Decision to reinforce. No new node is created; the existing node's last_reinforced_at is bumped."
                    },
                    "supersedes": {
                        "type": "string",
                        "description": "Phase 9: id of an existing Decision this one replaces. A new node is created with supersedes=<id> and the old node's outgoing edges are expired."
                    },
                    "force": {
                        "type": "boolean",
                        "description": "Phase 9: skip intent-confirmation similarity check and always write a sibling decision.",
                        "default": False
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
        ),
        # Phase 9 — synthesis tool. Cross-references a commit's diff with
        # Decision/Problem nodes linked to the affected files and asks
        # Gemini Pro to explain what changed and why.
        Tool(
            name="explain_change",
            description="Cross-references a git commit's diff with linked Decision/Problem nodes and returns a grounded Markdown explanation synthesised by Gemini Pro.",
            inputSchema={
                "type": "object",
                "properties": {
                    "commit_sha": {
                        "type": "string",
                        "description": "The git commit SHA to explain (short or full)."
                    },
                    "repo": {
                        "type": "string",
                        "description": "Optional absolute path to the repository."
                    }
                },
                "required": ["commit_sha"]
            }
        ),
        # Phase 9 — pure graph traversal. Predicts which modules are likely
        # affected by changes to `file_path` based on historical coupling.
        Tool(
            name="predict_impact",
            description="Returns a ranked Markdown list of modules likely affected by changes to a file, based on graph coupling (calls + imports + decision links). No LLM call.",
            inputSchema={
                "type": "object",
                "properties": {
                    "file_path": {
                        "type": "string",
                        "description": "Relative path of the file whose change-impact you want predicted."
                    },
                    "repo": {
                        "type": "string",
                        "description": "Optional absolute path to the repository."
                    }
                },
                "required": ["file_path"]
            }
        ),
        Tool(
            name="get_context_briefing",
            description=(
                "Returns a ranked, token-capped briefing of the most important "
                "context for this codebase. Use this at the START of a session "
                "to efficiently prime your understanding without overloading "
                "your context window. The briefing includes cluster summaries, "
                "recent high-confidence decisions, and active problems."
            ),
            inputSchema={
                "type": "object",
                "properties": {
                    "max_tokens": {
                        "type": "integer",
                        "description": "Maximum token budget for the briefing (default: 2000)",
                        "default": 2000,
                    },
                    "scope": {
                        "type": "string",
                        "description": "Optional module/directory scope to focus the briefing",
                    },
                    "repo": {
                        "type": "string",
                        "description": "Repository path (uses default if omitted)",
                    },
                },
            },
        )
    ]

async def handle_call_tool(name: str, arguments: dict) -> list[TextContent | ImageContent | EmbeddedResource]:
    """
    Handle tool calls with argument validation and type coercion.
    """
    from memex.graph.otel import tool_span
    from memex.graph.telemetry import detect_agent

    repo = str(arguments.get("repo")) if arguments.get("repo") else None
    agent = detect_agent()

    with tool_span(name, repo or ".", agent) as span:
        try:
            result = None
            if name == "get_project_context":
                scope = str(arguments.get("scope")) if arguments.get("scope") else None
                res = await get_project_context(scope, repo=repo)
                result = [TextContent(type="text", text=res)]
            elif name == "get_symbol_context":
                symbol_name = str(arguments.get("symbol_name", ""))
                file = str(arguments.get("file")) if arguments.get("file") else None
                if not symbol_name:
                    result = [TextContent(type="text", text="Error: 'symbol_name' is required.")]
                else:
                    res = await get_symbol_context(symbol_name, file, repo=repo)
                    result = [TextContent(type="text", text=res)]
            elif name == "get_recent_decisions":
                try:
                    days = int(arguments.get("days", 30))
                except (ValueError, TypeError):
                    days = 30
                module = str(arguments.get("module")) if arguments.get("module") else None
                res = await get_recent_decisions(days, module, repo=repo)
                result = [TextContent(type="text", text=res)]
            elif name == "get_open_problems":
                module = str(arguments.get("module")) if arguments.get("module") else None
                res = await get_open_problems(module, repo=repo)
                result = [TextContent(type="text", text=res)]
            elif name == "search_context":
                query = str(arguments.get("query", ""))
                try:
                    top_k = int(arguments.get("top_k", 8))
                except (ValueError, TypeError):
                    top_k = 8
                res = await search_context(query, top_k, repo=repo)
                result = [TextContent(type="text", text=res)]
            elif name == "get_stale_context":
                try:
                    threshold = float(arguments.get("threshold", 0.5))
                except (ValueError, TypeError):
                    threshold = 0.5
                res = await get_stale_context(threshold, repo=repo)
                result = [TextContent(type="text", text=res)]
            elif name == "record_decision":
                text = str(arguments.get("text", ""))
                module = str(arguments.get("module")) if arguments.get("module") else None
                symbol = str(arguments.get("symbol")) if arguments.get("symbol") else None
                rationale = str(arguments.get("rationale")) if arguments.get("rationale") else None
                # Phase 9 — governance kwargs
                corroborates = str(arguments.get("corroborates")) if arguments.get("corroborates") else None
                supersedes = str(arguments.get("supersedes")) if arguments.get("supersedes") else None
                force = bool(arguments.get("force", False))
                res = await record_decision(
                    text, module, symbol, rationale, repo=repo,
                    corroborates=corroborates, supersedes=supersedes, force=force,
                )
                result = [TextContent(type="text", text=res)]
            elif name == "record_problem":
                text = str(arguments.get("text", ""))
                module = str(arguments.get("module")) if arguments.get("module") else None
                severity = str(arguments.get("severity", "medium"))
                res = await record_problem(text, module, severity, repo=repo)
                result = [TextContent(type="text", text=res)]
            elif name == "resolve_problem":
                problem_id = str(arguments.get("problem_id", ""))
                resolution_text = str(arguments.get("resolution_text", ""))
                res = await resolve_problem(problem_id, resolution_text, repo=repo)
                result = [TextContent(type="text", text=res)]
            elif name == "invalidate_edge":
                edge_id = str(arguments.get("edge_id", ""))
                reason = str(arguments.get("reason", ""))
                res = await invalidate_edge(edge_id, reason, repo=repo)
                result = [TextContent(type="text", text=res)]
            # Phase 9 — new tools
            elif name == "explain_change":
                commit_sha = str(arguments.get("commit_sha", ""))
                if not commit_sha:
                    result = [TextContent(type="text", text="Error: 'commit_sha' is required.")]
                else:
                    res = await explain_change(commit_sha, repo=repo)
                    result = [TextContent(type="text", text=res)]
            elif name == "predict_impact":
                file_path = str(arguments.get("file_path", ""))
                if not file_path:
                    result = [TextContent(type="text", text="Error: 'file_path' is required.")]
                else:
                    res = await predict_impact(file_path, repo=repo)
                    result = [TextContent(type="text", text=res)]
            elif name == "get_context_briefing":
                try:
                    max_tokens = int(arguments.get("max_tokens", 2000))
                except (ValueError, TypeError):
                    max_tokens = 2000
                scope = str(arguments.get("scope")) if arguments.get("scope") else None
                res = await get_context_briefing(max_tokens, scope, repo=repo)
                result = [TextContent(type="text", text=res)]
            else:
                result = [TextContent(type="text", text=f"Tool {name} not found")]

            if span is not None:
                from opentelemetry.trace import StatusCode
                span.set_status(StatusCode.OK)
                if result and hasattr(result[0], "text"):
                    span.set_attribute("memex.result.tokens", len(result[0].text) // 4)
                    span.set_attribute("memex.result.length", len(result[0].text))

            return result

        except Exception as e:
            if span is not None:
                from opentelemetry.trace import StatusCode
                span.set_status(StatusCode.ERROR, str(e))
                span.record_exception(e)
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
        # Canonical form so the server's read-side repo_path join key matches
        # the watcher's write-side key regardless of --repo spelling (B1).
        config.repo_root = canonical_repo_path(repo_root)
    except ValueError as e:
        logger.error("Configuration error: %s", e)
        raise ConfigError(str(e))

    # 2. Check Neo4j connectivity (skipped in introspection-only mode so MCP
    # directory sandboxes can enumerate tools without a live backend).
    if os.getenv("MEMEX_INTROSPECTION_ONLY") == "1":
        logger.info("MEMEX_INTROSPECTION_ONLY=1 — skipping Neo4j connectivity check")
    else:
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

async def run_server(repo_root: str, transport: str = "stdio", host: str = "127.0.0.1", port: int = 8000):
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
    parser.add_argument("--host", default="127.0.0.1", help="HTTP host (use 0.0.0.0 to expose on all interfaces)")
    parser.add_argument("--port", type=int, default=8000, help="HTTP port")
    
    args = parser.parse_args()
    
    # Configure logging to stderr for stdio transport compatibility
    logging.basicConfig(level=logging.INFO, stream=sys.stderr)
    
    asyncio.run(run_server(args.repo, args.transport, args.host, args.port))
