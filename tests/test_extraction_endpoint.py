import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from uuid import UUID, uuid4

from orm_models import Job, Project, Source


@pytest.fixture
def test_project(db: Session):
    """Create a test project with unique name."""
    # Use UUID in name to ensure uniqueness
    project_name = f"test_project_{uuid4().hex[:8]}"
    project = Project(
        name=project_name,
        description="Test project for extraction",
        extraction_schema={
            "name": "test_extraction",
            "fields": [
                {"name": "text", "type": "text", "required": True},
                {"name": "category", "type": "enum", "values": ["test"]},
            ],
        },
    )
    db.add(project)
    db.commit()
    db.refresh(project)

    yield project

    # Cleanup after test
    try:
        db.delete(project)
        db.commit()
    except Exception:
        db.rollback()


@pytest.fixture
def test_sources(db: Session, test_project: Project):
    """Create test sources."""
    sources = []
    for i in range(3):
        source = Source(
            project_id=test_project.id,
            source_type="web",
            uri=f"https://example.com/doc{i}_{uuid4().hex[:8]}",  # Unique URIs
            source_group="TestCo",
            status="pending" if i < 2 else "completed",
        )
        db.add(source)
        sources.append(source)
    db.commit()
    for source in sources:
        db.refresh(source)
    return sources


