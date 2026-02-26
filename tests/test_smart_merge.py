"""Tests for SmartMergeService confidence filtering."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from services.reports.smart_merge import MergeCandidate, SmartMergeService


@pytest.fixture
def merge_service():
    """Create SmartMergeService with mock LLM client."""
    llm_client = MagicMock()
    llm_client.complete = AsyncMock()
    return SmartMergeService(llm_client=llm_client, min_confidence=0.3)


@pytest.fixture
def column_meta():
    """Create mock column metadata."""
    meta = MagicMock()
    meta.field_type = "text"
    meta.description = "Company name"
    meta.enum_values = None
    return meta


class TestConfidenceNoneBypass:
    """Test Phase 3C: confidence=None excluded from merge."""

    async def test_none_confidence_excluded(self, merge_service, column_meta):
        """Candidates with confidence=None should be excluded."""
        candidates = [
            MergeCandidate(value="Acme", source_url="url1", source_title="Page1", confidence=None),
            MergeCandidate(value="Acme Corp", source_url="url2", source_title="Page2", confidence=0.8),
        ]

        result = await merge_service.merge_column("name", column_meta, candidates)

        # Only the 0.8 candidate should pass → single value short-circuit
        assert result.value == "Acme Corp"
        assert result.confidence == 0.8

    async def test_all_none_confidence_returns_null(self, merge_service, column_meta):
        """All None confidence → all filtered → null result."""
        candidates = [
            MergeCandidate(value="Acme", source_url="url1", source_title="Page1", confidence=None),
            MergeCandidate(value="Acme Corp", source_url="url2", source_title="Page2", confidence=None),
        ]

        result = await merge_service.merge_column("name", column_meta, candidates)

        assert result.value is None
        assert result.confidence == 0.0

    async def test_low_confidence_excluded(self, merge_service, column_meta):
        """Candidates below min_confidence (0.3) should be excluded."""
        candidates = [
            MergeCandidate(value="Bad", source_url="url1", source_title="Page1", confidence=0.1),
            MergeCandidate(value="Good", source_url="url2", source_title="Page2", confidence=0.8),
        ]

        result = await merge_service.merge_column("name", column_meta, candidates)

        assert result.value == "Good"

    async def test_valid_confidence_passes(self, merge_service, column_meta):
        """Candidates at or above min_confidence should pass."""
        candidates = [
            MergeCandidate(value="OK", source_url="url1", source_title="Page1", confidence=0.3),
            MergeCandidate(value="Good", source_url="url2", source_title="Page2", confidence=0.8),
        ]

        result = await merge_service.merge_column("name", column_meta, candidates)

        # Both pass → multiple non-null → either identical check or LLM merge
        assert result.value is not None
