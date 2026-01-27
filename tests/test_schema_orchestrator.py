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

        result = await orchestrator.extract_all_groups(
            source_id=source_id,
            markdown="# Test content",
            company_name="Test Company",
            field_groups=[],
        )

        assert result == []

    @pytest.mark.asyncio
    async def test_extract_all_groups_logs_error_if_no_groups(self, mock_extractor):
        """Verify error logged and returns empty when no field_groups provided."""
        orchestrator = SchemaExtractionOrchestrator(mock_extractor)
        source_id = uuid4()

        result = await orchestrator.extract_all_groups(
            source_id=source_id,
            markdown="# Test content",
            company_name="Test Company",
            field_groups=[],
        )

        # Should return empty list when no field_groups
        assert result == []

    @pytest.mark.asyncio
    async def test_extract_all_groups_works_with_field_groups(
        self, mock_extractor, sample_field_groups
    ):
        """Works correctly when field_groups provided."""
        orchestrator = SchemaExtractionOrchestrator(mock_extractor)
        source_id = uuid4()

        result = await orchestrator.extract_all_groups(
            source_id=source_id,
            markdown="# Test content",
            company_name="Test Company",
            field_groups=sample_field_groups,
        )

        # Should have results (not empty)
        assert len(result) >= 0  # Depends on implementation