class TestCreateExtractionJob:
    """Test POST /api/v1/projects/{project_id}/extract endpoint."""

    def test_extract_endpoint_requires_authentication(
        self, client: TestClient, test_project: Project
    ):
        """Extract endpoint should require API key."""
        response = client.post(
            f"/api/v1/projects/{test_project.id}/extract",
            json={},
        )
        assert response.status_code == 401

    def test_extract_endpoint_accepts_valid_request(
        self, client: TestClient, valid_api_key: str, test_project: Project
    ):
        """Should accept valid extraction request and return 202 Accepted."""
        response = client.post(
            f"/api/v1/projects/{test_project.id}/extract",
            headers={"X-API-Key": valid_api_key},
            json={},
        )
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data
        assert "status" in data
        assert data["status"] == "queued"
        assert data["project_id"] == str(test_project.id)

    def test_extract_endpoint_returns_valid_job_id(
        self, client: TestClient, valid_api_key: str, test_project: Project
    ):
        """Job ID should be a valid UUID format."""
        response = client.post(
            f"/api/v1/projects/{test_project.id}/extract",
            headers={"X-API-Key": valid_api_key},
            json={},
        )
        assert response.status_code == 202
        data = response.json()
        job_id = data["job_id"]
        # Basic UUID format check
        assert isinstance(job_id, str)
        assert len(job_id) == 36
        assert job_id.count("-") == 4

    def test_extract_endpoint_validates_project_id_format(
        self, client: TestClient, valid_api_key: str
    ):
        """Should return 422 for invalid project UUID format."""
        response = client.post(
            "/api/v1/projects/not-a-valid-uuid/extract",
            headers={"X-API-Key": valid_api_key},
            json={},
        )
        assert response.status_code == 422

    def test_extract_endpoint_returns_404_for_nonexistent_project(
        self, client: TestClient, valid_api_key: str
    ):
        """Should return 404 for non-existent project_id."""
        fake_project_id = str(uuid4())
        response = client.post(
            f"/api/v1/projects/{fake_project_id}/extract",
            headers={"X-API-Key": valid_api_key},
            json={},
        )
        assert response.status_code == 404
        data = response.json()
        assert "detail" in data

    def test_extract_endpoint_accepts_source_ids(
        self,
        client: TestClient,
        valid_api_key: str,
        test_project: Project,
        test_sources: list[Source],
    ):
        """Should accept specific source_ids for extraction."""
        source_id = str(test_sources[0].id)
        response = client.post(
            f"/api/v1/projects/{test_project.id}/extract",
            headers={"X-API-Key": valid_api_key},
            json={"source_ids": [source_id]},
        )
        assert response.status_code == 202
        data = response.json()
        assert data["source_count"] == 1

    def test_extract_endpoint_validates_source_ids_format(
        self, client: TestClient, valid_api_key: str, test_project: Project
    ):
        """Should return 422 for invalid source_id format."""
        response = client.post(
            f"/api/v1/projects/{test_project.id}/extract",
            headers={"X-API-Key": valid_api_key},
            json={"source_ids": ["not-a-valid-uuid"]},
        )
        assert response.status_code == 422

    def test_extract_endpoint_validates_source_exists(
        self, client: TestClient, valid_api_key: str, test_project: Project
    ):
        """Should return 404 for non-existent source_id."""
        fake_source_id = str(uuid4())
        response = client.post(
            f"/api/v1/projects/{test_project.id}/extract",
            headers={"X-API-Key": valid_api_key},
            json={"source_ids": [fake_source_id]},
        )
        assert response.status_code == 404

    def test_extract_endpoint_validates_source_belongs_to_project(
        self,
        client: TestClient,
        valid_api_key: str,
        test_project: Project,
        test_sources: list[Source],
        db: Session,
    ):
        """Should return 400 if source doesn't belong to project."""
        # Create another project with unique name
        other_project = Project(
            name=f"other_project_{uuid4().hex[:8]}",
            description="Other project",
            extraction_schema={"name": "test", "fields": []},
        )
        db.add(other_project)
        db.commit()
        db.refresh(other_project)

        try:
            # Try to extract source from other project
            response = client.post(
                f"/api/v1/projects/{other_project.id}/extract",
                headers={"X-API-Key": valid_api_key},
                json={"source_ids": [str(test_sources[0].id)]},
            )
            assert response.status_code == 400
        finally:
            # Cleanup
            db.delete(other_project)
            db.commit()

    def test_extract_endpoint_accepts_optional_profile(
        self, client: TestClient, valid_api_key: str, test_project: Project
    ):
        """Should accept optional profile parameter."""
        response = client.post(
            f"/api/v1/projects/{test_project.id}/extract",
            headers={"X-API-Key": valid_api_key},
            json={"profile": "detailed"},
        )
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data

    def test_extract_endpoint_counts_pending_sources_when_no_source_ids(
        self,
        client: TestClient,
        valid_api_key: str,
        test_project: Project,
        test_sources: list[Source],
    ):
        """Should count pending sources when source_ids not provided."""
        response = client.post(
            f"/api/v1/projects/{test_project.id}/extract",
            headers={"X-API-Key": valid_api_key},
            json={},
        )
        assert response.status_code == 202
        data = response.json()
        # Should count only pending sources (2 out of 3)
        assert data["source_count"] == 2

    def test_extract_endpoint_accepts_multiple_source_ids(
        self,
        client: TestClient,
        valid_api_key: str,
        test_project: Project,
        test_sources: list[Source],
    ):
        """Should accept multiple source IDs."""
        source_ids = [str(test_sources[0].id), str(test_sources[1].id)]
        response = client.post(
            f"/api/v1/projects/{test_project.id}/extract",
            headers={"X-API-Key": valid_api_key},
            json={"source_ids": source_ids},
        )
        assert response.status_code == 202
        data = response.json()
        assert data["source_count"] == 2

    def test_extract_endpoint_response_includes_metadata(
        self,
        client: TestClient,
        valid_api_key: str,
        test_project: Project,
        test_sources: list[Source],
    ):
        """Response should include useful metadata."""
        response = client.post(
            f"/api/v1/projects/{test_project.id}/extract",
            headers={"X-API-Key": valid_api_key},
            json={"source_ids": [str(test_sources[0].id)], "profile": "detailed"},
        )
        assert response.status_code == 202
        data = response.json()
        assert "job_id" in data
        assert "status" in data
        assert "source_count" in data
        assert "project_id" in data
        assert data["status"] == "queued"
        assert data["source_count"] == 1
        assert data["project_id"] == str(test_project.id)


