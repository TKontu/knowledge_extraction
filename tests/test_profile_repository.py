"""Tests for profile repository."""

import pytest
from sqlalchemy.orm import sessionmaker, Session
from uuid import uuid4

from models import ExtractionProfile


@pytest.fixture
def test_db_session(test_db_engine):
    """Create test database session with transaction rollback."""
    from orm_models import Profile

    # Start a connection and transaction
    connection = test_db_engine.connect()
    transaction = connection.begin()

    # Create session bound to the connection
    TestSession = sessionmaker(bind=connection)
    session = TestSession()

    # Seed builtin profiles (may not exist if migration wasn't run)
    BUILTIN_PROFILES = [
        {
            "name": "technical_specs",
            "categories": ["specs", "hardware", "requirements", "compatibility", "performance"],
            "prompt_focus": "Hardware specifications, system requirements, supported platforms, performance metrics, compatibility information",
            "depth": "detailed",
        },
        {
            "name": "api_docs",
            "categories": ["endpoints", "authentication", "rate_limits", "sdks", "versioning"],
            "prompt_focus": "API endpoints, authentication methods, rate limits, SDK availability, API versioning, request/response formats",
            "depth": "detailed",
        },
        {
            "name": "security",
            "categories": ["certifications", "compliance", "encryption", "audit", "access_control"],
            "prompt_focus": "Security certifications (SOC2, ISO27001, etc), compliance standards, encryption methods, audit capabilities, access control features",
            "depth": "comprehensive",
        },
        {
            "name": "pricing",
            "categories": ["pricing", "tiers", "limits", "features"],
            "prompt_focus": "Pricing tiers, feature inclusions per tier, usage limits, enterprise options, free tier details",
            "depth": "detailed",
        },
        {
            "name": "general",
            "categories": ["general", "features", "technical", "integration"],
            "prompt_focus": "General technical facts about the product, features, integrations, and capabilities",
            "depth": "summary",
        },
    ]
    for p in BUILTIN_PROFILES:
        profile = Profile(
            name=p["name"],
            categories=p["categories"],
            prompt_focus=p["prompt_focus"],
            depth=p["depth"],
            is_builtin=True,
        )
        session.add(profile)
    session.flush()

    # Insert custom test profile
    custom_profile = Profile(
        name="custom_test",
        categories=["test_category"],
        prompt_focus="Custom test profile",
        depth="summary",
        custom_instructions="Custom instructions here",
        is_builtin=False,
    )
    session.add(custom_profile)
    session.flush()

    yield session

    # Rollback transaction and close connection
    session.close()
    transaction.rollback()
    connection.close()


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
        assert profile.prompt_focus == "Hardware specifications, system requirements, supported platforms, performance metrics, compatibility information"
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

        assert len(profiles) == 6  # 5 builtin + 1 custom
        assert all(isinstance(p, ExtractionProfile) for p in profiles)
        profile_names = [p.name for p in profiles]
        # Check for some builtin profiles
        assert "technical_specs" in profile_names
        assert "api_docs" in profile_names
        assert "security" in profile_names
        assert "pricing" in profile_names
        assert "general" in profile_names
        # Check for custom profile
        assert "custom_test" in profile_names

    def test_list_builtin_profiles_only(self, test_db_session: Session):
        """Test listing only built-in profiles."""
        from services.extraction.profiles import ProfileRepository

        repo = ProfileRepository(test_db_session)
        profiles = repo.list_builtin()

        assert len(profiles) == 5  # All seeded builtin profiles
        assert all(isinstance(p, ExtractionProfile) for p in profiles)
        assert all(p.is_builtin for p in profiles)
        profile_names = [p.name for p in profiles]
        # Check for all builtin profiles
        assert "technical_specs" in profile_names
        assert "api_docs" in profile_names
        assert "security" in profile_names
        assert "pricing" in profile_names
        assert "general" in profile_names
        # Ensure custom profile is not included
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
        """Test listing profiles when database has no profiles."""
        from orm_models import Profile
        from services.extraction.profiles import ProfileRepository

        # Use a transactional session so we don't destroy production profiles
        connection = test_db_engine.connect()
        transaction = connection.begin()
        TestSession = sessionmaker(bind=connection)
        session = TestSession()

        try:
            # Delete all profiles within transaction (will be rolled back)
            session.query(Profile).delete()
            session.flush()

            repo = ProfileRepository(session)
            profiles = repo.list_all()

            assert profiles == []
        finally:
            session.close()
            transaction.rollback()
            connection.close()

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
