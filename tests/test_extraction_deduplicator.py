"""Tests for ExtractionDeduplicator."""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from src.services.storage.deduplication import (
    ExtractionDeduplicator,
)


class TestExtractionDeduplicatorInit:
    """Tests for ExtractionDeduplicator initialization."""

    def test_init_with_default_threshold(self) -> None:
        """Test initialization with default threshold."""
        embedding_service = MagicMock()
        qdrant_repo = MagicMock()

        deduplicator = ExtractionDeduplicator(
            embedding_service=embedding_service,
            qdrant_repo=qdrant_repo,
        )

        assert deduplicator._threshold == 0.90
        assert deduplicator._embedding_service == embedding_service
        assert deduplicator._qdrant_repo == qdrant_repo

    def test_init_with_custom_threshold(self) -> None:
        """Test initialization with custom threshold."""
        embedding_service = MagicMock()
        qdrant_repo = MagicMock()

        deduplicator = ExtractionDeduplicator(
            embedding_service=embedding_service,
            qdrant_repo=qdrant_repo,
            threshold=0.85,
        )

        assert deduplicator._threshold == 0.85


class TestCheckDuplicate:
    """Tests for check_duplicate() method."""

    @pytest.fixture
    def embedding_service(self) -> MagicMock:
        """Create mock embedding service."""
        service = MagicMock()
        service.embed = AsyncMock(return_value=[0.1] * 1024)
        return service

    @pytest.fixture
    def qdrant_repo(self) -> MagicMock:
        """Create mock qdrant repository."""
        return MagicMock()

    @pytest.fixture
    def deduplicator(
        self, embedding_service: MagicMock, qdrant_repo: MagicMock
    ) -> ExtractionDeduplicator:
        """Create deduplicator instance."""
        return ExtractionDeduplicator(
            embedding_service=embedding_service,
            qdrant_repo=qdrant_repo,
        )

    async def test_check_duplicate_finds_similar(
        self,
        deduplicator: ExtractionDeduplicator,
        embedding_service: MagicMock,
        qdrant_repo: MagicMock,
    ) -> None:
        """Test that similar extraction is detected as duplicate."""
        from src.services.storage.qdrant.repository import SearchResult

        project_id = uuid4()
        source_group = "test-group"
        text_content = "Paris is the capital of France"
        similar_id = uuid4()

        # Mock search to return high similarity match
        search_result = SearchResult(
            extraction_id=similar_id,
            score=0.95,
            payload={},
        )
        qdrant_repo.search = AsyncMock(return_value=[search_result])

        result = await deduplicator.check_duplicate(
            project_id=project_id,
            source_group=source_group,
            text_content=text_content,
        )

        # Verify embedding was generated
        embedding_service.embed.assert_called_once_with(text_content)

        # Verify search was called with correct filters
        qdrant_repo.search.assert_called_once()
        call_args = qdrant_repo.search.call_args
        assert call_args.kwargs["limit"] == 1
        assert call_args.kwargs["filters"]["project_id"] == str(project_id)
        assert call_args.kwargs["filters"]["source_group"] == source_group

        # Verify result
        assert result.is_duplicate is True
        assert result.similar_extraction_id == similar_id
        assert result.similarity_score == 0.95

    async def test_check_duplicate_no_match(
        self,
        deduplicator: ExtractionDeduplicator,
        qdrant_repo: MagicMock,
    ) -> None:
        """Test when no similar extraction exists."""
        project_id = uuid4()
        source_group = "test-group"
        text_content = "Unique fact content"

        # Mock search to return empty results
        qdrant_repo.search = AsyncMock(return_value=[])

        result = await deduplicator.check_duplicate(
            project_id=project_id,
            source_group=source_group,
            text_content=text_content,
        )

        assert result.is_duplicate is False
        assert result.similar_extraction_id is None
        assert result.similarity_score is None

    async def test_check_duplicate_below_threshold(
        self,
        deduplicator: ExtractionDeduplicator,
        qdrant_repo: MagicMock,
    ) -> None:
        """Test similarity below threshold is not marked as duplicate."""
        from src.services.storage.qdrant.repository import SearchResult

        project_id = uuid4()
        source_group = "test-group"
        text_content = "Similar but not identical"

        # Mock search to return below-threshold match
        search_result = SearchResult(
            extraction_id=uuid4(),
            score=0.89,  # Below 0.90 threshold
            payload={},
        )
        qdrant_repo.search = AsyncMock(return_value=[search_result])

        result = await deduplicator.check_duplicate(
            project_id=project_id,
            source_group=source_group,
            text_content=text_content,
        )

        assert result.is_duplicate is False
        assert result.similar_extraction_id is None
        assert result.similarity_score is None

    async def test_check_duplicate_at_threshold(
        self,
        deduplicator: ExtractionDeduplicator,
        qdrant_repo: MagicMock,
    ) -> None:
        """Test similarity exactly at threshold is marked as duplicate."""
        from src.services.storage.qdrant.repository import SearchResult

        project_id = uuid4()
        source_group = "test-group"
        text_content = "Exact threshold match"
        similar_id = uuid4()

        # Mock search to return exact threshold match
        search_result = SearchResult(
            extraction_id=similar_id,
            score=0.90,  # Exactly at threshold
            payload={},
        )
        qdrant_repo.search = AsyncMock(return_value=[search_result])

        result = await deduplicator.check_duplicate(
            project_id=project_id,
            source_group=source_group,
            text_content=text_content,
        )

        assert result.is_duplicate is True
        assert result.similar_extraction_id == similar_id
        assert result.similarity_score == 0.90

    async def test_check_duplicate_scoped_to_project(
        self,
        deduplicator: ExtractionDeduplicator,
        qdrant_repo: MagicMock,
    ) -> None:
        """Test deduplication is scoped to project_id."""
        project_id = uuid4()
        source_group = "test-group"
        text_content = "Project-scoped fact"

        qdrant_repo.search = AsyncMock(return_value=[])

        await deduplicator.check_duplicate(
            project_id=project_id,
            source_group=source_group,
            text_content=text_content,
        )

        # Verify project_id filter was applied
        call_args = qdrant_repo.search.call_args
        assert call_args.kwargs["filters"]["project_id"] == str(project_id)

    async def test_check_duplicate_scoped_to_source_group(
        self,
        deduplicator: ExtractionDeduplicator,
        qdrant_repo: MagicMock,
    ) -> None:
        """Test deduplication is scoped to source_group."""
        project_id = uuid4()
        source_group = "group-a"
        text_content = "Group-scoped fact"

        qdrant_repo.search = AsyncMock(return_value=[])

        await deduplicator.check_duplicate(
            project_id=project_id,
            source_group=source_group,
            text_content=text_content,
        )

        # Verify source_group filter was applied
        call_args = qdrant_repo.search.call_args
        assert call_args.kwargs["filters"]["source_group"] == source_group

    async def test_check_duplicate_returns_best_match(
        self,
        deduplicator: ExtractionDeduplicator,
        qdrant_repo: MagicMock,
    ) -> None:
        """Test that only the best match is returned."""
        from src.services.storage.qdrant.repository import SearchResult

        project_id = uuid4()
        source_group = "test-group"
        text_content = "Multiple matches"
        best_match_id = uuid4()

        # Mock search to return multiple results (Qdrant returns sorted by score)
        search_results = [
            SearchResult(extraction_id=best_match_id, score=0.95, payload={}),
            SearchResult(extraction_id=uuid4(), score=0.92, payload={}),
        ]
        qdrant_repo.search = AsyncMock(return_value=search_results)

        result = await deduplicator.check_duplicate(
            project_id=project_id,
            source_group=source_group,
            text_content=text_content,
        )

        # Verify we only asked for limit=1
        call_args = qdrant_repo.search.call_args
        assert call_args.kwargs["limit"] == 1

        # Verify result contains the best match
        assert result.is_duplicate is True
        assert result.similar_extraction_id == best_match_id
        assert result.similarity_score == 0.95


