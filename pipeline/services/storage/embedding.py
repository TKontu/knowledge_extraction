"""Embedding service for generating text embeddings."""

from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from config import Settings


class EmbeddingService:
    """Generate embeddings via BGE-large-en."""

    def __init__(self, settings: Settings):
        """Initialize EmbeddingService.

        Args:
            settings: Application settings.
        """
        self.client = AsyncOpenAI(
            base_url=settings.openai_embedding_base_url,
            api_key=settings.openai_api_key,
        )
        self.model = settings.rag_embedding_model

    @property
    def dimension(self) -> int:
        """Get embedding dimension.

        Returns:
            Embedding dimension (1024 for BGE-large-en).
        """
        return 1024

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=60),
    )
    async def embed(self, text: str) -> list[float]:
        """Generate embedding for single text.

        Args:
            text: Text to embed.

        Returns:
            Embedding vector (1024 dimensions).

        Raises:
            Exception: If API call fails after retries.
        """
        response = await self.client.embeddings.create(
            model=self.model,
            input=text,
        )
        return response.data[0].embedding

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=60),
    )
    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts.

        Args:
            texts: List of texts to embed.

        Returns:
            List of embedding vectors (each 1024 dimensions).

        Raises:
            Exception: If API call fails after retries.
        """
        if not texts:
            return []

        response = await self.client.embeddings.create(
            model=self.model,
            input=texts,
        )
        return [item.embedding for item in response.data]
