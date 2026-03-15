"""Tests for ExtractionEmbeddingService."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from services.extraction.embedding_pipeline import (
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
        """Returns EmbeddingResult with 0 for empty list."""
        result = await embedding_svc.embed_and_upsert([])
        assert result.embedded_count == 0
        assert result.errors == []

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

        assert result.embedded_count == 1
        assert result.errors == []
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

        assert result.embedded_count == 0
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
        assert result.embedded_count == 0
        assert len(result.errors) == 1
