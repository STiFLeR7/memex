"""Telemetry and validation statistics aggregation layer (v0.5.1).

Aggregates tool calls, token metrics, agent clients, and validation health.
Shared by the CLI stats command and the HTTP GET /stats API.
"""

from __future__ import annotations
import os
import sqlite3
import asyncio
import logging
from datetime import datetime, timezone, timedelta
from typing import Any, Optional, Dict, List
from pathlib import Path

from memex.graph.client import get_graph_client
from memex.graph.telemetry import TelemetryDB, normalize_repo_path

logger = logging.getLogger(__name__)


def _query_db_sync(db: TelemetryDB, normalized_repo: Optional[str]) -> Dict[str, Any]:
    """Synchronous SQLite aggregation execution."""
    now = datetime.now(timezone.utc)
    
    # 1. Boundaries
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    today_start_str = today_start.isoformat()
    
    seven_days_ago = now - timedelta(days=7)
    seven_days_ago_str = seven_days_ago.isoformat()
    
    thirty_days_ago = now - timedelta(days=30)
    thirty_days_ago_str = thirty_days_ago.isoformat()
    
    def execute_summary_query(conn: sqlite3.Connection, start_time_str: Optional[str]) -> Dict[str, Any]:
        if start_time_str:
            if normalized_repo:
                query = """
                    SELECT
                        COUNT(*) as total_calls,
                        coalesce(SUM(tokens_returned), 0) as tokens_returned,
                        coalesce(SUM(tokens_naive), 0) as tokens_naive,
                        coalesce(SUM(tokens_saved), 0) as tokens_saved
                    FROM tool_calls
                    WHERE repo_path = ? AND called_at >= ?
                """
                params = (normalized_repo, start_time_str)
            else:
                query = """
                    SELECT
                        COUNT(*) as total_calls,
                        coalesce(SUM(tokens_returned), 0) as tokens_returned,
                        coalesce(SUM(tokens_naive), 0) as tokens_naive,
                        coalesce(SUM(tokens_saved), 0) as tokens_saved
                    FROM tool_calls
                    WHERE called_at >= ?
                """
                params = (start_time_str,)
        else:
            if normalized_repo:
                query = """
                    SELECT
                        COUNT(*) as total_calls,
                        coalesce(SUM(tokens_returned), 0) as tokens_returned,
                        coalesce(SUM(tokens_naive), 0) as tokens_naive,
                        coalesce(SUM(tokens_saved), 0) as tokens_saved
                    FROM tool_calls
                    WHERE repo_path = ?
                """
                params = (normalized_repo,)
            else:
                query = """
                    SELECT
                        COUNT(*) as total_calls,
                        coalesce(SUM(tokens_returned), 0) as tokens_returned,
                        coalesce(SUM(tokens_naive), 0) as tokens_naive,
                        coalesce(SUM(tokens_saved), 0) as tokens_saved
                    FROM tool_calls
                """
                params = ()
                
        row = conn.execute(query, params).fetchone()
        calls = row["total_calls"] or 0
        returned = row["tokens_returned"] or 0
        naive = row["tokens_naive"] or 0
        saved = row["tokens_saved"] or 0
        
        reduction = 0.0
        if naive > 0:
            reduction = (saved / naive) * 100.0
            
        return {
            "tool_calls": calls,
            "tokens_returned": returned,
            "tokens_naive": naive,
            "tokens_saved": saved,
            "reduction_pct": round(reduction, 1)
        }

    with db.get_connection() as conn:
        today_data = execute_summary_query(conn, today_start_str)
        seven_days = execute_summary_query(conn, seven_days_ago_str)
        thirty_days = execute_summary_query(conn, thirty_days_ago_str)
        lifetime = execute_summary_query(conn, None)
        
        # 7 days: average savings per call
        avg_savings_7 = 0.0
        if seven_days["tool_calls"] > 0:
            avg_savings_7 = seven_days["tokens_saved"] / seven_days["tool_calls"]
        seven_days["avg_savings_per_call"] = round(avg_savings_7, 1)
        
        # 30 days: average daily savings
        avg_daily_30 = thirty_days["tokens_saved"] / 30.0
        thirty_days["avg_daily_savings"] = round(avg_daily_30, 1)
        
        # 2. Top Tools
        if normalized_repo:
            top_tools_query = """
                SELECT
                    tool_name,
                    COUNT(*) as calls,
                    coalesce(SUM(tokens_saved), 0) as tokens_saved
                FROM tool_calls
                WHERE repo_path = ?
                GROUP BY tool_name
                ORDER BY tokens_saved DESC, calls DESC
            """
            top_tools_params = (normalized_repo,)
        else:
            top_tools_query = """
                SELECT
                    tool_name,
                    COUNT(*) as calls,
                    coalesce(SUM(tokens_saved), 0) as tokens_saved
                FROM tool_calls
                GROUP BY tool_name
                ORDER BY tokens_saved DESC, calls DESC
            """
            top_tools_params = ()
            
        top_tools_rows = conn.execute(top_tools_query, top_tools_params).fetchall()
        top_tools = []
        for r in top_tools_rows:
            top_tools.append({
                "tool_name": r["tool_name"],
                "calls": r["calls"],
                "tokens_saved": r["tokens_saved"]
            })
            
        # 3. Agent Breakdown
        if normalized_repo:
            agents_query = """
                SELECT
                    agent,
                    COUNT(*) as calls,
                    coalesce(SUM(tokens_saved), 0) as tokens_saved
                FROM tool_calls
                WHERE repo_path = ?
                GROUP BY agent
            """
            agents_params = (normalized_repo,)
        else:
            agents_query = """
                SELECT
                    agent,
                    COUNT(*) as calls,
                    coalesce(SUM(tokens_saved), 0) as tokens_saved
                FROM tool_calls
                GROUP BY agent
            """
            agents_params = ()
            
        agent_rows = conn.execute(agents_query, agents_params).fetchall()
        
    agents_map = {
        "claude-code": "Claude Code",
        "gemini-cli": "Gemini CLI",
        "cursor": "Cursor",
        "codex": "Codex",
    }
    
    total_lifetime_calls = lifetime["tool_calls"]
    
    agent_groups = {
        "Claude Code": {"calls": 0, "tokens_saved": 0},
        "Gemini CLI": {"calls": 0, "tokens_saved": 0},
        "Cursor": {"calls": 0, "tokens_saved": 0},
        "Codex": {"calls": 0, "tokens_saved": 0},
        "Other": {"calls": 0, "tokens_saved": 0},
    }
    
    for r in agent_rows:
        agent_raw = r["agent"]
        display_name = agents_map.get(agent_raw, "Other")
        agent_groups[display_name]["calls"] += r["calls"]
        agent_groups[display_name]["tokens_saved"] += r["tokens_saved"]
        
    agents_list = []
    for name, data in agent_groups.items():
        if data["calls"] > 0:
            pct = (data["calls"] / total_lifetime_calls * 100.0) if total_lifetime_calls > 0 else 0.0
            agents_list.append({
                "agent": name,
                "calls": data["calls"],
                "pct": round(pct, 1),
                "tokens_saved": data["tokens_saved"]
            })
            
    agents_list.sort(key=lambda x: x["calls"], reverse=True)
    
    return {
        "today": today_data,
        "last_7_days": seven_days,
        "last_30_days": thirty_days,
        "lifetime": lifetime,
        "top_tools": top_tools,
        "agents": agents_list
    }


