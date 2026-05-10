import os
from dataclasses import dataclass, field
from typing import Optional, List
from dotenv import load_dotenv

# Load .env file if it exists
load_dotenv()

@dataclass
class Config:
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str
    gemini_api_key: str
    neo4j_database: str = "neo4j"
    
    # Model configuration
    gemini_model: str = "gemini-2.0-flash"
    embedding_model: str = "models/gemini-embedding-2"
    
    # Performance & Timing
    debounce_window: float = 0.8
    poll_interval: float = 0.5
    
    # Scheduler configuration
    decay_hour: int = 2
    decay_minute: int = 0
    decay_hours_threshold: int = 24
    
    # Ignored directories
    ignored_patterns: List[str] = field(default_factory=lambda: [
        ".git", "__pycache__", "node_modules", ".venv", "dist", "build", ".memex"
    ])
    
    repo_root: str = "."
    log_level: str = "INFO"

def load_config() -> Config:
    """
    Loads configuration from environment variables and validates required fields.
    """
    required_vars = [
        "NEO4J_URI",
        "NEO4J_USER",
        "NEO4J_PASSWORD",
        "GEMINI_API_KEY",
    ]
    
    missing = [var for var in required_vars if not os.getenv(var)]
    
    if missing:
        raise ValueError(f"Missing required environment variables: {', '.join(missing)}")
    
    # Load ignored patterns from comma-separated string if provided
    ignored = os.getenv("MEMEX_IGNORED_PATTERNS")
    ignored_list = ignored.split(",") if ignored else [
        ".git", "__pycache__", "node_modules", ".venv", "dist", "build", ".memex"
    ]
    
    return Config(
        neo4j_uri=os.environ["NEO4J_URI"],
        neo4j_user=os.environ["NEO4J_USER"],
        neo4j_password=os.environ["NEO4J_PASSWORD"],
        gemini_api_key=os.environ["GEMINI_API_KEY"],
        neo4j_database=os.getenv("NEO4J_DATABASE", "neo4j"),
        gemini_model=os.getenv("GEMINI_MODEL", "gemini-2.5-flash"),
        embedding_model=os.getenv("EMBEDDING_MODEL", "models/gemini-embedding-2"),
        debounce_window=float(os.getenv("DEBOUNCE_WINDOW", "0.8")),
        poll_interval=float(os.getenv("POLL_INTERVAL", "0.5")),
        decay_hour=int(os.getenv("DECAY_HOUR", "2")),
        decay_minute=int(os.getenv("DECAY_MINUTE", "0")),
        decay_hours_threshold=int(os.getenv("DECAY_HOURS_THRESHOLD", "24")),
        ignored_patterns=ignored_list,
        log_level=os.getenv("GRAPHITI_LOG_LEVEL", "INFO"),
    )

# Singleton instance for the application
_config: Optional[Config] = None

def get_config() -> Config:
    global _config
    if _config is None:
        _config = load_config()
    return _config
