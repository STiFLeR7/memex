import argparse
import sys
import asyncio
import logging
import os
import subprocess
import time
from pathlib import Path
from memex.watcher.daemon import run_daemon
from memex.watcher.git_hook import install_hooks
from memex.mcp_server.server import run_server
from memex.graph.client import get_graph_client
from memex.mcp_server.queries import get_node_counts, get_stale_edges
from memex.watcher.registry import (
    add_repository,
    remove_repository,
    get_repositories,
    add_key,
    list_keys,
    revoke_key,
)

# Setup basic logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    stream=sys.stderr
)
logger = logging.getLogger("memex.cli")

async def get_node_counts_safe():
    try:
        return await get_node_counts()
    except Exception:
        return None

async def run_stats_command(repo_root: str | None, days: int = 30, as_json: bool = False) -> None:
    try:
        from memex.graph.stats import get_stats_data, print_rich_stats
        import json
        
        path = repo_root or "."
        stats = await get_stats_data(path)
        
        if as_json:
            print(json.dumps(stats, indent=2))
        else:
            print_rich_stats(stats, repo_path=path)
            
    except Exception as e:
        print(f"Error printing stats: {e}", file=sys.stderr)
        sys.exit(1)

async def print_status(repo_root: str | None):
    if repo_root:
        repo_path = Path(repo_root).resolve()
        print(f"\nmemex status for {repo_path}")
        
        # 1. Check if paused
        if (repo_path / ".memex" / "paused").exists():
            print("Status: PAUSED")
        else:
            print("Status: ACTIVE")
            
        # 2. Get Graph Counts
        counts = await get_node_counts_safe()
        if counts:
            print(f"Graph: {counts.get('modules', 0)} modules, {counts.get('symbols', 0)} symbols, "
                  f"{counts.get('decisions', 0)} decisions, {counts.get('problems', 0)} open problems")
        else:
            print("Graph: Could not connect to Neo4j to retrieve node counts.")

        # 3. Watcher health — surface swallowed indexing errors / skipped NL
        # episodes so degradation isn't invisible (audit Q1/B7).
        from memex.watcher.health import read_health
        h = read_health(str(repo_path))
        if h:
            errs = h.get("handler_errors", 0)
            skips = h.get("episodes_skipped", 0)
            if errs:
                print(f"Health: {errs} indexing error(s) — last at {h.get('last_error_at', '?')} "
                      f"({h.get('handler_errors_by', {})})")
            if skips:
                print(f"Health: {skips} NL episode(s) skipped (structured graph still written; "
                      "likely Gemini quota — check https://ai.studio/spend)")
            if not errs and not skips:
                print(f"Health: OK — last indexed {h.get('last_indexed_at', 'n/a')}")
    else:
        print("\nmemex status summary (Global)")
        repos = get_repositories()
        if not repos:
            print("No repositories registered.")
            return

        active_count = 0
        for repo in repos:
            status = "ACTIVE" if repo.active else "INACTIVE"
            if repo.active:
                if (Path(repo.path) / ".memex" / "paused").exists():
                    status = "PAUSED"
                else:
                    active_count += 1
            print(f"  {repo.name} ({repo.path}) - [{status}]")
        
        print(f"\nTotal Registered: {len(repos)} | Active/Watching: {active_count}")
        
        counts = await get_node_counts_safe()
        if counts:
            print(f"Global Graph: {counts.get('modules', 0)} modules, {counts.get('symbols', 0)} symbols, "
                  f"{counts.get('decisions', 0)} decisions, {counts.get('problems', 0)} open problems")

