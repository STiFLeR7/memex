"""Tests for the retrieval-tracing harness (Phase 10).

Key invariants:
    - ``log()`` appends one JSON line per call (JSONL contract).
    - File rotates when it exceeds ``MAX_BYTES`` (monkey-patched small
      for the test so we don't actually write 10 MB).
    - ``weekly_summary()`` aggregates only records inside the trailing
      7-day window.
    - Fresh / missing trace file yields safe empty defaults.
    - ``memex doctor`` prints the summary line every run.
"""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from memex.mcp_server import tracing as tracing_mod
from memex.mcp_server.tracing import (
    RetrievalTrace,
    get_retrieval_trace,
    reset_retrieval_traces,
)


@pytest.fixture(autouse=True)
def _reset_singletons():
    """Make sure singleton cache doesn't leak across tests."""
    reset_retrieval_traces()
    yield
    reset_retrieval_traces()


def test_retrieval_trace_appends_jsonl(tmp_path):
    """Three log() calls → three JSON lines in the trace file."""
    trace = RetrievalTrace(str(tmp_path))
    trace.log("query 1", {"modules": 2}, tokens=120, top_uuids=["u1", "u2"])
    trace.log("query 2", {"decisions": 1}, tokens=80, top_uuids=["u3"])
    trace.log("query 3", {"problems": 0}, tokens=40, top_uuids=[])

    path = tmp_path / ".memex" / "retrieval_trace.jsonl"
    assert path.exists()
    lines = [ln for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
    assert len(lines) == 3
    parsed = [json.loads(ln) for ln in lines]
    assert parsed[0]["query"] == "query 1"
    assert parsed[0]["tokens"] == 120
    assert parsed[1]["modality_counts"]["decisions"] == 1
    assert parsed[2]["top_uuids"] == []
    # Each record must carry a UTC ISO timestamp.
    for rec in parsed:
        assert "ts" in rec
        # Round-trip parseability:
        datetime.fromisoformat(rec["ts"].replace("Z", "+00:00"))


def test_retrieval_trace_rotates_at_10mb(tmp_path, monkeypatch):
    """When the file exceeds MAX_BYTES, the next log() rotates the file
    to ``.1``. We patch MAX_BYTES tiny so the test doesn't write 10 MB."""
    monkeypatch.setattr(tracing_mod, "MAX_BYTES", 256)

    trace = RetrievalTrace(str(tmp_path))
    # Each record is ~150-250 bytes; a handful crosses the 256-byte cap.
    for i in range(20):
        trace.log(
            query=f"query {i} " + ("x" * 40),
            modality_counts={"modules": i, "decisions": i},
            tokens=100 + i,
            top_uuids=[f"uuid-{i}-a", f"uuid-{i}-b"],
        )

    base = tmp_path / ".memex" / "retrieval_trace.jsonl"
    rotated_1 = tmp_path / ".memex" / "retrieval_trace.jsonl.1"
    assert base.exists()
    assert rotated_1.exists(), (
        "expected `.1` rotation after exceeding MAX_BYTES; "
        f"files: {sorted(p.name for p in (tmp_path / '.memex').iterdir())}"
    )


def test_weekly_summary_aggregates_recent_queries(tmp_path):
    """Mix records inside/outside the 7-day window; only recent ones
    contribute to the aggregate. ``top_results`` ranks by UUID count."""
    trace = RetrievalTrace(str(tmp_path))
    path = tmp_path / ".memex" / "retrieval_trace.jsonl"
    path.parent.mkdir(exist_ok=True)

    now = datetime.now(timezone.utc)
    long_ago = now - timedelta(days=30)
    recent = [now - timedelta(days=d) for d in (0, 1, 2)]

    records = [
        # Stale — outside the window, must be ignored.
        {"ts": long_ago.isoformat(), "query": "old",
         "modality_counts": {"modules": 1}, "tokens": 1000,
         "top_uuids": ["should-not-appear"], "composite": {}},
        # 3 recent records.
        {"ts": recent[0].isoformat(), "query": "r1",
         "modality_counts": {"modules": 1}, "tokens": 100,
         "top_uuids": ["uA", "uB"], "composite": {}},
        {"ts": recent[1].isoformat(), "query": "r2",
         "modality_counts": {"decisions": 1}, "tokens": 200,
         "top_uuids": ["uA"], "composite": {}},
        {"ts": recent[2].isoformat(), "query": "r3",
         "modality_counts": {"problems": 1}, "tokens": 300,
         "top_uuids": ["uC"], "composite": {}},
    ]
    with open(path, "w", encoding="utf-8") as f:
        for rec in records:
            f.write(json.dumps(rec) + "\n")

    summary = trace.weekly_summary()
    assert summary["total_queries"] == 3
    # avg of 100, 200, 300 = 200
    assert summary["avg_tokens"] == 200
    # uA appears twice → first in ranking
    top_uuids = [r["uuid"] for r in summary["top_results"]]
    assert top_uuids[0] == "uA"
    # Stale uuid must not leak in.
    assert "should-not-appear" not in top_uuids
    # last_query_at should be the most recent (recent[0]).
    assert summary["last_query_at"] is not None


def test_weekly_summary_handles_empty_trace(tmp_path):
    """Fresh repo, no file → safe empty defaults, not an exception."""
    trace = RetrievalTrace(str(tmp_path))
    summary = trace.weekly_summary()
    assert summary == {
        "total_queries": 0,
        "avg_tokens":    0,
        "top_results":   [],
        "last_query_at": None,
    }


def test_get_retrieval_trace_singleton_per_repo(tmp_path):
    """Same repo path → same RetrievalTrace instance."""
    a = get_retrieval_trace(str(tmp_path))
    b = get_retrieval_trace(str(tmp_path))
    assert a is b


@pytest.mark.asyncio
async def test_doctor_surfaces_tracing_summary_line(tmp_path):
    """``memex doctor`` must print the retrieval-tracing summary line
    on every run, even on a fresh repo (then it prints the 'no queries
    traced yet' form)."""
    fake_summary = {
        "total_queries": 42,
        "avg_tokens":    321,
        "top_results":   [{"uuid": "u-top", "count": 7}],
        "last_query_at": datetime(2025, 5, 1, 12, 0, tzinfo=timezone.utc),
    }
    fake_trace = MagicMock()
    fake_trace.weekly_summary.return_value = fake_summary

    captured: list[str] = []

    def fake_print(*args, **kw):
        captured.append(" ".join(str(a) for a in args))

    with patch("subprocess.check_output", return_value=b"v1.0"):
        with patch("memex.cli.get_graph_client", new_callable=AsyncMock):
            with patch("memex.cli.get_stale_edges", new_callable=AsyncMock, return_value=[]):
                with patch("memex.cli_doctor_tracing.get_retrieval_trace",
                           return_value=fake_trace):
                    with patch("os.getenv", return_value="fake-key"):
                        with patch("memex.cli.Path.exists", return_value=True):
                            with patch("memex.cli.Path.read_text",
                                       return_value="memex hook"):
                                with patch("builtins.print", side_effect=fake_print):
                                    from memex import cli as cli_mod
                                    with pytest.raises(SystemExit):
                                        await cli_mod.run_doctor(str(tmp_path))

    joined = "\n".join(captured)
    assert "retrieval tracing" in joined
    assert "42" in joined  # total_queries surfaced
    assert "321" in joined  # avg_tokens surfaced


@pytest.mark.asyncio
async def test_doctor_tracing_line_for_empty_repo(tmp_path):
    """When no queries have been traced, the doctor line is still
    printed — just with the 'no queries traced yet' phrase."""
    fake_trace = MagicMock()
    fake_trace.weekly_summary.return_value = {
        "total_queries": 0, "avg_tokens": 0,
        "top_results": [], "last_query_at": None,
    }

    captured: list[str] = []

    def fake_print(*args, **kw):
        captured.append(" ".join(str(a) for a in args))

    with patch("subprocess.check_output", return_value=b"v1.0"):
        with patch("memex.cli.get_graph_client", new_callable=AsyncMock):
            with patch("memex.cli.get_stale_edges", new_callable=AsyncMock, return_value=[]):
                with patch("memex.cli_doctor_tracing.get_retrieval_trace",
                           return_value=fake_trace):
                    with patch("os.getenv", return_value="fake-key"):
                        with patch("memex.cli.Path.exists", return_value=True):
                            with patch("memex.cli.Path.read_text",
                                       return_value="memex hook"):
                                with patch("builtins.print", side_effect=fake_print):
                                    from memex import cli as cli_mod
                                    with pytest.raises(SystemExit):
                                        await cli_mod.run_doctor(str(tmp_path))

    joined = "\n".join(captured)
    assert "retrieval tracing" in joined
    assert "no queries traced yet" in joined