class TestGetTextFromExtractionData:
    """Tests for get_text_from_extraction_data() method."""

    @pytest.fixture
    def deduplicator(self) -> ExtractionDeduplicator:
        """Create deduplicator instance."""
        return ExtractionDeduplicator(
            embedding_service=MagicMock(),
            qdrant_repo=MagicMock(),
        )

    async def test_get_text_from_fact_text_field(
        self, deduplicator: ExtractionDeduplicator
    ) -> None:
        """Test extraction uses fact_text field first."""
        data = {
            "fact_text": "This is the fact",
            "text": "Alternate text",
            "content": "Other content",
        }

        result = await deduplicator.get_text_from_extraction_data(data)

        assert result == "This is the fact"

    async def test_get_text_from_text_field(
        self, deduplicator: ExtractionDeduplicator
    ) -> None:
        """Test falls back to text field."""
        data = {
            "text": "This is the text",
            "content": "Other content",
        }

        result = await deduplicator.get_text_from_extraction_data(data)

        assert result == "This is the text"

    async def test_get_text_from_content_field(
        self, deduplicator: ExtractionDeduplicator
    ) -> None:
        """Test falls back to content field."""
        data = {
            "content": "This is the content",
            "summary": "Summary text",
        }

        result = await deduplicator.get_text_from_extraction_data(data)

        assert result == "This is the content"

    async def test_get_text_serializes_dict(
        self, deduplicator: ExtractionDeduplicator
    ) -> None:
        """Test falls back to JSON serialization."""
        data = {
            "entity": "Paris",
            "type": "City",
            "country": "France",
        }

        result = await deduplicator.get_text_from_extraction_data(data)

        # Result should be JSON string
        import json

        parsed = json.loads(result)
        assert parsed == data


