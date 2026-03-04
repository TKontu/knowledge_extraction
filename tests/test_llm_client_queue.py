"""Tests for LLMClient with queue mode integration.

TDD: These tests define the expected behavior for LLMClient queue mode
and the extract_entities() method.
"""

from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestLLMClientExtractEntities:
    """Tests for LLMClient.extract_entities() method."""

    @pytest.fixture
    def llm_config(self):
        """Create LLMConfig for testing."""
        from config import LLMConfig
        return LLMConfig(
            base_url="http://localhost:9003/v1",
            embedding_base_url="http://localhost:9003/v1",
            api_key="test",
            model="test-model",
            embedding_model="bge-m3",
            embedding_dimension=1024,
            http_timeout=60,
            max_tokens=4096,
            max_retries=3,
            retry_backoff_min=1,
            retry_backoff_max=30,
            base_temperature=0.1,
            retry_temperature_increment=0.1,
        )

    @pytest.fixture
    def mock_queue(self):
        """Create mock LLM request queue."""
        queue = AsyncMock()
        queue.submit = AsyncMock(return_value="test-request-id")
        queue.wait_for_result = AsyncMock()
        return queue

    @pytest.mark.asyncio
    async def test_extract_entities_exists(self, llm_config):
        """Test that extract_entities method exists on LLMClient."""
        from services.llm.client import LLMClient

        client = LLMClient(llm_config)
        assert hasattr(client, "extract_entities")
        assert callable(client.extract_entities)

    @pytest.mark.asyncio
    async def test_extract_entities_via_queue(self, llm_config, mock_queue):
        """Test entity extraction through queue."""
        from services.llm.client import LLMClient
        from services.llm.models import LLMResponse

        mock_queue.wait_for_result.return_value = LLMResponse(
            request_id="test-id",
            status="success",
            result={
                "entities": [
                    {"type": "plan", "value": "Professional Plan", "normalized": "professional_plan", "attributes": {}},
                    {"type": "feature", "value": "API Access", "normalized": "api_access", "attributes": {}},
                ]
            },
            error=None,
            processing_time_ms=100,
            completed_at=datetime.now(UTC),
        )

        client = LLMClient(llm_config, llm_queue=mock_queue)

        extraction_data = {"fact_text": "Professional Plan includes API Access"}
        entity_types = [
            {"name": "plan", "description": "Pricing plan names"},
            {"name": "feature", "description": "Product features"},
        ]

        result = await client.extract_entities(
            extraction_data=extraction_data,
            entity_types=entity_types,
            source_group="TestCompany",
        )

        # Should have submitted to queue
        mock_queue.submit.assert_called_once()
        mock_queue.wait_for_result.assert_called_once()

        # Should return entities
        assert len(result) == 2
        assert result[0]["type"] == "plan"
        assert result[1]["type"] == "feature"

    @pytest.mark.asyncio
    async def test_extract_entities_request_type(self, llm_config, mock_queue):
        """Test that correct request type is submitted for entity extraction."""
        from services.llm.models import LLMRequest, LLMResponse

        mock_queue.wait_for_result.return_value = LLMResponse(
            request_id="test-id",
            status="success",
            result={"entities": []},
            error=None,
            processing_time_ms=100,
            completed_at=datetime.now(UTC),
        )

        from services.llm.client import LLMClient
        client = LLMClient(llm_config, llm_queue=mock_queue)

        await client.extract_entities(
            extraction_data={"fact_text": "Test"},
            entity_types=[{"name": "plan", "description": "Plans"}],
            source_group="TestCo",
        )

        call_args = mock_queue.submit.call_args
        submitted_request = call_args[0][0]

        assert isinstance(submitted_request, LLMRequest)
        assert submitted_request.request_type == "extract_entities"
        assert "extraction_data" in submitted_request.payload
        assert "entity_types" in submitted_request.payload
        assert "source_group" in submitted_request.payload
        # Prompts should be in payload
        assert "system_prompt" in submitted_request.payload
        assert "user_prompt" in submitted_request.payload

    @pytest.mark.asyncio
    async def test_extract_entities_prompts_in_payload(self, llm_config, mock_queue):
        """Test that entity extraction prompts are in payload."""
        from services.llm.models import LLMResponse

        mock_queue.wait_for_result.return_value = LLMResponse(
            request_id="test-id",
            status="success",
            result={"entities": []},
            error=None,
            processing_time_ms=100,
            completed_at=datetime.now(UTC),
        )

        from services.llm.client import LLMClient
        client = LLMClient(llm_config, llm_queue=mock_queue)

        await client.extract_entities(
            extraction_data={"fact_text": "Enterprise plan with 1000 API calls/month"},
            entity_types=[
                {"name": "plan", "description": "Pricing plan names"},
                {"name": "limit", "description": "Usage limits"},
            ],
            source_group="TestCompany",
        )

        call_args = mock_queue.submit.call_args
        submitted_request = call_args[0][0]

        # Verify prompts contain entity type info
        assert "plan" in submitted_request.payload["system_prompt"]
        assert "limit" in submitted_request.payload["system_prompt"]
        assert "TestCompany" in submitted_request.payload["system_prompt"]

    @pytest.mark.asyncio
    async def test_extract_entities_direct_mode(self, llm_config):
        """Test entity extraction in direct mode (no queue)."""
        from services.llm.client import LLMClient

        client = LLMClient(llm_config, llm_queue=None)

        # Mock the direct client
        client.client = MagicMock()
        client.client.chat.completions.create = AsyncMock(
            return_value=MagicMock(
                choices=[
                    MagicMock(
                        message=MagicMock(
                            content='{"entities": [{"type": "plan", "value": "Pro", "normalized": "pro", "attributes": {}}]}'
                        )
                    )
                ]
            )
        )

        result = await client.extract_entities(
            extraction_data={"fact_text": "Pro plan available"},
            entity_types=[{"name": "plan", "description": "Plans"}],
            source_group="TestCo",
        )

        client.client.chat.completions.create.assert_called_once()
        assert len(result) == 1
        assert result[0]["type"] == "plan"

    @pytest.mark.asyncio
    async def test_extract_entities_handles_error(self, llm_config, mock_queue):
        """Test that entity extraction handles errors."""
        from exceptions import LLMExtractionError
        from services.llm.client import LLMClient
        from services.llm.models import LLMResponse

        mock_queue.wait_for_result.return_value = LLMResponse(
            request_id="test-id",
            status="error",
            result=None,
            error="Entity extraction failed",
            processing_time_ms=100,
            completed_at=datetime.now(UTC),
        )

        client = LLMClient(llm_config, llm_queue=mock_queue)

        with pytest.raises(LLMExtractionError) as exc_info:
            await client.extract_entities(
                extraction_data={"fact_text": "Test"},
                entity_types=[{"name": "plan", "description": "Plans"}],
                source_group="TestCo",
            )

        assert "Entity extraction failed" in str(exc_info.value)


