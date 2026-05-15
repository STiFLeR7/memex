import os
import yaml
from pydantic import BaseModel, Field
from typing import Optional, List, Dict
from dotenv import load_dotenv

# Load .env file if it exists
load_dotenv()

class HarnessConfig(BaseModel):
    initial_decision_confidence: float = 0.6
    corroboration_window_days: int = 14

class Config(BaseModel):
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str
    gemini_api_key: str
    neo4j_database: str = "neo4j"
    
    # Model configuration
    gemini_model: str = "gemini-2.5-flash"
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

def load_config() -> Config:
    """
    Loads configuration from environment variables and optionally config.yaml.
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

    # Load from config.yaml if it exists
    config_yaml_path = os.path.join(os.getcwd(), "config.yaml")
    if os.path.exists(config_yaml_path):
        with open(config_yaml_path, "r") as f:
            yaml_data = yaml.safe_load(f)
            if yaml_data:
                config_dict.update(yaml_data)

    try:
        return Config(**config_dict)
    except Exception as e:
        # Re-raise with a more helpful message if required fields are missing
        required_vars = ["NEO4J_URI", "NEO4J_USER", "NEO4J_PASSWORD", "GEMINI_API_KEY"]
        missing = [v for v in required_vars if v not in config_dict]
        if missing:
             raise ValueError(f"Missing required configuration: {', '.join(missing)}")
        raise e

# Singleton instance for the application
_config: Optional[Config] = None

def get_config() -> Config:
    global _config
    if _config is None:
        _config = load_config()
    return _config
