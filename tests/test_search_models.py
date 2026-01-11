"""Tests for search API models."""

import pytest
from pydantic import ValidationError

from models import SearchRequest, SearchResponse, SearchResultItem


class TestSearchRequest:
    """Tests for SearchRequest model validation."""

    def test_search_request_validates_query_length(self):
        """Empty query should be rejected."""
        with pytest.raises(ValidationError) as exc_info:
            SearchRequest(query="")

        errors = exc_info.value.errors()
        assert any(e["loc"] == ("query",) for e in errors)

    def test_search_request_limit_bounds_min(self):
        """Limit must be at least 1."""
        with pytest.raises(ValidationError) as exc_info:
            SearchRequest(query="test query", limit=0)

        errors = exc_info.value.errors()
        assert any(
            e["loc"] == ("limit",) and "greater than or equal to 1" in str(e["msg"])
            for e in errors
        )

    def test_search_request_limit_bounds_max(self):
        """Limit must be at most 100."""
        with pytest.raises(ValidationError) as exc_info:
            SearchRequest(query="test query", limit=101)

        errors = exc_info.value.errors()
        assert any(
            e["loc"] == ("limit",) and "less than or equal to 100" in str(e["msg"])
            for e in errors
        )

    def test_search_request_accepts_valid_input(self):
        """Should accept valid input with all fields."""
        request = SearchRequest(
            query="test query",
            limit=50,
            source_groups=["group1", "group2"],
            filters={"category": "pricing"},
        )
        assert request.query == "test query"
        assert request.limit == 50
        assert request.source_groups == ["group1", "group2"]
        assert request.filters == {"category": "pricing"}

    def test_search_request_has_default_limit(self):
        """Should use default limit of 10."""
        request = SearchRequest(query="test query")
        assert request.limit == 10

    def test_search_request_query_max_length(self):
        """Query should not exceed 1000 characters."""
        long_query = "a" * 1001
        with pytest.raises(ValidationError) as exc_info:
            SearchRequest(query=long_query)

        errors = exc_info.value.errors()
        assert any(
            e["loc"] == ("query",) and "at most 1000 characters" in str(e["msg"])
            for e in errors
        )


class TestSearchResultItem:
    """Tests for SearchResultItem model."""

    def test_search_result_item_serialization(self):
        """Should serialize result item correctly."""
        item = SearchResultItem(
            extraction_id="123e4567-e89b-12d3-a456-426614174000",
            score=0.95,
            data={"text": "example", "category": "pricing"},
            source_group="CompanyA",
            source_uri="https://example.com/doc",
            confidence=0.88,
        )

        # Should serialize to dict
        data = item.model_dump()
        assert data["extraction_id"] == "123e4567-e89b-12d3-a456-426614174000"
        assert data["score"] == 0.95
        assert data["data"] == {"text": "example", "category": "pricing"}
        assert data["source_group"] == "CompanyA"
        assert data["source_uri"] == "https://example.com/doc"
        assert data["confidence"] == 0.88

    def test_search_result_item_optional_confidence(self):
        """Confidence field should be optional."""
        item = SearchResultItem(
            extraction_id="123e4567-e89b-12d3-a456-426614174000",
            score=0.95,
            data={"text": "example"},
            source_group="CompanyA",
            source_uri="https://example.com/doc",
            confidence=None,
        )
        assert item.confidence is None


class TestSearchResponse:
    """Tests for SearchResponse model."""

    def test_search_response_serialization(self):
        """Should serialize search response correctly."""
        results = [
            SearchResultItem(
                extraction_id="123e4567-e89b-12d3-a456-426614174000",
                score=0.95,
                data={"text": "example1"},
                source_group="CompanyA",
                source_uri="https://example.com/doc1",
                confidence=0.88,
            ),
            SearchResultItem(
                extraction_id="223e4567-e89b-12d3-a456-426614174000",
                score=0.85,
                data={"text": "example2"},
                source_group="CompanyB",
                source_uri="https://example.com/doc2",
                confidence=None,
            ),
        ]

        response = SearchResponse(
            results=results,
            query="test query",
            total=2,
        )

        data = response.model_dump()
        assert data["query"] == "test query"
        assert data["total"] == 2
        assert len(data["results"]) == 2
        assert data["results"][0]["score"] == 0.95
        assert data["results"][1]["score"] == 0.85

    def test_search_response_empty_results(self):
        """Should handle empty results list."""
        response = SearchResponse(
            results=[],
            query="no matches",
            total=0,
        )

        assert response.results == []
        assert response.total == 0
        assert response.query == "no matches"
