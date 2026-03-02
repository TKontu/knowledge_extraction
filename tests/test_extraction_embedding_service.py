"""Tests for ExtractionEmbeddingService."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from services.extraction.embedding_pipeline import (
    EmbeddingResult,
    ExtractionEmbeddingService,
)


@pytest.fixture
def mock_embedding_service():
    """Mock EmbeddingService."""
    return AsyncMock()


@pytest.fixture
def mock_qdrant_repo():
    """Mock QdrantRepository."""
    return AsyncMock()


@pytest.fixture
def embedding_svc(mock_embedding_service, mock_qdrant_repo):
    """Create ExtractionEmbeddingService with mocks."""
    return ExtractionEmbeddingService(mock_embedding_service, mock_qdrant_repo)


class TestExtractionToText:
    """Tests for extraction_to_text static method."""

    def test_includes_extraction_type(self):
        """Includes extraction_type in text."""
        extraction = Mock()
        extraction.extraction_type = "product_info"
        extraction.data = {}

        text = ExtractionEmbeddingService.extraction_to_text(extraction)
        assert "Type: product_info" in text

    def test_includes_data_fields(self):
        """Includes data key-value pairs."""
        extraction = Mock()
        extraction.extraction_type = None
        extraction.data = {"name": "Widget", "price": "$10"}

        text = ExtractionEmbeddingService.extraction_to_text(extraction)
        assert "name: Widget" in text
        assert "price: $10" in text

    def test_skips_internal_fields(self):
        """Skips fields starting with _ and 'confidence'."""
        extraction = Mock()
        extraction.extraction_type = None
        extraction.data = {
            "name": "Widget",
            "_quotes": ["src"],
            "confidence": 0.9,
        }

        text = ExtractionEmbeddingService.extraction_to_text(extraction)
        assert "name: Widget" in text
        assert "_quotes" not in text
        assert "confidence" not in text

    def test_handles_list_values(self):
        """Handles list values including dicts and primitives."""
        extraction = Mock()
        extraction.extraction_type = None
        extraction.data = {
            "items": [
                {"name": "A", "value": 1},
                "plain_item",
            ]
        }

        text = ExtractionEmbeddingService.extraction_to_text(extraction)
        assert "name: A" in text
        assert "plain_item" in text

    def test_empty_data_returns_empty_string(self):
        """Returns empty string for no data and no type."""
        extraction = Mock()
        extraction.extraction_type = None
        extraction.data = {}

        text = ExtractionEmbeddingService.extraction_to_text(extraction)
        assert text == ""

    def test_none_data_returns_type_only(self):
        """Returns only type when data is None."""
        extraction = Mock()
        extraction.extraction_type = "info"
        extraction.data = None

        text = ExtractionEmbeddingService.extraction_to_text(extraction)
        assert text == "Type: info"


class TestEmbedAndUpsert:
    """Tests for embed_and_upsert method."""

    async def test_empty_list_returns_zero(self, embedding_svc):
        """Returns 0 for empty list."""
        result = await embedding_svc.embed_and_upsert([])
        assert result == 0

    async def test_embeds_and_upserts_extractions(
        self, embedding_svc, mock_embedding_service, mock_qdrant_repo
    ):
        """Embeds extraction texts and upserts to Qdrant."""
        extraction = Mock()
        extraction.id = uuid4()
        extraction.project_id = uuid4()
        extraction.source_id = uuid4()
        extraction.source_group = "TestCo"
        extraction.extraction_type = "info"
        extraction.data = {"name": "Widget"}

        mock_embedding_service.embed_batch.return_value = [[0.1] * 768]

        result = await embedding_svc.embed_and_upsert([extraction])

        assert result == 1
        mock_embedding_service.embed_batch.assert_called_once()
        mock_qdrant_repo.upsert_batch.assert_called_once()

    async def test_skips_empty_text_extractions(
        self, embedding_svc, mock_embedding_service, mock_qdrant_repo
    ):
        """Skips extractions that produce empty text."""
        extraction = Mock()
        extraction.extraction_type = None
        extraction.data = {}

        result = await embedding_svc.embed_and_upsert([extraction])

        assert result == 0
        mock_embedding_service.embed_batch.assert_not_called()

    async def test_handles_embedding_failure(
        self, embedding_svc, mock_embedding_service
    ):
        """Returns 0 on embedding failure."""
        extraction = Mock()
        extraction.id = uuid4()
        extraction.extraction_type = "info"
        extraction.data = {"name": "Widget"}

        mock_embedding_service.embed_batch.side_effect = Exception("API error")

        result = await embedding_svc.embed_and_upsert([extraction])
        assert result == 0


class TestEmbedFacts:
    """Tests for embed_facts method."""

    async def test_empty_list_returns_empty_result(self, embedding_svc):
        """Returns EmbeddingResult with 0 for empty input."""
        result = await embedding_svc.embed_facts(
            fact_extractions=[],
            project_id=uuid4(),
            source_group="TestCo",
        )
        assert isinstance(result, EmbeddingResult)
        assert result.embedded_count == 0
        assert result.errors == []

    async def test_embeds_facts_and_upserts(
        self, embedding_svc, mock_embedding_service, mock_qdrant_repo
    ):
        """Embeds fact texts and upserts to Qdrant."""
        fact = Mock()
        fact.fact = "Widget has feature X"
        fact.category = "features"

        extraction = Mock()
        extraction.id = uuid4()

        mock_embedding_service.embed_batch.return_value = [[0.1] * 768]

        result = await embedding_svc.embed_facts(
            fact_extractions=[(fact, extraction)],
            project_id=uuid4(),
            source_group="TestCo",
        )

        assert result.embedded_count == 1
        assert result.errors == []
        mock_embedding_service.embed_batch.assert_called_once_with(
            ["Widget has feature X"]
        )
        mock_qdrant_repo.upsert_batch.assert_called_once()

    async def test_updates_embedding_ids_when_repo_provided(
        self, embedding_svc, mock_embedding_service
    ):
        """Updates extraction embedding IDs when extraction_repo is provided."""
        fact = Mock()
        fact.fact = "Test fact"
        fact.category = "general"

        extraction = Mock()
        extraction.id = uuid4()

        mock_embedding_service.embed_batch.return_value = [[0.1] * 768]
        mock_extraction_repo = Mock()

        result = await embedding_svc.embed_facts(
            fact_extractions=[(fact, extraction)],
            project_id=uuid4(),
            source_group="TestCo",
            extraction_repo=mock_extraction_repo,
        )

        assert result.embedded_count == 1
        mock_extraction_repo.update_embedding_ids_batch.assert_called_once_with(
            [extraction.id]
        )

    async def test_handles_embedding_failure(
        self, embedding_svc, mock_embedding_service
    ):
        """Returns errors on embedding failure."""
        fact = Mock()
        fact.fact = "Test fact"
        fact.category = "general"

        extraction = Mock()
        extraction.id = uuid4()

        mock_embedding_service.embed_batch.side_effect = Exception("API error")

        result = await embedding_svc.embed_facts(
            fact_extractions=[(fact, extraction)],
            project_id=uuid4(),
            source_group="TestCo",
        )

        assert result.embedded_count == 0
        assert len(result.errors) == 1
        assert "API error" in result.errors[0]

    async def test_multiple_facts_batched(
        self, embedding_svc, mock_embedding_service, mock_qdrant_repo
    ):
        """Multiple facts are batched in a single embed call."""
        facts_and_extractions = []
        for i in range(3):
            fact = Mock()
            fact.fact = f"Fact {i}"
            fact.category = f"cat{i}"
            extraction = Mock()
            extraction.id = uuid4()
            facts_and_extractions.append((fact, extraction))

        mock_embedding_service.embed_batch.return_value = [
            [0.1] * 768,
            [0.2] * 768,
            [0.3] * 768,
        ]

        result = await embedding_svc.embed_facts(
            fact_extractions=facts_and_extractions,
            project_id=uuid4(),
            source_group="TestCo",
        )

        assert result.embedded_count == 3
        mock_embedding_service.embed_batch.assert_called_once_with(
            ["Fact 0", "Fact 1", "Fact 2"]
        )
        mock_qdrant_repo.upsert_batch.assert_called_once()
