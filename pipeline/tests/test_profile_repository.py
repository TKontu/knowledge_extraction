"""Tests for profile repository."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, Session
from uuid import uuid4

from models import ExtractionProfile


@pytest.fixture
def test_db_engine():
    """Create in-memory SQLite database for testing."""
    engine = create_engine("sqlite:///:memory:")
    return engine


@pytest.fixture
def test_db_session(test_db_engine):
    """Create test database session with sample profiles."""
    from orm_models import Base, Profile

    # Create all tables
    Base.metadata.create_all(test_db_engine)

    # Create session
    TestSession = sessionmaker(bind=test_db_engine)
    session = TestSession()

    # Insert test profiles
    technical_profile = Profile(
        name="technical_specs",
        categories=["specs", "hardware", "requirements", "compatibility", "performance"],
        prompt_focus="Hardware specifications, system requirements, supported platforms",
        depth="detailed",
        is_builtin=True,
    )
    api_profile = Profile(
        name="api_docs",
        categories=["endpoints", "authentication", "rate_limits", "sdks", "versioning"],
        prompt_focus="API endpoints, authentication methods, rate limits",
        depth="detailed",
        is_builtin=True,
    )
    custom_profile = Profile(
        name="custom_test",
        categories=["test_category"],
        prompt_focus="Custom test profile",
        depth="summary",
        custom_instructions="Custom instructions here",
        is_builtin=False,
    )

    session.add_all([technical_profile, api_profile, custom_profile])
    session.commit()

    yield session

    session.close()
    Base.metadata.drop_all(test_db_engine)


class TestProfileRepository:
    """Tests for ProfileRepository class."""

    def test_get_profile_by_name_returns_profile(self, test_db_session: Session):
        """Test getting an existing profile by name."""
        from services.extraction.profiles import ProfileRepository

        repo = ProfileRepository(test_db_session)
        profile = repo.get_by_name("technical_specs")

        assert profile is not None
        assert isinstance(profile, ExtractionProfile)
        assert profile.name == "technical_specs"
        assert profile.categories == ["specs", "hardware", "requirements", "compatibility", "performance"]
        assert profile.prompt_focus == "Hardware specifications, system requirements, supported platforms"
        assert profile.depth == "detailed"
        assert profile.is_builtin is True
        assert profile.custom_instructions is None

    def test_get_profile_by_name_returns_none_when_not_found(self, test_db_session: Session):
        """Test getting a profile that doesn't exist."""
        from services.extraction.profiles import ProfileRepository

        repo = ProfileRepository(test_db_session)
        profile = repo.get_by_name("nonexistent_profile")

        assert profile is None

    def test_get_profile_with_custom_instructions(self, test_db_session: Session):
        """Test getting a profile with custom instructions."""
        from services.extraction.profiles import ProfileRepository

        repo = ProfileRepository(test_db_session)
        profile = repo.get_by_name("custom_test")

        assert profile is not None
        assert profile.name == "custom_test"
        assert profile.custom_instructions == "Custom instructions here"
        assert profile.is_builtin is False

    def test_list_all_profiles(self, test_db_session: Session):
        """Test listing all profiles."""
        from services.extraction.profiles import ProfileRepository

        repo = ProfileRepository(test_db_session)
        profiles = repo.list_all()

        assert len(profiles) == 3
        assert all(isinstance(p, ExtractionProfile) for p in profiles)
        profile_names = [p.name for p in profiles]
        assert "technical_specs" in profile_names
        assert "api_docs" in profile_names
        assert "custom_test" in profile_names

    def test_list_builtin_profiles_only(self, test_db_session: Session):
        """Test listing only built-in profiles."""
        from services.extraction.profiles import ProfileRepository

        repo = ProfileRepository(test_db_session)
        profiles = repo.list_builtin()

        assert len(profiles) == 2
        assert all(isinstance(p, ExtractionProfile) for p in profiles)
        assert all(p.is_builtin for p in profiles)
        profile_names = [p.name for p in profiles]
        assert "technical_specs" in profile_names
        assert "api_docs" in profile_names
        assert "custom_test" not in profile_names

    def test_profile_exists_returns_true_when_exists(self, test_db_session: Session):
        """Test checking if a profile exists."""
        from services.extraction.profiles import ProfileRepository

        repo = ProfileRepository(test_db_session)
        exists = repo.exists("technical_specs")

        assert exists is True

    def test_profile_exists_returns_false_when_not_found(self, test_db_session: Session):
        """Test checking if a nonexistent profile exists."""
        from services.extraction.profiles import ProfileRepository

        repo = ProfileRepository(test_db_session)
        exists = repo.exists("nonexistent_profile")

        assert exists is False

    def test_list_all_returns_empty_list_when_no_profiles(self, test_db_engine):
        """Test listing profiles when database is empty."""
        from orm_models import Base, Profile
        from services.extraction.profiles import ProfileRepository

        # Create clean session without adding profiles
        Base.metadata.create_all(test_db_engine)
        TestSession = sessionmaker(bind=test_db_engine)
        session = TestSession()

        repo = ProfileRepository(session)
        profiles = repo.list_all()

        assert profiles == []

        session.close()
        Base.metadata.drop_all(test_db_engine)

    def test_get_profile_case_sensitive(self, test_db_session: Session):
        """Test that profile names are case-sensitive."""
        from services.extraction.profiles import ProfileRepository

        repo = ProfileRepository(test_db_session)
        profile = repo.get_by_name("Technical_Specs")  # Wrong case

        # SQLite is case-insensitive by default, but PostgreSQL is case-sensitive
        # This test documents the expected behavior
        assert profile is None

    def test_repository_requires_session(self):
        """Test that ProfileRepository requires a database session."""
        from services.extraction.profiles import ProfileRepository

        with pytest.raises(TypeError):
            ProfileRepository()  # Should fail without session argument
