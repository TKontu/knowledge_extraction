"""Tests for report synthesis service."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.llm.client import LLMClient, LLMExtractionError
from services.reports.synthesis import ReportSynthesizer, SynthesisResult


@pytest.fixture
def mock_llm_client():
    """Create a mock LLM client."""
    client = MagicMock(spec=LLMClient)
    client.complete = AsyncMock()
    return client


@pytest.fixture
def synthesizer(mock_llm_client):
    """Create a ReportSynthesizer instance with mock LLM client."""
    return ReportSynthesizer(mock_llm_client)


@pytest.fixture
def sample_facts():
    """Sample facts for testing."""
    return [
        {
            "data": {"fact": "REMPCO lead screws require minimal maintenance"},
            "confidence": 0.9,
            "source_uri": "https://rempco.com/lead-screws",
            "source_title": "Lead Screws Page",
        },
        {
            "data": {"fact": "Two-stage coupling process for extra long screws"},
            "confidence": 0.85,
            "source_uri": "https://rempco.com/manufacturing",
            "source_title": "Manufacturing Page",
        },
    ]


class TestReportSynthesizer:
    """Test ReportSynthesizer class."""

    @pytest.mark.asyncio
    async def test_synthesize_facts_combines_with_attribution(
        self, synthesizer, mock_llm_client, sample_facts
    ):
        """Verify facts are combined with [Source: X] citations."""
        # Mock LLM response
        mock_llm_client.complete.return_value = {
            "synthesized_text": "REMPCO lead screws require minimal maintenance [Source: Lead Screws Page]. Manufacturing uses two-stage coupling for extra long screws [Source: Manufacturing Page].",
            "sources_used": [
                "https://rempco.com/lead-screws",
                "https://rempco.com/manufacturing",
            ],
            "confidence": 0.88,
            "conflicts_noted": [],
        }

        result = await synthesizer.synthesize_facts(sample_facts)

        assert isinstance(result, SynthesisResult)
        assert "[Source:" in result.synthesized_text
        assert len(result.sources_used) == 2
        assert "https://rempco.com/lead-screws" in result.sources_used
        assert result.confidence > 0.8
        mock_llm_client.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_synthesize_facts_notes_conflicts(
        self, synthesizer, mock_llm_client
    ):
        """Verify conflicting facts are noted."""
        conflicting_facts = [
            {
                "data": {"fact": "Product A costs $100"},
                "confidence": 0.9,
                "source_uri": "https://example.com/pricing1",
                "source_title": "Pricing Page 1",
            },
            {
                "data": {"fact": "Product A costs $150"},
                "confidence": 0.85,
                "source_uri": "https://example.com/pricing2",
                "source_title": "Pricing Page 2",
            },
        ]

        mock_llm_client.complete.return_value = {
            "synthesized_text": "Product A pricing varies between sources",
            "sources_used": [
                "https://example.com/pricing1",
                "https://example.com/pricing2",
            ],
            "confidence": 0.75,
            "conflicts_noted": ["Pricing discrepancy: $100 vs $150"],
        }

        result = await synthesizer.synthesize_facts(conflicting_facts)

        assert len(result.conflicts_noted) > 0
        assert "Pricing discrepancy" in result.conflicts_noted[0]

    @pytest.mark.asyncio
    async def test_synthesize_facts_chunks_large_inputs(
        self, synthesizer, mock_llm_client
    ):
        """Verify large fact sets are chunked to avoid token limits."""
        # Create 20 facts (exceeds MAX_FACTS_PER_SYNTHESIS = 15)
        large_fact_set = [
            {
                "data": {"fact": f"Fact number {i}"},
                "confidence": 0.9,
                "source_uri": f"https://example.com/page{i}",
                "source_title": f"Page {i}",
            }
            for i in range(20)
        ]

        # Mock responses for chunks
        mock_llm_client.complete.return_value = {
            "synthesized_text": "Chunked synthesis result",
            "sources_used": ["https://example.com/page0"],
            "confidence": 0.9,
            "conflicts_noted": [],
        }

        result = await synthesizer.synthesize_facts(large_fact_set)

        # Should be called multiple times (once per chunk)
        assert mock_llm_client.complete.call_count >= 2
        assert isinstance(result, SynthesisResult)

    @pytest.mark.asyncio
    async def test_merge_field_values_boolean(self, synthesizer):
        """Verify boolean uses any()."""
        values = [
            {"value": False, "source_uri": "https://example.com/1", "confidence": 0.8},
            {"value": True, "source_uri": "https://example.com/2", "confidence": 0.9},
            {"value": False, "source_uri": "https://example.com/3", "confidence": 0.85},
        ]

        result = await synthesizer.merge_field_values(
            field_name="has_feature", values=values, field_type="boolean"
        )

        assert result.value is True  # any() returns True if any value is True
        assert len(result.sources) == 3
        assert result.confidence == 0.9  # max confidence

    @pytest.mark.asyncio
    async def test_merge_field_values_number(self, synthesizer):
        """Verify number uses max."""
        values = [
            {"value": 100, "source_uri": "https://example.com/1", "confidence": 0.8},
            {"value": 250, "source_uri": "https://example.com/2", "confidence": 0.9},
            {"value": 150, "source_uri": "https://example.com/3", "confidence": 0.85},
        ]

        result = await synthesizer.merge_field_values(
            field_name="max_capacity", values=values, field_type="number"
        )

        assert result.value == 250  # max value
        assert len(result.sources) == 3
        assert result.confidence == 0.9

    @pytest.mark.asyncio
    async def test_merge_field_values_text_uses_llm(
        self, synthesizer, mock_llm_client
    ):
        """Verify text fields call LLM for synthesis."""
        values = [
            {
                "value": "High-quality steel construction",
                "source_uri": "https://example.com/1",
                "confidence": 0.9,
            },
            {
                "value": "Durable steel materials",
                "source_uri": "https://example.com/2",
                "confidence": 0.85,
            },
        ]

        mock_llm_client.complete.return_value = {
            "merged_text": "High-quality durable steel construction",
            "sources_used": ["https://example.com/1", "https://example.com/2"],
            "confidence": 0.88,
        }

        result = await synthesizer.merge_field_values(
            field_name="material", values=values, field_type="text"
        )

        assert result.value == "High-quality durable steel construction"
        assert len(result.sources) == 2
        mock_llm_client.complete.assert_called_once()

    @pytest.mark.asyncio
    async def test_merge_field_values_list_deduplicates(self, synthesizer):
        """Verify list values are deduplicated."""
        values = [
            {
                "value": ["option1", "option2"],
                "source_uri": "https://example.com/1",
                "confidence": 0.9,
            },
            {
                "value": ["option2", "option3"],
                "source_uri": "https://example.com/2",
                "confidence": 0.85,
            },
            {
                "value": ["option1", "option4"],
                "source_uri": "https://example.com/3",
                "confidence": 0.8,
            },
        ]

        result = await synthesizer.merge_field_values(
            field_name="features", values=values, field_type="list"
        )

        # Should have deduplicated values
        assert len(result.value) == 4
        assert all(
            opt in result.value for opt in ["option1", "option2", "option3", "option4"]
        )

    @pytest.mark.asyncio
    async def test_fallback_on_llm_failure(self, synthesizer, mock_llm_client):
        """Verify graceful fallback when LLM fails."""
        mock_llm_client.complete.side_effect = LLMExtractionError("LLM timeout")

        facts = [
            {
                "data": {"fact": "Test fact"},
                "confidence": 0.9,
                "source_uri": "https://example.com/test",
                "source_title": "Test Page",
            }
        ]

        result = await synthesizer.synthesize_facts(facts)

        # Should return fallback result
        assert isinstance(result, SynthesisResult)
        assert "Test fact" in result.synthesized_text
        assert "Fallback" in result.conflicts_noted[0]
        assert result.confidence == 0.7  # fallback confidence

    @pytest.mark.asyncio
    async def test_empty_facts_returns_empty_result(self, synthesizer):
        """Verify empty input is handled."""
        result = await synthesizer.synthesize_facts([])

        assert result.synthesized_text == "No facts available."
        assert result.sources_used == []
        assert result.confidence == 0.0
        assert result.conflicts_noted == []

    @pytest.mark.asyncio
    async def test_merge_field_values_empty_values(self, synthesizer):
        """Verify empty values list returns None."""
        result = await synthesizer.merge_field_values(
            field_name="test_field", values=[], field_type="text"
        )

        assert result.value is None
        assert result.sources == []
        assert result.confidence == 0.0

    @pytest.mark.asyncio
    async def test_merge_text_single_unique_value(self, synthesizer, mock_llm_client):
        """Verify single unique text value doesn't call LLM."""
        values = [
            {
                "value": "Same text",
                "source_uri": "https://example.com/1",
                "confidence": 0.9,
            },
            {
                "value": "Same text",
                "source_uri": "https://example.com/2",
                "confidence": 0.85,
            },
        ]

        result = await synthesizer.merge_field_values(
            field_name="description", values=values, field_type="text"
        )

        # Should not call LLM for identical values
        mock_llm_client.complete.assert_not_called()
        assert result.value == "Same text"
        assert result.confidence == 0.95  # high confidence for single unique value

    @pytest.mark.asyncio
    async def test_format_facts_for_prompt(self, synthesizer, sample_facts):
        """Verify facts are formatted correctly for LLM prompt."""
        formatted = synthesizer._format_facts_for_prompt(sample_facts)

        assert "REMPCO lead screws" in formatted
        assert "confidence: 0.90" in formatted
        assert "Lead Screws Page" in formatted
        assert "https://rempco.com/lead-screws" in formatted

    @pytest.mark.asyncio
    async def test_synthesis_type_parameter(self, synthesizer, mock_llm_client):
        """Verify synthesis_type is passed to prompt."""
        mock_llm_client.complete.return_value = {
            "synthesized_text": "Comparison result",
            "sources_used": [],
            "confidence": 0.9,
            "conflicts_noted": [],
        }

        facts = [
            {
                "data": {"fact": "Fact 1"},
                "confidence": 0.9,
                "source_uri": "https://example.com/1",
                "source_title": "Page 1",
            }
        ]

        await synthesizer.synthesize_facts(facts, synthesis_type="compare")

        # Check that the call was made with 'compare' in the prompt
        call_args = mock_llm_client.complete.call_args
        assert "compare" in call_args.kwargs["user_prompt"].lower()

    @pytest.mark.asyncio
    async def test_chunked_synthesis_uses_two_pass_unification(
        self, synthesizer, mock_llm_client
    ):
        """Verify large fact sets use two-pass synthesis with unification."""
        # Create 20 facts (exceeds MAX_FACTS_PER_SYNTHESIS = 15)
        large_fact_set = [
            {
                "data": {"fact": f"Fact number {i}"},
                "confidence": 0.9,
                "source_uri": f"https://example.com/page{i}",
                "source_title": f"Page {i}",
            }
            for i in range(20)
        ]

        # Track call order to verify two-pass approach
        call_count = [0]

        def mock_complete(**kwargs):
            call_count[0] += 1
            system_prompt = kwargs.get("system_prompt", "")
            # Unification pass uses "merging multiple synthesized text sections"
            if "merging multiple synthesized text sections" in system_prompt:
                return {
                    "unified_text": "Unified synthesis of all facts with deduplication",
                    "conflicts_noted": [],
                }
            else:
                # Chunk pass
                return {
                    "synthesized_text": f"Chunk {call_count[0]} synthesis",
                    "sources_used": ["https://example.com/page0"],
                    "confidence": 0.9,
                    "conflicts_noted": [],
                }

        mock_llm_client.complete.side_effect = mock_complete

        result = await synthesizer.synthesize_facts(large_fact_set)

        # Should have: 2 chunk calls + 1 unification call = 3 total
        assert mock_llm_client.complete.call_count >= 3
        assert "Unified synthesis" in result.synthesized_text

    @pytest.mark.asyncio
    async def test_unification_fallback_on_llm_failure(
        self, synthesizer, mock_llm_client
    ):
        """Verify fallback when unification LLM call fails."""
        # Create 20 facts to trigger chunking
        large_fact_set = [
            {
                "data": {"fact": f"Fact number {i}"},
                "confidence": 0.9,
                "source_uri": f"https://example.com/page{i}",
                "source_title": f"Page {i}",
            }
            for i in range(20)
        ]

        call_count = [0]

        def mock_complete(**kwargs):
            call_count[0] += 1
            system_prompt = kwargs.get("system_prompt", "")
            # Unification pass uses "merging multiple synthesized text sections"
            if "merging multiple synthesized text sections" in system_prompt:
                raise LLMExtractionError("Unification failed")
            else:
                # Chunk passes succeed
                return {
                    "synthesized_text": f"Chunk {call_count[0]} result",
                    "sources_used": ["https://example.com/page0"],
                    "confidence": 0.9,
                    "conflicts_noted": [],
                }

        mock_llm_client.complete.side_effect = mock_complete

        result = await synthesizer.synthesize_facts(large_fact_set)

        # Should fallback to section-based output
        assert "Section" in result.synthesized_text
        assert "Fallback: LLM unification unavailable" in result.conflicts_noted

    @pytest.mark.asyncio
    async def test_fallback_unify_preserves_chunk_content(self, synthesizer):
        """Verify fallback unification preserves all chunk content."""
        chunk_results = [
            SynthesisResult(
                synthesized_text="First chunk content",
                sources_used=["https://example.com/1"],
                confidence=0.9,
                conflicts_noted=[],
            ),
            SynthesisResult(
                synthesized_text="Second chunk content",
                sources_used=["https://example.com/2"],
                confidence=0.85,
                conflicts_noted=["Minor conflict"],
            ),
        ]

        result = synthesizer._fallback_unify(
            chunk_results,
            all_sources=["https://example.com/1", "https://example.com/2"],
            all_conflicts=["Minor conflict"],
        )

        assert "First chunk content" in result.synthesized_text
        assert "Second chunk content" in result.synthesized_text
        assert "Section 1" in result.synthesized_text
        assert "Section 2" in result.synthesized_text
        assert len(result.sources_used) == 2
        assert "Fallback: LLM unification unavailable" in result.conflicts_noted
