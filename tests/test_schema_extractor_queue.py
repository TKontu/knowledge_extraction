"""Tests for SchemaExtractor with LLM queue integration.

TDD: Tests for queue-based extraction mode.
"""

import asyncio
from datetime import datetime, timedelta, UTC
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from services.extraction.field_groups import MANUFACTURING_GROUP, PRODUCTS_GEARBOX_GROUP


class TestSchemaExtractorQueueMode:
    """Tests for SchemaExtractor using LLM request queue."""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings."""
        settings = MagicMock()
        settings.openai_base_url = "http://localhost:9003/v1"
        settings.openai_api_key = "test"
        settings.llm_http_timeout = 60
        settings.llm_model = "test-model"
        settings.llm_request_timeout = 300
        # Retry settings
        settings.llm_max_retries = 3
        settings.llm_base_temperature = 0.1
        settings.llm_retry_temperature_increment = 0.05
        settings.llm_retry_backoff_min = 2
        settings.llm_retry_backoff_max = 30
        settings.llm_max_tokens = 4096
        return settings

    @pytest.fixture
    def mock_queue(self):
        """Create mock LLM request queue."""
        queue = AsyncMock()
        queue.submit = AsyncMock(return_value="test-request-id")
        queue.wait_for_result = AsyncMock()
        return queue

    @pytest.mark.asyncio
    async def test_uses_queue_when_provided(self, mock_settings, mock_queue):
        """Test that extractor uses queue when provided."""
        from services.llm.models import LLMResponse
        from services.extraction.schema_extractor import SchemaExtractor

        # Mock successful response from queue
        mock_queue.wait_for_result.return_value = LLMResponse(
            request_id="test-request-id",
            status="success",
            result={"manufactures_gearboxes": True, "manufactures_motors": False},
            error=None,
            processing_time_ms=100,
            completed_at=datetime.now(UTC),
        )

        extractor = SchemaExtractor(mock_settings, llm_queue=mock_queue)

        result = await extractor.extract_field_group(
            content="We manufacture planetary gearboxes.",
            field_group=MANUFACTURING_GROUP,
            company_name="Test Company",
        )

        # Should have submitted to queue
        mock_queue.submit.assert_called_once()
        mock_queue.wait_for_result.assert_called_once()

        # Should return the result
        assert result["manufactures_gearboxes"] is True
        assert result["manufactures_motors"] is False

    @pytest.mark.asyncio
    async def test_submits_correct_request_type(self, mock_settings, mock_queue):
        """Test that correct request type is submitted."""
        from services.llm.models import LLMRequest, LLMResponse

        mock_queue.wait_for_result.return_value = LLMResponse(
            request_id="test-id",
            status="success",
            result={},
            error=None,
            processing_time_ms=100,
            completed_at=datetime.now(UTC),
        )

        from services.extraction.schema_extractor import SchemaExtractor
        extractor = SchemaExtractor(mock_settings, llm_queue=mock_queue)

        await extractor.extract_field_group(
            content="Test content",
            field_group=MANUFACTURING_GROUP,
            company_name="Test Co",
        )

        # Check the submitted request
        call_args = mock_queue.submit.call_args
        submitted_request = call_args[0][0]

        assert isinstance(submitted_request, LLMRequest)
        assert submitted_request.request_type == "extract_field_group"
        assert "content" in submitted_request.payload
        assert "field_group" in submitted_request.payload
        assert "source_context" in submitted_request.payload

    @pytest.mark.asyncio
    async def test_queue_payload_includes_prompts(self, mock_settings, mock_queue):
        """Test that queue payload includes system_prompt and user_prompt."""
        from services.llm.models import LLMRequest, LLMResponse

        mock_queue.wait_for_result.return_value = LLMResponse(
            request_id="test-id",
            status="success",
            result={"manufactures_gearboxes": True},
            error=None,
            processing_time_ms=100,
            completed_at=datetime.now(UTC),
        )

        from services.extraction.schema_extractor import SchemaExtractor
        extractor = SchemaExtractor(mock_settings, llm_queue=mock_queue)

        await extractor.extract_field_group(
            content="We manufacture gearboxes and motors.",
            field_group=MANUFACTURING_GROUP,
            company_name="TestCorp",
        )

        # Check the submitted request
        call_args = mock_queue.submit.call_args
        submitted_request = call_args[0][0]

        # Verify prompts are in payload
        assert "system_prompt" in submitted_request.payload
        assert "user_prompt" in submitted_request.payload
        # Check prompts have meaningful content
        assert "extract" in submitted_request.payload["system_prompt"].lower()
        assert "TestCorp" in submitted_request.payload["user_prompt"]
        assert "manufacture" in submitted_request.payload["user_prompt"].lower()

    @pytest.mark.asyncio
    async def test_handles_queue_error_response(self, mock_settings, mock_queue):
        """Test that error responses from queue are handled."""
        from services.llm.models import LLMResponse
        from services.extraction.schema_extractor import SchemaExtractor, LLMExtractionError

        mock_queue.wait_for_result.return_value = LLMResponse(
            request_id="test-id",
            status="error",
            result=None,
            error="LLM processing failed",
            processing_time_ms=100,
            completed_at=datetime.now(UTC),
        )

        extractor = SchemaExtractor(mock_settings, llm_queue=mock_queue)

        with pytest.raises(LLMExtractionError) as exc_info:
            await extractor.extract_field_group(
                content="Test content",
                field_group=MANUFACTURING_GROUP,
                company_name="Test Co",
            )

        assert "LLM processing failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_handles_queue_timeout_response(self, mock_settings, mock_queue):
        """Test that timeout responses from queue are handled."""
        from services.llm.models import LLMResponse
        from services.extraction.schema_extractor import SchemaExtractor, LLMExtractionError

        mock_queue.wait_for_result.return_value = LLMResponse(
            request_id="test-id",
            status="timeout",
            result=None,
            error="Request expired",
            processing_time_ms=0,
            completed_at=datetime.now(UTC),
        )

        extractor = SchemaExtractor(mock_settings, llm_queue=mock_queue)

        with pytest.raises(LLMExtractionError) as exc_info:
            await extractor.extract_field_group(
                content="Test content",
                field_group=MANUFACTURING_GROUP,
                company_name="Test Co",
            )

        assert "timeout" in str(exc_info.value).lower() or "expired" in str(exc_info.value).lower()

    @pytest.mark.asyncio
    async def test_falls_back_to_direct_when_no_queue(self, mock_settings):
        """Test that extractor uses direct LLM calls when no queue provided."""
        from services.extraction.schema_extractor import SchemaExtractor

        # No queue provided - should use direct mode
        extractor = SchemaExtractor(mock_settings, llm_queue=None)

        # Mock the direct client
        extractor.client = MagicMock()
        extractor.client.chat.completions.create = AsyncMock(
            return_value=MagicMock(
                choices=[
                    MagicMock(
                        message=MagicMock(
                            content='{"manufactures_gearboxes": true}'
                        )
                    )
                ]
            )
        )

        result = await extractor.extract_field_group(
            content="Test content",
            field_group=MANUFACTURING_GROUP,
            company_name="Test Co",
        )

        # Should have called the client directly
        extractor.client.chat.completions.create.assert_called_once()
        assert result["manufactures_gearboxes"] is True

    @pytest.mark.asyncio
    async def test_product_extraction_via_queue(self, mock_settings, mock_queue):
        """Test product list extraction through queue."""
        from services.llm.models import LLMResponse
        from services.extraction.schema_extractor import SchemaExtractor

        mock_queue.wait_for_result.return_value = LLMResponse(
            request_id="test-id",
            status="success",
            result={
                "products": [
                    {"product_name": "D Series", "power_rating_kw": 100}
                ],
                "confidence": 0.9,
            },
            error=None,
            processing_time_ms=150,
            completed_at=datetime.now(UTC),
        )

        extractor = SchemaExtractor(mock_settings, llm_queue=mock_queue)

        result = await extractor.extract_field_group(
            content="Our D Series gearbox offers 100kW.",
            field_group=PRODUCTS_GEARBOX_GROUP,
            company_name="Test Co",
        )

        assert len(result["products"]) == 1
        assert result["products"][0]["product_name"] == "D Series"


class TestSchemaExtractorQueueIntegration:
    """Integration tests for queue-based extraction."""

    @pytest.fixture
    def mock_settings(self):
        settings = MagicMock()
        settings.openai_base_url = "http://localhost:9003/v1"
        settings.openai_api_key = "test"
        settings.llm_http_timeout = 60
        settings.llm_model = "test-model"
        settings.llm_request_timeout = 300
        # Retry settings
        settings.llm_max_retries = 3
        settings.llm_base_temperature = 0.1
        settings.llm_retry_temperature_increment = 0.05
        settings.llm_retry_backoff_min = 2
        settings.llm_retry_backoff_max = 30
        settings.llm_max_tokens = 4096
        return settings

    @pytest.mark.asyncio
    async def test_concurrent_extractions_via_queue(self, mock_settings):
        """Test that multiple extractions can run concurrently via queue."""
        from services.llm.models import LLMRequest, LLMResponse
        from services.extraction.schema_extractor import SchemaExtractor

        # Track concurrent submissions
        max_concurrent = 0
        current_concurrent = 0
        lock = asyncio.Lock()
        submitted_requests = []

        async def mock_submit(request: LLMRequest):
            nonlocal max_concurrent, current_concurrent
            async with lock:
                current_concurrent += 1
                max_concurrent = max(max_concurrent, current_concurrent)
                submitted_requests.append(request.request_id)
            return request.request_id

        async def mock_wait(request_id, timeout=300):
            nonlocal current_concurrent
            await asyncio.sleep(0.05)  # Simulate processing
            async with lock:
                current_concurrent -= 1
            return LLMResponse(
                request_id=request_id,
                status="success",
                result={"manufactures_gearboxes": True},
                error=None,
                processing_time_ms=50,
                completed_at=datetime.now(UTC),
            )

        mock_queue = AsyncMock()
        mock_queue.submit = mock_submit
        mock_queue.wait_for_result = mock_wait

        extractor = SchemaExtractor(mock_settings, llm_queue=mock_queue)

        # Run 5 extractions concurrently
        tasks = [
            extractor.extract_field_group(
                content=f"Content {i}",
                field_group=MANUFACTURING_GROUP,
                company_name=f"Company {i}",
            )
            for i in range(5)
        ]

        results = await asyncio.gather(*tasks)

        # All should complete
        assert len(results) == 5
        assert len(submitted_requests) == 5

        # Should have had concurrent submissions
        assert max_concurrent > 1, f"Expected concurrent submissions, got max {max_concurrent}"