class TestJobPersistence:
    """Test that extraction jobs are persisted to the database."""

    def test_create_extraction_job_persists_to_database(
        self,
        client: TestClient,
        valid_api_key: str,
        test_project: Project,
        test_sources: list[Source],
        db: Session,
    ):
        """Creating an extraction job should persist it to the database."""
        source_id = str(test_sources[0].id)
        response = client.post(
            f"/api/v1/projects/{test_project.id}/extract",
            headers={"X-API-Key": valid_api_key},
            json={"source_ids": [source_id], "profile": "detailed"},
        )
        assert response.status_code == 202
        job_id = response.json()["job_id"]

        # Verify job exists in database
        db_job = db.query(Job).filter(Job.id == UUID(job_id)).first()
        assert db_job is not None
        assert str(db_job.id) == job_id
        assert db_job.type == "extract"
        assert db_job.status == "queued"
        assert db_job.payload is not None
        assert db_job.payload["project_id"] == str(test_project.id)
        assert db_job.payload["source_ids"] == [source_id]
        assert db_job.payload["profile"] == "detailed"

    def test_multiple_extraction_jobs_persist_independently(
        self,
        client: TestClient,
        valid_api_key: str,
        test_project: Project,
        test_sources: list[Source],
        db: Session,
    ):
        """Multiple extraction jobs should be stored independently."""
        job_ids = []
        for i, source in enumerate(test_sources[:2]):
            response = client.post(
                f"/api/v1/projects/{test_project.id}/extract",
                headers={"X-API-Key": valid_api_key},
                json={"source_ids": [str(source.id)]},
            )
            assert response.status_code == 202
            job_ids.append(response.json()["job_id"])

        # Verify all jobs exist in database
        for i, job_id in enumerate(job_ids):
            db_job = db.query(Job).filter(Job.id == UUID(job_id)).first()
            assert db_job is not None
            assert db_job.payload["source_ids"] == [str(test_sources[i].id)]


