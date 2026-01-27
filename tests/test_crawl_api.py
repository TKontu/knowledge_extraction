"""Tests for crawl API endpoints."""

from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from orm_models import Job


class TestCreateCrawlJob:
    """Test POST /api/v1/crawl endpoint."""

    def test_crawl_endpoint_requires_authentication(self, client: TestClient):
        """Crawl endpoint should require API key."""
        response = client.post(
            "/api/v1/crawl",
            json={
                "url": "https://example.com",
                "project_id": str(uuid4()),
                "company": "TestCo",
            },
        )
        assert response.status_code == 401

    def test_creates_crawl_job_with_minimal_params(
        self, client: TestClient, valid_api_key: str
    ):
        """Should create crawl job with minimal required parameters."""
        project_id = str(uuid4())
        response = client.post(
            "/api/v1/crawl",
            headers={"X-API-Key": valid_api_key},
            json={
                "url": "https://example.com",
                "project_id": project_id,
                "company": "TestCo",
            },
        )
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "queued"
        assert data["url"] == "https://example.com"
        assert data["project_id"] == project_id
        assert data["company"] == "TestCo"
        assert data["max_depth"] == 2  # default
        assert data["limit"] == 100  # default
        assert "job_id" in data

    def test_creates_crawl_job_with_all_params(
        self, client: TestClient, valid_api_key: str
    ):
        """Should create crawl job with all optional parameters."""
        project_id = str(uuid4())
        response = client.post(
            "/api/v1/crawl",
            headers={"X-API-Key": valid_api_key},
            json={
                "url": "https://example.com",
                "project_id": project_id,
                "company": "TestCo",
                "max_depth": 3,
                "limit": 50,
                "include_paths": ["/blog/*", "/docs/*"],
                "exclude_paths": ["/login", "/admin/*"],
                "allow_backward_links": True,
                "auto_extract": False,
                "profile": "technical_docs",
            },
        )
        assert response.status_code == 202
        data = response.json()
        assert data["status"] == "queued"
        assert data["max_depth"] == 3
        assert data["limit"] == 50
        assert "job_id" in data

    def test_validates_url_required(self, client: TestClient, valid_api_key: str):
        """Should return 422 if url field is missing."""
        response = client.post(
            "/api/v1/crawl",
            headers={"X-API-Key": valid_api_key},
            json={
                "project_id": str(uuid4()),
                "company": "TestCo",
            },
        )
        assert response.status_code == 422

    def test_validates_project_id_required(
        self, client: TestClient, valid_api_key: str
    ):
        """Should return 422 if project_id field is missing."""
        response = client.post(
            "/api/v1/crawl",
            headers={"X-API-Key": valid_api_key},
            json={
                "url": "https://example.com",
                "company": "TestCo",
            },
        )
        assert response.status_code == 422

    def test_validates_company_required(self, client: TestClient, valid_api_key: str):
        """Should return 422 if company field is missing."""
        response = client.post(
            "/api/v1/crawl",
            headers={"X-API-Key": valid_api_key},
            json={
                "url": "https://example.com",
                "project_id": str(uuid4()),
            },
        )
        assert response.status_code == 422

    def test_validates_max_depth_range(self, client: TestClient, valid_api_key: str):
        """Should validate max_depth is between 1 and 10."""
        # Test too low
        response = client.post(
            "/api/v1/crawl",
            headers={"X-API-Key": valid_api_key},
            json={
                "url": "https://example.com",
                "project_id": str(uuid4()),
                "company": "TestCo",
                "max_depth": 0,
            },
        )
        assert response.status_code == 422

        # Test too high
        response = client.post(
            "/api/v1/crawl",
            headers={"X-API-Key": valid_api_key},
            json={
                "url": "https://example.com",
                "project_id": str(uuid4()),
                "company": "TestCo",
                "max_depth": 11,
            },
        )
        assert response.status_code == 422

    def test_validates_limit_range(self, client: TestClient, valid_api_key: str):
        """Should validate limit is between 1 and 1000."""
        # Test too low
        response = client.post(
            "/api/v1/crawl",
            headers={"X-API-Key": valid_api_key},
            json={
                "url": "https://example.com",
                "project_id": str(uuid4()),
                "company": "TestCo",
                "limit": 0,
            },
        )
        assert response.status_code == 422

        # Test too high
        response = client.post(
            "/api/v1/crawl",
            headers={"X-API-Key": valid_api_key},
            json={
                "url": "https://example.com",
                "project_id": str(uuid4()),
                "company": "TestCo",
                "limit": 1001,
            },
        )
        assert response.status_code == 422

    def test_returns_valid_job_id(self, client: TestClient, valid_api_key: str):
        """Job ID should be a valid UUID format."""
        response = client.post(
            "/api/v1/crawl",
            headers={"X-API-Key": valid_api_key},
            json={
                "url": "https://example.com",
                "project_id": str(uuid4()),
                "company": "TestCo",
            },
        )
        assert response.status_code == 202
        data = response.json()
        job_id = data["job_id"]
        # Verify UUID format
        assert isinstance(job_id, str)
        assert len(job_id) == 36
        assert job_id.count("-") == 4
        # Should be parseable as UUID
        UUID(job_id)

    def test_job_persists_to_database(
        self, client: TestClient, valid_api_key: str, db: Session
    ):
        """Creating a crawl job should persist it to the database."""
        project_id = str(uuid4())
        response = client.post(
            "/api/v1/crawl",
            headers={"X-API-Key": valid_api_key},
            json={
                "url": "https://example.com/docs",
                "project_id": project_id,
                "company": "TestCo",
                "max_depth": 3,
                "limit": 50,
                "profile": "api_docs",
            },
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]

        # Verify job exists in database
        db_job = db.query(Job).filter(Job.id == UUID(job_id)).first()
        assert db_job is not None
        assert str(db_job.id) == job_id
        assert db_job.type == "crawl"
        assert db_job.status == "queued"
        assert db_job.payload is not None
        assert db_job.payload["url"] == "https://example.com/docs"
        assert db_job.payload["project_id"] == project_id
        assert db_job.payload["company"] == "TestCo"
        assert db_job.payload["max_depth"] == 3
        assert db_job.payload["limit"] == 50
        assert db_job.payload["profile"] == "api_docs"
        assert db_job.payload["firecrawl_job_id"] is None


