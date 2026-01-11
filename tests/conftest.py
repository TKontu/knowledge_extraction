import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
import os


@pytest.fixture
def client():
    """Create test client for FastAPI app."""
    from main import app

    return TestClient(app)


@pytest.fixture
def valid_api_key():
    """Return valid API key from config."""
    from config import settings

    return settings.api_key


@pytest.fixture
def invalid_api_key():
    """Return an invalid API key."""
    return "invalid-key-12345"


@pytest.fixture
def db():
    """Create a database session for testing."""
    from database import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture(scope="session")
def test_db_engine():
    """Create PostgreSQL database engine for testing.

    Uses the same database as the main app but ensures tests use transactions
    that can be rolled back.
    """
    database_url = os.environ.get(
        "DATABASE_URL",
        "postgresql+psycopg://techfacts:techfacts@localhost:5432/techfacts"
    )
    engine = create_engine(database_url)
    return engine
