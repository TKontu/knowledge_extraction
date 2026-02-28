"""Tests for merge conflict detection."""

from unittest.mock import Mock, patch

import pytest

from services.extraction.field_groups import FieldDefinition, FieldGroup
from services.extraction.schema_orchestrator import SchemaExtractionOrchestrator


@pytest.fixture
def mock_extractor():
    from unittest.mock import AsyncMock
    extractor = Mock()
    extractor.extract_field_group = AsyncMock(return_value={})
    return extractor


@pytest.fixture
def orchestrator(mock_extractor):
    return SchemaExtractionOrchestrator(mock_extractor)


@pytest.fixture
def mixed_group():
    return FieldGroup(
        name="company_info",
        description="Company information",
        fields=[
            FieldDefinition(name="name", field_type="text", description="Company name"),
            FieldDefinition(name="employees", field_type="integer", description="Employee count"),
            FieldDefinition(name="is_public", field_type="boolean", description="Public?"),
            FieldDefinition(name="industry", field_type="enum", description="Industry",
                          enum_values=["manufacturing", "services", "technology"]),
        ],
        prompt_hint="",
    )


class TestNumericConflict:
    """Test numeric conflict detection (>10% relative difference)."""

    def test_numeric_conflict_recorded(self, orchestrator, mixed_group):
        """Values differing by >10% should be flagged."""
        chunk_results = [
            {"employees": 100, "confidence": 0.8},
            {"employees": 200, "confidence": 0.7},
        ]
        with patch("services.extraction.schema_orchestrator.settings") as s:
            s.extraction_source_quoting_enabled = False
            s.extraction_conflict_detection_enabled = True
            merged = orchestrator._merge_chunk_results(chunk_results, mixed_group)

        assert "_conflicts" in merged
        assert "employees" in merged["_conflicts"]
        conflict = merged["_conflicts"]["employees"]
        assert conflict["resolution"] == "max"
        assert conflict["resolved_value"] == 200
        assert len(conflict["values"]) == 2

    def test_numeric_no_conflict_within_10pct(self, orchestrator, mixed_group):
        """Values within 10% should not be flagged."""
        chunk_results = [
            {"employees": 100, "confidence": 0.8},
            {"employees": 105, "confidence": 0.7},
        ]
        with patch("services.extraction.schema_orchestrator.settings") as s:
            s.extraction_source_quoting_enabled = False
            s.extraction_conflict_detection_enabled = True
            merged = orchestrator._merge_chunk_results(chunk_results, mixed_group)

        conflicts = merged.get("_conflicts", {})
        assert "employees" not in conflicts


class TestBooleanConflict:
    """Test boolean conflict detection (not unanimous)."""

    def test_boolean_split_recorded(self, orchestrator, mixed_group):
        """Boolean disagreement should be recorded."""
        chunk_results = [
            {"is_public": True, "confidence": 0.8},
            {"is_public": False, "confidence": 0.7},
        ]
        with patch("services.extraction.schema_orchestrator.settings") as s:
            s.extraction_source_quoting_enabled = False
            s.extraction_conflict_detection_enabled = True
            merged = orchestrator._merge_chunk_results(chunk_results, mixed_group)

        assert "_conflicts" in merged
        assert "is_public" in merged["_conflicts"]
        assert merged["_conflicts"]["is_public"]["resolution"] == "majority_vote"

    def test_boolean_unanimous_no_conflict(self, orchestrator, mixed_group):
        """Unanimous boolean should not be flagged."""
        chunk_results = [
            {"is_public": True, "confidence": 0.8},
            {"is_public": True, "confidence": 0.7},
        ]
        with patch("services.extraction.schema_orchestrator.settings") as s:
            s.extraction_source_quoting_enabled = False
            s.extraction_conflict_detection_enabled = True
            merged = orchestrator._merge_chunk_results(chunk_results, mixed_group)

        conflicts = merged.get("_conflicts", {})
        assert "is_public" not in conflicts


class TestTextConflict:
    """Test text/enum conflict detection (>1 unique value)."""

    def test_text_disagreement_recorded(self, orchestrator, mixed_group):
        chunk_results = [
            {"name": "Acme Corp", "confidence": 0.8},
            {"name": "ACME Corporation", "confidence": 0.7},
        ]
        with patch("services.extraction.schema_orchestrator.settings") as s:
            s.extraction_source_quoting_enabled = False
            s.extraction_conflict_detection_enabled = True
            merged = orchestrator._merge_chunk_results(chunk_results, mixed_group)

        assert "_conflicts" in merged
        assert "name" in merged["_conflicts"]
        assert merged["_conflicts"]["name"]["resolution"] == "concat"

    def test_identical_text_no_conflict(self, orchestrator, mixed_group):
        chunk_results = [
            {"name": "Acme Corp", "confidence": 0.8},
            {"name": "Acme Corp", "confidence": 0.7},
        ]
        with patch("services.extraction.schema_orchestrator.settings") as s:
            s.extraction_source_quoting_enabled = False
            s.extraction_conflict_detection_enabled = True
            merged = orchestrator._merge_chunk_results(chunk_results, mixed_group)

        conflicts = merged.get("_conflicts", {})
        assert "name" not in conflicts


class TestConflictFlag:
    """Test that flag controls conflict detection."""

    def test_flag_off_no_conflicts_key(self, orchestrator, mixed_group):
        """When disabled, no _conflicts key should appear."""
        chunk_results = [
            {"name": "Acme", "employees": 100, "confidence": 0.8},
            {"name": "ACME", "employees": 200, "confidence": 0.7},
        ]
        with patch("services.extraction.schema_orchestrator.settings") as s:
            s.extraction_source_quoting_enabled = False
            s.extraction_conflict_detection_enabled = False
            merged = orchestrator._merge_chunk_results(chunk_results, mixed_group)

        assert "_conflicts" not in merged

    def test_single_chunk_no_conflicts(self, orchestrator, mixed_group):
        """Single chunk should never produce conflicts."""
        chunk_results = [
            {"name": "Acme", "employees": 100, "confidence": 0.8},
        ]
        with patch("services.extraction.schema_orchestrator.settings") as s:
            s.extraction_source_quoting_enabled = False
            s.extraction_conflict_detection_enabled = True
            merged = orchestrator._merge_chunk_results(chunk_results, mixed_group)

        assert "_conflicts" not in merged
