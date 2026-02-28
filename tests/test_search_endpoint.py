"""Tests for search API endpoint."""

from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from orm_models import Extraction, Project, Source
from services.storage.search import ExtractionSearchResult


@pytest.fixture
def test_project(db: Session):
    """Create a test project."""
    project_name = f"test_search_project_{uuid4().hex[:8]}"
    project = Project(
        name=project_name,
        description="Test project for search",
        extraction_schema={
            "name": "test_extraction",
            "fields": [
                {"name": "text", "type": "text", "required": True},
                {"name": "category", "type": "enum", "values": ["pricing", "features"]},
            ],
        },
    )
    db.add(project)
    db.flush()
    db.refresh(project)

    return project


@pytest.fixture
def test_sources(db: Session, test_project: Project):
    """Create test sources."""
    sources = []
    for i, group in enumerate(["CompanyA", "CompanyB"]):
        source = Source(
            project_id=test_project.id,
            source_type="web",
            uri=f"https://example.com/doc{i}_{uuid4().hex[:8]}",
            source_group=group,
            status="completed",
        )
        db.add(source)
        sources.append(source)
    db.flush()
    for source in sources:
        db.refresh(source)
    return sources


@pytest.fixture
def test_extractions(db: Session, test_project: Project, test_sources: list[Source]):
    """Create test extractions."""
    extractions = []
    for i, source in enumerate(test_sources):
        extraction = Extraction(
            project_id=test_project.id,
            source_id=source.id,
            data={"text": f"Sample text {i}", "category": "pricing" if i == 0 else "features"},
            extraction_type="test_extraction",
            source_group=source.source_group,
            confidence=0.95 - (i * 0.1),
        )
        db.add(extraction)
        extractions.append(extraction)
    db.flush()
    for extraction in extractions:
        db.refresh(extraction)
    return extractions


