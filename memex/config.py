import os
from dataclasses import dataclass
from typing import Optional
from dotenv import load_dotenv

# Load .env file if it exists
load_dotenv()

@dataclass
class Config:
    neo4j_uri: str
    neo4j_user: str
    neo4j_password: str
    gemini_api_key: str
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
    
    return Config(
        neo4j_uri=os.environ["NEO4J_URI"],
        neo4j_user=os.environ["NEO4J_USER"],
        neo4j_password=os.environ["NEO4J_PASSWORD"],
        gemini_api_key=os.environ["GEMINI_API_KEY"],
        log_level=os.getenv("GRAPHITI_LOG_LEVEL", "INFO"),
    )

# Singleton instance for the application
_config: Optional[Config] = None

def get_config() -> Config:
    global _config
    if _config is None:
        _config = load_config()
    return _config
