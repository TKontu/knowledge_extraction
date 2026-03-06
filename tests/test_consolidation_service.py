"""Tests for consolidation service (DB integration layer)."""

import pytest
from sqlalchemy.orm import Session

from database import engine
from orm_models import ConsolidatedExtraction, Project, Source
from services.extraction.consolidation_service import ConsolidationService
from services.projects.repository import ProjectRepository
from services.storage.repositories.extraction import ExtractionRepository


@pytest.fixture
def db_session():
    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)
    yield session
    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def test_project(db_session):
    project = Project(
        name="test_consolidation_project",
        extraction_schema={
            "field_groups": [
                {
                    "name": "company_info",
                    "description": "Company information",
                    "fields": [
                        {"name": "company_name", "field_type": "string"},
                        {"name": "employee_count", "field_type": "integer"},
                        {"name": "description", "field_type": "text"},
                    ],
                    "prompt_hint": "",
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
        uri="https://example.com/test1",
        source_group="abb",
    )
    db_session.add(source)
    db_session.flush()
    return source


@pytest.fixture
def test_source_2(db_session, test_project):
    source = Source(
        project_id=test_project.id,
        uri="https://example.com/test2",
        source_group="abb",
    )
    db_session.add(source)
    db_session.flush()
    return source


@pytest.fixture
def extraction_repo(db_session):
    return ExtractionRepository(db_session)


@pytest.fixture
def project_repo(db_session):
    return ProjectRepository(db_session)


@pytest.fixture
def service(db_session, project_repo):
    return ConsolidationService(db_session, project_repo)


def _create_extraction(
    repo,
    project,
    source,
    data,
    source_group="abb",
    confidence=0.9,
    grounding_scores=None,
):
    return repo.create(
        project_id=project.id,
        source_id=source.id,
        data=data,
        extraction_type="company_info",
        source_group=source_group,
        confidence=confidence,
        grounding_scores=grounding_scores,
    )


class TestConsolidateSourceGroup:
    def test_creates_consolidated_record(
        self,
        service,
        extraction_repo,
        test_project,
        test_source,
        test_source_2,
        db_session,
    ):
        """Basic consolidation creates a record in the DB."""
        _create_extraction(
            extraction_repo,
            test_project,
            test_source,
            data={
                "company_name": "ABB",
                "employee_count": 105000,
                "_quotes": {
                    "company_name": "ABB Corp",
                    "employee_count": "105,000 employees",
                },
            },
            grounding_scores={"company_name": 1.0, "employee_count": 1.0},
        )
        _create_extraction(
            extraction_repo,
            test_project,
            test_source_2,
            data={
                "company_name": "ABB",
                "employee_count": 105000,
                "_quotes": {
                    "company_name": "ABB Ltd",
                    "employee_count": "about 105,000",
                },
            },
            confidence=0.85,
            grounding_scores={"company_name": 1.0, "employee_count": 1.0},
        )

        records = service.consolidate_source_group(test_project.id, "abb")
        assert len(records) == 1

        # Verify DB record
        from sqlalchemy import select

        result = db_session.execute(
            select(ConsolidatedExtraction).where(
                ConsolidatedExtraction.project_id == test_project.id,
                ConsolidatedExtraction.source_group == "abb",
            )
        )
        db_record = result.scalar_one()
        assert db_record.data["company_name"] == "ABB"
        assert db_record.data["employee_count"] == 105000
        assert db_record.source_count == 2

    def test_handles_no_extractions(self, service, test_project, db_session):
        """Source group with 0 extractions returns empty list."""
        records = service.consolidate_source_group(test_project.id, "nonexistent")
        assert records == []

    def test_provenance_stored(
        self, service, extraction_repo, test_project, test_source, db_session
    ):
        _create_extraction(
            extraction_repo,
            test_project,
            test_source,
            data={"company_name": "ABB", "_quotes": {"company_name": "ABB Corp"}},
            grounding_scores={"company_name": 1.0},
        )

        service.consolidate_source_group(test_project.id, "abb")

        from sqlalchemy import select

        db_record = db_session.execute(
            select(ConsolidatedExtraction).where(
                ConsolidatedExtraction.project_id == test_project.id,
            )
        ).scalar_one()

        assert db_record.provenance is not None
        assert "company_name" in db_record.provenance

    def test_source_count_correct(
        self,
        service,
        extraction_repo,
        test_project,
        test_source,
        test_source_2,
        db_session,
    ):
        _create_extraction(
            extraction_repo,
            test_project,
            test_source,
            data={"company_name": "ABB"},
        )
        _create_extraction(
            extraction_repo,
            test_project,
            test_source_2,
            data={"company_name": "ABB"},
        )

        service.consolidate_source_group(test_project.id, "abb")

        from sqlalchemy import select

        db_record = db_session.execute(
            select(ConsolidatedExtraction).where(
                ConsolidatedExtraction.project_id == test_project.id,
            )
        ).scalar_one()
        assert db_record.source_count == 2

    def test_grounded_count_correct(
        self,
        service,
        extraction_repo,
        test_project,
        test_source,
        test_source_2,
        db_session,
    ):
        _create_extraction(
            extraction_repo,
            test_project,
            test_source,
            data={"company_name": "ABB", "_quotes": {"company_name": "ABB Corp"}},
            grounding_scores={"company_name": 1.0},
        )
        _create_extraction(
            extraction_repo,
            test_project,
            test_source_2,
            data={"company_name": "ABB"},
            grounding_scores={"company_name": 0.0},
        )

        service.consolidate_source_group(test_project.id, "abb")

        from sqlalchemy import select

        db_record = db_session.execute(
            select(ConsolidatedExtraction).where(
                ConsolidatedExtraction.project_id == test_project.id,
            )
        ).scalar_one()
        assert db_record.grounded_count >= 1


class TestConsolidateProject:
    def test_all_source_groups_processed(
        self, service, extraction_repo, test_project, test_source, db_session
    ):
        _create_extraction(
            extraction_repo,
            test_project,
            test_source,
            data={"company_name": "ABB"},
            source_group="abb",
        )

        # Create second source group
        source2 = Source(
            project_id=test_project.id,
            uri="https://example.com/siemens",
            source_group="siemens",
        )
        db_session.add(source2)
        db_session.flush()
        _create_extraction(
            extraction_repo,
            test_project,
            source2,
            data={"company_name": "Siemens"},
            source_group="siemens",
        )

        result = service.consolidate_project(test_project.id)
        assert result["source_groups"] >= 2
        assert result["records_created"] >= 2
        assert result["errors"] == 0

    def test_project_not_found_returns_zero_counts(self, service):
        from uuid import uuid4

        result = service.consolidate_project(uuid4())
        assert result["source_groups"] == 0
        assert result["records_created"] == 0
        assert result["errors"] == 0


class TestReconsolidate:
    def test_idempotent(
        self, service, extraction_repo, test_project, test_source, db_session
    ):
        """Running twice produces same result, updated_at changes."""
        _create_extraction(
            extraction_repo,
            test_project,
            test_source,
            data={"company_name": "ABB"},
        )

        service.consolidate_source_group(test_project.id, "abb")

        from sqlalchemy import select

        first = db_session.execute(
            select(ConsolidatedExtraction).where(
                ConsolidatedExtraction.project_id == test_project.id,
            )
        ).scalar_one()
        first_data = dict(first.data)
        first_id = first.id

        # Reconsolidate
        service.reconsolidate(test_project.id)

        db_session.expire_all()
        second = db_session.execute(
            select(ConsolidatedExtraction).where(
                ConsolidatedExtraction.project_id == test_project.id,
            )
        ).scalar_one()

        assert second.data == first_data
        # Reconsolidation produces equivalent data (delete+insert, not upsert)
        assert second.source_group == first.source_group

    def test_specific_source_groups(
        self, service, extraction_repo, test_project, test_source, db_session
    ):
        _create_extraction(
            extraction_repo,
            test_project,
            test_source,
            data={"company_name": "ABB"},
            source_group="abb",
        )
        service.consolidate_source_group(test_project.id, "abb")

        result = service.reconsolidate(test_project.id, source_groups=["abb"])
        assert result["records_created"] >= 1

    def test_error_in_one_group_does_not_crash(
        self, service, extraction_repo, test_project, test_source, db_session
    ):
        """reconsolidate with explicit groups isolates per-group errors."""
        _create_extraction(
            extraction_repo,
            test_project,
            test_source,
            data={"company_name": "ABB"},
            source_group="abb",
        )

        def failing_upsert(*args, **kwargs):
            raise RuntimeError("simulated DB error")

        service._upsert_record = failing_upsert

        result = service.reconsolidate(test_project.id, source_groups=["abb"])
        assert result["errors"] == 1
        assert result["source_groups"] == 1

        # Session still usable
        from sqlalchemy import text

        row = db_session.execute(text("SELECT 1")).scalar()
        assert row == 1


class TestConsolidateProjectSessionRollback:
    """Verify session rollback after per-group failure keeps loop healthy."""

    def test_error_in_one_group_does_not_crash_loop(
        self, service, extraction_repo, test_project, test_source, db_session
    ):
        """If one source group fails, the loop completes and session stays usable."""
        _create_extraction(
            extraction_repo,
            test_project,
            test_source,
            data={"company_name": "ABB"},
            source_group="abb",
        )

        # Force _upsert_record to always fail
        def failing_upsert(*args, **kwargs):
            raise RuntimeError("simulated DB error")

        service._upsert_record = failing_upsert

        result = service.consolidate_project(test_project.id)
        assert result["errors"] == 1
        assert result["source_groups"] == 1

        # Session should still be usable after rollback
        from sqlalchemy import text

        row = db_session.execute(text("SELECT 1")).scalar()
        assert row == 1


class TestSavepointIsolation:
    """C1: Verify that a failure in one source group preserves others."""

    def test_successful_group_survives_later_failure(
        self, service, extraction_repo, test_project, test_source, db_session
    ):
        """Group A succeeds, group B fails — group A's record must survive."""
        # Create extractions in two source groups
        source_b = Source(
            project_id=test_project.id,
            uri="https://example.com/group_b",
            source_group="group_b",
        )
        db_session.add(source_b)
        db_session.flush()

        _create_extraction(
            extraction_repo, test_project, test_source,
            data={"company_name": "ABB"}, source_group="group_a",
        )
        _create_extraction(
            extraction_repo, test_project, source_b,
            data={"company_name": "Siemens"}, source_group="group_b",
        )
        db_session.flush()

        # Make group_b fail during upsert by patching
        original_upsert = service._upsert_record
        call_count = 0

        def fail_on_second_call(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count > 1:
                raise RuntimeError("simulated failure on group B")
            return original_upsert(*args, **kwargs)

        service._upsert_record = fail_on_second_call

        result = service._process_source_groups(
            test_project.id, ["group_a", "group_b"]
        )
        assert result["errors"] == 1
        assert result["records_created"] >= 1

        # Group A's consolidated record must be present
        from sqlalchemy import select

        records = db_session.execute(
            select(ConsolidatedExtraction).where(
                ConsolidatedExtraction.project_id == test_project.id,
            )
        ).scalars().all()
        assert len(records) == 1
        assert records[0].source_group == "group_a"


class TestStaleRecordCleanup:
    """H2: Verify that reconsolidation removes stale records."""

    def test_removed_extraction_type_is_cleaned_up(
        self, service, extraction_repo, test_project, test_source, db_session
    ):
        """If an extraction type disappears, its consolidated record is deleted."""
        # First, create a consolidated record by consolidating normally
        _create_extraction(
            extraction_repo, test_project, test_source,
            data={"company_name": "ABB"},
        )
        service.consolidate_source_group(test_project.id, "abb")

        from sqlalchemy import select

        records = db_session.execute(
            select(ConsolidatedExtraction).where(
                ConsolidatedExtraction.project_id == test_project.id,
            )
        ).scalars().all()
        assert len(records) == 1

        # Now delete all extractions and reconsolidate — stale record should be gone
        from sqlalchemy import delete
        from orm_models import Extraction

        db_session.execute(
            delete(Extraction).where(Extraction.project_id == test_project.id)
        )
        db_session.flush()

        # consolidate_source_group returns [] when no extractions exist,
        # but the delete happens before the early-return check, so stale
        # records ARE cleaned up even when there's nothing to consolidate.
        service.consolidate_source_group(test_project.id, "abb")

        records = db_session.execute(
            select(ConsolidatedExtraction).where(
                ConsolidatedExtraction.project_id == test_project.id,
            )
        ).scalars().all()
        assert len(records) == 0


class TestConsolidateEndpoint:
    """Test the API endpoint via direct function call (no HTTP)."""

    def test_trigger_consolidation(
        self, service, extraction_repo, test_project, test_source, db_session
    ):
        _create_extraction(
            extraction_repo,
            test_project,
            test_source,
            data={"company_name": "ABB"},
        )

        result = service.consolidate_project(test_project.id)
        assert "source_groups" in result
        assert "records_created" in result


class TestZeroConfidenceHandling:
    """Verify that confidence=0.0 is not treated as None (falsy bug)."""

    def test_zero_confidence_produces_zero_weight(
        self,
        service,
        extraction_repo,
        test_project,
        test_source,
        test_source_2,
        db_session,
    ):
        """confidence=0.0 should produce near-zero weight, not 0.5."""
        # High-confidence extraction says "ABB"
        _create_extraction(
            extraction_repo,
            test_project,
            test_source,
            data={"company_name": "ABB", "_quotes": {"company_name": "ABB Corp"}},
            confidence=0.9,
            grounding_scores={"company_name": 1.0},
        )
        # Zero-confidence extraction says "Siemens" — should be outweighed
        _create_extraction(
            extraction_repo,
            test_project,
            test_source_2,
            data={"company_name": "Siemens", "_quotes": {"company_name": "Siemens AG"}},
            confidence=0.0,
            grounding_scores={"company_name": 1.0},
        )

        records = service.consolidate_source_group(test_project.id, "abb")
        assert len(records) == 1
        # With the fix, 0.0 confidence → near-zero weight → ABB wins
        # Before the fix, 0.0 was treated as 0.5 → could flip the result
        assert records[0].fields["company_name"].value == "ABB"