class TestSearchEndpoint:
    """Tests for POST /api/v1/projects/{project_id}/search."""

    @patch("api.v1.search.SearchService")
    def test_search_returns_results(
        self,
        mock_search_service_class,
        client: TestClient,
        valid_api_key: str,
        test_project: Project,
        test_extractions: list[Extraction],
    ):
        """Valid search should return results."""
        # Mock the search service instance
        mock_service = MagicMock()
        mock_search_service_class.return_value = mock_service

        # Mock search results
        extraction = test_extractions[0]
        mock_results = [
            ExtractionSearchResult(
                extraction_id=extraction.id,
                score=0.95,
                data=extraction.data,
                source_group=extraction.source_group,
                source_uri="https://example.com/doc0",
                confidence=extraction.confidence,
            )
        ]
        mock_service.search = AsyncMock(return_value=mock_results)

        # Make request
        response = client.post(
            f"/api/v1/projects/{test_project.id}/search",
            headers={"X-API-Key": valid_api_key},
            json={"query": "test query", "limit": 10},
        )

        # Assertions
        assert response.status_code == 200
        data = response.json()
        assert "results" in data
        assert "query" in data
        assert "total" in data
        assert data["query"] == "test query"
        assert data["total"] == 1
        assert len(data["results"]) == 1
        assert data["results"][0]["score"] == 0.95
        assert data["results"][0]["extraction_id"] == str(extraction.id)

    def test_search_project_not_found(
        self, client: TestClient, valid_api_key: str
    ):
        """Should return 404 for non-existent project."""
        fake_project_id = str(uuid4())
        response = client.post(
            f"/api/v1/projects/{fake_project_id}/search",
            headers={"X-API-Key": valid_api_key},
            json={"query": "test query"},
        )
        assert response.status_code == 404
        data = response.json()
        assert "detail" in data
        assert fake_project_id in data["detail"]

    def test_search_invalid_project_id(
        self, client: TestClient, valid_api_key: str
    ):
        """Should return 422 for invalid UUID format."""
        response = client.post(
            "/api/v1/projects/not-a-valid-uuid/search",
            headers={"X-API-Key": valid_api_key},
            json={"query": "test query"},
        )
        assert response.status_code == 422
        data = response.json()
        assert "detail" in data

    @patch("api.v1.search.SearchService")
    def test_search_with_source_group_filter(
        self,
        mock_search_service_class,
        client: TestClient,
        valid_api_key: str,
        test_project: Project,
        test_extractions: list[Extraction],
    ):
        """Should filter by source_group."""
        mock_service = MagicMock()
        mock_search_service_class.return_value = mock_service

        # Mock filtered results
        extraction = test_extractions[0]
        mock_results = [
            ExtractionSearchResult(
                extraction_id=extraction.id,
                score=0.95,
                data=extraction.data,
                source_group="CompanyA",
                source_uri="https://example.com/doc0",
                confidence=extraction.confidence,
            )
        ]
        mock_service.search = AsyncMock(return_value=mock_results)

        response = client.post(
            f"/api/v1/projects/{test_project.id}/search",
            headers={"X-API-Key": valid_api_key},
            json={"query": "test query", "source_groups": ["CompanyA"]},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["results"][0]["source_group"] == "CompanyA"

        # Verify search was called with correct filters
        mock_service.search.assert_called_once()
        call_kwargs = mock_service.search.call_args.kwargs
        assert call_kwargs["source_groups"] == ["CompanyA"]

    @patch("api.v1.search.SearchService")
    def test_search_with_jsonb_filters(
        self,
        mock_search_service_class,
        client: TestClient,
        valid_api_key: str,
        test_project: Project,
        test_extractions: list[Extraction],
    ):
        """Should filter by JSONB data."""
        mock_service = MagicMock()
        mock_search_service_class.return_value = mock_service

        extraction = test_extractions[0]
        mock_results = [
            ExtractionSearchResult(
                extraction_id=extraction.id,
                score=0.95,
                data={"text": "Sample text 0", "category": "pricing"},
                source_group="CompanyA",
                source_uri="https://example.com/doc0",
                confidence=extraction.confidence,
            )
        ]
        mock_service.search = AsyncMock(return_value=mock_results)

        response = client.post(
            f"/api/v1/projects/{test_project.id}/search",
            headers={"X-API-Key": valid_api_key},
            json={"query": "pricing info", "filters": {"category": "pricing"}},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["results"][0]["data"]["category"] == "pricing"

        # Verify filters were passed
        mock_service.search.assert_called_once()
        call_kwargs = mock_service.search.call_args.kwargs
        assert call_kwargs["jsonb_filters"] == {"category": "pricing"}

    @patch("api.v1.search.SearchService")
    def test_search_empty_results(
        self,
        mock_search_service_class,
        client: TestClient,
        valid_api_key: str,
        test_project: Project,
    ):
        """Should return empty list when no matches."""
        mock_service = MagicMock()
        mock_search_service_class.return_value = mock_service
        mock_service.search = AsyncMock(return_value=[])

        response = client.post(
            f"/api/v1/projects/{test_project.id}/search",
            headers={"X-API-Key": valid_api_key},
            json={"query": "no matches"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["results"] == []
        assert data["total"] == 0
        assert data["query"] == "no matches"

    @patch("api.v1.search.SearchService")
    def test_search_respects_limit(
        self,
        mock_search_service_class,
        client: TestClient,
        valid_api_key: str,
        test_project: Project,
    ):
        """Should honor limit parameter."""
        mock_service = MagicMock()
        mock_search_service_class.return_value = mock_service

        # Create mock results (5 results but limit to 3)
        mock_results = [
            ExtractionSearchResult(
                extraction_id=uuid4(),
                score=0.9 - (i * 0.1),
                data={"text": f"Result {i}"},
                source_group="CompanyA",
                source_uri=f"https://example.com/doc{i}",
                confidence=0.95,
            )
            for i in range(3)
        ]
        mock_service.search = AsyncMock(return_value=mock_results)

        response = client.post(
            f"/api/v1/projects/{test_project.id}/search",
            headers={"X-API-Key": valid_api_key},
            json={"query": "test", "limit": 3},
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["results"]) == 3
        assert data["total"] == 3

        # Verify limit was passed to search
        mock_service.search.assert_called_once()
        call_kwargs = mock_service.search.call_args.kwargs
        assert call_kwargs["limit"] == 3

    def test_search_requires_authentication(
        self, client: TestClient, test_project: Project
    ):
        """Search endpoint should require API key."""
        response = client.post(
            f"/api/v1/projects/{test_project.id}/search",
            json={"query": "test"},
        )
        assert response.status_code == 401

    def test_search_validates_request_body(
        self, client: TestClient, valid_api_key: str, test_project: Project
    ):
        """Should validate request body schema."""
        # Empty query should fail
        response = client.post(
            f"/api/v1/projects/{test_project.id}/search",
            headers={"X-API-Key": valid_api_key},
            json={"query": ""},
        )
        assert response.status_code == 422

    def test_search_limit_validation(
        self, client: TestClient, valid_api_key: str, test_project: Project
    ):
        """Should validate limit bounds."""
        # Limit too high
        response = client.post(
            f"/api/v1/projects/{test_project.id}/search",
            headers={"X-API-Key": valid_api_key},
            json={"query": "test", "limit": 101},
        )
        assert response.status_code == 422

        # Limit too low
        response = client.post(
            f"/api/v1/projects/{test_project.id}/search",
            headers={"X-API-Key": valid_api_key},
            json={"query": "test", "limit": 0},
        )
        assert response.status_code == 422

    @patch("api.v1.search.SearchService")
    def test_search_response_includes_all_fields(
        self,
        mock_search_service_class,
        client: TestClient,
        valid_api_key: str,
        test_project: Project,
    ):
        """Response should include all required fields."""
        mock_service = MagicMock()
        mock_search_service_class.return_value = mock_service

        extraction_id = uuid4()
        mock_results = [
            ExtractionSearchResult(
                extraction_id=extraction_id,
                score=0.95,
                data={"text": "Test result", "category": "pricing"},
                source_group="CompanyA",
                source_uri="https://example.com/doc",
                confidence=0.88,
            )
        ]
        mock_service.search = AsyncMock(return_value=mock_results)

        response = client.post(
            f"/api/v1/projects/{test_project.id}/search",
            headers={"X-API-Key": valid_api_key},
            json={"query": "test query"},
        )

        assert response.status_code == 200
        data = response.json()

        # Check top-level fields
        assert "results" in data
        assert "query" in data
        assert "total" in data

        # Check result item fields
        result = data["results"][0]
        assert "extraction_id" in result
        assert "score" in result
        assert "data" in result
        assert "source_group" in result
        assert "source_uri" in result
        assert "confidence" in result

        assert result["extraction_id"] == str(extraction_id)
        assert result["score"] == 0.95
        assert result["source_group"] == "CompanyA"
        assert result["source_uri"] == "https://example.com/doc"
        assert result["confidence"] == 0.88

    @patch("api.v1.search.SearchService")
    def test_search_with_null_confidence(
        self,
        mock_search_service_class,
        client: TestClient,
        valid_api_key: str,
        test_project: Project,
    ):
        """Should handle null confidence values."""
        mock_service = MagicMock()
        mock_search_service_class.return_value = mock_service

        mock_results = [
            ExtractionSearchResult(
                extraction_id=uuid4(),
                score=0.95,
                data={"text": "Test result"},
                source_group="CompanyA",
                source_uri="https://example.com/doc",
                confidence=None,
            )
        ]
        mock_service.search = AsyncMock(return_value=mock_results)

        response = client.post(
            f"/api/v1/projects/{test_project.id}/search",
            headers={"X-API-Key": valid_api_key},
            json={"query": "test"},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["results"][0]["confidence"] is None
