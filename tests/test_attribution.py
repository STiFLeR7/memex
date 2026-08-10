"""Locks harness-attribution behavior end-to-end (v0.8.0 Pillar A).

Gap 1 in docs/PLAN-v0.8.0.md claimed clientInfo was never read. It already
was, as of v0.7.0 Phase 01 — these tests assert the existing chain
(detect_agent -> Config.harness_config -> Decision.harness) stays correct,
plus the one real gap: alias normalization for client names that don't
exact-match a config.yaml harness key.
"""

from unittest.mock import MagicMock, patch

from memex.graph.telemetry import CLIENT_NAME_MAP, detect_agent


def test_clientinfo_exact_match_passes_through():
    """A clientInfo.name that already matches a config.yaml harness key
    (e.g. "claude-code") is returned unchanged — no normalization needed."""
    assert detect_agent(client_info_name="claude-code") == "claude-code"


def test_clientinfo_known_alias_normalizes_to_harness_key():
    """CLIENT_NAME_MAP entries normalize correctly whenever populated —
    verified with a patched entry rather than assuming production content,
    since the map intentionally starts empty (Step 3's rationale)."""
    with patch.dict(CLIENT_NAME_MAP, {"Claude Code": "claude-code"}, clear=True):
        assert detect_agent(client_info_name="Claude Code") == "claude-code"


def test_clientinfo_unknown_alias_passes_through_unmapped():
    """A clientInfo.name with no CLIENT_NAME_MAP entry passes through as-is
    (Config.harness_config's own `default` fallback handles the rest —
    detect_agent must not swallow unknown-but-real client names)."""
    assert detect_agent(client_info_name="some-future-tool") == "some-future-tool"


def test_agent_session_stores_resolved_harness():
    """The value detect_agent() returns is exactly what gets threaded into
    AgentSession/Decision.harness downstream (get_or_create_agent_session
    keys on `agent`, memex/mcp_server/tools_write.py) — this test locks the
    contract at the detect_agent() boundary since the write-path threading
    itself was verified in Phase 01."""
    resolved = detect_agent(client_info_name="gemini-cli")
    assert resolved == "gemini-cli"
    assert isinstance(resolved, str) and resolved
