"""Tests for EntityExtractor using LLMClient.extract_entities().

TDD: These tests define the expected behavior after refactoring EntityExtractor
to use the new LLMClient.extract_entities() method instead of direct LLM calls.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest


class TestEntityExtractorUsesLLMClient:
    """Tests that EntityExtractor properly delegates to LLMClient."""

    @pytest.fixture
    def mock_llm_client(self):
        """Create mock LLM client with extract_entities method."""
        client = MagicMock()
        client.extract_entities = AsyncMock(
            return_value=[
                {
                    "type": "plan",
                    "value": "Pro Plan",
                    "normalized": "pro_plan",
                    "attributes": {},
                },
                {
                    "type": "feature",
                    "value": "API Access",
                    "normalized": "api_access",
                    "attributes": {},
                },
            ]
        )
        return client

    @pytest.fixture
    def mock_entity_repo(self):
        """Create mock entity repository."""
        repo = AsyncMock()

        # Mock get_or_create to return entity and created flag
        async def mock_get_or_create(**kwargs):
            entity = MagicMock()
            entity.id = uuid4()
            entity.entity_type = kwargs["entity_type"]
            entity.value = kwargs["value"]
            entity.normalized_value = kwargs["normalized_value"]
            entity.attributes = kwargs["attributes"]
            return entity, True

        repo.get_or_create = mock_get_or_create
        # link_to_extraction returns (ExtractionEntity, bool) tuple
        mock_link = MagicMock()
        repo.link_to_extraction = AsyncMock(return_value=(mock_link, True))
        return repo

    @pytest.mark.asyncio
    async def test_extract_calls_llm_client_extract_entities(
        self, mock_llm_client, mock_entity_repo
    ):
        """Test that EntityExtractor.extract() calls llm_client.extract_entities()."""
        from services.knowledge.extractor import EntityExtractor

        extractor = EntityExtractor(
            llm_client=mock_llm_client,
            entity_repo=mock_entity_repo,
        )

        extraction_data = {"fact_text": "Pro Plan includes API Access"}
        entity_types = [
            {"name": "plan", "description": "Pricing plans"},
            {"name": "feature", "description": "Product features"},
        ]

        await extractor.extract(
            extraction_id=uuid4(),
            extraction_data=extraction_data,
            project_id=uuid4(),
            entity_types=entity_types,
            source_group="TestCompany",
        )

        # Should have called llm_client.extract_entities
        mock_llm_client.extract_entities.assert_called_once_with(
            extraction_data=extraction_data,
            entity_types=entity_types,
            source_group="TestCompany",
        )

    @pytest.mark.asyncio
    async def test_no_direct_llm_client_access(self, mock_llm_client, mock_entity_repo):
        """Test that EntityExtractor doesn't access llm_client.client directly."""
        from services.knowledge.extractor import EntityExtractor

        # Don't set client attribute - it shouldn't be accessed
        mock_llm_client.client = None

        extractor = EntityExtractor(
            llm_client=mock_llm_client,
            entity_repo=mock_entity_repo,
        )

        # Should work without accessing .client
        await extractor.extract(
            extraction_id=uuid4(),
            extraction_data={"fact_text": "Test"},
            project_id=uuid4(),
            entity_types=[{"name": "test", "description": "Test type"}],
            source_group="Test",
        )

        # Verify .client was never accessed (no attribute error means success)
        mock_llm_client.extract_entities.assert_called_once()

    @pytest.mark.asyncio
    async def test_stores_entities_from_llm_response(
        self, mock_llm_client, mock_entity_repo
    ):
        """Test that entities from LLMClient response are stored correctly."""
        from services.knowledge.extractor import EntityExtractor

        extractor = EntityExtractor(
            llm_client=mock_llm_client,
            entity_repo=mock_entity_repo,
        )

        project_id = uuid4()
        extraction_id = uuid4()

        entities = await extractor.extract(
            extraction_id=extraction_id,
            extraction_data={"fact_text": "Pro Plan includes API Access"},
            project_id=project_id,
            entity_types=[
                {"name": "plan", "description": "Plans"},
                {"name": "feature", "description": "Features"},
            ],
            source_group="TestCompany",
        )

        # Should have 2 entities stored
        assert len(entities) == 2

    @pytest.mark.asyncio
    async def test_links_entities_to_extraction(
        self, mock_llm_client, mock_entity_repo
    ):
        """Test that stored entities are linked to the extraction."""
        from services.knowledge.extractor import EntityExtractor

        extractor = EntityExtractor(
            llm_client=mock_llm_client,
            entity_repo=mock_entity_repo,
        )

        extraction_id = uuid4()

        await extractor.extract(
            extraction_id=extraction_id,
            extraction_data={"fact_text": "Pro Plan includes API Access"},
            project_id=uuid4(),
            entity_types=[{"name": "plan", "description": "Plans"}],
            source_group="TestCompany",
        )

        # Should have called link_to_extraction for each entity
        assert mock_entity_repo.link_to_extraction.call_count == 2  # 2 entities

    @pytest.mark.asyncio
    async def test_normalizes_entities(self, mock_llm_client, mock_entity_repo):
        """Test that EntityExtractor still normalizes entity values."""
        from services.knowledge.extractor import EntityExtractor

        # LLM returns entities without proper normalization
        mock_llm_client.extract_entities = AsyncMock(
            return_value=[
                {
                    "type": "limit",
                    "value": "10,000/min",
                    "normalized": "10,000/min",
                    "attributes": {},
                },
            ]
        )

        # Track what gets passed to get_or_create
        created_values = []

        async def track_get_or_create(**kwargs):
            created_values.append(kwargs)
            entity = MagicMock()
            entity.id = uuid4()
            entity.entity_type = kwargs["entity_type"]
            entity.value = kwargs["value"]
            entity.normalized_value = kwargs["normalized_value"]
            return entity, True

        mock_entity_repo.get_or_create = track_get_or_create

        extractor = EntityExtractor(
            llm_client=mock_llm_client,
            entity_repo=mock_entity_repo,
        )

        await extractor.extract(
            extraction_id=uuid4(),
            extraction_data={"fact_text": "Rate limit is 10,000/min"},
            project_id=uuid4(),
            entity_types=[{"name": "limit", "description": "Usage limits"}],
            source_group="TestCompany",
        )

        # Should have normalized the limit value
        assert len(created_values) == 1
        assert created_values[0]["entity_type"] == "limit"
        # The normalized value should be processed by _normalize method
        # Format: number_per_unit (e.g., "10000_per_minute")
        assert "_per_" in created_values[0]["normalized_value"]

    @pytest.mark.asyncio
    async def test_handles_empty_llm_response(self, mock_llm_client, mock_entity_repo):
        """Test that empty LLM response is handled gracefully."""
        from services.knowledge.extractor import EntityExtractor

        mock_llm_client.extract_entities = AsyncMock(return_value=[])

        extractor = EntityExtractor(
            llm_client=mock_llm_client,
            entity_repo=mock_entity_repo,
        )

        entities = await extractor.extract(
            extraction_id=uuid4(),
            extraction_data={"fact_text": "No entities here"},
            project_id=uuid4(),
            entity_types=[{"name": "plan", "description": "Plans"}],
            source_group="TestCompany",
        )

        assert entities == []