class TestGetCrawlStatus:
    """Test GET /api/v1/crawl/{job_id} endpoint."""

    def test_get_crawl_status_requires_authentication(self, client: TestClient):
        """GET crawl status endpoint should require API key."""
        job_id = str(uuid4())
        response = client.get(f"/api/v1/crawl/{job_id}")
        assert response.status_code == 401

    def test_returns_404_for_nonexistent_job(
        self, client: TestClient, valid_api_key: str
    ):
        """Should return 404 for non-existent job_id."""
        job_id = str(uuid4())
        response = client.get(
            f"/api/v1/crawl/{job_id}",
            headers={"X-API-Key": valid_api_key},
        )
        assert response.status_code == 404
        data = response.json()
        assert "detail" in data

    def test_validates_job_id_format(self, client: TestClient, valid_api_key: str):
        """Should return 422 for invalid UUID format."""
        invalid_job_id = "not-a-valid-uuid"
        response = client.get(
            f"/api/v1/crawl/{invalid_job_id}",
            headers={"X-API-Key": valid_api_key},
        )
        assert response.status_code == 422

    def test_returns_queued_status(self, client: TestClient, valid_api_key: str):
        """Should return job with queued status."""
        # Create a crawl job
        create_response = client.post(
            "/api/v1/crawl",
            headers={"X-API-Key": valid_api_key},
            json={
                "url": "https://example.com",
                "project_id": str(uuid4()),
                "company": "TestCo",
            },
        )
        job_id = create_response.json()["job_id"]

        # Get its status
        response = client.get(
            f"/api/v1/crawl/{job_id}",
            headers={"X-API-Key": valid_api_key},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["job_id"] == job_id
        assert data["status"] == "queued"

    def test_includes_all_required_fields(self, client: TestClient, valid_api_key: str):
        """Should return job with all required fields."""
        project_id = str(uuid4())
        create_response = client.post(
            "/api/v1/crawl",
            headers={"X-API-Key": valid_api_key},
            json={
                "url": "https://example.com/docs",
                "project_id": project_id,
                "company": "TestCo",
                "max_depth": 3,
                "limit": 50,
            },
        )
        job_id = create_response.json()["job_id"]

        # Get job status
        response = client.get(
            f"/api/v1/crawl/{job_id}",
            headers={"X-API-Key": valid_api_key},
        )
        assert response.status_code == 200
        data = response.json()

        # Check all required fields
        assert data["job_id"] == job_id
        assert data["status"] == "queued"
        assert data["url"] == "https://example.com/docs"
        assert "created_at" in data
        assert data["pages_total"] is None  # Not started yet
        assert data["pages_completed"] is None
        assert data["sources_created"] is None
        assert data["error"] is None
        assert data["completed_at"] is None

    def test_reads_from_database(
        self, client: TestClient, valid_api_key: str, db: Session
    ):
        """Getting crawl status should read from the database."""
        project_id = str(uuid4())
        create_response = client.post(
            "/api/v1/crawl",
            headers={"X-API-Key": valid_api_key},
            json={
                "url": "https://example.com",
                "project_id": project_id,
                "company": "TestCo",
            },
        )
        job_id = create_response.json()["job_id"]

        # Get job status via API
        get_response = client.get(
            f"/api/v1/crawl/{job_id}",
            headers={"X-API-Key": valid_api_key},
        )
        assert get_response.status_code == 200
        data = get_response.json()

        # Verify data matches what's in database
        db_job = db.query(Job).filter(Job.id == UUID(job_id)).first()
        assert db_job is not None
        assert data["job_id"] == str(db_job.id)
        assert data["status"] == db_job.status
        assert data["url"] == db_job.payload["url"]
        assert data["created_at"] == db_job.created_at.isoformat()

    def test_returns_404_for_scrape_job(
        self, client: TestClient, valid_api_key: str, db: Session
    ):
        """Should return 404 when querying for a scrape job (wrong type)."""
        # Create a scrape job
        project_id = str(uuid4())
        scrape_response = client.post(
            "/api/v1/scrape",
            headers={"X-API-Key": valid_api_key},
            json={
                "urls": ["https://example.com"],
                "project_id": project_id,
                "company": "TestCo",
            },
        )
        scrape_job_id = scrape_response.json()["job_id"]

        # Try to get it as crawl job
        response = client.get(
            f"/api/v1/crawl/{scrape_job_id}",
            headers={"X-API-Key": valid_api_key},
        )
        assert response.status_code == 404


class TestCrawlJobPersistence:
    """Test that crawl jobs persist correctly."""

    def test_job_persists_across_multiple_get_requests(
        self, client: TestClient, valid_api_key: str
    ):
        """Crawl job should persist and be retrievable multiple times."""
        project_id = str(uuid4())
        create_response = client.post(
            "/api/v1/crawl",
            headers={"X-API-Key": valid_api_key},
            json={
                "url": "https://example.com",
                "project_id": project_id,
                "company": "TestCo",
                "profile": "technical_specs",
            },
        )
        job_id = create_response.json()["job_id"]

        # Get the job multiple times
        for _ in range(3):
            response = client.get(
                f"/api/v1/crawl/{job_id}",
                headers={"X-API-Key": valid_api_key},
            )
            assert response.status_code == 200
            data = response.json()
            assert data["job_id"] == job_id
            assert data["url"] == "https://example.com"

    def test_multiple_crawl_jobs_persist_independently(
        self, client: TestClient, valid_api_key: str, db: Session
    ):
        """Multiple crawl jobs should be stored independently in database."""
        job_ids = []
        for i in range(3):
            project_id = str(uuid4())
            response = client.post(
                "/api/v1/crawl",
                headers={"X-API-Key": valid_api_key},
                json={
                    "url": f"https://example{i}.com",
                    "project_id": project_id,
                    "company": f"Company{i}",
                },
            )
            assert response.status_code == 202
            job_ids.append(response.json()["job_id"])

        # Verify all jobs exist in database with correct data
        for i, job_id in enumerate(job_ids):
            db_job = db.query(Job).filter(Job.id == UUID(job_id)).first()
            assert db_job is not None
            assert db_job.type == "crawl"
            assert db_job.payload["company"] == f"Company{i}"
            assert db_job.payload["url"] == f"https://example{i}.com"