async def get_stats_data(repo_path: Optional[str] = None) -> Dict[str, Any]:
    """Retrieves all telemetry statistics and validation health."""
    db = TelemetryDB()
    normalized_repo = None
    if repo_path:
        try:
            normalized_repo = normalize_repo_path(repo_path)
        except Exception:
            normalized_repo = os.path.abspath(repo_path)
            
    # 1. Fetch DB Stats in thread pool
    stats = await asyncio.to_thread(_query_db_sync, db, normalized_repo)
    
    # 2. Retrieve validation health from Neo4j
    validation_health = {
        "validated": 0,
        "unvalidated": 0,
        "corroborated": 0,
        "last_review_days_ago": None
    }
    try:
        client = await get_graph_client()
        query = """
        MATCH (d:Entity)
        WHERE (d.type = 'Decision' OR d.name CONTAINS 'Decision')
          AND ($repo IS NULL OR d.repo_path = $repo)
        RETURN
          sum(case when coalesce(d.validated, false) = true then 1 else 0 end) as validated,
          sum(case when coalesce(d.validated, false) = false and coalesce(d.excluded, false) = false then 1 else 0 end) as unvalidated,
          sum(case when coalesce(d.corroborated, false) = true then 1 else 0 end) as corroborated,
          max(coalesce(d.validated_at, d.updated_at)) as last_validated_at
        """
        res = await client.driver.execute_query(query, params={"repo": normalized_repo})
        if res.records:
            rec = res.records[0].data()
            validation_health["validated"] = rec.get("validated") or 0
            validation_health["unvalidated"] = rec.get("unvalidated") or 0
            validation_health["corroborated"] = rec.get("corroborated") or 0
            
            last_validated_at = rec.get("last_validated_at")
            if last_validated_at:
                if isinstance(last_validated_at, str):
                    try:
                        s = last_validated_at.replace("Z", "+00:00")
                        dt = datetime.fromisoformat(s)
                    except Exception:
                        dt = None
                else:
                    dt = last_validated_at
                if dt:
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    now = datetime.now(timezone.utc)
                    validation_health["last_review_days_ago"] = (now - dt).days
    except Exception as e:
        logger.warning(f"Failed to fetch validation health in get_stats_data: {e}")
        
    stats["validation_health"] = validation_health
    return stats


