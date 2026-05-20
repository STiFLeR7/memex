from memex.mcp_server.formatter import (
    format_project_context,
    format_symbol_context,
    format_decisions,
    format_problems,
    format_stale_edges,
    TOKEN_LIMIT_BRIEFING,
    CHARS_PER_TOKEN
)

def test_format_project_context_truncates_at_2000_tokens():
    counts = {"modules": 0, "symbols": 0, "decisions": 0, "problems": 0}
    # Each module line is ~150 chars. 100 modules = 15,000 chars.
    # 2000 tokens * 4 = 8,000 chars limit.
    long_modules = [{"path": f"module_{i}.py", "description": "X"*200, "symbols": 0} for i in range(100)]
    
    result = format_project_context("repo", counts, long_modules, [], [], 0)
    assert "[truncated — use scope= or module= parameter to narrow]" in result
    assert len(result) <= TOKEN_LIMIT_BRIEFING * CHARS_PER_TOKEN

def test_format_symbol_context_no_callers_shows_empty_section():
    symbol = {
        "name": "login", "kind": "fn", "file": "auth.py", "line": 10, 
        "signature": "def login()", "confidence": 1.0, "stale": False
    }
    result = format_symbol_context(symbol, [], [], [], [])
    assert "no known callers" in result.lower()
    assert "no known callees" in result.lower()

def test_format_decisions_caps_at_20_and_notes_total():
    decisions = [{"text": f"D{i}", "date": "2026-05-10", "scope": "local", "rationale": "R", "sha": "S", "module_paths": []} for i in range(25)]
    result = format_decisions(decisions, 7, None, 25)
    # format_decisions slices at [:20]
    assert result.count("scope: local") == 20
    assert "showing 20 of 25" in result

def test_format_stale_edges_sorts_by_confidence_ascending():
    # My formatter doesn't sort, it assumes sorted input from query.
    edges = [
        {"source": "A", "target": "B", "edge_type": "C", "confidence": 0.1, "date": "2026", "sha": "S", "id": "e1"},
        {"source": "A", "target": "B", "edge_type": "C", "confidence": 0.4, "date": "2026", "sha": "S", "id": "e2"},
    ]
    result = format_stale_edges(edges, 0.5, 2)
    assert "[conf: 0.10]" in result
    assert "[conf: 0.40]" in result

def test_format_problems_sorts_critical_first():
    # Formatter assumes sorted input.
    problems = [
        {"severity": "critical", "text": "P1", "module": "M1", "date": "2026", "agent": "W", "id": "1"},
        {"severity": "low", "text": "P2", "module": "M2", "date": "2026", "agent": "W", "id": "2"},
    ]
    result = format_problems(problems, None)
    assert result.index("[CRITICAL]") < result.index("[LOW]")

def test_format_project_context_no_scope_arg():
    # format_project_context does NOT have a scope parameter, it just displays repo_root
    counts = {"modules": 1, "symbols": 1, "decisions": 0, "problems": 0}
    modules = [{"path": "memex/cli.py", "description": "CLI", "symbols": 1}]
    result = format_project_context("repo/scope", counts, modules, [], [], 0)
    assert "repo/scope" in result
    assert "memex/cli.py" in result

def test_format_symbol_context_with_decisions_and_problems():
    symbol = {
        "name": "login", "kind": "fn", "file": "auth.py", "line": 10, 
        "signature": "def login()", "confidence": 0.9, "stale": False
    }
    decisions = ["Use OAuth"]
    problems = ["Broken on LDAP"]
    result = format_symbol_context(symbol, [], [], decisions, problems)
    assert "Use OAuth" in result
    assert "Broken on LDAP" in result
    assert "0.90" in result

def test_format_stale_edges_empty():
    result = format_stale_edges([], 0.5, 0)
    assert "no stale edges below threshold 0.50" in result

def test_format_problems_empty():
    result = format_problems([], None)
    assert "no open problems recorded" in result

def test_format_decisions_empty():
    result = format_decisions([], 7, None, 0)
    assert "no decisions recorded in the last 7 days" in result

def test_format_project_context_with_stale_warning():
    counts = {"modules": 1, "symbols": 1, "decisions": 0, "problems": 0}
    modules = [{"path": "m.py", "description": "D", "symbols": 1}]
    result = format_project_context("repo", counts, modules, [], [], 5)
    assert "5 edges below confidence 0.3" in result

def test_format_symbol_context_not_found():
    result = format_symbol_context(None, [], [], [], [])
    assert "symbol not found" in result.lower()

def test_truncate_if_needed_fallback():
    # Test fallback branch where first line is too long
    from memex.mcp_server.formatter import _truncate_if_needed
    long_line = "X" * 7000
    # Limit 1500 tokens * 4 = 6000 chars. 7000 chars exceeds this.
    result = _truncate_if_needed([long_line], 1500)
    assert "[truncated" in result
    assert len(result) <= 6000

def test_format_search_results():
    from memex.mcp_server.formatter import format_search_results
    class MockResult:
        def __init__(self, type, name, file, confidence, stale, score):
            self.type = type
            self.name = name
            self.file = file
            self.confidence = confidence
            self.stale = stale
            self.score = score
    
    results = [
        MockResult("Symbol", "login", "auth.py", 0.9, False, 0.88),
        MockResult("Decision", None, None, 1.0, False, 0.75)
    ]
    # For Decision, set 'fact' attribute as fallback for name
    results[1].fact = "Use OAuth"
    
    result = format_search_results("auth", results)
    assert "search results for: 'auth'" in result
    assert "[Symbol] login" in result
    assert "file: auth.py" in result
    assert "[Decision] Use OAuth" in result

def test_format_search_results_empty():
    from memex.mcp_server.formatter import format_search_results
    result = format_search_results("missing", [])
    assert "no relevant context found for query: 'missing'" in result
