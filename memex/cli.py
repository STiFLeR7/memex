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

async def print_status(repo_root: str):
    print(f"\nmemex status for {Path(repo_root).resolve()}")
    
    # 1. Check if paused
    if (Path(repo_root) / ".memex" / "paused").exists():
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
        print(f"[FAIL] Neo4j reachable         Could not connect. Ensure 'docker-compose up -d' is running.")
        all_pass = False

    # 5. Gemini API key
    gemini_key = os.getenv("GEMINI_API_KEY")
    if gemini_key:
        try:
            from google import genai
            print(f"[PASS] Gemini API key          GEMINI_API_KEY set")
        except Exception:
            print("[FAIL] Gemini API key          GEMINI_API_KEY set but SDK missing")
            all_pass = False
    else:
        print("[FAIL] Gemini API key          GEMINI_API_KEY environment variable not set")
        all_pass = False

    # 6. Git hooks
    hook_path = repo_path / ".git" / "hooks" / "post-commit"
    if hook_path.exists() and "memex" in hook_path.read_text():
        print(f"[PASS] git hooks installed     post-commit hook found in .git/hooks/")
    else:
        print(f"[FAIL] git hooks installed     not found. Run 'memex init' to install.")
        all_pass = False

    # 7. Watchdog running (daemon pid)
    pid_file = repo_path / ".memex" / "daemon.pid"
    if pid_file.exists():
        print(f"[PASS] watchdog running        .memex/daemon.pid exists")
    else:
        print(f"[FAIL] watchdog running        no pid file. Run 'memex watch' to start.")
        all_pass = False

    # 8. Stale edges check
    try:
        stale = await get_stale_edges(threshold=0.5, limit=1)
        if stale:
            print(f"[WARN] Stale edges found       run `get_stale_context()` in your agent for details")
    except Exception:
        pass

    if all_pass:
        print("\nAll checks passed. memex is ready.")
        sys.exit(0)
    else:
        print("\nSome checks failed. Please resolve the issues above.")
        sys.exit(1)

def main(args=None):
    # Common parser for shared arguments
    parent_parser = argparse.ArgumentParser(add_help=False)
    parent_parser.add_argument("--repo", default=".", help="Path to the repository")

    parser = argparse.ArgumentParser(description="memex CLI - Knowledge Graph Watcher")
    subparsers = parser.add_subparsers(dest="command", required=True)
    
    # init
    subparsers.add_parser("init", help="Initialize memex hooks", parents=[parent_parser])
    
    # watch
    subparsers.add_parser("watch", help="Start watcher daemon", parents=[parent_parser])
    
    # status
    subparsers.add_parser("status", help="Show current status", parents=[parent_parser])

    # pause
    subparsers.add_parser("pause", help="Suspend watcher", parents=[parent_parser])

    # resume
    subparsers.add_parser("resume", help="Resume watcher", parents=[parent_parser])

    # serve
    subparsers.add_parser("serve", help="Start MCP server", parents=[parent_parser])

    # doctor
    subparsers.add_parser("doctor", help="Check system health", parents=[parent_parser])
    
    parsed_args = parser.parse_args(args)
    repo_root = parsed_args.repo

    if parsed_args.command == "init":
        install_hooks(repo_root)
        (Path(repo_root) / ".memex").mkdir(exist_ok=True)
        print(f"memex initialized in {Path(repo_root).resolve()}")

    elif parsed_args.command == "watch":
        asyncio.run(run_daemon(repo_root))

    elif parsed_args.command == "status":
        asyncio.run(print_status(repo_root))

    elif parsed_args.command == "pause":
        (Path(repo_root) / ".memex").mkdir(exist_ok=True)
        (Path(repo_root) / ".memex" / "paused").touch()
        print("memex watcher PAUSED.")

    elif parsed_args.command == "resume":
        pause_file = (Path(repo_root) / ".memex" / "paused")
        if pause_file.exists():
            pause_file.unlink()
        print("memex watcher RESUMED.")

    elif parsed_args.command == "serve":
        asyncio.run(run_server(repo_root))

    elif parsed_args.command == "doctor":
        asyncio.run(run_doctor(repo_root))

if __name__ == "__main__":
    main()
