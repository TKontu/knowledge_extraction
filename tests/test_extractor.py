"""Tests for extraction orchestrator."""

import pytest
from uuid import uuid4
from unittest.mock import AsyncMock, Mock

from models import ExtractionProfile, ExtractedFact, ExtractionResult, DocumentChunk


@pytest.fixture
def sample_profile():
    """Create a sample extraction profile."""
    return ExtractionProfile(
        name="technical_specs",
        categories=["specs", "hardware", "requirements"],
        prompt_focus="Hardware specifications and requirements",
        depth="detailed",
        is_builtin=True,
    )


@pytest.fixture
def sample_markdown():
    """Create sample markdown content that will chunk into multiple parts."""
    # Create a large enough document to force chunking
    # Default max_tokens is 8000, so each section needs to be > 8000 tokens
    # ~4 chars per token, so need > 32000 characters per section
    return """# Product Documentation

## Hardware Requirements

""" + ("Minimum 8GB RAM and 4 CPU cores required for optimal performance. " * 600) + """

## Performance

""" + ("Supports up to 10,000 requests per second with high throughput and low latency. " * 600) + """

## Compatibility

Compatible with Linux, Windows, and macOS.
"""


@pytest.fixture
def short_markdown():
    """Create short markdown that fits in one chunk."""
    return """# Quick Start

This product requires Docker 20.10 or higher.
"""


@pytest.fixture
def mock_llm_client():
    """Create mock LLM client."""
    client = Mock()
    client.extract_facts = AsyncMock()
    return client


