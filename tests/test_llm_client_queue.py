"""Tests for LLMClient with queue mode integration.

TDD: These tests define the expected behavior for LLMClient queue mode
and the new extract_entities() method.
"""

import asyncio
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestLLMClientQueueMode:
    """Tests for LLMClient using LLM request queue."""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings."""
        settings = MagicMock()
        settings.openai_base_url = "http://localhost:9003/v1"
        settings.openai_api_key = "test"
        settings.llm_http_timeout = 60
        settings.llm_model = "test-model"
        settings.llm_request_timeout = 300
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
        """Test that LLMClient uses queue when provided."""
        from services.llm.client import LLMClient
        from services.llm.models import LLMResponse

        # Mock successful response from queue
        mock_queue.wait_for_result.return_value = LLMResponse(
            request_id="test-request-id",
            status="success",
            result={
                "facts": [
                    {"fact": "Test fact", "category": "general", "confidence": 0.9}
                ]
            },
            error=None,
            processing_time_ms=100,
            completed_at=datetime.now(UTC),
        )

        client = LLMClient(mock_settings, llm_queue=mock_queue)

        result = await client.extract_facts(
            content="Test content with facts.",
            categories=["general", "technical"],
            profile_name="test",
        )

        # Should have submitted to queue
        mock_queue.submit.assert_called_once()
        mock_queue.wait_for_result.assert_called_once()

        # Should return extracted facts
        assert len(result) == 1
        assert result[0].fact == "Test fact"
        assert result[0].category == "general"
        assert result[0].confidence == 0.9

    @pytest.mark.asyncio
    async def test_submits_correct_request_type_for_facts(
        self, mock_settings, mock_queue
    ):
        """Test that correct request type is submitted for fact extraction."""
        from services.llm.models import LLMRequest, LLMResponse

        mock_queue.wait_for_result.return_value = LLMResponse(
            request_id="test-id",
            status="success",
            result={"facts": []},
            error=None,
            processing_time_ms=100,
            completed_at=datetime.now(UTC),
        )

        from services.llm.client import LLMClient

        client = LLMClient(mock_settings, llm_queue=mock_queue)

        await client.extract_facts(
            content="Test content",
            categories=["tech"],
            profile_name="test",
        )

        # Check the submitted request
        call_args = mock_queue.submit.call_args
        submitted_request = call_args[0][0]

        assert isinstance(submitted_request, LLMRequest)
        assert submitted_request.request_type == "extract_facts"
        assert "content" in submitted_request.payload
        assert "categories" in submitted_request.payload
        assert "profile_name" in submitted_request.payload
        # Key insight: prompts should be in payload
        assert "system_prompt" in submitted_request.payload
        assert "user_prompt" in submitted_request.payload

    @pytest.mark.asyncio
    async def test_includes_prompts_in_payload(self, mock_settings, mock_queue):
        """Test that pre-built prompts are included in queue request payload."""
        from services.llm.models import LLMResponse

        mock_queue.wait_for_result.return_value = LLMResponse(
            request_id="test-id",
            status="success",
            result={"facts": []},
            error=None,
            processing_time_ms=100,
            completed_at=datetime.now(UTC),
        )

        from services.llm.client import LLMClient

        client = LLMClient(mock_settings, llm_queue=mock_queue)

        await client.extract_facts(
            content="Test content about gearboxes.",
            categories=["technical", "product"],
            profile_name="manufacturing",
        )

        call_args = mock_queue.submit.call_args
        submitted_request = call_args[0][0]

        # Verify prompts are present and built correctly
        assert "system_prompt" in submitted_request.payload
        assert "user_prompt" in submitted_request.payload
        assert "technical" in submitted_request.payload["system_prompt"]
        assert "product" in submitted_request.payload["system_prompt"]
        assert (
            "Test content about gearboxes" in submitted_request.payload["user_prompt"]
        )

    @pytest.mark.asyncio
    async def test_handles_queue_error_response(self, mock_settings, mock_queue):
        """Test that error responses from queue are handled."""
        from services.llm.client import LLMClient, LLMExtractionError
        from services.llm.models import LLMResponse

        mock_queue.wait_for_result.return_value = LLMResponse(
            request_id="test-id",
            status="error",
            result=None,
            error="LLM processing failed",
            processing_time_ms=100,
            completed_at=datetime.now(UTC),
        )

        client = LLMClient(mock_settings, llm_queue=mock_queue)

        with pytest.raises(LLMExtractionError) as exc_info:
            await client.extract_facts(
                content="Test content",
                categories=["general"],
                profile_name="test",
            )

        assert "LLM processing failed" in str(exc_info.value)

    @pytest.mark.asyncio
    async def test_handles_queue_timeout_response(self, mock_settings, mock_queue):
        """Test that timeout responses from queue are handled."""
        from services.llm.client import LLMClient, LLMExtractionError
        from services.llm.models import LLMResponse

        mock_queue.wait_for_result.return_value = LLMResponse(
            request_id="test-id",
            status="timeout",
            result=None,
            error="Request expired",
            processing_time_ms=0,
            completed_at=datetime.now(UTC),
        )

        client = LLMClient(mock_settings, llm_queue=mock_queue)

        with pytest.raises(LLMExtractionError) as exc_info:
            await client.extract_facts(
                content="Test content",
                categories=["general"],
                profile_name="test",
            )

        assert (
            "timeout" in str(exc_info.value).lower()
            or "expired" in str(exc_info.value).lower()
        )

    @pytest.mark.asyncio
    async def test_falls_back_to_direct_when_no_queue(self, mock_settings):
        """Test that LLMClient uses direct LLM calls when no queue provided."""
        from services.llm.client import LLMClient

        # No queue provided - should use direct mode
        client = LLMClient(mock_settings, llm_queue=None)

        # Mock the direct client
        client.client = MagicMock()
        client.client.chat.completions.create = AsyncMock(
            return_value=MagicMock(
                choices=[
                    MagicMock(
                        message=MagicMock(
                            content='{"facts": [{"fact": "Direct fact", "category": "general", "confidence": 0.8}]}'
                        )
                    )
                ]
            )
        )

        result = await client.extract_facts(
            content="Test content",
            categories=["general"],
            profile_name="test",
        )

        # Should have called the client directly
        client.client.chat.completions.create.assert_called_once()
        assert len(result) == 1
        assert result[0].fact == "Direct fact"


class TestLLMClientExtractEntities:
    """Tests for LLMClient.extract_entities() method."""

    @pytest.fixture
    def mock_settings(self):
        """Create mock settings."""
        settings = MagicMock()
        settings.openai_base_url = "http://localhost:9003/v1"
        settings.openai_api_key = "test"
        settings.llm_http_timeout = 60
        settings.llm_model = "test-model"
        settings.llm_request_timeout = 300
        return settings

    @pytest.fixture
    def mock_queue(self):
        """Create mock LLM request queue."""
        queue = AsyncMock()
        queue.submit = AsyncMock(return_value="test-request-id")
        queue.wait_for_result = AsyncMock()
        return queue

    @pytest.mark.asyncio
    async def test_extract_entities_exists(self, mock_settings):
        """Test that extract_entities method exists on LLMClient."""
        from services.llm.client import LLMClient

        client = LLMClient(mock_settings)
        assert hasattr(client, "extract_entities")
        assert callable(client.extract_entities)

    @pytest.mark.asyncio
    async def test_extract_entities_via_queue(self, mock_settings, mock_queue):
        """Test entity extraction through queue."""
        from services.llm.client import LLMClient
        from services.llm.models import LLMResponse

        mock_queue.wait_for_result.return_value = LLMResponse(
            request_id="test-id",
            status="success",
            result={
                "entities": [
                    {
                        "type": "plan",
                        "value": "Professional Plan",
                        "normalized": "professional_plan",
                        "attributes": {},
                    },
                    {
                        "type": "feature",
                        "value": "API Access",
                        "normalized": "api_access",
                        "attributes": {},
                    },
                ]
            },
            error=None,
            processing_time_ms=100,
            completed_at=datetime.now(UTC),
        )

        client = LLMClient(mock_settings, llm_queue=mock_queue)

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
    async def test_extract_entities_request_type(self, mock_settings, mock_queue):
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

        client = LLMClient(mock_settings, llm_queue=mock_queue)

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
    async def test_extract_entities_prompts_in_payload(self, mock_settings, mock_queue):
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

        client = LLMClient(mock_settings, llm_queue=mock_queue)

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
    async def test_extract_entities_direct_mode(self, mock_settings):
        """Test entity extraction in direct mode (no queue)."""
        from services.llm.client import LLMClient

        client = LLMClient(mock_settings, llm_queue=None)

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
    async def test_extract_entities_handles_error(self, mock_settings, mock_queue):
        """Test that entity extraction handles errors."""
        from services.llm.client import LLMClient, LLMExtractionError
        from services.llm.models import LLMResponse

        mock_queue.wait_for_result.return_value = LLMResponse(
            request_id="test-id",
            status="error",
            result=None,
            error="Entity extraction failed",
            processing_time_ms=100,
            completed_at=datetime.now(UTC),
        )

        client = LLMClient(mock_settings, llm_queue=mock_queue)

        with pytest.raises(LLMExtractionError) as exc_info:
            await client.extract_entities(
                extraction_data={"fact_text": "Test"},
                entity_types=[{"name": "plan", "description": "Plans"}],
                source_group="TestCo",
            )

        assert "Entity extraction failed" in str(exc_info.value)


class TestLLMClientConcurrency:
    """Tests for concurrent operations via queue."""

    @pytest.fixture
    def mock_settings(self):
        settings = MagicMock()
        settings.openai_base_url = "http://localhost:9003/v1"
        settings.openai_api_key = "test"
        settings.llm_http_timeout = 60
        settings.llm_model = "test-model"
        settings.llm_request_timeout = 300
        return settings

    @pytest.mark.asyncio
    async def test_concurrent_fact_extractions_via_queue(self, mock_settings):
        """Test that multiple fact extractions can run concurrently via queue."""
        from services.llm.client import LLMClient
        from services.llm.models import LLMRequest, LLMResponse

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
                result={
                    "facts": [
                        {
                            "fact": f"Fact for {request_id}",
                            "category": "general",
                            "confidence": 0.8,
                        }
                    ]
                },
                error=None,
                processing_time_ms=50,
                completed_at=datetime.now(UTC),
            )

        mock_queue = AsyncMock()
        mock_queue.submit = mock_submit
        mock_queue.wait_for_result = mock_wait

        client = LLMClient(mock_settings, llm_queue=mock_queue)

        # Run 5 extractions concurrently
        tasks = [
            client.extract_facts(
                content=f"Content {i}",
                categories=["general"],
                profile_name=f"profile_{i}",
            )
            for i in range(5)
        ]

        results = await asyncio.gather(*tasks)

        # All should complete
        assert len(results) == 5
        assert len(submitted_requests) == 5

        # Should have had concurrent submissions
        assert max_concurrent > 1, (
            f"Expected concurrent submissions, got max {max_concurrent}"
        )
