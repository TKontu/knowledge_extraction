"""Embedding service for generating text embeddings."""

import asyncio

import httpx
import structlog
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from config import Settings

logger = structlog.get_logger(__name__)

# Safety cap: ~7000 tokens, within bge-m3's 8192 token limit.
# Prevents HTTP 400 crashes from vLLM when input exceeds model max.
MAX_EMBED_CHARS = 28000


class EmbeddingService:
    """Generate embeddings via OpenAI-compatible API (bge-m3).

    Provides concurrency-controlled access to embedding and reranking APIs.
    Uses a semaphore to limit concurrent requests to the embedding server.
    """

    # Class-level semaphore shared across all instances
    _semaphore: asyncio.Semaphore | None = None
    _max_concurrent: int = 50

    @classmethod
    def _get_semaphore(cls) -> asyncio.Semaphore:
        """Get or create the shared semaphore.

        Returns:
            Shared semaphore for concurrency control.
        """
        if cls._semaphore is None:
            cls._semaphore = asyncio.Semaphore(cls._max_concurrent)
        return cls._semaphore

    @classmethod
    def configure_concurrency(cls, max_concurrent: int) -> None:
        """Configure the maximum concurrent requests.

        Should be called once at startup before any requests are made.

        Args:
            max_concurrent: Maximum concurrent embedding/rerank requests.
        """
        cls._max_concurrent = max_concurrent
        cls._semaphore = asyncio.Semaphore(max_concurrent)

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
        self._reranker_model = settings.reranker_model
        self._http_client: httpx.AsyncClient | None = None

        # Configure class-level concurrency from settings (only on first instance)
        if EmbeddingService._semaphore is None:
            EmbeddingService.configure_concurrency(settings.embedding_max_concurrent)

    @property
    def dimension(self) -> int:
        """Get embedding dimension.

        Returns:
            Embedding dimension (1024 for bge-m3).
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
        if len(text) > MAX_EMBED_CHARS:
            logger.debug("embedding_text_truncated", original_length=len(text))
            text = text[:MAX_EMBED_CHARS]

        async with self._get_semaphore():
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

        truncated = []
        for t in texts:
            if len(t) > MAX_EMBED_CHARS:
                logger.debug("embedding_text_truncated", original_length=len(t))
                truncated.append(t[:MAX_EMBED_CHARS])
            else:
                truncated.append(t)

        async with self._get_semaphore():
            response = await self.client.embeddings.create(
                model=self.model,
                input=truncated,
            )
            return [item.embedding for item in response.data]

    async def _get_http_client(self) -> httpx.AsyncClient:
        """Get or create the shared HTTP client.

        Returns:
            Shared httpx.AsyncClient instance.
        """
        if self._http_client is None:
            self._http_client = httpx.AsyncClient(timeout=60.0)
        return self._http_client

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=60),
    )
    async def rerank(
        self,
        query: str,
        documents: list[str],
        model: str | None = None,
    ) -> list[tuple[int, float]]:
        """Rerank documents by relevance to query.

        Uses the /v1/rerank endpoint (OpenAI-compatible) to score
        document relevance. Returns results sorted by score descending.

        Args:
            query: The query text to rank documents against.
            documents: List of document texts to rank.
            model: Reranker model name. If None, uses the configured reranker_model.

        Returns:
            List of (index, score) tuples sorted by relevance (highest first).
            Index refers to position in original documents list.

        Raises:
            Exception: If API call fails after retries.
        """
        if not documents:
            return []

        if not model:
            model = self._reranker_model

        async with self._get_semaphore():
            # Use shared httpx client for rerank (openai client doesn't support it)
            http_client = await self._get_http_client()
            response = await http_client.post(
                f"{self.client.base_url}rerank",
                json={
                    "model": model,
                    "query": query,
                    "documents": documents,
                },
                headers={"Authorization": f"Bearer {self.client.api_key}"},
            )
            response.raise_for_status()
            data = response.json()

        # Parse results - format: {"results": [{"index": 0, "relevance_score": 0.9}, ...]}
        results = data.get("results", [])
        ranked = [(r["index"], r["relevance_score"]) for r in results]

        # Sort by score descending
        ranked.sort(key=lambda x: x[1], reverse=True)

        return ranked
