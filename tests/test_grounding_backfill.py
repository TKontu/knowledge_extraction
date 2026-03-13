"""Tests for grounding score DB operations and backfill logic."""

import pytest
from sqlalchemy.orm import Session

from database import engine
from orm_models import Project, Source
from services.extraction.grounding import compute_grounding_scores
from services.storage.repositories.extraction import (
    ExtractionRepository,
)


@pytest.fixture
def db_session():
    """Create a fresh database session for each test."""
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def test_project(db_session):
    """Create a test project with field_groups schema."""
    project = Project(
        name="test_grounding_project",
        extraction_schema={
            "field_groups": [
                {
                    "name": "company_info",
                    "description": "Company information",
                    "fields": [
                        {"name": "company_name", "field_type": "string"},
                        {"name": "employee_count", "field_type": "integer"},
                        {"name": "description", "field_type": "summary"},
                        {"name": "manufactures_gears", "field_type": "boolean"},
                    ],
                    "prompt_hint": "",
                },
                {
                    "name": "products",
                    "description": "Product list",
                    "fields": [
                        {"name": "name", "field_type": "string"},
                        {"name": "power_rating_kw", "field_type": "float"},
                    ],
                    "prompt_hint": "",
                    "is_entity_list": True,
                },
            ]
        },
    )
    db_session.add(project)
    db_session.flush()
    return project


@pytest.fixture
def test_source(db_session, test_project):
    source = Source(
        project_id=test_project.id,
        uri="https://example.com/test",
        source_group="test_company",
    )
    db_session.add(source)
    db_session.flush()
    return source


@pytest.fixture
def extraction_repo(db_session):
    return ExtractionRepository(db_session)


class TestGroundingScoresColumn:
    """Test that grounding_scores column works on the Extraction model."""

    def test_extraction_grounding_scores_nullable(
        self, extraction_repo, test_project, test_source, db_session
    ):
        """Old extractions have grounding_scores=None."""
        extraction = extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"company_name": "ABB"},
            extraction_type="company_info",
            source_group="test_company",
        )
        assert extraction.grounding_scores is None

    def test_extraction_grounding_scores_set_on_create(
        self, extraction_repo, test_project, test_source, db_session
    ):
        """New extractions can have grounding_scores set."""
        extraction = extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"company_name": "ABB"},
            extraction_type="company_info",
            source_group="test_company",
            grounding_scores={"company_name": 1.0},
        )
        assert extraction.grounding_scores == {"company_name": 1.0}


class TestUpdateGroundingScores:
    """Test repository methods for updating grounding scores."""

    def test_update_grounding_scores_single(
        self, extraction_repo, test_project, test_source, db_session
    ):
        extraction = extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"company_name": "ABB", "employee_count": 105000},
            extraction_type="company_info",
            source_group="test_company",
        )
        scores = {"company_name": 1.0, "employee_count": 0.0}
        extraction_repo.update_grounding_scores(extraction.id, scores)

        refreshed = extraction_repo.get(extraction.id)
        assert refreshed.grounding_scores == scores

    def test_update_grounding_scores_batch(
        self, extraction_repo, test_project, test_source, db_session
    ):
        ext1 = extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"company_name": "ABB"},
            extraction_type="company_info",
            source_group="company_a",
        )
        ext2 = extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"company_name": "Siemens"},
            extraction_type="company_info",
            source_group="company_b",
        )

        updates = [
            (ext1.id, {"company_name": 1.0}),
            (ext2.id, {"company_name": 0.5}),
        ]
        count = extraction_repo.update_grounding_scores_batch(updates)
        assert count == 2

        r1 = extraction_repo.get(ext1.id)
        r2 = extraction_repo.get(ext2.id)
        assert r1.grounding_scores == {"company_name": 1.0}
        assert r2.grounding_scores == {"company_name": 0.5}

    def test_update_grounding_scores_batch_empty(self, extraction_repo):
        assert extraction_repo.update_grounding_scores_batch([]) == 0

    def test_update_grounding_scores_overwrites(
        self, extraction_repo, test_project, test_source, db_session
    ):
        """Updating scores twice overwrites the previous value."""
        extraction = extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={"company_name": "ABB"},
            extraction_type="company_info",
            source_group="test_company",
        )
        extraction_repo.update_grounding_scores(extraction.id, {"company_name": 0.0})
        extraction_repo.update_grounding_scores(extraction.id, {"company_name": 1.0})

        refreshed = extraction_repo.get(extraction.id)
        assert refreshed.grounding_scores == {"company_name": 1.0}


