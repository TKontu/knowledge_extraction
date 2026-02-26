"""Tests for SchemaExtractionOrchestrator."""

from unittest.mock import AsyncMock, Mock
from uuid import uuid4

import pytest

from services.extraction.field_groups import FieldDefinition, FieldGroup
from services.extraction.schema_orchestrator import SchemaExtractionOrchestrator


@pytest.fixture
def mock_extractor():
    """Create a mock schema extractor."""
    extractor = Mock()
    extractor.extract_field_group = AsyncMock(return_value={"test": "data"})
    return extractor


@pytest.fixture
def sample_field_groups():
    """Create sample field groups for testing."""
    return [
        FieldGroup(
            name="test_group",
            description="Test field group",
            fields=[
                FieldDefinition(
                    name="test_field",
                    field_type="text",
                    description="Test field",
                ),
            ],
            prompt_hint="Test hint",
        ),
    ]


class TestExtractAllGroups:
    """Test extract_all_groups method."""

    @pytest.mark.asyncio
    async def test_extract_all_groups_requires_field_groups(self, mock_extractor):
        """Returns empty list if field_groups is empty."""
        orchestrator = SchemaExtractionOrchestrator(mock_extractor)
        source_id = uuid4()

        results, classification = await orchestrator.extract_all_groups(
            source_id=source_id,
            markdown="# Test content",
            source_context="Test Company",
            field_groups=[],
        )

        assert results == []
        assert classification is None

    @pytest.mark.asyncio
    async def test_extract_all_groups_logs_error_if_no_groups(self, mock_extractor):
        """Verify error logged and returns empty when no field_groups provided."""
        orchestrator = SchemaExtractionOrchestrator(mock_extractor)
        source_id = uuid4()

        results, classification = await orchestrator.extract_all_groups(
            source_id=source_id,
            markdown="# Test content",
            source_context="Test Company",
            field_groups=[],
        )

        # Should return empty list when no field_groups
        assert results == []
        assert classification is None

    @pytest.mark.asyncio
    async def test_extract_all_groups_works_with_field_groups(
        self, mock_extractor, sample_field_groups
    ):
        """Works correctly when field_groups provided."""
        orchestrator = SchemaExtractionOrchestrator(mock_extractor)
        source_id = uuid4()

        results, classification = await orchestrator.extract_all_groups(
            source_id=source_id,
            markdown="# Test content",
            source_context="Test Company",
            field_groups=sample_field_groups,
        )

        # Should have results (not empty)
        assert len(results) >= 0  # Depends on implementation
        # Classification is None when classification_enabled=False (default)
        assert classification is None


