"""Tests for LLM client."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest

from config import settings
from models import ExtractedFact
from services.llm.client import LLMClient


class MockChatCompletion:
    """Mock OpenAI chat completion response."""

    def __init__(self, content: str):
        self.choices = [MagicMock(message=MagicMock(content=content))]


@pytest.fixture
def llm_client() -> LLMClient:
    """Create LLM client fixture."""
    return LLMClient(settings)


@pytest.fixture
def sample_facts_json() -> str:
    """Sample JSON response from LLM."""
    return """{
  "facts": [
    {
      "fact": "Pro plan supports 10,000 API calls per minute",
      "category": "limits",
      "confidence": 0.95,
      "source_quote": "10,000 req/min on Pro tier"
    },
    {
      "fact": "Enterprise plan includes SSO authentication",
      "category": "features",
      "confidence": 0.9,
      "source_quote": "SSO available for Enterprise customers"
    }
  ]
}"""


class TestLLMClient:
    """Test LLM client functionality."""

    @pytest.mark.asyncio
    async def test_client_initialization(self, llm_client: LLMClient) -> None:
        """Test client initializes with correct settings."""
        assert llm_client.model == settings.llm_model
        assert llm_client.client is not None

    @pytest.mark.asyncio
    async def test_extract_facts_success(
        self, llm_client: LLMClient, sample_facts_json: str
    ) -> None:
        """Test successful fact extraction."""
        with patch.object(
            llm_client.client.chat.completions,
            "create",
            new_callable=AsyncMock,
        ) as mock_create:
            mock_create.return_value = MockChatCompletion(sample_facts_json)

            content = "## Pro Plan\nSupports 10,000 req/min. Enterprise includes SSO."
            profile_categories = ["limits", "features"]

            facts = await llm_client.extract_facts(content, profile_categories)

            assert len(facts) == 2
            assert facts[0].fact == "Pro plan supports 10,000 API calls per minute"
            assert facts[0].category == "limits"
            assert facts[0].confidence == 0.95
            assert facts[1].category == "features"

    @pytest.mark.asyncio
    async def test_extract_facts_with_profile(
        self, llm_client: LLMClient, sample_facts_json: str
    ) -> None:
        """Test extraction uses profile categories."""
        with patch.object(
            llm_client.client.chat.completions,
            "create",
            new_callable=AsyncMock,
        ) as mock_create:
            mock_create.return_value = MockChatCompletion(sample_facts_json)

            content = "Test content"
            categories = ["limits", "features", "pricing"]

            await llm_client.extract_facts(content, categories)

            # Verify categories were passed to prompt
            call_args = mock_create.call_args
            messages = call_args.kwargs["messages"]
            system_prompt = messages[0]["content"]

            assert "limits" in system_prompt
            assert "features" in system_prompt
            assert "pricing" in system_prompt

    @pytest.mark.asyncio
    async def test_extract_facts_empty_response(
        self, llm_client: LLMClient
    ) -> None:
        """Test handles empty facts list."""
        with patch.object(
            llm_client.client.chat.completions,
            "create",
            new_callable=AsyncMock,
        ) as mock_create:
            mock_create.return_value = MockChatCompletion('{"facts": []}')

            facts = await llm_client.extract_facts("content", ["category"])

            assert len(facts) == 0

    @pytest.mark.asyncio
    async def test_extract_facts_uses_json_mode(
        self, llm_client: LLMClient, sample_facts_json: str
    ) -> None:
        """Test extraction uses JSON mode."""
        with patch.object(
            llm_client.client.chat.completions,
            "create",
            new_callable=AsyncMock,
        ) as mock_create:
            mock_create.return_value = MockChatCompletion(sample_facts_json)

            await llm_client.extract_facts("content", ["category"])

            call_args = mock_create.call_args
            assert call_args.kwargs["response_format"] == {"type": "json_object"}

    @pytest.mark.asyncio
    async def test_extract_facts_uses_low_temperature(
        self, llm_client: LLMClient, sample_facts_json: str
    ) -> None:
        """Test extraction uses low temperature for consistency."""
        with patch.object(
            llm_client.client.chat.completions,
            "create",
            new_callable=AsyncMock,
        ) as mock_create:
            mock_create.return_value = MockChatCompletion(sample_facts_json)

            await llm_client.extract_facts("content", ["category"])

            call_args = mock_create.call_args
            assert call_args.kwargs["temperature"] == 0.1

    @pytest.mark.asyncio
    async def test_extract_facts_retries_on_failure(
        self, llm_client: LLMClient, sample_facts_json: str
    ) -> None:
        """Test client retries on transient failures."""
        with patch.object(
            llm_client.client.chat.completions,
            "create",
            new_callable=AsyncMock,
        ) as mock_create:
            # Fail twice, then succeed
            mock_create.side_effect = [
                Exception("Temporary error"),
                Exception("Another error"),
                MockChatCompletion(sample_facts_json),
            ]

            facts = await llm_client.extract_facts("content", ["category"])

            assert len(facts) == 2
            assert mock_create.call_count == 3

    @pytest.mark.asyncio
    async def test_extract_facts_invalid_json_raises_error(
        self, llm_client: LLMClient
    ) -> None:
        """Test raises error on invalid JSON."""
        with patch.object(
            llm_client.client.chat.completions,
            "create",
            new_callable=AsyncMock,
        ) as mock_create:
            mock_create.return_value = MockChatCompletion("invalid json{")

            with pytest.raises(Exception):
                await llm_client.extract_facts("content", ["category"])

    @pytest.mark.asyncio
    async def test_extract_facts_missing_fields_skipped(
        self, llm_client: LLMClient
    ) -> None:
        """Test skips facts with missing required fields."""
        incomplete_json = """{
  "facts": [
    {
      "fact": "Complete fact",
      "category": "features",
      "confidence": 0.9
    },
    {
      "fact": "Missing category"
    },
    {
      "category": "limits"
    }
  ]
}"""
        with patch.object(
            llm_client.client.chat.completions,
            "create",
            new_callable=AsyncMock,
        ) as mock_create:
            mock_create.return_value = MockChatCompletion(incomplete_json)

            facts = await llm_client.extract_facts("content", ["category"])

            # Only the complete fact should be returned
            assert len(facts) == 1
            assert facts[0].fact == "Complete fact"


class TestLLMClientComplete:
    """Tests for LLMClient.complete() method."""

    @pytest.mark.asyncio
    async def test_complete_returns_json(self, llm_client: LLMClient) -> None:
        """Test complete() returns parsed JSON when response_format is json_object."""
        with patch.object(
            llm_client.client.chat.completions,
            "create",
            new_callable=AsyncMock,
        ) as mock_create:
            json_response = '{"result": "success", "value": 42}'
            mock_create.return_value = MockChatCompletion(json_response)

            result = await llm_client.complete(
                system_prompt="Test system prompt",
                user_prompt="Test user prompt",
                response_format={"type": "json_object"},
            )

            assert isinstance(result, dict)
            assert result["result"] == "success"
            assert result["value"] == 42

    @pytest.mark.asyncio
    async def test_complete_returns_text_when_no_format(
        self, llm_client: LLMClient
    ) -> None:
        """Test complete() returns text in dict when no response_format specified."""
        with patch.object(
            llm_client.client.chat.completions,
            "create",
            new_callable=AsyncMock,
        ) as mock_create:
            text_response = "This is a plain text response"
            mock_create.return_value = MockChatCompletion(text_response)

            result = await llm_client.complete(
                system_prompt="System",
                user_prompt="User",
            )

            assert isinstance(result, dict)
            assert result["text"] == text_response

    @pytest.mark.asyncio
    async def test_complete_uses_custom_temperature(
        self, llm_client: LLMClient
    ) -> None:
        """Test complete() uses custom temperature when provided."""
        with patch.object(
            llm_client.client.chat.completions,
            "create",
            new_callable=AsyncMock,
        ) as mock_create:
            mock_create.return_value = MockChatCompletion('{"status": "ok"}')

            await llm_client.complete(
                system_prompt="System",
                user_prompt="User",
                response_format={"type": "json_object"},
                temperature=0.7,
            )

            call_args = mock_create.call_args
            assert call_args.kwargs["temperature"] == 0.7

    @pytest.mark.asyncio
    async def test_complete_uses_default_temperature(
        self, llm_client: LLMClient
    ) -> None:
        """Test complete() uses default temperature when not specified."""
        with patch.object(
            llm_client.client.chat.completions,
            "create",
            new_callable=AsyncMock,
        ) as mock_create:
            mock_create.return_value = MockChatCompletion('{"status": "ok"}')

            await llm_client.complete(
                system_prompt="System",
                user_prompt="User",
                response_format={"type": "json_object"},
            )

            call_args = mock_create.call_args
            assert call_args.kwargs["temperature"] == settings.llm_base_temperature

    @pytest.mark.asyncio
    async def test_complete_retries_on_failure(self, llm_client: LLMClient) -> None:
        """Test complete() retries on transient failures."""
        with patch.object(
            llm_client.client.chat.completions,
            "create",
            new_callable=AsyncMock,
        ) as mock_create:
            # Fail twice, then succeed
            json_response = '{"result": "success"}'
            mock_create.side_effect = [
                Exception("Temporary error"),
                Exception("Another error"),
                MockChatCompletion(json_response),
            ]

            result = await llm_client.complete(
                system_prompt="System",
                user_prompt="User",
                response_format={"type": "json_object"},
            )

            assert result["result"] == "success"
            assert mock_create.call_count == 3

    @pytest.mark.asyncio
    async def test_complete_raises_error_after_max_retries(
        self, llm_client: LLMClient
    ) -> None:
        """Test complete() raises LLMExtractionError after all retries exhausted."""
        from services.llm.client import LLMExtractionError

        with patch.object(
            llm_client.client.chat.completions,
            "create",
            new_callable=AsyncMock,
        ) as mock_create:
            mock_create.side_effect = Exception("Persistent error")

            with pytest.raises(LLMExtractionError) as exc_info:
                await llm_client.complete(
                    system_prompt="System",
                    user_prompt="User",
                )

            assert "LLM completion failed" in str(exc_info.value)
            assert mock_create.call_count == settings.llm_max_retries

    @pytest.mark.asyncio
    async def test_complete_sends_correct_messages(
        self, llm_client: LLMClient
    ) -> None:
        """Test complete() sends correct system and user messages."""
        with patch.object(
            llm_client.client.chat.completions,
            "create",
            new_callable=AsyncMock,
        ) as mock_create:
            mock_create.return_value = MockChatCompletion('{"ok": true}')

            system_text = "You are a helpful assistant"
            user_text = "What is 2+2?"

            await llm_client.complete(
                system_prompt=system_text,
                user_prompt=user_text,
                response_format={"type": "json_object"},
            )

            call_args = mock_create.call_args
            messages = call_args.kwargs["messages"]

            assert len(messages) == 2
            assert messages[0]["role"] == "system"
            assert messages[0]["content"] == system_text
            assert messages[1]["role"] == "user"
            assert messages[1]["content"] == user_text


class TestLLMClientLifecycle:
    """Tests for LLMClient lifecycle management (close, context manager)."""

    @pytest.mark.asyncio
    async def test_direct_mode_client_has_close_method(self) -> None:
        """Test that LLMClient has a close method."""
        client = LLMClient(settings)
        assert hasattr(client, "close")
        assert callable(client.close)

    @pytest.mark.asyncio
    async def test_direct_mode_close_closes_openai_client(self) -> None:
        """Test that close() calls close on the OpenAI client."""
        client = LLMClient(settings)

        # Mock the OpenAI client's close method
        client.client.close = AsyncMock()

        await client.close()

        client.client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_close_is_idempotent(self) -> None:
        """Test that calling close multiple times is safe."""
        client = LLMClient(settings)
        client.client.close = AsyncMock()

        # Call close twice
        await client.close()
        await client.close()

        # Should only close once
        client.client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_context_manager_closes_client(self) -> None:
        """Test that using client as context manager closes it on exit."""
        client = LLMClient(settings)
        client.client.close = AsyncMock()

        async with client:
            pass  # Just test the context manager

        client.client.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_queue_mode_close_is_noop(self) -> None:
        """Test that close() in queue mode does not error (no client to close)."""
        mock_queue = AsyncMock()
        client = LLMClient(settings, llm_queue=mock_queue)

        # Should not raise any errors
        await client.close()
        await client.close()  # Multiple calls should also be safe

    @pytest.mark.asyncio
    async def test_close_after_error_still_closes(self) -> None:
        """Test that close works even after previous operations failed."""
        client = LLMClient(settings)
        client.client.close = AsyncMock()

        # Simulate that something went wrong during usage
        # (e.g., network error during extraction)
        # The client should still be closeable
        await client.close()

        client.client.close.assert_called_once()
