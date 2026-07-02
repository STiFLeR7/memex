import os
import yaml
from pathlib import Path
from pydantic import BaseModel, Field
from typing import Optional, List, Dict
from dotenv import load_dotenv

# Load .env file if it exists
load_dotenv()


def canonical_repo_path(p: Optional[str]) -> Optional[str]:
    """Canonicalize a repo path so the watcher (write) and MCP server (read)
    always produce the byte-identical `repo_path` join key (audit B1).

    Collapses `.`/`..`/trailing separators and symlinks via ``resolve()``,
    emits POSIX separators, and case-folds on Windows (case-insensitive FS).
    Idempotent. Passes through ``None``/empty unchanged so callers don't have
    to special-case them.
    """
    if not p:
        return p
    try:
        resolved = Path(p).resolve()
    except Exception:
        resolved = Path(p)
    s = resolved.as_posix()
    if os.name == "nt":
        s = s.lower()
    return s

class HarnessConfig(BaseModel):
    initial_decision_confidence: float = 0.6
    corroboration_window_days: int = 14


# Phase 7 — Retrieval composite-reranker config (ARCHITECTURE-v0.3.0 §8).
# Defaults mirror the module constants in ``memex.mcp_server.reranker`` so
# explicit config and module-default behaviour stay in sync.
class RetrievalConfig(BaseModel):
    recency_tau_days: int = 90                # τ — exponential decay (days)
    conf_floor: float = 0.5                   # confidence factor floor
    rehearsal_weight: float = 0.1             # access_count log coefficient
    rrf_k: int = 60                           # RRF constant for cross-modality merge
    conflict_similarity_threshold: float = 0.4  # below this + overlapping validity = conflict (Phase 7)
    contradiction_similarity_threshold: float = 0.85  # MCP-write intent-confirmation threshold (Phase 9)


class Config(BaseModel):
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str
    gemini_api_key: str
    neo4j_database: str = "neo4j"
    
    # Model configuration
    gemini_model: str = "gemini-2.5-flash"
    # Phase 9 — Gemini Pro used for synthesis tools (explain_change). Flash
    # remains the default for extractive / classification tasks; Pro is used
    # only when the tool description explicitly calls for grounded synthesis.
    pro_model: str = "gemini-2.5-pro"
    embedding_model: str = "models/gemini-embedding-2"
    
    # Performance & Timing
    debounce_window: float = 0.8
    poll_interval: float = 0.5
    
    # Scheduler configuration
    decay_hour: int = 2
    decay_minute: int = 0
    decay_hours_threshold: int = 24
    
    # Ignored directories
    ignored_patterns: List[str] = Field(default_factory=lambda: [
        ".git", "__pycache__", "node_modules", ".venv", "dist", "build", ".memex"
    ])
    
    repo_root: str = "."
    log_level: str = "INFO"

    # Harness configurations
    harnesses: Dict[str, HarnessConfig] = Field(default_factory=dict)

    # Phase 7 — composite-reranker / RRF / conflict-detection knobs.
    retrieval: RetrievalConfig = Field(default_factory=RetrievalConfig)

    def harness_config(self, harness: Optional[str]) -> HarnessConfig:
        """Resolve the HarnessConfig for ``harness``, falling back to the
        ``default`` entry and finally to the HarnessConfig defaults.

        ``harness`` is the writing client's identity (e.g. ``claude-code``,
        ``gemini-cli``, ``codex``) or ``None``/``"watcher"`` for the commit
        synthesiser. Unknown harnesses resolve to ``default`` so the config
        stays forward-compatible with clients we haven't named yet.
        """
        if harness and harness in self.harnesses:
            return self.harnesses[harness]
        if "default" in self.harnesses:
            return self.harnesses["default"]
        return HarnessConfig()

    def initial_confidence_for(self, harness: Optional[str]) -> float:
        """Initial ``base_confidence`` a freshly-written Decision should carry,
        keyed by the writing harness (Signal Pillar A). This is the single
        source of truth — agent writes and commit synthesis both route through
        here instead of hardcoding 0.6."""
        return self.harness_config(harness).initial_decision_confidence

