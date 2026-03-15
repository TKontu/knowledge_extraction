"""Tests for SearchService."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from orm_models import Extraction, Source
from services.storage.qdrant.repository import SearchResult
from services.storage.search import ExtractionSearchResult, SearchService


@pytest.fixture
def mock_embedding_service():
    """Create mock EmbeddingService."""
    service = MagicMock()
    service.embed = AsyncMock(return_value=[0.1] * 1024)
    return service


@pytest.fixture
def mock_qdrant_repo():
    """Create mock QdrantRepository."""
    repo = MagicMock()
    repo.search = AsyncMock(return_value=[])
    return repo


@pytest.fixture
def mock_extraction_repo():
    """Create mock ExtractionRepository."""
    repo = MagicMock()
    repo.filter_by_data = AsyncMock(return_value=[])
    repo.get = AsyncMock(return_value=None)
    return repo


@pytest.fixture
def search_service(mock_embedding_service, mock_qdrant_repo, mock_extraction_repo):
    """Create SearchService instance with mocks."""
    return SearchService(
        embedding_service=mock_embedding_service,
        qdrant_repo=mock_qdrant_repo,
        extraction_repo=mock_extraction_repo,
    )


class TestSearchServiceInit:
    """Test SearchService initialization."""

    def test_initializes_with_dependencies(
        self, mock_embedding_service, mock_qdrant_repo, mock_extraction_repo
    ):
        """Should initialize with three service dependencies."""
        service = SearchService(
            embedding_service=mock_embedding_service,
            qdrant_repo=mock_qdrant_repo,
            extraction_repo=mock_extraction_repo,
        )

        assert service.embedding == mock_embedding_service
        assert service.qdrant == mock_qdrant_repo
        assert service.extractions == mock_extraction_repo


class TestSearchServiceSearch:
    """Test SearchService.search() method."""

    async def test_search_generates_query_embedding(
        self, search_service, mock_embedding_service
    ):
        """Should call embedding service to embed the query."""
        project_id = uuid4()

        await search_service.search(project_id=project_id, query="test query", limit=10)

        mock_embedding_service.embed.assert_called_once_with("test query")

    async def test_search_over_fetches_from_qdrant(
        self, search_service, mock_qdrant_repo, mock_embedding_service
    ):
        """Should fetch limit * 2 results from Qdrant for over-fetching."""
        project_id = uuid4()
        mock_embedding_service.embed.return_value = [0.5] * 1024

        await search_service.search(project_id=project_id, query="test", limit=10)

        # Should request 20 results (limit * 2)
        mock_qdrant_repo.search.assert_called_once()
        call_kwargs = mock_qdrant_repo.search.call_args.kwargs
        assert call_kwargs["limit"] == 20

    async def test_search_applies_project_id_filter_to_qdrant(
        self, search_service, mock_qdrant_repo, mock_embedding_service
    ):
        """Should always filter by project_id in Qdrant."""
        project_id = uuid4()
        mock_embedding_service.embed.return_value = [0.5] * 1024

        await search_service.search(project_id=project_id, query="test", limit=10)

        call_kwargs = mock_qdrant_repo.search.call_args.kwargs
        assert call_kwargs["filters"]["project_id"] == str(project_id)

    async def test_search_applies_source_groups_filter_to_qdrant(
        self, search_service, mock_qdrant_repo, mock_embedding_service
    ):
        """Should apply source_groups filter to Qdrant when provided."""
        project_id = uuid4()
        mock_embedding_service.embed.return_value = [0.5] * 1024
        source_groups = ["company_a", "company_b"]

        await search_service.search(
            project_id=project_id,
            query="test",
            limit=10,
            source_groups=source_groups,
        )

        call_kwargs = mock_qdrant_repo.search.call_args.kwargs
        assert call_kwargs["filters"]["source_group"] == source_groups

    async def test_search_without_source_groups_filter(
        self, search_service, mock_qdrant_repo, mock_embedding_service
    ):
        """Should not include source_group filter when not provided."""
        project_id = uuid4()
        mock_embedding_service.embed.return_value = [0.5] * 1024

        await search_service.search(project_id=project_id, query="test", limit=10)

        call_kwargs = mock_qdrant_repo.search.call_args.kwargs
        assert "source_group" not in call_kwargs["filters"]

    async def test_search_applies_jsonb_filters_via_extraction_repo(
        self,
        search_service,
        mock_qdrant_repo,
        mock_extraction_repo,
        mock_embedding_service,
    ):
        """Should use ExtractionRepository.filter_by_data for JSONB filtering."""
        project_id = uuid4()
        extraction_id1 = uuid4()
        extraction_id2 = uuid4()

        # Mock Qdrant results
        mock_qdrant_repo.search.return_value = [
            SearchResult(extraction_id=extraction_id1, score=0.9, payload={}),
            SearchResult(extraction_id=extraction_id2, score=0.8, payload={}),
        ]

        # Mock JSONB filter results - only extraction_id1 matches
        mock_extraction = MagicMock(spec=Extraction)
        mock_extraction.id = extraction_id1
        mock_extraction_repo.filter_by_data.return_value = [mock_extraction]

        jsonb_filters = {"category": "pricing", "verified": True}

        await search_service.search(
            project_id=project_id,
            query="test",
            limit=10,
            jsonb_filters=jsonb_filters,
        )

        # Should call filter_by_data with correct arguments
        mock_extraction_repo.filter_by_data.assert_called_once_with(
            project_id=project_id,
            filters=jsonb_filters,
        )

    async def test_search_filters_qdrant_results_by_jsonb_matches(
        self,
        search_service,
        mock_qdrant_repo,
        mock_extraction_repo,
        mock_embedding_service,
    ):
        """Should filter Qdrant results to only include JSONB-matching extractions."""
        project_id = uuid4()
        extraction_id1 = uuid4()
        extraction_id2 = uuid4()
        extraction_id3 = uuid4()

        # Mock Qdrant results - 3 results
        mock_qdrant_repo.search.return_value = [
            SearchResult(extraction_id=extraction_id1, score=0.9, payload={}),
            SearchResult(extraction_id=extraction_id2, score=0.8, payload={}),
            SearchResult(extraction_id=extraction_id3, score=0.7, payload={}),
        ]

        # Mock JSONB filter results - only extraction_id1 and extraction_id3 match
        mock_ext1 = MagicMock(spec=Extraction)
        mock_ext1.id = extraction_id1
        mock_ext1.data = {"fact": "test1"}
        mock_ext1.confidence = 0.9
        mock_ext1.source_group = "company_a"
        mock_ext1.source_id = uuid4()

        mock_ext3 = MagicMock(spec=Extraction)
        mock_ext3.id = extraction_id3
        mock_ext3.data = {"fact": "test3"}
        mock_ext3.confidence = 0.7
        mock_ext3.source_group = "company_b"
        mock_ext3.source_id = uuid4()

        mock_extraction_repo.filter_by_data.return_value = [mock_ext1, mock_ext3]

        # Mock get() to return full extraction data
        async def mock_get(extraction_id):
            if extraction_id == extraction_id1:
                return mock_ext1
            elif extraction_id == extraction_id3:
                return mock_ext3
            return None

        mock_extraction_repo.get = AsyncMock(side_effect=mock_get)

        # Mock source lookups
        mock_source1 = MagicMock(spec=Source)
        mock_source1.uri = "https://example.com/1"
        mock_source3 = MagicMock(spec=Source)
        mock_source3.uri = "https://example.com/3"

        search_service._get_source = AsyncMock(side_effect=[mock_source1, mock_source3])

        results = await search_service.search(
            project_id=project_id,
            query="test",
            limit=10,
            jsonb_filters={"category": "pricing"},
        )

        # Should only return 2 results (extraction_id1 and extraction_id3)
        assert len(results) == 2
        assert results[0].extraction_id == extraction_id1
        assert results[0].score == 0.9
        assert results[1].extraction_id == extraction_id3
        assert results[1].score == 0.7

    async def test_search_trims_results_to_limit(
        self,
        search_service,
        mock_qdrant_repo,
        mock_extraction_repo,
        mock_embedding_service,
    ):
        """Should return at most 'limit' results even if more match."""
        project_id = uuid4()

        # Create 5 mock extractions
        mock_extractions = []
        mock_qdrant_results = []
        for i in range(5):
            ext_id = uuid4()
            mock_ext = MagicMock(spec=Extraction)
            mock_ext.id = ext_id
            mock_ext.data = {"fact": f"test{i}"}
            mock_ext.confidence = 0.9 - (i * 0.1)
            mock_ext.source_group = "company"
            mock_ext.source_id = uuid4()
            mock_extractions.append(mock_ext)

            mock_qdrant_results.append(
                SearchResult(extraction_id=ext_id, score=0.9 - (i * 0.1), payload={})
            )

        mock_qdrant_repo.search.return_value = mock_qdrant_results

        # Mock get() to return extractions
        async def mock_get(extraction_id):
            for ext in mock_extractions:
                if ext.id == extraction_id:
                    return ext
            return None

        mock_extraction_repo.get = AsyncMock(side_effect=mock_get)

        # Mock source lookups
        mock_source = MagicMock(spec=Source)
        mock_source.uri = "https://example.com"
        search_service._get_source = AsyncMock(return_value=mock_source)

        # Request limit of 3
        results = await search_service.search(
            project_id=project_id,
            query="test",
            limit=3,
        )

        # Should only return 3 results
        assert len(results) == 3

    async def test_search_returns_empty_list_when_no_qdrant_results(
        self, search_service, mock_qdrant_repo
    ):
        """Should return empty list when Qdrant returns no results."""
        project_id = uuid4()
        mock_qdrant_repo.search.return_value = []

        results = await search_service.search(
            project_id=project_id,
            query="test",
            limit=10,
        )

        assert results == []

    async def test_search_returns_empty_list_when_jsonb_filters_eliminate_all(
        self, search_service, mock_qdrant_repo, mock_extraction_repo
    ):
        """Should return empty list when JSONB filters eliminate all Qdrant results."""
        project_id = uuid4()
        extraction_id1 = uuid4()

        # Qdrant has results
        mock_qdrant_repo.search.return_value = [
            SearchResult(extraction_id=extraction_id1, score=0.9, payload={})
        ]

        # But JSONB filtering returns nothing
        mock_extraction_repo.filter_by_data.return_value = []

        results = await search_service.search(
            project_id=project_id,
            query="test",
            limit=10,
            jsonb_filters={"category": "nonexistent"},
        )

        assert results == []

    async def test_search_enriches_with_full_extraction_data(
        self, search_service, mock_qdrant_repo, mock_extraction_repo
    ):
        """Should enrich results with full Extraction data from database."""
        project_id = uuid4()
        extraction_id = uuid4()
        source_id = uuid4()

        # Mock Qdrant result
        mock_qdrant_repo.search.return_value = [
            SearchResult(extraction_id=extraction_id, score=0.95, payload={})
        ]

        # Mock full extraction data
        mock_extraction = MagicMock(spec=Extraction)
        mock_extraction.id = extraction_id
        mock_extraction.data = {"fact_text": "Test fact", "category": "feature"}
        mock_extraction.confidence = 0.88
        mock_extraction.source_group = "acme_corp"
        mock_extraction.source_id = source_id

        mock_extraction_repo.get = AsyncMock(return_value=mock_extraction)

        # Mock source
        mock_source = MagicMock(spec=Source)
        mock_source.uri = "https://acme.com/docs"
        search_service._get_source = AsyncMock(return_value=mock_source)

        results = await search_service.search(
            project_id=project_id,
            query="test",
            limit=10,
        )

        assert len(results) == 1
        result = results[0]
        assert isinstance(result, ExtractionSearchResult)
        assert result.extraction_id == extraction_id
        assert result.score == 0.95
        assert result.data == {"fact_text": "Test fact", "category": "feature"}
        assert result.confidence == 0.88
        assert result.source_group == "acme_corp"
        assert result.source_uri == "https://acme.com/docs"

    async def test_search_handles_missing_source_gracefully(
        self, search_service, mock_qdrant_repo, mock_extraction_repo
    ):
        """Should use empty string for source_uri when source is missing."""
        project_id = uuid4()
        extraction_id = uuid4()

        # Mock Qdrant result
        mock_qdrant_repo.search.return_value = [
            SearchResult(extraction_id=extraction_id, score=0.9, payload={})
        ]

        # Mock extraction
        mock_extraction = MagicMock(spec=Extraction)
        mock_extraction.id = extraction_id
        mock_extraction.data = {"fact": "test"}
        mock_extraction.confidence = 0.9
        mock_extraction.source_group = "company"
        mock_extraction.source_id = uuid4()

        mock_extraction_repo.get = AsyncMock(return_value=mock_extraction)

        # Source lookup returns None
        search_service._get_source = AsyncMock(return_value=None)

        results = await search_service.search(
            project_id=project_id,
            query="test",
            limit=10,
        )

        assert len(results) == 1
        assert results[0].source_uri == ""

    async def test_search_maintains_score_ordering(
        self, search_service, mock_qdrant_repo, mock_extraction_repo
    ):
        """Should maintain Qdrant score ordering in final results."""
        project_id = uuid4()

        # Create extractions with specific scores
        extractions_data = [
            (uuid4(), 0.95),
            (uuid4(), 0.87),
            (uuid4(), 0.75),
        ]

        mock_qdrant_results = [
            SearchResult(extraction_id=ext_id, score=score, payload={})
            for ext_id, score in extractions_data
        ]
        mock_qdrant_repo.search.return_value = mock_qdrant_results

        # Mock extractions
        async def mock_get(extraction_id):
            mock_ext = MagicMock(spec=Extraction)
            mock_ext.id = extraction_id
            mock_ext.data = {"fact": "test"}
            mock_ext.confidence = 0.9
            mock_ext.source_group = "company"
            mock_ext.source_id = uuid4()
            return mock_ext

        mock_extraction_repo.get = AsyncMock(side_effect=mock_get)

        # Mock source
        mock_source = MagicMock(spec=Source)
        mock_source.uri = "https://example.com"
        search_service._get_source = AsyncMock(return_value=mock_source)

        results = await search_service.search(
            project_id=project_id,
            query="test",
            limit=10,
        )

        # Verify ordering maintained
        assert len(results) == 3
        assert results[0].score == 0.95
        assert results[1].score == 0.87
        assert results[2].score == 0.75