def print_rich_stats(stats: Dict[str, Any], repo_path: Optional[str] = None) -> None:
    """Renders formatted statistics on the CLI terminal using rich."""
    from rich.console import Console
    from rich.table import Table
    from rich.text import Text
    from rich.panel import Panel
    
    console = Console()
    
    title = "memex statistics"
    if repo_path:
        title += f" [dim]({repo_path})[/dim]"
    console.print(f"\n[bold]{title}[/bold]\n")
    
    # 1. Summary Table
    summary_table = Table(box=None, show_header=True, header_style="bold cyan")
    summary_table.add_column("Period", width=15)
    summary_table.add_column("Calls", justify="right")
    summary_table.add_column("Returned", justify="right")
    summary_table.add_column("Naive", justify="right")
    summary_table.add_column("Saved", justify="right")
    summary_table.add_column("Reduction", justify="right")
    
    today = stats["today"]
    summary_table.add_row(
        "today",
        f"{today['tool_calls']:,}",
        f"{today['tokens_returned']:,}",
        f"{today['tokens_naive']:,}",
        f"[green]{today['tokens_saved']:,}[/green]",
        f"[cyan]{today['reduction_pct']}%[/cyan]"
    )
    
    d7 = stats["last_7_days"]
    summary_table.add_row(
        "last 7 days",
        f"{d7['tool_calls']:,}",
        f"{d7['tokens_returned']:,}",
        f"{d7['tokens_naive']:,}",
        f"[green]{d7['tokens_saved']:,}[/green]",
        f"[cyan]{d7['reduction_pct']}%[/cyan] (avg {d7['avg_savings_per_call']:,}/call)"
    )
    
    d30 = stats["last_30_days"]
    summary_table.add_row(
        "last 30 days",
        f"{d30['tool_calls']:,}",
        f"{d30['tokens_returned']:,}",
        f"{d30['tokens_naive']:,}",
        f"[green]{d30['tokens_saved']:,}[/green]",
        f"[cyan]{d30['reduction_pct']}%[/cyan] (avg {d30['avg_daily_savings']:,}/day)"
    )
    
    lt = stats["lifetime"]
    summary_table.add_row(
        "lifetime",
        f"{lt['tool_calls']:,}",
        f"{lt['tokens_returned']:,}",
        f"{lt['tokens_naive']:,}",
        f"[green]{lt['tokens_saved']:,}[/green]",
        f"[cyan]{lt['reduction_pct']}%[/cyan]"
    )
    
    console.print(summary_table)
    console.print()
    
    def format_tokens_short(tokens: int) -> str:
        if tokens >= 1_000_000:
            return f"{tokens / 1_000_000:.1f}M"
        elif tokens >= 1_000:
            return f"{tokens / 1_000:.1f}K"
        return str(tokens)

    # 2. Top Tools Table
    tools_table = Table(box=None, show_header=True, header_style="bold yellow")
    tools_table.add_column("Top Tools", width=25)
    tools_table.add_column("Calls", justify="right")
    tools_table.add_column("Tokens Saved", justify="right")
    
    for tool in stats["top_tools"][:5]:
        saved_str = format_tokens_short(tool["tokens_saved"])
        tools_table.add_row(
            tool["tool_name"],
            f"{tool['calls']:,}",
            f"[green]{saved_str}[/green]"
        )
    if not stats["top_tools"]:
        tools_table.add_row("no tools called yet", "0", "0")
        
    console.print(tools_table)
    console.print()
    
    # 3. Agent Clients Table
    agents_table = Table(box=None, show_header=True, header_style="bold magenta")
    agents_table.add_column("Agent Clients", width=20)
    agents_table.add_column("Calls", justify="right")
    agents_table.add_column("Saved", justify="right")
    agents_table.add_column("Share", justify="right")
    
    for agent in stats["agents"]:
        saved_str = format_tokens_short(agent["tokens_saved"])
        agents_table.add_row(
            agent["agent"],
            f"{agent['calls']:,}",
            f"[green]{saved_str}[/green]",
            f"[cyan]{agent['pct']:.0f}%[/cyan]"
        )
    if not stats["agents"]:
        agents_table.add_row("no agents recorded", "0", "0", "0%")
        
    console.print(agents_table)
    console.print()
    
    # 4. Most Valuable Tool
    if stats["top_tools"]:
        mv_tool = stats["top_tools"][0]
        panel_content = Text.assemble(
            "Most Valuable Tool: ", (mv_tool["tool_name"], "bold yellow"),
            "\nSaved: ", (f"{mv_tool['tokens_saved']:,}", "bold green"), " tokens"
        )
        console.print(Panel(panel_content, border_style="green", expand=False))
        console.print()
        
    # 5. Validation Health
    vh = stats["validation_health"]
    console.print("[bold]validation health:[/bold]")
    console.print(f"  validated:       {vh['validated']}")
    console.print(f"  unvalidated:     {vh['unvalidated']}")
    console.print(f"  corroborated:   {vh['corroborated']}")
    last_review = vh['last_review_days_ago']
    last_review_str = f"{last_review} day(s) ago" if last_review is not None else "never"
    console.print(f"  last review:     {last_review_str}")
    console.print()
