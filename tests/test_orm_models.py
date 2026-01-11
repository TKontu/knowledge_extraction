"""Tests for SQLAlchemy ORM models."""

import pytest
from datetime import datetime, UTC
from uuid import uuid4

from sqlalchemy import text
from sqlalchemy.orm import sessionmaker, Session

# Will be imported once created
# from orm_models import Base, Job, Page, Fact, Profile, Report, RateLimit


@pytest.fixture
def test_db_session(test_db_engine):
    """Create test database session with transaction rollback."""
    # Start a connection and transaction
    connection = test_db_engine.connect()
    transaction = connection.begin()

    # Create session bound to the connection
    TestSession = sessionmaker(bind=connection)
    session = TestSession()

    yield session

    # Rollback transaction and close connection
    session.close()
    transaction.rollback()
    connection.close()


class TestJobModel:
    """Tests for Job ORM model."""

    def test_job_model_exists(self):
        """Test that Job model can be imported."""
        from orm_models import Job
        assert Job is not None

    def test_create_job_with_minimal_fields(self, test_db_session: Session):
        """Test creating a job with only required fields."""
        from orm_models import Job

        job = Job(
            type="scrape",
            payload={"urls": ["https://example.com"], "company": "Example Corp"}
        )
        test_db_session.add(job)
        test_db_session.commit()

        assert job.id is not None
        assert job.type == "scrape"
        assert job.status == "queued"  # Default value
        assert job.priority == 0  # Default value
        assert job.payload == {"urls": ["https://example.com"], "company": "Example Corp"}
        assert job.created_at is not None
        assert job.started_at is None
        assert job.completed_at is None
        assert job.result is None
        assert job.error is None

    def test_create_job_with_all_fields(self, test_db_session: Session):
        """Test creating a job with all fields populated."""
        from orm_models import Job

        now = datetime.now(UTC)
        job = Job(
            type="extraction",
            status="completed",
            priority=5,
            payload={"page_id": "123"},
            result={"facts": ["fact1", "fact2"]},
            error=None,
            started_at=now,
            completed_at=now
        )
        test_db_session.add(job)
        test_db_session.commit()

        assert job.id is not None
        assert job.type == "extraction"
        assert job.status == "completed"
        assert job.priority == 5
        assert job.result == {"facts": ["fact1", "fact2"]}
        # SQLite doesn't preserve timezone info, so compare without tz
        assert job.started_at.replace(tzinfo=None) == now.replace(tzinfo=None)
        assert job.completed_at.replace(tzinfo=None) == now.replace(tzinfo=None)

    def test_job_with_error(self, test_db_session: Session):
        """Test creating a failed job with error message."""
        from orm_models import Job

        job = Job(
            type="scrape",
            status="failed",
            payload={"urls": ["https://example.com"]},
            error="Connection timeout"
        )
        test_db_session.add(job)
        test_db_session.commit()

        assert job.status == "failed"
        assert job.error == "Connection timeout"

    def test_query_job_by_id(self, test_db_session: Session):
        """Test querying a job by ID."""
        from orm_models import Job

        job = Job(type="scrape", payload={"test": "data"})
        test_db_session.add(job)
        test_db_session.commit()

        job_id = job.id

        # Query by ID
        retrieved_job = test_db_session.query(Job).filter(Job.id == job_id).first()
        assert retrieved_job is not None
        assert retrieved_job.id == job_id
        assert retrieved_job.type == "scrape"

    def test_query_jobs_by_status(self, test_db_session: Session):
        """Test querying jobs by status."""
        from orm_models import Job

        # Store job IDs to query for them specifically
        job1 = Job(type="scrape", status="queued", payload={"test": "1"})
        job2 = Job(type="scrape", status="running", payload={"test": "2"})
        job3 = Job(type="scrape", status="queued", payload={"test": "3"})

        test_db_session.add_all([job1, job2, job3])
        test_db_session.commit()

        # Get the IDs of jobs we just created
        test_job_ids = [job1.id, job2.id, job3.id]

        # Query only for jobs we created in this test that are queued
        queued_jobs = (
            test_db_session.query(Job)
            .filter(Job.status == "queued")
            .filter(Job.id.in_(test_job_ids))
            .all()
        )
        assert len(queued_jobs) == 2