async def run_doctor(repo_root: str):
    print("\nmemex doctor — checking prerequisites\n")
    repo_path = Path(repo_root).resolve()
    all_pass = True

    # 1. Python version
    py_ver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
    if sys.version_info >= (3, 11):
        print(f"[PASS] Python 3.11+            found Python {py_ver}")
    else:
        print(f"[FAIL] Python 3.11+            found Python {py_ver}. Please upgrade.")
        all_pass = False

    # 2. uv check
    try:
        # On Windows, we might need to check uv.exe
        uv_cmd = "uv.exe" if os.name == "nt" else "uv"
        uv_ver = subprocess.check_output([uv_cmd, "--version"]).decode().strip()
        print(f"[PASS] uv                      found {uv_ver}")
    except Exception:
        print("[FAIL] uv                      not found. Install via 'curl -LsSf https://astral.sh/uv/install.sh | sh'")
        all_pass = False

    # 3. Docker check
    try:
        docker_cmd = "docker.exe" if os.name == "nt" else "docker"
        docker_ver = subprocess.check_output([docker_cmd, "--version"]).decode().strip()
        print(f"[PASS] Docker                  found {docker_ver}")
    except Exception:
        print("[FAIL] Docker                  not found or not running. Please install/start Docker.")
        all_pass = False

    # 4. Neo4j connectivity
    start_time = time.time()
    try:
        client = await get_graph_client()
        await client.driver.execute_query("RETURN 1")
        elapsed = int((time.time() - start_time) * 1000)
        print(f"[PASS] Neo4j reachable         bolt://localhost:7687 responded in {elapsed}ms")
    except Exception:
        print("[FAIL] Neo4j reachable         Could not connect. Ensure 'docker-compose up -d' is running.")
        all_pass = False

    # 5. Gemini API key
    gemini_key = os.getenv("GEMINI_API_KEY")
    if gemini_key:
        try:
            # The import IS the check — fails if the SDK isn't installed.
            from google import genai  # noqa: F401
            print("[PASS] Gemini API key          GEMINI_API_KEY set")
        except Exception:
            print("[FAIL] Gemini API key          GEMINI_API_KEY set but SDK missing")
            all_pass = False
    else:
        print("[FAIL] Gemini API key          GEMINI_API_KEY environment variable not set")
        all_pass = False

    # 6. Git hooks
    hook_path = repo_path / ".git" / "hooks" / "post-commit"
    if hook_path.exists() and "memex" in hook_path.read_text():
        print("[PASS] git hooks installed     post-commit hook found in .git/hooks/")
    else:
        print("[FAIL] git hooks installed     not found. Run 'memex init' to install.")
        all_pass = False

    # 7. Watchdog running (daemon pid)
    pid_file = repo_path / ".memex" / "daemon.pid"
    if pid_file.exists():
        print("[PASS] watchdog running        .memex/daemon.pid exists")
    else:
        print("[FAIL] watchdog running        no pid file. Run 'memex watch' to start.")
        all_pass = False

    # 8. Stale edges check
    try:
        stale = await get_stale_edges(threshold=0.5, limit=1)
        if stale:
            print("[WARN] Stale edges found       run `get_stale_context()` in your agent for details")
    except Exception:
        pass

    # 8b. Watcher health — swallowed indexing errors / skipped NL episodes (Q1/B7)
    try:
        from memex.watcher.health import read_health
        h = read_health(str(repo_path))
        errs = h.get("handler_errors", 0)
        skips = h.get("episodes_skipped", 0)
        if errs:
            print(f"[WARN] watcher health          {errs} indexing error(s); last {h.get('last_error_at', '?')}")
        elif skips:
            print(f"[WARN] watcher health          {skips} NL episode(s) skipped (Gemini quota?); structured graph intact")
        else:
            print("[PASS] watcher health          no swallowed indexing errors recorded")
    except Exception:
        logger.debug("doctor: health summary failed", exc_info=True)

    # 9. Retrieval tracing weekly summary (Phase 10)
    try:
        from memex.cli_doctor_tracing import print_tracing_summary
        print_tracing_summary(str(repo_path))
    except Exception:
        # Never let the summary line break the doctor.
        logger.debug("doctor: tracing summary print failed", exc_info=True)

    if all_pass:
        print("\nAll checks passed. memex is ready.")
        sys.exit(0)
    else:
        print("\nSome checks failed. Please resolve the issues above.")
        sys.exit(1)