class TestListExtractions:
    """Test GET /api/v1/projects/{project_id}/extractions endpoint."""

    def test_list_extractions_requires_authentication(
        self, client: TestClient, test_project: Project
    ):
        """List extractions endpoint should require API key."""
        response = client.get(f"/api/v1/projects/{test_project.id}/extractions")
        assert response.status_code == 401

    def test_list_extractions_validates_project_id_format(
        self, client: TestClient, valid_api_key: str
    ):
        """Should return 422 for invalid project UUID format."""
        response = client.get(
            "/api/v1/projects/not-a-valid-uuid/extractions",
            headers={"X-API-Key": valid_api_key},
        )
        assert response.status_code == 422

    def test_list_extractions_returns_404_for_nonexistent_project(
        self, client: TestClient, valid_api_key: str
    ):
        """Should return 404 for non-existent project_id."""
        fake_project_id = str(uuid4())
        response = client.get(
            f"/api/v1/projects/{fake_project_id}/extractions",
            headers={"X-API-Key": valid_api_key},
        )
        assert response.status_code == 404

    def test_list_extractions_returns_empty_list(
        self, client: TestClient, valid_api_key: str, test_project: Project
    ):
        """Should return empty list when no extractions exist."""
        response = client.get(
            f"/api/v1/projects/{test_project.id}/extractions",
            headers={"X-API-Key": valid_api_key},
        )
        assert response.status_code == 200
        data = response.json()
        assert "extractions" in data
        assert data["extractions"] == []
        assert data["total"] == 0
        assert data["limit"] == 50
        assert data["offset"] == 0

    def test_list_extractions_includes_all_fields(
        self,
        client: TestClient,
        valid_api_key: str,
        test_project: Project,
        test_sources: list[Source],
        db: Session,
    ):
        """Response should include all required fields."""
        # Create test extraction
        from orm_models import Extraction

        extraction = Extraction(
            project_id=test_project.id,
            source_id=test_sources[0].id,
            data={"text": "test fact", "category": "test"},
            extraction_type="test_extraction",
            source_group="TestCo",
            confidence=0.95,
        )
        db.add(extraction)
        db.commit()
        db.refresh(extraction)

        response = client.get(
            f"/api/v1/projects/{test_project.id}/extractions",
            headers={"X-API-Key": valid_api_key},
        )
        assert response.status_code == 200
        data = response.json()
        assert len(data["extractions"]) == 1

        ext = data["extractions"][0]
        assert "id" in ext
        assert "source_id" in ext
        assert "data" in ext
        assert "extraction_type" in ext
        assert "source_group" in ext
        assert "confidence" in ext
        assert "extracted_at" in ext
        assert "created_at" in ext

        assert ext["id"] == str(extraction.id)
        assert ext["source_id"] == str(test_sources[0].id)
        assert ext["data"] == {"text": "test fact", "category": "test"}
        assert ext["extraction_type"] == "test_extraction"
        assert ext["source_group"] == "TestCo"
        assert ext["confidence"] == 0.95

    def test_list_extractions_filters_by_source_id(
        self,
        client: TestClient,
        valid_api_key: str,
        test_project: Project,
        test_sources: list[Source],
        db: Session,
    ):
        """Should filter extractions by source_id."""
        from orm_models import Extraction

        # Create extractions for different sources
        for i, source in enumerate(test_sources[:2]):
            extraction = Extraction(
                project_id=test_project.id,
                source_id=source.id,
                data={"text": f"fact {i}"},
                extraction_type="test",
                source_group="TestCo",
            )
            db.add(extraction)
        db.commit()

        # Filter by first source
        response = client.get(
            f"/api/v1/projects/{test_project.id}/extractions?source_id={test_sources[0].id}",
            headers={"X-API-Key": valid_api_key},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["extractions"][0]["source_id"] == str(test_sources[0].id)

    def test_list_extractions_filters_by_source_group(
        self,
        client: TestClient,
        valid_api_key: str,
        test_project: Project,
        test_sources: list[Source],
        db: Session,
    ):
        """Should filter extractions by source_group."""
        from orm_models import Extraction

        # Create extractions for different groups
        extraction1 = Extraction(
            project_id=test_project.id,
            source_id=test_sources[0].id,
            data={"text": "fact 1"},
            extraction_type="test",
            source_group="GroupA",
        )
        extraction2 = Extraction(
            project_id=test_project.id,
            source_id=test_sources[1].id,
            data={"text": "fact 2"},
            extraction_type="test",
            source_group="GroupB",
        )
        db.add_all([extraction1, extraction2])
        db.commit()

        response = client.get(
            f"/api/v1/projects/{test_project.id}/extractions?source_group=GroupA",
            headers={"X-API-Key": valid_api_key},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 1
        assert data["extractions"][0]["source_group"] == "GroupA"

    def test_list_extractions_filters_by_min_confidence(
        self,
        client: TestClient,
        valid_api_key: str,
        test_project: Project,
        test_sources: list[Source],
        db: Session,
    ):
        """Should filter extractions by minimum confidence."""
        from orm_models import Extraction

        # Create extractions with different confidence levels
        for i, conf in enumerate([0.5, 0.8, 0.95]):
            extraction = Extraction(
                project_id=test_project.id,
                source_id=test_sources[0].id,
                data={"text": f"fact {i}"},
                extraction_type="test",
                source_group="TestCo",
                confidence=conf,
            )
            db.add(extraction)
        db.commit()

        response = client.get(
            f"/api/v1/projects/{test_project.id}/extractions?min_confidence=0.8",
            headers={"X-API-Key": valid_api_key},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 2
        for ext in data["extractions"]:
            assert ext["confidence"] >= 0.8

    def test_list_extractions_pagination(
        self,
        client: TestClient,
        valid_api_key: str,
        test_project: Project,
        test_sources: list[Source],
        db: Session,
    ):
        """Should paginate results correctly."""
        from orm_models import Extraction

        # Create 10 extractions
        for i in range(10):
            extraction = Extraction(
                project_id=test_project.id,
                source_id=test_sources[0].id,
                data={"text": f"fact {i}"},
                extraction_type="test",
                source_group="TestCo",
            )
            db.add(extraction)
        db.commit()

        # Get first page
        response = client.get(
            f"/api/v1/projects/{test_project.id}/extractions?limit=5&offset=0",
            headers={"X-API-Key": valid_api_key},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 10
        assert data["limit"] == 5
        assert data["offset"] == 0
        assert len(data["extractions"]) == 5

        # Get second page
        response = client.get(
            f"/api/v1/projects/{test_project.id}/extractions?limit=5&offset=5",
            headers={"X-API-Key": valid_api_key},
        )
        assert response.status_code == 200
        data = response.json()
        assert data["total"] == 10
        assert data["limit"] == 5
        assert data["offset"] == 5
        assert len(data["extractions"]) == 5

    def test_list_extractions_validates_source_id_format(
        self, client: TestClient, valid_api_key: str, test_project: Project
    ):
        """Should return 422 for invalid source_id format."""
        response = client.get(
            f"/api/v1/projects/{test_project.id}/extractions?source_id=not-a-uuid",
            headers={"X-API-Key": valid_api_key},
        )
        assert response.status_code == 422