class TestEntityExtractorNormalization:
    """Tests for entity normalization logic (preserved from original)."""

    @pytest.fixture
    def mock_llm_client(self):
        client = MagicMock()
        client.extract_entities = AsyncMock(return_value=[])
        return client

    @pytest.fixture
    def mock_entity_repo(self):
        return AsyncMock()

    def test_normalize_limit_with_per_minute(self, mock_llm_client, mock_entity_repo):
        """Test normalization of rate limits."""
        from services.knowledge.extractor import EntityExtractor

        extractor = EntityExtractor(
            llm_client=mock_llm_client,
            entity_repo=mock_entity_repo,
        )

        result = extractor._normalize("limit", "10,000/min")
        assert result == "10000_per_minute"

    def test_normalize_limit_with_per_hour(self, mock_llm_client, mock_entity_repo):
        """Test normalization of hourly limits."""
        from services.knowledge.extractor import EntityExtractor

        extractor = EntityExtractor(
            llm_client=mock_llm_client,
            entity_repo=mock_entity_repo,
        )

        result = extractor._normalize("limit", "5000 per hr")
        assert result == "5000_per_hour"

    def test_normalize_pricing(self, mock_llm_client, mock_entity_repo):
        """Test normalization of pricing."""
        from services.knowledge.extractor import EntityExtractor

        extractor = EntityExtractor(
            llm_client=mock_llm_client,
            entity_repo=mock_entity_repo,
        )

        result = extractor._normalize("pricing", "$19.99/month")
        assert result == "1999_per_month"

    def test_normalize_plan_name(self, mock_llm_client, mock_entity_repo):
        """Test normalization of plan names."""
        from services.knowledge.extractor import EntityExtractor

        extractor = EntityExtractor(
            llm_client=mock_llm_client,
            entity_repo=mock_entity_repo,
        )

        result = extractor._normalize("plan", "Professional Plan")
        assert result == "professional plan"


