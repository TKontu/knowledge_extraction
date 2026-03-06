"""Tests for inline grounding: scores computed during extraction pipeline."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.extraction.field_groups import FieldDefinition, FieldGroup
from services.extraction.grounding import compute_grounding_scores

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

        # Mock _extract_chunks_batched to return merged data with quotes
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
        orchestrator._merge_chunk_results = MagicMock(return_value=dict(chunk_result))
        orchestrator._is_empty_result = MagicMock(return_value=(False, []))

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
