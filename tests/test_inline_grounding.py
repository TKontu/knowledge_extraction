"""Tests for inline grounding: scores computed during extraction pipeline."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.extraction.field_groups import FieldDefinition, FieldGroup
from services.extraction.grounding import (
    compute_grounding_scores,
    verify_numeric_in_quote,
)

# ── Unit: extract_group adds grounding_scores to result ──


def _make_field_group(fields: list[tuple[str, str]] | None = None) -> FieldGroup:
    """Helper to build a FieldGroup."""
    if fields is None:
        fields = [
            ("company_name", "string"),
            ("employee_count", "integer"),
            ("description", "text"),
        ]
    return FieldGroup(
        name="company_info",
        description="Company information",
        fields=[
            FieldDefinition(name=n, field_type=t, description="")
            for n, t in fields
        ],
        prompt_hint="",
    )


class TestGroundingScoresFromFieldGroup:
    """Test that field types can be extracted from FieldGroup for grounding."""

    def test_field_types_from_field_group(self):
        """FieldGroup fields provide the field_name->field_type mapping."""
        group = _make_field_group()
        field_types = {f.name: f.field_type for f in group.fields}
        assert field_types == {
            "company_name": "string",
            "employee_count": "integer",
            "description": "text",
        }

    def test_compute_scores_with_field_group_types(self):
        """compute_grounding_scores works with field types derived from FieldGroup."""
        group = _make_field_group()
        field_types = {f.name: f.field_type for f in group.fields}

        data = {
            "company_name": "ABB",
            "employee_count": 105000,
            "description": "A global tech company",
            "_quotes": {
                "company_name": "ABB Corp is a leader",
                "employee_count": "about 105,000 employees",
            },
        }

        scores = compute_grounding_scores(data, field_types)
        # string "ABB" in "ABB Corp is a leader" -> 1.0
        assert scores["company_name"] == 1.0
        # integer 105000 in "about 105,000 employees" -> 1.0
        assert scores["employee_count"] == 1.0
        # text fields excluded (grounding mode "none")
        assert "description" not in scores

    def test_empty_data_returns_empty_scores(self):
        group = _make_field_group()
        field_types = {f.name: f.field_type for f in group.fields}
        scores = compute_grounding_scores({}, field_types)
        assert scores == {}

    def test_missing_quotes_gives_zero(self):
        group = _make_field_group()
        field_types = {f.name: f.field_type for f in group.fields}

        data = {"company_name": "ABB", "employee_count": 105000}
        scores = compute_grounding_scores(data, field_types)
        # No _quotes at all -> 0.0 for required fields
        assert scores["company_name"] == 0.0
        assert scores["employee_count"] == 0.0


# ── Integration: SchemaExtractionOrchestrator.extract_all_groups ──


class TestExtractGroupInlineGrounding:
    """Test that extract_group() in schema_orchestrator computes grounding_scores."""

    @pytest.fixture
    def group(self):
        return _make_field_group()

    @pytest.mark.asyncio
    async def test_result_includes_grounding_scores(self, group):
        """extract_group result dict includes grounding_scores key."""
        from services.extraction.schema_orchestrator import SchemaExtractionOrchestrator

        # Create minimal orchestrator with mocked dependencies
        mock_extraction_config = MagicMock()
        mock_extraction_config.validation_enabled = False
        mock_extraction_config.extraction_batch_size = 4
        mock_extraction_config.content_limit = 20000
        mock_extraction_config.domain_dedup_enabled = False
        mock_extraction_config.chunk_max_tokens = 5000
        mock_extraction_config.chunk_overlap_tokens = 200

        mock_classification_config = MagicMock()
        mock_classification_config.enabled = False

        orchestrator = SchemaExtractionOrchestrator(
            schema_extractor=MagicMock(),
            extraction_config=mock_extraction_config,
            classification_config=mock_classification_config,
        )

        # Mock _extract_chunks_batched to return chunk data with quotes.
        # Let _merge_chunk_results run naturally so per-chunk grounding
        # scores are computed with aligned value+quote pairs.
        chunk_result = {
            "company_name": "ABB",
            "employee_count": 105000,
            "confidence": 0.9,
            "_quotes": {
                "company_name": "ABB is a global company",
                "employee_count": "approximately 105,000",
            },
        }
        orchestrator._extract_chunks_batched = AsyncMock(return_value=[chunk_result])

        results, _ = await orchestrator.extract_all_groups(
            source_id="test-source-id",
            markdown="Some markdown content",
            source_context="abb",
            field_groups=[group],
        )

        assert len(results) == 1
        result = results[0]
        assert "grounding_scores" in result
        assert result["grounding_scores"]["company_name"] == 1.0
        assert result["grounding_scores"]["employee_count"] == 1.0

    @pytest.mark.asyncio
    async def test_empty_result_has_empty_scores(self, group):
        """Empty extraction result -> empty grounding_scores."""
        from services.extraction.schema_orchestrator import SchemaExtractionOrchestrator

        mock_extraction_config = MagicMock()
        mock_extraction_config.validation_enabled = False
        mock_extraction_config.extraction_batch_size = 4
        mock_extraction_config.content_limit = 20000
        mock_extraction_config.domain_dedup_enabled = False
        mock_extraction_config.chunk_max_tokens = 5000
        mock_extraction_config.chunk_overlap_tokens = 200

        mock_classification_config = MagicMock()
        mock_classification_config.enabled = False

        orchestrator = SchemaExtractionOrchestrator(
            schema_extractor=MagicMock(),
            extraction_config=mock_extraction_config,
            classification_config=mock_classification_config,
        )

        # No chunk results -> empty data
        orchestrator._extract_chunks_batched = AsyncMock(return_value=[])

        results, _ = await orchestrator.extract_all_groups(
            source_id="test-source-id",
            markdown="Some content",
            source_context="abb",
            field_groups=[group],
        )

        assert len(results) == 1
        result = results[0]
        assert result["grounding_scores"] == {}


# ── Unit: Per-chunk grounding alignment in _merge_chunk_results ──


class TestPerChunkGroundingAlignment:
    """Grounding scores are computed per-chunk with aligned value+quote pairs.

    This prevents misalignment when the merged value comes from a different
    chunk than the merged quote (e.g., merge_dedupe, majority_vote strategies).
    """

    @pytest.fixture
    def orchestrator(self):
        from services.extraction.schema_orchestrator import SchemaExtractionOrchestrator

        return SchemaExtractionOrchestrator(MagicMock())

    def test_single_chunk_scores_aligned(self, orchestrator):
        """Single chunk: value and quote from same chunk → correct score."""
        group = _make_field_group([("company_name", "string")])
        chunk_results = [
            {
                "company_name": "ABB",
                "confidence": 0.9,
                "_quotes": {"company_name": "ABB Corp is a leader"},
            },
        ]
        merged = orchestrator._merge_chunk_results(chunk_results, group)
        scores = merged["_grounding_scores"]
        assert scores["company_name"] == 1.0

    def test_multi_chunk_best_score_wins(self, orchestrator):
        """Multiple chunks: best per-chunk score is kept."""
        group = _make_field_group([("employee_count", "integer")])
        chunk_results = [
            {
                "employee_count": 2500,
                "confidence": 0.8,
                "_quotes": {"employee_count": "about 2,500 employees"},
            },
            {
                "employee_count": 2500,
                "confidence": 0.7,
                "_quotes": {"employee_count": "no employee info here"},
            },
        ]
        merged = orchestrator._merge_chunk_results(chunk_results, group)
        scores = merged["_grounding_scores"]
        # Chunk 1 has value 2500 with quote mentioning 2,500 → 1.0
        # Chunk 2 has value 2500 with quote NOT mentioning it → 0.0
        # Best = 1.0
        assert scores["employee_count"] == 1.0

    def test_list_field_scored_per_chunk(self, orchestrator):
        """List fields: each chunk's items scored against its own quote."""
        group = _make_field_group([("products", "list")])
        # merge_dedupe strategy: items from both chunks merged
        chunk_results = [
            {
                "products": ["Gear A", "Gear B"],
                "confidence": 0.9,
                "_quotes": {"products": "We make Gear A and Gear B"},
            },
            {
                "products": ["Motor X"],
                "confidence": 0.8,
                "_quotes": {"products": "Introducing Motor X"},
            },
        ]
        merged = orchestrator._merge_chunk_results(chunk_results, group)
        scores = merged["_grounding_scores"]
        # Both chunks have their items grounded in their own quotes
        assert scores["products"] == 1.0

    def test_misaligned_quote_does_not_inflate_score(self, orchestrator):
        """Value from chunk A, quote from chunk B → per-chunk scoring avoids
        inflating the score from accidental matches."""
        group = _make_field_group([("employee_count", "integer")])
        chunk_results = [
            {
                "employee_count": 5000,
                "confidence": 0.7,
                # Quote says 5000 but this chunk's value is 5000 → grounded
                "_quotes": {"employee_count": "employs 5,000 people"},
            },
            {
                "employee_count": 140000,
                "confidence": 0.9,
                # Quote says 140-year history, NOT 140000 employees
                "_quotes": {"employee_count": "over 140-year history"},
            },
        ]
        merged = orchestrator._merge_chunk_results(chunk_results, group)
        scores = merged["_grounding_scores"]
        # highest_confidence picks 140000 from chunk 2
        # But chunk 2's own value (140000) against own quote → 0.0
        # Chunk 1's own value (5000) against own quote → 1.0
        # Best per-chunk = 1.0 (chunk 1 was grounded)
        assert scores["employee_count"] == 1.0

    def test_no_quotes_gives_zero(self, orchestrator):
        """No quotes in any chunk → score 0.0."""
        group = _make_field_group([("company_name", "string")])
        chunk_results = [
            {"company_name": "ABB", "confidence": 0.9},
        ]
        merged = orchestrator._merge_chunk_results(chunk_results, group)
        scores = merged["_grounding_scores"]
        assert scores["company_name"] == 0.0

    def test_text_fields_excluded(self, orchestrator):
        """Text fields (grounding mode 'none') are excluded from scores."""
        group = _make_field_group([("description", "text")])
        chunk_results = [
            {
                "description": "A tech company",
                "confidence": 0.9,
                "_quotes": {"description": "A tech company"},
            },
        ]
        merged = orchestrator._merge_chunk_results(chunk_results, group)
        scores = merged["_grounding_scores"]
        assert "description" not in scores

    def test_boolean_fields_excluded(self, orchestrator):
        """Boolean fields (grounding mode 'semantic') are excluded."""
        group = _make_field_group([("has_factory", "boolean")])
        chunk_results = [
            {
                "has_factory": True,
                "confidence": 0.9,
                "_quotes": {"has_factory": "our manufacturing facility"},
            },
        ]
        merged = orchestrator._merge_chunk_results(chunk_results, group)
        scores = merged["_grounding_scores"]
        assert "has_factory" not in scores

    def test_null_field_excluded(self, orchestrator):
        """Fields with None merged value are excluded from scores."""
        group = _make_field_group([("company_name", "string")])
        chunk_results = [
            {"confidence": 0.9},  # company_name absent
        ]
        merged = orchestrator._merge_chunk_results(chunk_results, group)
        scores = merged["_grounding_scores"]
        assert "company_name" not in scores


