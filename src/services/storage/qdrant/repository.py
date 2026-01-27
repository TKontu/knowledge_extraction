"""Qdrant repository for embedding storage and search."""

from dataclasses import dataclass
from uuid import UUID

from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    FieldCondition,
    Filter,
    MatchValue,
    PointStruct,
    VectorParams,
)


@dataclass
class EmbeddingItem:
    """Item for batch embedding operations."""

    extraction_id: UUID
    embedding: list[float]
    payload: dict


@dataclass
class SearchResult:
    """Result from semantic search."""

    extraction_id: UUID
    score: float
    payload: dict


class QdrantRepository:
    """Repository for Qdrant vector database operations."""

    def __init__(self, client: QdrantClient):
        """Initialize QdrantRepository.

        Args:
            client: Qdrant client instance.
        """
        self.client = client
        self.collection_name = "extractions"

    async def init_collection(self) -> None:
        """Create collection if it doesn't exist.

        Creates a collection with:
        - Vector size: 1024 (BGE-large-en)
        - Distance metric: Cosine
        """
        # Check if collection exists
        collections = self.client.get_collections().collections
        if any(c.name == self.collection_name for c in collections):
            return

        # Create collection with BGE-large-en configuration
        self.client.create_collection(
            collection_name=self.collection_name,
            vectors_config=VectorParams(
                size=1024,  # BGE-large-en dimension
                distance=Distance.COSINE,
            ),
        )

    async def upsert(
        self,
        extraction_id: UUID,
        embedding: list[float],
        payload: dict,
    ) -> str:
        """Insert or update embedding.

        Args:
            extraction_id: UUID of the extraction.
            embedding: Vector embedding (1024 dimensions).
            payload: Metadata to store with the point.

        Returns:
            Point ID (string version of extraction_id).
        """
        point_id = str(extraction_id)

        # Upsert point (will insert new or update existing)
        self.client.upsert(
            collection_name=self.collection_name,
            points=[
                PointStruct(
                    id=point_id,
                    vector=embedding,
                    payload=payload,
                )
            ],
        )

        return point_id

    async def upsert_batch(self, items: list[EmbeddingItem]) -> list[str]:
        """Batch upsert for efficiency.

        Args:
            items: List of EmbeddingItem objects to upsert.

        Returns:
            List of point IDs (string versions of extraction_ids).
        """
        if not items:
            return []

        # Convert items to PointStruct objects
        points = [
            PointStruct(
                id=str(item.extraction_id),
                vector=item.embedding,
                payload=item.payload,
            )
            for item in items
        ]

        # Batch upsert
        self.client.upsert(
            collection_name=self.collection_name,
            points=points,
        )

        return [str(item.extraction_id) for item in items]

    async def search(
        self,
        query_embedding: list[float],
        limit: int = 10,
        filters: dict | None = None,
    ) -> list[SearchResult]:
        """Semantic search with optional filters.

        Args:
            query_embedding: Query vector embedding (1024 dimensions).
            limit: Maximum number of results to return.
            filters: Optional filters to apply (dict of field: value pairs).

        Returns:
            List of SearchResult objects ordered by similarity score.
        """
        # Build filter if provided
        query_filter = None
        if filters:
            conditions = [
                FieldCondition(key=key, match=MatchValue(value=value))
                for key, value in filters.items()
            ]
            query_filter = Filter(must=conditions)

        # Perform vector search
        search_results = self.client.search(
            collection_name=self.collection_name,
            query_vector=query_embedding,
            limit=limit,
            query_filter=query_filter,
        )

        # Convert to SearchResult objects
        return [
            SearchResult(
                extraction_id=UUID(result.id),
                score=result.score,
                payload=result.payload or {},
            )
            for result in search_results
        ]

    async def delete(self, extraction_id: UUID) -> bool:
        """Delete embedding (for re-extraction).

        Args:
            extraction_id: UUID of the extraction to delete.

        Returns:
            True if deletion succeeded (always returns True, idempotent).
        """
        point_id = str(extraction_id)

        # Delete point (idempotent - doesn't fail if point doesn't exist)
        self.client.delete(
            collection_name=self.collection_name,
            points_selector=[point_id],
        )

        return True
