"""Tests for LLMWorker using prompts from payload.

TDD: These tests verify that the LLMWorker uses pre-built prompts
from the request payload instead of building them internally.
"""

from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, MagicMock

import pytest


class TestLLMWorkerPromptsFromPayload:
    """Tests for LLMWorker using prompts from request payload."""

    @pytest.fixture
    def mock_redis(self):
        """Create mock Redis client."""
        redis = AsyncMock()
        redis.xgroup_create = AsyncMock()
        redis.xreadgroup = AsyncMock(return_value=[])
        redis.xack = AsyncMock()
        redis.setex = AsyncMock()
        return redis

    @pytest.fixture
    def mock_llm_client(self):
        """Create mock LLM client."""
        client = AsyncMock()
        client.chat = AsyncMock()
        client.chat.completions = AsyncMock()
        client.chat.completions.create = AsyncMock(
            return_value=MagicMock(
                choices=[MagicMock(message=MagicMock(content='{"facts": []}'))]
            )
        )
        return client

    @pytest.fixture
    def worker(self, mock_redis, mock_llm_client):
        """Create LLMWorker with mocks."""
        from src.services.llm.worker import LLMWorker

        return LLMWorker(
            redis=mock_redis,
            llm_client=mock_llm_client,
            worker_id="test-worker-1",
            initial_concurrency=10,
            max_concurrency=50,
            min_concurrency=5,
        )

    @pytest.mark.asyncio
    async def test_extract_facts_uses_prompts_from_payload(
        self, worker, mock_redis, mock_llm_client
    ):
        """Test that extract_facts uses system_prompt and user_prompt from payload."""
        from src.services.llm.models import LLMRequest

        custom_system_prompt = (
            "You are a custom fact extractor. Categories: tech, science."
        )
        custom_user_prompt = (
            "Extract facts from this custom content:\n---\nTest content\n---"
        )

        request = LLMRequest(
            request_id="test-prompts",
            request_type="extract_facts",
            payload={
                "content": "Test content",
                "categories": ["tech", "science"],
                "profile_name": "test",
                "system_prompt": custom_system_prompt,
                "user_prompt": custom_user_prompt,
                "model": "test-model",
            },
            priority=5,
            created_at=datetime.now(UTC),
            timeout_at=datetime.now(UTC) + timedelta(seconds=300),
        )

        mock_redis.xreadgroup = AsyncMock(
            return_value=[("llm:requests", [("entry-1", {"data": request.to_json()})])]
        )

        await worker.process_batch()

        # Verify LLM was called with prompts from payload
        mock_llm_client.chat.completions.create.assert_called_once()
        call_kwargs = mock_llm_client.chat.completions.create.call_args[1]
        messages = call_kwargs["messages"]

        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == custom_system_prompt
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == custom_user_prompt

    @pytest.mark.asyncio
    async def test_extract_entities_uses_prompts_from_payload(
        self, worker, mock_redis, mock_llm_client
    ):
        """Test that extract_entities uses system_prompt and user_prompt from payload."""
        from src.services.llm.models import LLMRequest

        mock_llm_client.chat.completions.create = AsyncMock(
            return_value=MagicMock(
                choices=[MagicMock(message=MagicMock(content='{"entities": []}'))]
            )
        )

        custom_system_prompt = "Extract entities from data. Source Group: TestCo"
        custom_user_prompt = "Extract entities:\nPro plan available"

        request = LLMRequest(
            request_id="test-entity-prompts",
            request_type="extract_entities",
            payload={
                "extraction_data": {"fact_text": "Pro plan available"},
                "entity_types": [{"name": "plan", "description": "Plans"}],
                "source_group": "TestCo",
                "system_prompt": custom_system_prompt,
                "user_prompt": custom_user_prompt,
                "model": "test-model",
            },
            priority=5,
            created_at=datetime.now(UTC),
            timeout_at=datetime.now(UTC) + timedelta(seconds=300),
        )

        mock_redis.xreadgroup = AsyncMock(
            return_value=[("llm:requests", [("entry-1", {"data": request.to_json()})])]
        )

        await worker.process_batch()

        # Verify LLM was called with prompts from payload
        mock_llm_client.chat.completions.create.assert_called_once()
        call_kwargs = mock_llm_client.chat.completions.create.call_args[1]
        messages = call_kwargs["messages"]

        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == custom_system_prompt
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == custom_user_prompt

    @pytest.mark.asyncio
    async def test_extract_field_group_uses_prompts_from_payload(
        self, worker, mock_redis, mock_llm_client
    ):
        """Test that extract_field_group uses prompts from payload when available."""
        from src.services.llm.models import LLMRequest

        custom_system_prompt = "You are extracting manufacturing info."
        custom_user_prompt = (
            "Company: TestCo\n\nExtract manufacturing info:\n---\nContent\n---"
        )

        request = LLMRequest(
            request_id="test-field-group-prompts",
            request_type="extract_field_group",
            payload={
                "content": "Content",
                "field_group": {
                    "name": "manufacturing",
                    "description": "Manufacturing info",
                },
                "company_name": "TestCo",
                "system_prompt": custom_system_prompt,
                "user_prompt": custom_user_prompt,
                "model": "test-model",
            },
            priority=5,
            created_at=datetime.now(UTC),
            timeout_at=datetime.now(UTC) + timedelta(seconds=300),
        )

        mock_redis.xreadgroup = AsyncMock(
            return_value=[("llm:requests", [("entry-1", {"data": request.to_json()})])]
        )

        await worker.process_batch()

        # Verify LLM was called with prompts from payload
        mock_llm_client.chat.completions.create.assert_called_once()
        call_kwargs = mock_llm_client.chat.completions.create.call_args[1]
        messages = call_kwargs["messages"]

        assert messages[0]["role"] == "system"
        assert messages[0]["content"] == custom_system_prompt
        assert messages[1]["role"] == "user"
        assert messages[1]["content"] == custom_user_prompt

    @pytest.mark.asyncio
    async def test_falls_back_to_internal_prompts_when_not_in_payload(
        self, worker, mock_redis, mock_llm_client
    ):
        """Test backward compatibility: falls back to internal prompt building."""
        from src.services.llm.models import LLMRequest

        # Request without prompts in payload (old format)
        request = LLMRequest(
            request_id="test-fallback",
            request_type="extract_facts",
            payload={
                "content": "Test content for fallback",
                "categories": ["general"],
                "profile_name": "test",
                # Note: No system_prompt or user_prompt
            },
            priority=5,
            created_at=datetime.now(UTC),
            timeout_at=datetime.now(UTC) + timedelta(seconds=300),
        )

        mock_redis.xreadgroup = AsyncMock(
            return_value=[("llm:requests", [("entry-1", {"data": request.to_json()})])]
        )

        await worker.process_batch()

        # Should still call LLM (with internally generated prompts)
        mock_llm_client.chat.completions.create.assert_called_once()
        call_kwargs = mock_llm_client.chat.completions.create.call_args[1]
        messages = call_kwargs["messages"]

        # Verify prompts were generated (contain content or categories)
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        # Fallback prompts should contain the content or category info
        assert (
            "general" in messages[0]["content"]
            or "Test content" in messages[1]["content"]
        )


