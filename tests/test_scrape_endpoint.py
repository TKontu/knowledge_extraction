import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from uuid import UUID, uuid4

from orm_models import Job

# Shared project_id for all tests
TEST_PROJECT_ID = str(uuid4())


class TestScrapeEndpoint:
    """Test POST /api/v1/scrape endpoint."""

    def test_scrape_endpoint_requires_authentication(self, client: TestClient):
        """Scrape endpoint should require API key."""
        response = client.post(
            "/api/v1/scrape",
            json={"urls": ["https://example.com"], "company": "Example Inc", "project_id": TEST_PROJECT_ID},
        )
        assert response.status_code == 401

    def test_scrape_endpoint_accepts_valid_request(
        self, client: TestClient, valid_api_key: str
    ):
        """Should accept valid scrape request and return 202 Accepted."""
        response = client.post(
            "/api/v1/scrape",
            headers={"X-API-Key": valid_api_key},
            json={
                "urls": ["https://example.com/docs"],
                "company": "Example Inc",
                "project_id": TEST_PROJECT_ID,
            },
        )
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data
        assert "status" in data
        assert data["status"] == "queued"

    def test_scrape_endpoint_returns_valid_job_id(
        self, client: TestClient, valid_api_key: str
    ):
        """Job ID should be a valid UUID format."""
        response = client.post(
            "/api/v1/scrape",
            headers={"X-API-Key": valid_api_key},
            json={
                "urls": ["https://example.com"],
                "company": "TestCo",
                "project_id": TEST_PROJECT_ID,
            },
        )
        assert response.status_code == 202
        data = response.json()
        job_id = data["job_id"]
        # Basic UUID format check (8-4-4-4-12 hex chars)
        assert isinstance(job_id, str)
        assert len(job_id) == 36
        assert job_id.count("-") == 4

    def test_scrape_endpoint_accepts_optional_profile(
        self, client: TestClient, valid_api_key: str
    ):
        """Should accept optional profile parameter."""
        response = client.post(
            "/api/v1/scrape",
            headers={"X-API-Key": valid_api_key},
            json={
                "urls": ["https://example.com"],
                "company": "TestCo",
                "project_id": TEST_PROJECT_ID,
                "profile": "api_docs",
            },
        )
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data

    def test_scrape_endpoint_validates_urls_required(
        self, client: TestClient, valid_api_key: str
    ):
        """Should return 422 if urls field is missing."""
        response = client.post(
            "/api/v1/scrape",
            headers={"X-API-Key": valid_api_key},
            json={"company": "TestCo", "project_id": TEST_PROJECT_ID},
        )
        assert response.status_code == 422

    def test_scrape_endpoint_validates_company_required(
        self, client: TestClient, valid_api_key: str
    ):
        """Should return 422 if company field is missing."""
        response = client.post(
            "/api/v1/scrape",
            headers={"X-API-Key": valid_api_key},
            json={"urls": ["https://example.com"], "project_id": TEST_PROJECT_ID},
        )
        assert response.status_code == 422

    def test_scrape_endpoint_validates_urls_is_list(
        self, client: TestClient, valid_api_key: str
    ):
        """Should return 422 if urls is not a list."""
        response = client.post(
            "/api/v1/scrape",
            headers={"X-API-Key": valid_api_key},
            json={
                "urls": "https://example.com",  # String instead of list
                "company": "TestCo",
                "project_id": TEST_PROJECT_ID,
            },
        )
        assert response.status_code == 422

    def test_scrape_endpoint_validates_urls_not_empty(
        self, client: TestClient, valid_api_key: str
    ):
        """Should return 422 if urls list is empty."""
        response = client.post(
            "/api/v1/scrape",
            headers={"X-API-Key": valid_api_key},
            json={
                "urls": [],
                "company": "TestCo",
                "project_id": TEST_PROJECT_ID,
            },
        )
        assert response.status_code == 422

    def test_scrape_endpoint_accepts_multiple_urls(
        self, client: TestClient, valid_api_key: str
    ):
        """Should accept multiple URLs in request."""
        response = client.post(
            "/api/v1/scrape",
            headers={"X-API-Key": valid_api_key},
            json={
                "urls": [
                    "https://example.com/docs",
                    "https://example.com/api",
                    "https://example.com/guides",
                ],
                "company": "TestCo",
                "project_id": TEST_PROJECT_ID,
            },
        )
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data
        assert "url_count" in data
        assert data["url_count"] == 3

    def test_scrape_endpoint_response_includes_metadata(
        self, client: TestClient, valid_api_key: str
    ):
        """Response should include useful metadata."""
        response = client.post(
            "/api/v1/scrape",
            headers={"X-API-Key": valid_api_key},
            json={
                "urls": ["https://example.com"],
                "company": "TestCo",
                "project_id": TEST_PROJECT_ID,
                "profile": "technical_specs",
            },
        )
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data
        assert "status" in data
        assert "url_count" in data
        assert "company" in data
        assert data["company"] == "TestCo"
        assert data["url_count"] == 1