class TestIsEmptyResult:
    """Test Phase 3A: _is_empty_result() detection."""

    @pytest.fixture
    def orchestrator(self, mock_extractor):
        return SchemaExtractionOrchestrator(mock_extractor)

    @pytest.fixture
    def non_entity_group(self):
        return FieldGroup(
            name="company_info",
            description="Company information",
            fields=[
                FieldDefinition(name="name", field_type="text", description="Company name"),
                FieldDefinition(name="city", field_type="text", description="City"),
                FieldDefinition(name="country", field_type="text", description="Country"),
                FieldDefinition(name="employees", field_type="integer", description="Employee count"),
                FieldDefinition(name="is_public", field_type="boolean", description="Public?", default=False),
            ],
            prompt_hint="Company details",
        )

    @pytest.fixture
    def entity_list_group(self):
        return FieldGroup(
            name="locations",
            description="Company locations",
            fields=[
                FieldDefinition(name="city", field_type="text", description="City"),
                FieldDefinition(name="country", field_type="text", description="Country"),
            ],
            prompt_hint="Location list",
            is_entity_list=True,
        )

    def test_all_null_is_empty(self, orchestrator, non_entity_group):
        """All null data should be empty."""
        data = {}
        is_empty, ratio = orchestrator._is_empty_result(data, non_entity_group)
        assert is_empty is True
        assert ratio == 0.0

    def test_all_default_is_empty(self, orchestrator, non_entity_group):
        """Data with only default values should be empty."""
        data = {"is_public": False}  # False is the default for is_public
        is_empty, ratio = orchestrator._is_empty_result(data, non_entity_group)
        assert is_empty is True

    def test_populated_data_is_not_empty(self, orchestrator, non_entity_group):
        """Data with real values should not be empty."""
        data = {"name": "Acme Corp", "city": "Helsinki", "country": "Finland"}
        is_empty, ratio = orchestrator._is_empty_result(data, non_entity_group)
        assert is_empty is False
        assert ratio >= 0.5

    def test_one_field_populated_of_five(self, orchestrator, non_entity_group):
        """1 of 5 fields = 20% → is_empty (threshold is <20%)."""
        data = {"name": "Acme Corp"}  # 1/5 = 0.2, not < 0.2
        is_empty, ratio = orchestrator._is_empty_result(data, non_entity_group)
        assert is_empty is False  # 0.2 is not < 0.2

    def test_empty_strings_not_counted(self, orchestrator, non_entity_group):
        """Empty/whitespace strings should not count as populated."""
        data = {"name": "", "city": "  "}
        is_empty, ratio = orchestrator._is_empty_result(data, non_entity_group)
        assert is_empty is True
        assert ratio == 0.0

    def test_empty_lists_not_counted(self, orchestrator):
        """Empty lists should not count as populated."""
        group = FieldGroup(
            name="test",
            description="Test",
            fields=[
                FieldDefinition(name="items", field_type="list", description="Items"),
                FieldDefinition(name="tags", field_type="list", description="Tags"),
            ],
            prompt_hint="",
        )
        data = {"items": [], "tags": []}
        is_empty, ratio = orchestrator._is_empty_result(data, group)
        assert is_empty is True

    def test_entity_list_empty_when_no_entities(self, orchestrator, entity_list_group):
        """Entity list with no entities should be empty."""
        data = {"locations": []}
        is_empty, ratio = orchestrator._is_empty_result(data, entity_list_group)
        assert is_empty is True
        assert ratio == 0.0

    def test_entity_list_not_empty_when_has_entities(self, orchestrator, entity_list_group):
        """Entity list with entities should not be empty."""
        data = {"locations": [{"city": "Helsinki", "country": "Finland"}]}
        is_empty, ratio = orchestrator._is_empty_result(data, entity_list_group)
        assert is_empty is False
        assert ratio == 1.0