def main(args=None):
    # Common parser for shared arguments
    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument("--repo", help="Path to the repository")

    parser = argparse.ArgumentParser(description="memex CLI - Knowledge Graph Watcher")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # init
    subparsers.add_parser("init", help="Initialize memex hooks", parents=[parent_parser])
    
    # watch
    subparsers.add_parser("watch", help="Start watcher daemon", parents=[parent_parser])
    
    # status
    subparsers.add_parser("status", help="Show current status", parents=[parent_parser])

    # list
    subparsers.add_parser("list", help="List registered repositories", parents=[parent_parser])

    # remove
    remove_parser = subparsers.add_parser("remove", help="Unregister a repository")
    remove_parser.add_argument("--repo", required=True, help="Path to the repository")

    # pause
    subparsers.add_parser("pause", help="Suspend watcher", parents=[parent_parser])

    # resume
    subparsers.add_parser("resume", help="Resume watcher", parents=[parent_parser])

    # serve
    serve_parser = subparsers.add_parser("serve", help="Start MCP server", parents=[parent_parser])
    serve_parser.add_argument("--transport", choices=["stdio", "http", "both"], default="stdio", help="Transport to use")
    serve_parser.add_argument("--host", default="127.0.0.1", help="HTTP host (use 0.0.0.0 to expose on all interfaces)")
    serve_parser.add_argument("--port", type=int, default=8000, help="HTTP port")
    serve_parser.add_argument("--env-file", dest="env_file", help="Path to an .env file to load before startup (in addition to <repo>/.env)")

    # doctor
    subparsers.add_parser("doctor", help="Check system health", parents=[parent_parser])

    # keys
    keys_parser = subparsers.add_parser("keys", help="Manage authentication keys", parents=[parent_parser])
    keys_subparsers = keys_parser.add_subparsers(dest="keys_command", required=True)
    
    # keys add
    keys_add_parser = keys_subparsers.add_parser("add", help="Add a new key")
    keys_add_parser.add_argument("name", help="Name for the key")
    
    # keys list
    keys_subparsers.add_parser("list", help="List all keys")
    
    # keys revoke
    keys_revoke_parser = keys_subparsers.add_parser("revoke", help="Revoke a key")
    keys_revoke_parser.add_argument("name", help="Name of the key to revoke")

    # ------------------------------------------------------------------
    # v0.3.0 subcommand scaffolding (Phase 5.9)
    # Handlers below in main() return "not implemented" until phases land.
    # ------------------------------------------------------------------

    # archive (Phase 6)
    archive_parser = subparsers.add_parser("archive", help="Tombstone & archive cold nodes", parents=[parent_parser])
    archive_parser.add_argument("--restore", metavar="NODE_ID", help="Restore a specific archived node")
    archive_parser.add_argument("--stats", action="store_true", help="Show archive size and oldest entry")

    # stats (Phase 11)
    stats_parser = subparsers.add_parser("stats", help="Show context token savings and telemetry stats", parents=[parent_parser])
    stats_parser.add_argument("--days", type=int, default=30, help="Show stats for the last N days (deprecated)")
    stats_parser.add_argument("--json", action="store_true", help="Output raw JSON format")

    # review (Phase 8)
    subparsers.add_parser("review", help="Validate synthesised decisions (lowest-confidence-first)", parents=[parent_parser])

    # graph (Phase 10)
    graph_parser = subparsers.add_parser("graph", help="Export static D3.js graph visualisation", parents=[parent_parser])
    graph_parser.add_argument("--open", action="store_true", help="Open the generated HTML in a browser")
    graph_parser.add_argument("--output", default="graph.html", help="Output HTML path")

    # cluster (Phase 6 / v0.3.1 — dev2 deliverable 3)
    cluster_parser = subparsers.add_parser(
        "cluster",
        help="Run a one-shot Leiden cluster pass over Modules (hybrid edges)",
        parents=[parent_parser],
    )
    cluster_parser.add_argument(
        "--rerun",
        action="store_true",
        help="Re-cluster even if Cluster nodes already exist (default: idempotent)",
    )
    cluster_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the clustering result without writing to Neo4j",
    )
    cluster_parser.add_argument(
        "--refresh-summaries",
        action="store_true",
        help="Force-regenerate Gemini summaries for all clusters",
    )

    # memory-tool (Move 1)
    mt_parser = subparsers.add_parser("memory-tool", help="Anthropic memory-tool backend adapter", parents=[parent_parser])
    mt_sub = mt_parser.add_subparsers(dest="memory_tool_command", required=True)
    mt_serve = mt_sub.add_parser("serve", help="Serve the memex memory-tool backend", parents=[parent_parser])
    mt_serve.add_argument("--transport", choices=["stdio", "http"], default="stdio", help="Transport to use")
    mt_serve.add_argument("--port", type=int, default=7464, help="HTTP port (transport=http)")
    mt_serve.add_argument(
        "--listen-public",
        action="store_true",
        help="Bind 0.0.0.0 instead of 127.0.0.1 (transport=http). Off by default to limit exposure on shared machines.",
    )

    parsed_args = parser.parse_args(args)
    repo_root = parsed_args.repo

    if parsed_args.command == "init":
        path = repo_root or "."
        install_hooks(path)
        (Path(path) / ".memex").mkdir(exist_ok=True)
        add_repository(path)
        print(f"memex initialized and registered in {Path(path).resolve()}")

        # v0.3.1 Deliverable 3: kick off a one-shot cluster pass over the
        # source tree. The watcher hasn't populated Module nodes in Neo4j
        # yet, so we discover modules by walking the filesystem. Persist
        # results best-effort — if Neo4j isn't reachable, log and move on
        # so `memex init` doesn't hard-fail on a missing docker stack.
        try:
            from memex.graph.cluster_runner import run_init_cluster_pass
            asyncio.run(run_init_cluster_pass(path))
        except Exception:
            logger.warning(
                "init: cluster pass failed (non-fatal — run `memex cluster` later)",
                exc_info=True,
            )

    elif parsed_args.command == "watch":
        asyncio.run(run_daemon(repo_root))

    elif parsed_args.command == "status":
        asyncio.run(print_status(repo_root))

    elif parsed_args.command == "list":
        repos = get_repositories()
        if not repos:
            print("No repositories registered.")
        else:
            print("\nRegistered Repositories:")
            for r in repos:
                status = "ACTIVE" if r.active else "INACTIVE"
                print(f"  - {r.name}: {r.path} [{status}]")

    elif parsed_args.command == "remove":
        remove_repository(parsed_args.repo)
        print(f"Repository {parsed_args.repo} unregistered.")

    elif parsed_args.command == "pause":
        path = repo_root or "."
        (Path(path) / ".memex").mkdir(exist_ok=True)
        (Path(path) / ".memex" / "paused").touch()
        print(f"memex watcher PAUSED for {Path(path).resolve()}.")

    elif parsed_args.command == "resume":
        path = repo_root or "."
        pause_file = (Path(path) / ".memex" / "paused")
        if pause_file.exists():
            pause_file.unlink()
        print(f"memex watcher RESUMED for {Path(path).resolve()}.")

    elif parsed_args.command == "serve":
        path = repo_root or "."
        from dotenv import load_dotenv as _load_dotenv
        repo_env = Path(path) / ".env"
        # override=True: when the user passes --repo / --env-file explicitly,
        # those files represent intent — they should beat stale shell/User env
        # vars inherited from the parent process.
        if repo_env.exists():
            _load_dotenv(repo_env, override=True)
        if parsed_args.env_file:
            _load_dotenv(parsed_args.env_file, override=True)
        # Re-init cached config so values loaded above are picked up, and so
        # config.yaml resolves against the repo (not the client's CWD). B2.
        from memex import config as _config_mod
        try:
            _config_mod._config = _config_mod.load_config(path)
        except Exception:
            # Defer surfacing the error to run_server/create_server, which
            # already raises a clear ConfigError. Keep prior reset behaviour.
            _config_mod._config = None
        asyncio.run(run_server(path, parsed_args.transport, parsed_args.host, parsed_args.port))

    elif parsed_args.command == "doctor":
        path = repo_root or "."
        asyncio.run(run_doctor(path))

    elif parsed_args.command == "keys":
        if parsed_args.keys_command == "add":
            key = add_key(parsed_args.name)
            print(f"Key '{parsed_args.name}' added successfully:")
            print(f"  {key}")
            print("\nIMPORTANT: This is the only time the full key will be shown. Store it securely.")

        elif parsed_args.keys_command == "list":
            keys = list_keys()
            if not keys:
                print("No keys found.")
            else:
                print("\nAuthentication Keys:")
                print(f"{'Name':<20} {'Key (Truncated)':<20} {'Created At':<30}")
                print("-" * 70)
                for k in keys:
                    print(f"{k['name']:<20} {k['key']:<20} {k['created_at']:<30}")

        elif parsed_args.keys_command == "revoke":
            if revoke_key(parsed_args.name):
                print(f"Key '{parsed_args.name}' revoked.")
            else:
                print(f"Key '{parsed_args.name}' not found.")

    # ------------------------------------------------------------------
    # v0.3.0 subcommand handlers — scaffolded; implementations land per phase
    # ------------------------------------------------------------------

    elif parsed_args.command == "archive":
        try:
            from memex.graph.archive import run_archive_command
            asyncio.run(run_archive_command(
                repo_root=repo_root or ".",
                restore=parsed_args.restore,
                stats=parsed_args.stats,
            ))
        except ImportError:
            print("memex archive: not yet implemented in v0.3.0-alpha (Phase 6 in progress)", file=sys.stderr)
            sys.exit(2)

    elif parsed_args.command == "stats":
        asyncio.run(run_stats_command(repo_root, parsed_args.days, parsed_args.json))

    elif parsed_args.command == "review":
        try:
            from memex.cli_review import run_review_command
            asyncio.run(run_review_command(repo_root or "."))
        except ImportError:
            print("memex review: not yet implemented in v0.3.0-alpha (Phase 8 in progress)", file=sys.stderr)
            sys.exit(2)

    elif parsed_args.command == "graph":
        try:
            from memex.cli_graph import run_graph_command
            asyncio.run(run_graph_command(
                repo_root=repo_root or ".",
                output=parsed_args.output,
                open_browser=parsed_args.open,
            ))
        except ImportError:
            print("memex graph: not yet implemented in v0.3.0-alpha (Phase 10 in progress)", file=sys.stderr)
            sys.exit(2)

    elif parsed_args.command == "cluster":
        from memex.graph.cluster_runner import run_cluster_command
        asyncio.run(run_cluster_command(
            repo_root=repo_root or ".",
            rerun=parsed_args.rerun,
            dry_run=parsed_args.dry_run,
            refresh_summaries=parsed_args.refresh_summaries,
        ))

    elif parsed_args.command == "memory-tool":
        if parsed_args.memory_tool_command == "serve":
            try:
                from memex.memory_tool.server import run_memory_tool_serve
                asyncio.run(run_memory_tool_serve(
                    repo_root=repo_root or ".",
                    transport=parsed_args.transport,
                    port=parsed_args.port,
                    listen_public=parsed_args.listen_public,
                ))
            except ImportError:
                print("memex memory-tool serve: not yet implemented in v0.3.0-alpha (Move 1 in progress)", file=sys.stderr)
                sys.exit(2)


if __name__ == "__main__":
    main()
