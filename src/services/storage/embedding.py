"""Embedding service for generating text embeddings."""

import httpx
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
        self._reranker_model = settings.reranker_model
        self._http_client: httpx.AsyncClient | None = None

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
