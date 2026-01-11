"""Deduplication service for extractions using embedding similarity."""

import json
from dataclasses import dataclass
from uuid import UUID

from services.storage.embedding import EmbeddingService
from services.storage.qdrant.repository import QdrantRepository


@dataclass
class DeduplicationResult:
    """Result of deduplication check."""

    is_duplicate: bool
    similar_extraction_id: UUID | None = None
    similarity_score: float | None = None


class ExtractionDeduplicator:
    """Checks for duplicate extractions using embedding similarity."""

    DEFAULT_THRESHOLD = 0.90

    def __init__(
        self,
        embedding_service: EmbeddingService,
        qdrant_repo: QdrantRepository,
        threshold: float = DEFAULT_THRESHOLD,
    ):
        """Initialize deduplicator.

        Args:
            embedding_service: Service for generating embeddings.
            qdrant_repo: Repository for vector search.
            threshold: Similarity threshold for duplicate detection (default 0.90).
        """
        self._embedding_service = embedding_service
        self._qdrant_repo = qdrant_repo
        self._threshold = threshold

    async def check_duplicate(
        self,
        project_id: UUID,
        source_group: str,
        text_content: str,
    ) -> DeduplicationResult:
        """Check if similar extraction already exists.

        Args:
            project_id: Project ID to scope search.
            source_group: Source group to scope search.
            text_content: Text content to check for duplicates.

        Returns:
            DeduplicationResult indicating if duplicate exists.
        """
        # Generate embedding for the input text
        embedding = await self._embedding_service.embed(text_content)

        # Search for similar vectors with filters
        results = await self._qdrant_repo.search(
            query_embedding=embedding,
            limit=1,  # Only need best match
            filters={
                "project_id": str(project_id),
                "source_group": source_group,
            },
        )

        # Check if best match meets threshold
        if results and results[0].score >= self._threshold:
            return DeduplicationResult(
                is_duplicate=True,
                similar_extraction_id=results[0].extraction_id,
                similarity_score=results[0].score,
            )

        # No duplicate found
        return DeduplicationResult(
            is_duplicate=False,
            similar_extraction_id=None,
            similarity_score=None,
        )

    async def get_text_from_extraction_data(self, data: dict) -> str:
        """Extract text content from extraction data dict.

        Args:
            data: Extraction data dictionary.

        Returns:
            Text content suitable for embedding.
        """
        # Check common fields in priority order
        for field in ["fact_text", "text", "content", "summary"]:
            if field in data:
                return data[field]

        # Fall back to JSON serialization
        return json.dumps(data)

    async def check_extraction_data(
        self,
        project_id: UUID,
        source_group: str,
        extraction_data: dict,
    ) -> DeduplicationResult:
        """Check if extraction data is a duplicate.

        Convenience method that extracts text from data and checks for duplicates.

        Args:
            project_id: Project ID to scope search.
            source_group: Source group to scope search.
            extraction_data: Extraction data dictionary.

        Returns:
            DeduplicationResult indicating if duplicate exists.
        """
        # Extract text from data
        text_content = await self.get_text_from_extraction_data(extraction_data)

        # Delegate to check_duplicate
        return await self.check_duplicate(
            project_id=project_id,
            source_group=source_group,
            text_content=text_content,
        )
