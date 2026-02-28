import os

# Set test API key before any imports that load settings
# This must be at the very top to avoid validation errors
os.environ.setdefault("API_KEY", "test-api-key-for-pytest-minimum-16-chars")

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker


@pytest.fixture
def db():
    """Create a database session wrapped in a transaction that rolls back.

    Every INSERT/UPDATE/DELETE performed during the test is undone after the
    test completes, so the production database is never modified.
    """
    # IMPORTANT: Import without 'src.' prefix so we get the SAME module objects
    # the FastAPI app uses internally (pythonpath=["src"] in pyproject.toml).
    # Using 'src.database' would create a separate module and break
    # app.dependency_overrides since the function objects would differ.
    from database import engine

    connection = engine.connect()
    transaction = connection.begin()
    session = Session(bind=connection)

    yield session

    session.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def client(db):
    """Create test client that shares the transactional db session.

    Overrides the app's get_db dependency so every request handled by the
    FastAPI TestClient uses the same rolled-back transaction as the ``db``
    fixture.  This guarantees zero writes to the production database.
    """
    from database import get_db
    from main import app

    def override_get_db():
        try:
            yield db
        finally:
            pass  # Don't close — the db fixture handles rollback

    app.dependency_overrides[get_db] = override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def valid_api_key():
    """Return valid API key from config."""
    from config import settings

    return settings.api_key


@pytest.fixture
def invalid_api_key():
    """Return an invalid API key."""
    return "invalid-key-12345"


@pytest.fixture(scope="session")
def test_db_engine():
    """Create PostgreSQL database engine for testing.

    Uses the app's configured database_url (which reads from .env / settings)
    so tests connect to the same DB as the running deployment.
    """
    from config import settings

    database_url = settings.database_url.replace(
        "postgresql://", "postgresql+psycopg://"
    )
    engine = create_engine(database_url)
    return engine
