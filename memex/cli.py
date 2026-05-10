import argparse
import asyncio
import logging
import sys
import os
from pathlib import Path
from memex.watcher.daemon import run_daemon
from memex.mcp_server.server import run_server
from memex.watcher.git_hook import install_hooks
from memex.graph.client import get_graph_client

def setup_logging():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%H:%M:%S"
    )

async def print_status(repo_root: str):
    """Prints node counts from Neo4j."""
    try:
        client = await get_graph_client()
        result = await client.driver.execute_query(
            "MATCH (n) RETURN labels(n) as labels, count(n) as count"
        )
        print(f"Memex Status for {os.path.abspath(repo_root)}:")
        if not result.records:
            print("  Graph is empty.")
            return
            
        for record in result.records:
            labels = record.get("labels", [])
            count = record.get("count", 0)
            print(f"  {labels}: {count}")
    except Exception as e:
        print(f"Error fetching status: {e}")

def main():
    setup_logging()
    parser = argparse.ArgumentParser(prog="memex", description="Memex: Developer Context Continuity System")
    subparsers = parser.add_subparsers(dest="command", required=True)

    # watch
    watch_parser = subparsers.add_parser("watch", help="Start the watcher daemon")
    watch_parser.add_argument("--repo", default=".", help="Path to repository")

    # init
    init_parser = subparsers.add_parser("init", help="Initialize memex in a repository")
    init_parser.add_argument("--repo", default=".", help="Path to repository")

    # status
    status_parser = subparsers.add_parser("status", help="Show graph status")
    status_parser.add_argument("--repo", default=".", help="Path to repository")

    # pause
    pause_parser = subparsers.add_parser("pause", help="Pause the watcher daemon")
    pause_parser.add_argument("--repo", default=".", help="Path to repository")

    # resume
    resume_parser = subparsers.add_parser("resume", help="Resume the watcher daemon")
    resume_parser.add_argument("--repo", default=".", help="Path to repository")

    # serve
    serve_parser = subparsers.add_parser("serve", help="Start the MCP server")
    serve_parser.add_argument("--repo", default=".", help="Path to repository")

    args = parser.parse_args()
    repo_root = os.path.abspath(args.repo)
    memex_dir = Path(repo_root) / ".memex"
    pause_file = memex_dir / "paused"

    if args.command == "watch":
        try:
            asyncio.run(run_daemon(repo_root))
        except KeyboardInterrupt:
            pass

    elif args.command == "serve":
        try:
            asyncio.run(run_server(repo_root))
        except KeyboardInterrupt:
            pass

    elif args.command == "init":
        memex_dir.mkdir(exist_ok=True)
        try:
            install_hooks(repo_root)
            print(f"Initialized memex in {repo_root}")
        except Exception as e:
            print(f"Failed to initialize: {e}")

    elif args.command == "status":
        asyncio.run(print_status(repo_root))

    elif args.command == "pause":
        memex_dir.mkdir(exist_ok=True)
        pause_file.touch()
        print(f"Paused watcher for {repo_root}")

    elif args.command == "resume":
        if pause_file.exists():
            pause_file.unlink()
            print(f"Resumed watcher for {repo_root}")
        else:
            print("Watcher was not paused.")

if __name__ == "__main__":
    main()