class TestPageModel:
    """Tests for Page ORM model."""

    def test_page_model_exists(self):
        """Test that Page model can be imported."""
        from orm_models import Page
        assert Page is not None

    def test_create_page_with_required_fields(self, test_db_session: Session):
        """Test creating a page with required fields."""
        from orm_models import Page

        page = Page(
            url="https://example.com/docs",
            domain="example.com",
            company="Example Corp"
        )
        test_db_session.add(page)
        test_db_session.commit()

        assert page.id is not None
        assert page.url == "https://example.com/docs"
        assert page.domain == "example.com"
        assert page.company == "Example Corp"
        assert page.status == "completed"  # Default
        assert page.created_at is not None

    def test_page_url_must_be_unique(self, test_db_session: Session):
        """Test that page URL must be unique."""
        from orm_models import Page
        from sqlalchemy.exc import IntegrityError

        page1 = Page(url="https://example.com", domain="example.com", company="Test")
        test_db_session.add(page1)
        test_db_session.commit()

        page2 = Page(url="https://example.com", domain="example.com", company="Test")
        test_db_session.add(page2)

        with pytest.raises(IntegrityError):
            test_db_session.commit()


class TestFactModel:
    """Tests for Fact ORM model."""

    def test_fact_model_exists(self):
        """Test that Fact model can be imported."""
        from orm_models import Fact
        assert Fact is not None

    def test_create_fact_with_page_relationship(self, test_db_session: Session):
        """Test creating a fact linked to a page."""
        from orm_models import Page, Fact

        # Create page first
        page = Page(url="https://example.com", domain="example.com", company="Test")
        test_db_session.add(page)
        test_db_session.commit()

        # Create fact
        fact = Fact(
            page_id=page.id,
            fact_text="Example supports OAuth 2.0",
            category="authentication",
            confidence=0.95,
            profile_used="api_docs"
        )
        test_db_session.add(fact)
        test_db_session.commit()

        assert fact.id is not None
        assert fact.page_id == page.id
        assert fact.fact_text == "Example supports OAuth 2.0"
        assert fact.category == "authentication"
        assert fact.confidence == 0.95
        assert fact.profile_used == "api_docs"


class TestProfileModel:
    """Tests for Profile ORM model."""

    def test_profile_model_exists(self):
        """Test that Profile model can be imported."""
        from orm_models import Profile
        assert Profile is not None

    def test_create_profile(self, test_db_session: Session):
        """Test creating an extraction profile."""
        from orm_models import Profile

        profile = Profile(
            name="custom_profile",
            categories=["features", "pricing"],
            prompt_focus="Focus on features and pricing",
            depth="detailed",
            is_builtin=False
        )
        test_db_session.add(profile)
        test_db_session.commit()

        assert profile.id is not None
        assert profile.name == "custom_profile"
        assert profile.categories == ["features", "pricing"]
        assert profile.is_builtin is False

    def test_profile_name_must_be_unique(self, test_db_session: Session):
        """Test that profile name must be unique."""
        from orm_models import Profile
        from sqlalchemy.exc import IntegrityError

        profile1 = Profile(
            name="test_profile",
            categories=["test"],
            prompt_focus="test",
            depth="summary"
        )
        test_db_session.add(profile1)
        test_db_session.commit()

        profile2 = Profile(
            name="test_profile",
            categories=["test2"],
            prompt_focus="test2",
            depth="summary"
        )
        test_db_session.add(profile2)

        with pytest.raises(IntegrityError):
            test_db_session.commit()


class TestReportModel:
    """Tests for Report ORM model."""

    def test_report_model_exists(self):
        """Test that Report model can be imported."""
        from orm_models import Report
        assert Report is not None

    def test_create_report(self, test_db_session: Session):
        """Test creating a report."""
        from orm_models import Report

        report = Report(
            type="comparison",
            title="Auth Comparison: Provider A vs Provider B",
            content="# Comparison\n\nProvider A uses OAuth...",
            categories=["authentication"],
            format="md"
        )
        test_db_session.add(report)
        test_db_session.commit()

        assert report.id is not None
        assert report.type == "comparison"
        assert report.categories == ["authentication"]
        assert report.format == "md"


class TestRateLimitModel:
    """Tests for RateLimit ORM model."""

    def test_rate_limit_model_exists(self):
        """Test that RateLimit model can be imported."""
        from orm_models import RateLimit
        assert RateLimit is not None

    def test_create_rate_limit(self, test_db_session: Session):
        """Test creating a rate limit entry."""
        from orm_models import RateLimit
        from datetime import date

        rate_limit = RateLimit(
            domain="example.com",
            request_count=5,
            last_request=datetime.now(UTC),
            daily_count=50,
            daily_reset_at=date.today()
        )
        test_db_session.add(rate_limit)
        test_db_session.commit()

        assert rate_limit.domain == "example.com"
        assert rate_limit.request_count == 5
        assert rate_limit.daily_count == 50
