import logging
from typing import Optional, List
from graphiti_core import Graphiti
from graphiti_core.cross_encoder.client import CrossEncoderClient
from graphiti_core.embedder.gemini import GeminiEmbedder, GeminiEmbedderConfig
from memex.config import get_config
from google.genai import types

logger = logging.getLogger(__name__)

class NoOpCrossEncoder(CrossEncoderClient):
    """
    A CrossEncoder that does nothing, to bypass OpenAI requirements in Graphiti.
    """
    async def rank(self, query: str, documents: List[str]) -> List[float]:
        return [0.5] * len(documents)

class FixedGeminiEmbedder(GeminiEmbedder):
    """
    Bypasses a bug in Graphiti's GeminiEmbedder where batch embedding fails.
    """
    async def create_batch(self, input_data_list: list[str]) -> list[list[float]]:
        if not input_data_list:
            return []
            
        all_embeddings = []
        for item in input_data_list:
            result = await self.client.aio.models.embed_content(
                model=self.config.embedding_model,
                contents=item,
                config=types.EmbedContentConfig(output_dimensionality=self.config.embedding_dim),
            )
            if result.embeddings and len(result.embeddings) > 0:
                all_embeddings.append(result.embeddings[0].values)
            else:
                raise ValueError(f"No embedding returned for: {item}")
        return all_embeddings

class GraphClient:
    """
    Singleton Graphiti client for memex.
    """
    _instance: Optional[Graphiti] = None

    @classmethod
    async def get_instance(cls) -> Graphiti:
        if cls._instance is None:
            config = get_config()
            
            from graphiti_core.llm_client.gemini_client import GeminiClient
            from graphiti_core.llm_client.config import LLMConfig
            from google import genai
            
            # Initialize common genai client
            genai_client = genai.Client(api_key=config.gemini_api_key)
            
            # Configure LLM Client
            llm_config = LLMConfig(model=config.gemini_model)
            llm_client = GeminiClient(config=llm_config, client=genai_client)
            
            # Configure Embedder
            embedder_config = GeminiEmbedderConfig(embedding_model=config.embedding_model)
            embedder = FixedGeminiEmbedder(config=embedder_config, client=genai_client)
            
            # Initialize Graphiti
            cls._instance = Graphiti(
                uri=config.neo4j_uri,
                user=config.neo4j_user,
                password=config.neo4j_password,
                llm_client=llm_client,
                embedder=embedder,
                cross_encoder=NoOpCrossEncoder()
            )
            logger.info("Graphiti client initialized with model %s", config.gemini_model)
            
        return cls._instance

    @classmethod
    async def reset(cls):
        """Clears the singleton instance and closes the driver."""
        if cls._instance:
            try:
                await cls._instance.driver.close()
            except Exception:
                pass
            cls._instance = None
            logger.info("Graphiti client reset")

async def get_graph_client() -> Graphiti:
    return await GraphClient.get_instance()

async def reset_graph_client():
    await GraphClient.reset()