class TestCheckExtractionData:
    """Tests for check_extraction_data() convenience method."""

    @pytest.fixture
    def embedding_service(self) -> MagicMock:
        """Create mock embedding service."""
        service = MagicMock()
        service.embed = AsyncMock(return_value=[0.1] * 1024)
        return service

    @pytest.fixture
    def qdrant_repo(self) -> MagicMock:
        """Create mock qdrant repository."""
        return MagicMock()

    @pytest.fixture
    def deduplicator(
        self, embedding_service: MagicMock, qdrant_repo: MagicMock
    ) -> ExtractionDeduplicator:
        """Create deduplicator instance."""
        return ExtractionDeduplicator(
            embedding_service=embedding_service,
            qdrant_repo=qdrant_repo,
        )

    async def test_check_extraction_data_extracts_text(
        self,
        deduplicator: ExtractionDeduplicator,
        qdrant_repo: MagicMock,
        embedding_service: MagicMock,
    ) -> None:
        """Test that text is extracted from extraction data."""
        project_id = uuid4()
        source_group = "test-group"
        extraction_data = {"fact_text": "Paris is the capital of France"}

        qdrant_repo.search = AsyncMock(return_value=[])

        await deduplicator.check_extraction_data(
            project_id=project_id,
            source_group=source_group,
            extraction_data=extraction_data,
        )

        # Verify embed was called with extracted text
        embedding_service.embed.assert_called_once_with(
            "Paris is the capital of France"
        )

    async def test_check_extraction_data_delegates_to_check_duplicate(
        self,
        deduplicator: ExtractionDeduplicator,
        qdrant_repo: MagicMock,
    ) -> None:
        """Test that check_extraction_data uses check_duplicate internally."""
        from src.services.storage.qdrant.repository import SearchResult

        project_id = uuid4()
        source_group = "test-group"
        extraction_data = {"text": "Some fact"}
        similar_id = uuid4()

        # Mock search result
        search_result = SearchResult(extraction_id=similar_id, score=0.95, payload={})
        qdrant_repo.search = AsyncMock(return_value=[search_result])

        result = await deduplicator.check_extraction_data(
            project_id=project_id,
            source_group=source_group,
            extraction_data=extraction_data,
        )

        # Verify it returns the same result as check_duplicate would
        assert result.is_duplicate is True
        assert result.similar_extraction_id == similar_id
        assert result.similarity_score == 0.95