class TestLLMWorkerModelFromPayload:
    """Tests for LLMWorker using model from payload."""

    @pytest.fixture
    def mock_redis(self):
        redis = AsyncMock()
        redis.xgroup_create = AsyncMock()
        redis.xreadgroup = AsyncMock(return_value=[])
        redis.xack = AsyncMock()
        redis.setex = AsyncMock()
        return redis

    @pytest.fixture
    def mock_llm_client(self):
        client = AsyncMock()
        client.chat = AsyncMock()
        client.chat.completions = AsyncMock()
        client.chat.completions.create = AsyncMock(
            return_value=MagicMock(
                choices=[MagicMock(message=MagicMock(content='{"facts": []}'))]
            )
        )
        return client

    @pytest.fixture
    def worker(self, mock_redis, mock_llm_client):
        from src.services.llm.worker import LLMWorker

        return LLMWorker(
            redis=mock_redis,
            llm_client=mock_llm_client,
            worker_id="test-worker-1",
            initial_concurrency=10,
            model="default-model",  # Default model
        )

    @pytest.mark.asyncio
    async def test_uses_model_from_payload_when_provided(
        self, worker, mock_redis, mock_llm_client
    ):
        """Test that worker uses model from payload when specified."""
        from src.services.llm.models import LLMRequest

        request = LLMRequest(
            request_id="test-model",
            request_type="extract_facts",
            payload={
                "content": "Test",
                "categories": ["tech"],
                "profile_name": "test",
                "system_prompt": "System",
                "user_prompt": "User",
                "model": "custom-model-from-payload",  # Custom model in payload
            },
            priority=5,
            created_at=datetime.now(UTC),
            timeout_at=datetime.now(UTC) + timedelta(seconds=300),
        )

        mock_redis.xreadgroup = AsyncMock(
            return_value=[("llm:requests", [("entry-1", {"data": request.to_json()})])]
        )

        await worker.process_batch()

        call_kwargs = mock_llm_client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "custom-model-from-payload"

    @pytest.mark.asyncio
    async def test_uses_default_model_when_not_in_payload(
        self, worker, mock_redis, mock_llm_client
    ):
        """Test that worker uses default model when not in payload."""
        from src.services.llm.models import LLMRequest

        request = LLMRequest(
            request_id="test-default-model",
            request_type="extract_facts",
            payload={
                "content": "Test",
                "categories": ["tech"],
                "profile_name": "test",
                # No model in payload
            },
            priority=5,
            created_at=datetime.now(UTC),
            timeout_at=datetime.now(UTC) + timedelta(seconds=300),
        )

        mock_redis.xreadgroup = AsyncMock(
            return_value=[("llm:requests", [("entry-1", {"data": request.to_json()})])]
        )

        await worker.process_batch()

        call_kwargs = mock_llm_client.chat.completions.create.call_args[1]
        assert call_kwargs["model"] == "default-model"  # Worker's default