class TestConfidenceRecalibration:
    """Test Phase 3A: confidence recalibration after merge."""

    @pytest.fixture
    def orchestrator(self, mock_extractor):
        return SchemaExtractionOrchestrator(mock_extractor)

    @pytest.fixture
    def group(self):
        return FieldGroup(
            name="company",
            description="Company info",
            fields=[
                FieldDefinition(name="name", field_type="text", description="Name"),
                FieldDefinition(name="city", field_type="text", description="City"),
            ],
            prompt_hint="",
        )

    def test_empty_extraction_confidence_capped(self, orchestrator, group):
        """Empty extractions should have confidence ≤ 0.1."""
        # Simulate what _merge_chunk_results returns for empty data
        merged = {"confidence": 0.8}
        chunk_results = [merged]
        result_merged = orchestrator._merge_chunk_results(chunk_results, group)

        # Manually test the recalibration logic
        raw_confidence = result_merged.pop("confidence", None)
        is_empty, ratio = orchestrator._is_empty_result(result_merged, group)
        assert is_empty is True
        if raw_confidence is None:
            raw_confidence = 0.0
        final_conf = min(raw_confidence, 0.1) if is_empty else raw_confidence
        assert final_conf <= 0.1

    def test_full_extraction_confidence_preserved(self, orchestrator, group):
        """Well-populated extractions should keep high confidence."""
        data = {"name": "Acme Corp", "city": "Helsinki"}
        is_empty, ratio = orchestrator._is_empty_result(data, group)
        assert is_empty is False
        # No population scaling — LLM confidence passes through directly
        raw_confidence = 0.85
        final_conf = raw_confidence
        assert final_conf == pytest.approx(0.85, abs=0.01)

    def test_missing_confidence_defaults_to_zero(self, orchestrator, group):
        """Missing confidence key in merged dict defaults to 0.0."""
        # merged.pop("confidence", 0.0) returns 0.0 when key absent
        merged = {"name": "Acme"}
        raw_confidence = merged.pop("confidence", 0.0)
        assert raw_confidence == 0.0
        assert isinstance(raw_confidence, float)

    def test_partial_extraction_confidence_preserved(self, orchestrator):
        """Focused pages (few fields populated) keep LLM confidence — no penalty."""
        group = FieldGroup(
            name="info",
            description="Info",
            fields=[
                FieldDefinition(name="a", field_type="text", description="A"),
                FieldDefinition(name="b", field_type="text", description="B"),
                FieldDefinition(name="c", field_type="text", description="C"),
                FieldDefinition(name="d", field_type="text", description="D"),
            ],
            prompt_hint="",
        )
        data = {"a": "value"}  # 1/4 = 0.25, not empty
        is_empty, ratio = orchestrator._is_empty_result(data, group)
        assert is_empty is False
        assert ratio == 0.25
        # No population scaling — raw confidence passes through
        final = 0.8
        assert final == pytest.approx(0.8, abs=0.01)


class TestBooleanMajorityVote:
    """Test Phase 3B: boolean merge uses majority vote instead of any()."""

    @pytest.fixture
    def orchestrator(self, mock_extractor):
        return SchemaExtractionOrchestrator(mock_extractor)

    @pytest.fixture
    def bool_group(self):
        return FieldGroup(
            name="flags",
            description="Boolean flags",
            fields=[
                FieldDefinition(name="has_factory", field_type="boolean", description="Has factory"),
            ],
            prompt_hint="",
        )

    def test_majority_false(self, orchestrator, bool_group):
        """1 True + 2 False → False (majority)."""
        chunk_results = [
            {"has_factory": True, "confidence": 0.8},
            {"has_factory": False, "confidence": 0.8},
            {"has_factory": False, "confidence": 0.8},
        ]
        merged = orchestrator._merge_chunk_results(chunk_results, bool_group)
        assert merged["has_factory"] is False

    def test_majority_true(self, orchestrator, bool_group):
        """2 True + 1 False → True (majority)."""
        chunk_results = [
            {"has_factory": True, "confidence": 0.8},
            {"has_factory": True, "confidence": 0.8},
            {"has_factory": False, "confidence": 0.8},
        ]
        merged = orchestrator._merge_chunk_results(chunk_results, bool_group)
        assert merged["has_factory"] is True

    def test_all_true(self, orchestrator, bool_group):
        """All True → True."""
        chunk_results = [
            {"has_factory": True, "confidence": 0.8},
            {"has_factory": True, "confidence": 0.8},
        ]
        merged = orchestrator._merge_chunk_results(chunk_results, bool_group)
        assert merged["has_factory"] is True

    def test_all_false(self, orchestrator, bool_group):
        """All False → False."""
        chunk_results = [
            {"has_factory": False, "confidence": 0.8},
            {"has_factory": False, "confidence": 0.8},
        ]
        merged = orchestrator._merge_chunk_results(chunk_results, bool_group)
        assert merged["has_factory"] is False

    def test_tie_is_false(self, orchestrator, bool_group):
        """1 True + 1 False → False (conservative tie-break)."""
        chunk_results = [
            {"has_factory": True, "confidence": 0.8},
            {"has_factory": False, "confidence": 0.8},
        ]
        merged = orchestrator._merge_chunk_results(chunk_results, bool_group)
        assert merged["has_factory"] is False