# ── Integration: Pipeline stores grounding_scores on Extraction ──


class TestPipelineStoresGroundingScores:
    """Test that pipeline.py passes grounding_scores to Extraction objects."""

    def test_extraction_object_receives_grounding_scores(self):
        """When result has grounding_scores, Extraction gets them."""
        from orm_models import Extraction

        scores = {"company_name": 1.0, "employee_count": 0.0}
        extraction = Extraction(
            project_id="00000000-0000-0000-0000-000000000001",
            source_id="00000000-0000-0000-0000-000000000002",
            data={"company_name": "ABB"},
            extraction_type="company_info",
            source_group="abb",
            confidence=0.9,
            grounding_scores=scores,
        )
        assert extraction.grounding_scores == scores

    def test_extraction_object_without_grounding_scores(self):
        """When result lacks grounding_scores, Extraction has None."""
        from orm_models import Extraction

        extraction = Extraction(
            project_id="00000000-0000-0000-0000-000000000001",
            source_id="00000000-0000-0000-0000-000000000002",
            data={"company_name": "ABB"},
            extraction_type="company_info",
            source_group="abb",
            confidence=0.9,
        )
        assert extraction.grounding_scores is None


# ── Fix: zero values should ground correctly ──


class TestZeroValueGrounding:
    def test_zero_integer_in_quote(self):
        """Value 0 with quote containing '0' should score 1.0."""
        assert verify_numeric_in_quote(0, "they now have 0 employees") == 1.0

    def test_zero_float_in_quote(self):
        assert verify_numeric_in_quote(0.0, "0% growth rate") == 1.0

    def test_zero_not_in_quote(self):
        assert verify_numeric_in_quote(0, "no numbers here") == 0.0

    def test_zero_string_in_quote(self):
        assert verify_numeric_in_quote("0", "reduced to 0 units") == 1.0