def load_config(repo_root: Optional[str] = None) -> Config:
    """
    Loads configuration from environment variables and optionally config.yaml.

    ``config.yaml`` is resolved relative to ``repo_root`` when provided (audit
    B2) — the MCP server is spawned from the *client's* CWD, not the project
    root, so a CWD-relative lookup would silently miss ``<repo>/config.yaml``
    (or load a stray one). Falls back to CWD for backwards compatibility when
    no repo is given.
    """
    # Base configuration from environment variables
    env_config = {
        "neo4j_uri": os.getenv("NEO4J_URI"),
        "neo4j_user": os.getenv("NEO4J_USER"),
        "neo4j_password": os.getenv("NEO4J_PASSWORD"),
        "gemini_api_key": os.getenv("GEMINI_API_KEY"),
        "neo4j_database": os.getenv("NEO4J_DATABASE"),
        "gemini_model": os.getenv("GEMINI_MODEL"),
        "embedding_model": os.getenv("EMBEDDING_MODEL"),
        "debounce_window": os.getenv("DEBOUNCE_WINDOW"),
        "poll_interval": os.getenv("POLL_INTERVAL"),
        "decay_hour": os.getenv("DECAY_HOUR"),
        "decay_minute": os.getenv("DECAY_MINUTE"),
        "decay_hours_threshold": os.getenv("DECAY_HOURS_THRESHOLD"),
        "log_level": os.getenv("GRAPHITI_LOG_LEVEL"),
    }

    ignored = os.getenv("MEMEX_IGNORED_PATTERNS")
    if ignored:
        env_config["ignored_patterns"] = ignored.split(",")

    # Remove None values to allow Pydantic defaults or YAML overrides
    config_dict = {k: v for k, v in env_config.items() if v is not None}
    
    # Convert numeric strings from env to correct types for merging
    if "debounce_window" in config_dict: config_dict["debounce_window"] = float(config_dict["debounce_window"])
    if "poll_interval" in config_dict: config_dict["poll_interval"] = float(config_dict["poll_interval"])
    if "decay_hour" in config_dict: config_dict["decay_hour"] = int(config_dict["decay_hour"])
    if "decay_minute" in config_dict: config_dict["decay_minute"] = int(config_dict["decay_minute"])
    if "decay_hours_threshold" in config_dict: config_dict["decay_hours_threshold"] = int(config_dict["decay_hours_threshold"])

    # Load from config.yaml if it exists (relative to repo_root when known).
    config_base = repo_root if repo_root else os.getcwd()
    config_yaml_path = os.path.join(config_base, "config.yaml")
    if os.path.exists(config_yaml_path):
        with open(config_yaml_path, "r") as f:
            yaml_data = yaml.safe_load(f)
            if yaml_data:
                config_dict.update(yaml_data)

    try:
        return Config(**config_dict)
    except Exception as e:
        # Re-raise with a more helpful message if required fields are missing.
        required_vars = ["NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "GEMINI_API_KEY"]
        missing = [v for v in required_vars if v.lower() not in config_dict]
        if missing:
            # Introspection-only mode: allow the server to start without a live
            # backend so MCP clients (and directory sandboxes like glama.ai) can
            # enumerate tools. Tool calls themselves will still fail loudly.
            if os.getenv("MEMEX_INTROSPECTION_ONLY") == "1":
                placeholders = {
                    "neo4j_uri": "bolt://introspection-only:7687",
                    "neo4j_user": "introspection",
                    "neo4j_password": "introspection",
                    "gemini_api_key": "introspection-only",
                }
                for k, v in placeholders.items():
                    config_dict.setdefault(k, v)
                return Config(**config_dict)
            raise ValueError(
                f"Missing required configuration: {', '.join(missing)}. "
                "Set these as environment variables, or place them in a .env file at "
                "<repo>/.env (auto-loaded by `memex serve --repo <path>`), or pass "
                "`memex serve --env-file <path/to/.env>`."
            )
        raise e

# Singleton instance for the application
_config: Optional[Config] = None

def get_config() -> Config:
    global _config
    if _config is None:
        _config = load_config()
    return _config