class TestExtractionOrchestrator:
    """Tests for ExtractionOrchestrator class."""

    @pytest.mark.asyncio
    async def test_extract_from_short_document_single_chunk(
        self, sample_profile, short_markdown, mock_llm_client
    ):
        """Test extracting from a document that fits in one chunk."""
        from services.extraction.extractor import ExtractionOrchestrator

        # Mock LLM response for single chunk
        mock_llm_client.extract_facts.return_value = [
            ExtractedFact(
                fact="Requires Docker 20.10 or higher",
                category="requirements",
                confidence=0.95,
                source_quote="Docker 20.10 or higher",
            )
        ]

        page_id = uuid4()
        orchestrator = ExtractionOrchestrator(mock_llm_client)
        result = await orchestrator.extract(page_id, short_markdown, sample_profile)

        assert isinstance(result, ExtractionResult)
        assert result.page_id == page_id
        assert result.chunks_processed == 1
        assert len(result.facts) == 1
        assert result.facts[0].fact == "Requires Docker 20.10 or higher"
        assert result.extraction_time_ms > 0

        # Verify LLM was called once
        mock_llm_client.extract_facts.assert_called_once()

    @pytest.mark.asyncio
    async def test_extract_from_multi_chunk_document(
        self, sample_profile, sample_markdown, mock_llm_client
    ):
        """Test extracting from a document that requires multiple chunks."""
        from services.extraction.extractor import ExtractionOrchestrator

        # Mock LLM to return different facts based on content
        call_count = [0]

        async def mock_extract(content, categories, profile_name):
            call_count[0] += 1
            if call_count[0] == 1:
                return [
                    ExtractedFact(
                        fact="Minimum 8GB RAM required",
                        category="hardware",
                        confidence=0.95,
                        source_quote="8GB RAM",
                    )
                ]
            elif call_count[0] == 2:
                return [
                    ExtractedFact(
                        fact="Supports 10,000 requests per second",
                        category="specs",
                        confidence=0.9,
                        source_quote="10,000 requests per second",
                    )
                ]
            else:
                # For any additional chunks, return empty list
                return []

        mock_llm_client.extract_facts.side_effect = mock_extract

        page_id = uuid4()
        orchestrator = ExtractionOrchestrator(mock_llm_client)
        result = await orchestrator.extract(page_id, sample_markdown, sample_profile)

        assert isinstance(result, ExtractionResult)
        assert result.page_id == page_id
        assert result.chunks_processed >= 2  # At least 2 chunks for multi-chunk test
        assert len(result.facts) >= 2  # At least 2 facts from first two chunks

        # Verify facts from first two chunks are present
        fact_texts = [f.fact for f in result.facts]
        assert "Minimum 8GB RAM required" in fact_texts
        assert "Supports 10,000 requests per second" in fact_texts

    @pytest.mark.asyncio
    async def test_extract_handles_empty_markdown(
        self, sample_profile, mock_llm_client
    ):
        """Test extracting from empty markdown."""
        from services.extraction.extractor import ExtractionOrchestrator

        page_id = uuid4()
        orchestrator = ExtractionOrchestrator(mock_llm_client)
        result = await orchestrator.extract(page_id, "", sample_profile)

        assert isinstance(result, ExtractionResult)
        assert result.page_id == page_id
        assert result.chunks_processed == 0
        assert len(result.facts) == 0

        # LLM should not be called for empty content
        mock_llm_client.extract_facts.assert_not_called()

    @pytest.mark.asyncio
    async def test_extract_handles_llm_returning_no_facts(
        self, sample_profile, short_markdown, mock_llm_client
    ):
        """Test handling when LLM returns no facts."""
        from services.extraction.extractor import ExtractionOrchestrator

        # Mock LLM to return empty list
        mock_llm_client.extract_facts.return_value = []

        page_id = uuid4()
        orchestrator = ExtractionOrchestrator(mock_llm_client)
        result = await orchestrator.extract(page_id, short_markdown, sample_profile)

        assert isinstance(result, ExtractionResult)
        assert result.page_id == page_id
        assert result.chunks_processed == 1
        assert len(result.facts) == 0

    @pytest.mark.asyncio
    async def test_extract_deduplicates_exact_duplicate_facts(
        self, sample_profile, sample_markdown, mock_llm_client
    ):
        """Test that exact duplicate facts are removed."""
        from services.extraction.extractor import ExtractionOrchestrator

        # Mock LLM to return duplicate facts for all chunks
        duplicate_fact = ExtractedFact(
            fact="Supports 10,000 requests per second",
            category="specs",
            confidence=0.9,
            source_quote="10,000 requests per second",
        )

        # Return the same fact for every chunk
        async def mock_extract(content, categories, profile_name):
            return [duplicate_fact]

        mock_llm_client.extract_facts.side_effect = mock_extract

        page_id = uuid4()
        orchestrator = ExtractionOrchestrator(mock_llm_client)
        result = await orchestrator.extract(page_id, sample_markdown, sample_profile)

        assert isinstance(result, ExtractionResult)
        assert len(result.facts) == 1  # Duplicates should be removed
        assert result.facts[0].fact == "Supports 10,000 requests per second"
        assert result.chunks_processed >= 2  # Multi-chunk document

    @pytest.mark.asyncio
    async def test_extract_preserves_header_context(
        self, sample_profile, sample_markdown, mock_llm_client
    ):
        """Test that header context is preserved in extracted facts."""
        from services.extraction.extractor import ExtractionOrchestrator

        mock_llm_client.extract_facts.return_value = [
            ExtractedFact(
                fact="Minimum 8GB RAM required",
                category="hardware",
                confidence=0.95,
                source_quote="8GB RAM",
            )
        ]

        page_id = uuid4()
        orchestrator = ExtractionOrchestrator(mock_llm_client)
        result = await orchestrator.extract(page_id, sample_markdown, sample_profile)

        assert len(result.facts) > 0
        # Facts should have header context from chunking
        assert result.facts[0].header_context is not None

    @pytest.mark.asyncio
    async def test_extract_passes_correct_categories_to_llm(
        self, sample_profile, short_markdown, mock_llm_client
    ):
        """Test that profile categories are passed to LLM."""
        from services.extraction.extractor import ExtractionOrchestrator

        mock_llm_client.extract_facts.return_value = []

        page_id = uuid4()
        orchestrator = ExtractionOrchestrator(mock_llm_client)
        await orchestrator.extract(page_id, short_markdown, sample_profile)

        # Verify LLM was called with correct categories
        call_args = mock_llm_client.extract_facts.call_args
        assert call_args is not None
        assert call_args.kwargs["categories"] == sample_profile.categories
        assert call_args.kwargs["profile_name"] == sample_profile.name

    @pytest.mark.asyncio
    async def test_extract_handles_llm_exception(
        self, sample_profile, short_markdown, mock_llm_client
    ):
        """Test handling when LLM raises an exception."""
        from services.extraction.extractor import ExtractionOrchestrator

        # Mock LLM to raise exception
        mock_llm_client.extract_facts.side_effect = Exception("LLM service unavailable")

        page_id = uuid4()
        orchestrator = ExtractionOrchestrator(mock_llm_client)

        with pytest.raises(Exception, match="LLM service unavailable"):
            await orchestrator.extract(page_id, short_markdown, sample_profile)

    @pytest.mark.asyncio
    async def test_orchestrator_measures_extraction_time(
        self, sample_profile, short_markdown, mock_llm_client
    ):
        """Test that extraction time is measured."""
        from services.extraction.extractor import ExtractionOrchestrator

        mock_llm_client.extract_facts.return_value = []

        page_id = uuid4()
        orchestrator = ExtractionOrchestrator(mock_llm_client)
        result = await orchestrator.extract(page_id, short_markdown, sample_profile)

        # Extraction time should be positive
        assert result.extraction_time_ms > 0
        assert result.extraction_time_ms < 10000  # Should be reasonable (< 10 seconds for test)