class TestEntityExtractorWithQueueMode:
    """Tests verifying EntityExtractor works with queue mode LLMClient."""

    @pytest.fixture
    def mock_queue(self):
        """Create mock LLM request queue."""
        queue = AsyncMock()
        queue.submit = AsyncMock(return_value="test-request-id")
        queue.wait_for_result = AsyncMock()
        return queue

    @pytest.fixture
    def mock_settings(self):
        settings = MagicMock()
        settings.openai_base_url = "http://localhost:9003/v1"
        settings.openai_api_key = "test"
        settings.llm_http_timeout = 60
        settings.llm_model = "test-model"
        settings.llm_request_timeout = 300
        return settings

    @pytest.fixture
    def mock_entity_repo(self):
        repo = AsyncMock()

        async def mock_get_or_create(**kwargs):
            entity = MagicMock()
            entity.id = uuid4()
            entity.entity_type = kwargs["entity_type"]
            entity.value = kwargs["value"]
            entity.normalized_value = kwargs["normalized_value"]
            return entity, True

        repo.get_or_create = mock_get_or_create
        # link_to_extraction returns (ExtractionEntity, bool) tuple
        mock_link = MagicMock()
        repo.link_to_extraction = AsyncMock(return_value=(mock_link, True))
        return repo

    @pytest.mark.asyncio
    async def test_works_with_queue_mode_llm_client(
        self, mock_settings, mock_queue, mock_entity_repo
    ):
        """Test EntityExtractor works when LLMClient is in queue mode."""
        from services.knowledge.extractor import EntityExtractor
        from services.llm.client import LLMClient
        from src.services.llm.models import LLMResponse

        # Configure queue response
        mock_queue.wait_for_result.return_value = LLMResponse(
            request_id="test-id",
            status="success",
            result={
                "entities": [
                    {
                        "type": "plan",
                        "value": "Enterprise",
                        "normalized": "enterprise",
                        "attributes": {},
                    },
                ]
            },
            error=None,
            processing_time_ms=100,
            completed_at=datetime.now(UTC),
        )

        # Create LLMClient in queue mode
        llm_client = LLMClient(mock_settings, llm_queue=mock_queue)

        # Create EntityExtractor with queue-mode client
        extractor = EntityExtractor(
            llm_client=llm_client,
            entity_repo=mock_entity_repo,
        )

        entities = await extractor.extract(
            extraction_id=uuid4(),
            extraction_data={"fact_text": "Enterprise plan available"},
            project_id=uuid4(),
            entity_types=[{"name": "plan", "description": "Plans"}],
            source_group="TestCompany",
        )

        # Should have used the queue
        mock_queue.submit.assert_called_once()
        mock_queue.wait_for_result.assert_called_once()

        # Should have extracted entities
        assert len(entities) == 1