class TestBackfillLogic:
    """Test the backfill computation logic (pure function integration)."""

    def test_backfill_computes_correct_scores(self):
        """Simulate backfill: compute_grounding_scores on extraction data."""
        data = {
            "company_name": "ABB",
            "employee_count": 105000,
            "description": "A leading tech company",
            "manufactures_gears": True,
            "_quotes": {
                "company_name": "ABB is a global leader",
                "employee_count": "approximately 105,000 employees",
                "description": "ABB is a leading tech company",
                "manufactures_gears": "produces gears and motors",
            },
        }
        field_types = {
            "company_name": "string",
            "employee_count": "integer",
            "description": "summary",
            "manufactures_gears": "boolean",
        }
        scores = compute_grounding_scores(data, field_types)
        assert scores["company_name"] == 1.0
        assert scores["employee_count"] == 1.0
        assert "description" not in scores  # summary → none (skipped)
        assert "manufactures_gears" not in scores  # boolean → semantic

    def test_backfill_idempotent(self):
        """Running compute twice gives same result."""
        data = {
            "company_name": "ABB",
            "_quotes": {"company_name": "ABB Corp"},
        }
        field_types = {"company_name": "string"}
        scores1 = compute_grounding_scores(data, field_types)
        scores2 = compute_grounding_scores(data, field_types)
        assert scores1 == scores2

    def test_extract_field_types_from_schema(self):
        """Helper to extract field_types from extraction_schema."""
        from services.extraction.grounding import extract_field_types_from_schema

        schema = {
            "field_groups": [
                {
                    "name": "company_info",
                    "fields": [
                        {"name": "company_name", "field_type": "string"},
                        {"name": "employee_count", "field_type": "integer"},
                    ],
                },
                {
                    "name": "products",
                    "fields": [
                        {"name": "name", "field_type": "string"},
                        {"name": "power_rating_kw", "field_type": "float"},
                    ],
                },
            ]
        }
        result = extract_field_types_from_schema(schema)
        assert result == {
            "company_info": {
                "company_name": "string",
                "employee_count": "integer",
            },
            "products": {
                "name": "string",
                "power_rating_kw": "float",
            },
        }

    def test_extract_field_types_empty_schema(self):
        from services.extraction.grounding import extract_field_types_from_schema

        assert extract_field_types_from_schema({}) == {}
        assert extract_field_types_from_schema({"field_groups": []}) == {}

    def test_backfill_end_to_end_with_db(
        self, extraction_repo, test_project, test_source, db_session
    ):
        """End-to-end: create extractions, compute scores, update DB."""
        ext = extraction_repo.create(
            project_id=test_project.id,
            source_id=test_source.id,
            data={
                "company_name": "ABB",
                "employee_count": 105000,
                "_quotes": {
                    "company_name": "ABB is global",
                    "employee_count": "about 105,000 employees",
                },
            },
            extraction_type="company_info",
            source_group="test_company",
        )

        from services.extraction.grounding import extract_field_types_from_schema

        field_types_by_group = extract_field_types_from_schema(
            test_project.extraction_schema
        )
        field_types = field_types_by_group.get("company_info", {})
        scores = compute_grounding_scores(ext.data, field_types)

        extraction_repo.update_grounding_scores(ext.id, scores)

        refreshed = extraction_repo.get(ext.id)
        assert refreshed.grounding_scores["company_name"] == 1.0
        assert refreshed.grounding_scores["employee_count"] == 1.0