class TestGetJobStatus:
    """Test GET /api/v1/scrape/{job_id} endpoint."""

    def test_get_job_status_requires_authentication(self, client: TestClient):
        """GET job status endpoint should require API key."""
        job_id = "123e4567-e89b-12d3-a456-426614174000"
        response = client.get(f"/api/v1/scrape/{job_id}")
        assert response.status_code == 401

    def test_get_job_status_returns_404_for_nonexistent_job(
        self, client: TestClient, valid_api_key: str
    ):
        """Should return 404 for non-existent job_id."""
        job_id = "123e4567-e89b-12d3-a456-426614174000"
        response = client.get(
            f"/api/v1/scrape/{job_id}",
            headers={"X-API-Key": valid_api_key},
        )
        assert response.status_code == 404
        data = response.json()
        assert "detail" in data

    def test_get_job_status_validates_job_id_format(
        self, client: TestClient, valid_api_key: str
    ):
        """Should return 422 for invalid UUID format."""
        invalid_job_id = "not-a-valid-uuid"
        response = client.get(
            f"/api/v1/scrape/{invalid_job_id}",
            headers={"X-API-Key": valid_api_key},
        )
        assert response.status_code == 422

    def _create_scrape_job(self, client, valid_api_key):
        """Helper to create a scrape job and return the response."""
        return client.post(
            "/api/v1/scrape",
            headers={"X-API-Key": valid_api_key},
            json={
                "urls": ["https://example.com"],
                "company": "TestCo",
                "project_id": TEST_PROJECT_ID,
            },
        )

    def test_get_job_status_returns_queued_status(
        self, client: TestClient, valid_api_key: str
    ):
        """Should return job with queued status."""
        create_response = self._create_scrape_job(client, valid_api_key)
        job_id = create_response.json()["job_id"]

        response = client.get(
            f"/api/v1/scrape/{job_id}",
            headers={"X-API-Key": valid_api_key},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == job_id
        assert data["status"] == "queued"

    def test_get_job_status_includes_all_fields(
        self, client: TestClient, valid_api_key: str
    ):
        """Should return job with all required fields."""
        create_response = client.post(
            "/api/v1/scrape",
            headers={"X-API-Key": valid_api_key},
            json={
                "urls": ["https://example.com/docs", "https://example.com/api"],
                "company": "TestCo",
                "project_id": TEST_PROJECT_ID,
                "profile": "api_docs",
            },
        )
        job_id = create_response.json()["job_id"]

        response = client.get(
            f"/api/v1/scrape/{job_id}",
            headers={"X-API-Key": valid_api_key},
        )
        assert response.status_code == 200
        data = response.json()

        assert "job_id" in data
        assert "status" in data
        assert "company" in data
        assert "url_count" in data
        assert "profile" in data
        assert "created_at" in data

        assert data["job_id"] == job_id
        assert data["company"] == "TestCo"
        assert data["url_count"] == 2
        assert data["profile"] == "api_docs"

    def test_get_job_status_handles_different_statuses(
        self, client: TestClient, valid_api_key: str
    ):
        """Should handle different job statuses correctly."""
        create_response = self._create_scrape_job(client, valid_api_key)
        job_id = create_response.json()["job_id"]

        response = client.get(
            f"/api/v1/scrape/{job_id}",
            headers={"X-API-Key": valid_api_key},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["status"] in ["queued", "running", "completed", "failed"]

    def test_get_job_status_with_no_profile(
        self, client: TestClient, valid_api_key: str
    ):
        """Should handle jobs without profile."""
        create_response = self._create_scrape_job(client, valid_api_key)
        job_id = create_response.json()["job_id"]

        response = client.get(
            f"/api/v1/scrape/{job_id}",
            headers={"X-API-Key": valid_api_key},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["profile"] is None


class TestJobPersistence:
    """Test that jobs are persisted to the database."""

    def test_create_job_persists_to_database(
        self, client: TestClient, valid_api_key: str, db: Session
    ):
        """Creating a job should persist it to the database."""
        response = client.post(
            "/api/v1/scrape",
            headers={"X-API-Key": valid_api_key},
            json={
                "urls": ["https://example.com/docs"],
                "company": "TestCo",
                "project_id": TEST_PROJECT_ID,
                "profile": "api_docs",
            },
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]

        db_job = db.query(Job).filter(Job.id == UUID(job_id)).first()
        assert db_job is not None
        assert str(db_job.id) == job_id
        assert db_job.type == "scrape"
        assert db_job.status == "queued"
        assert db_job.payload is not None
        assert db_job.payload["company"] == "TestCo"
        assert db_job.payload["urls"] == ["https://example.com/docs"]
        assert db_job.payload["profile"] == "api_docs"

    def test_get_job_reads_from_database(
        self, client: TestClient, valid_api_key: str, db: Session
    ):
        """Getting job status should read from the database."""
        create_response = client.post(
            "/api/v1/scrape",
            headers={"X-API-Key": valid_api_key},
            json={
                "urls": ["https://example.com/api", "https://example.com/docs"],
                "company": "TestCo",
                "project_id": TEST_PROJECT_ID,
            },
        )
        job_id = create_response.json()["job_id"]

        get_response = client.get(
            f"/api/v1/scrape/{job_id}",
            headers={"X-API-Key": valid_api_key},
        )
        assert get_response.status_code == 200
        data = get_response.json()

        db_job = db.query(Job).filter(Job.id == UUID(job_id)).first()
        assert db_job is not None
        assert data["job_id"] == str(db_job.id)
        assert data["status"] == db_job.status
        assert data["company"] == db_job.payload["company"]
        assert data["url_count"] == len(db_job.payload["urls"])

    def test_job_persists_across_multiple_get_requests(
        self, client: TestClient, valid_api_key: str, db: Session
    ):
        """Job should persist and be retrievable multiple times."""
        create_response = client.post(
            "/api/v1/scrape",
            headers={"X-API-Key": valid_api_key},
            json={
                "urls": ["https://example.com"],
                "company": "TestCo",
                "project_id": TEST_PROJECT_ID,
                "profile": "technical_specs",
            },
        )
        job_id = create_response.json()["job_id"]

        for _ in range(3):
            response = client.get(
                f"/api/v1/scrape/{job_id}",
                headers={"X-API-Key": valid_api_key},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["job_id"] == job_id
            assert data["company"] == "TestCo"
            assert data["profile"] == "technical_specs"

        db_jobs = db.query(Job).filter(Job.id == UUID(job_id)).all()
        assert len(db_jobs) == 1

    def test_multiple_jobs_persist_independently(
        self, client: TestClient, valid_api_key: str, db: Session
    ):
        """Multiple jobs should be stored independently in database."""
        job_ids = []
        for i in range(3):
            response = client.post(
                "/api/v1/scrape",
                headers={"X-API-Key": valid_api_key},
                json={
                    "urls": [f"https://example{i}.com"],
                    "company": f"Company{i}",
                    "project_id": TEST_PROJECT_ID,
                },
            )
            assert response.status_code == 202
            job_ids.append(response.json()["job_id"])

        for i, job_id in enumerate(job_ids):
            db_job = db.query(Job).filter(Job.id == UUID(job_id)).first()
            assert db_job is not None
            assert db_job.payload["company"] == f"Company{i}"
            assert db_job.payload["urls"] == [f"https://example{i}.com"]

    def test_get_nonexistent_job_returns_404(
        self, client: TestClient, valid_api_key: str
    ):
        """Getting a nonexistent job should return 404."""
        fake_job_id = "123e4567-e89b-12d3-a456-426614174999"
        response = client.get(
            f"/api/v1/scrape/{fake_job_id}",
            headers={"X-API-Key": valid_api_key},
        )
        assert response.status_code == 404

    def test_job_includes_created_at_timestamp(
        self, client: TestClient, valid_api_key: str, db: Session
    ):
        """Job should include created_at timestamp from database."""
        response = client.post(
            "/api/v1/scrape",
            headers={"X-API-Key": valid_api_key},
            json={
                "urls": ["https://example.com"],
                "company": "TestCo",
                "project_id": TEST_PROJECT_ID,
            },
        )
        job_id = response.json()["job_id"]

        get_response = client.get(
            f"/api/v1/scrape/{job_id}",
            headers={"X-API-Key": valid_api_key},
        )
        data = get_response.json()

        db_job = db.query(Job).filter(Job.id == UUID(job_id)).first()
        assert "created_at" in data
        assert data["created_at"] == db_job.created_at.isoformat()
