import pytest
from fastapi.testclient import TestClient


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
