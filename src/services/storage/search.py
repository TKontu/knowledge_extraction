"""Search service for hybrid semantic + structured search."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from orm_models import Source
from services.storage.embedding import EmbeddingService
from services.storage.qdrant.repository import QdrantRepository
from services.storage.repositories.extraction import ExtractionRepository


@dataclass
class ExtractionSearchResult:
    """Result from semantic search with enriched data."""

    extraction_id: UUID
    score: float
    data: dict
    source_group: str
    source_uri: str
    confidence: float | None


class SearchService:
    """Combined semantic + structured search."""

    def __init__(
        self,
        embedding_service: EmbeddingService,
        qdrant_repo: QdrantRepository,
        extraction_repo: ExtractionRepository,
    ):
        """Initialize SearchService.

        Args:
            embedding_service: Service for generating embeddings.
            qdrant_repo: Repository for vector similarity search.
            extraction_repo: Repository for extraction CRUD and JSONB queries.
        """
        self.embedding = embedding_service
        self.qdrant = qdrant_repo
        self.extractions = extraction_repo

    async def search(
        self,
        project_id: UUID,
        query: str,
        limit: int = 10,
        source_groups: list[str] | None = None,
        jsonb_filters: dict[str, Any] | None = None,
    ) -> list[ExtractionSearchResult]:
        """Semantic search with optional structured filters.

        Args:
            project_id: Project UUID to scope the search.
            query: Natural language search query.
            limit: Maximum number of results to return.
            source_groups: Optional list of source groups to filter by.
            jsonb_filters: Optional JSONB filters to apply (e.g., {"category": "pricing"}).

        Returns:
            List of ExtractionSearchResult objects ordered by similarity score.
        """
        # Step 1: Generate query embedding
        query_embedding = await self.embedding.embed(query)

        # Step 2: Search Qdrant with over-fetching
        qdrant_filters = {"project_id": str(project_id)}
        if source_groups is not None:
            qdrant_filters["source_group"] = source_groups

        vector_results = await self.qdrant.search(
            query_embedding=query_embedding,
            limit=limit * 2,  # Over-fetch for post-filtering
            filters=qdrant_filters,
        )

        # Early exit if no results
        if not vector_results:
            return []

        # Step 3: Apply JSONB filters in PostgreSQL
        if jsonb_filters:
            # Get extractions matching JSONB filters
            matching_extractions = await self.extractions.filter_by_data(
                project_id=project_id,
                filters=jsonb_filters,
            )

            # Build set of valid extraction IDs
            valid_ids = {extraction.id for extraction in matching_extractions}

            # Filter vector results to only include valid IDs
            vector_results = [
                result for result in vector_results if result.extraction_id in valid_ids
            ]

            # Early exit if filtering eliminated all results
            if not vector_results:
                return []

        # Step 4: Enrich with full data and trim to limit
        enriched_results = []
        for result in vector_results[:limit]:  # Trim to final limit
            # Get full extraction data
            extraction = await self.extractions.get(result.extraction_id)
            if extraction is None:
                # Skip if extraction not found (defensive)
                continue

            # Get source URI
            source = await self._get_source(extraction.source_id)
            source_uri = source.uri if source else ""

            # Build enriched result
            enriched_results.append(
                ExtractionSearchResult(
                    extraction_id=result.extraction_id,
                    score=result.score,
                    data=extraction.data,
                    source_group=extraction.source_group,
                    source_uri=source_uri,
                    confidence=extraction.confidence,
                )
            )

        return enriched_results

    async def _get_source(self, source_id: UUID) -> Source | None:
        """Get source by ID.

        Args:
            source_id: Source UUID.

        Returns:
            Source instance or None if not found.
        """
        from sqlalchemy import select

        # Use extraction repository's session to query Source
        result = self.extractions._session.execute(
            select(Source).where(Source.id == source_id)
        )
        return result.scalar_one_or_none()
