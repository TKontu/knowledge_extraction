"""Tests for application startup and initialization."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, Mock
from qdrant_client import QdrantClient
from uuid import uuid4


class TestQdrantInitialization:
    """Test that Qdrant collection is initialized during application startup."""

    @pytest.mark.asyncio
    async def test_qdrant_collection_exists_after_startup(self):
        """Should create 'extractions' collection during application startup.

        Current bug: Collection is never initialized, causing 404 errors when
        storing facts after extraction.

        After fix: The lifespan function should call qdrant_repo.init_collection()
        to ensure the collection exists.
        """
        from qdrant_connection import qdrant_client
        from main import lifespan, app

        # Clean up any existing collection first
        try:
            qdrant_client.delete_collection("extractions")
        except Exception:
            pass

        # Verify collection doesn't exist before startup
        collections_before = qdrant_client.get_collections().collections
        collection_names_before = [c.name for c in collections_before]
        assert "extractions" not in collection_names_before

        # Simulate app startup by running the lifespan
        async with lifespan(app):
            # Check collections during app lifetime
            collections_during = qdrant_client.get_collections().collections
            collection_names_during = [c.name for c in collections_during]

            # The 'extractions' collection should now exist
            assert "extractions" in collection_names_during, \
                "The 'extractions' collection should be created during app startup"


class TestSearchEndpointQdrantUsage:
    """Test that search endpoint uses QdrantRepository correctly."""

    def test_search_endpoint_should_use_qdrant_client_not_settings(self):
        """Search endpoint should pass qdrant_client to QdrantRepository, not settings.

        Current bug: Line 59 in src/api/v1/search.py has:
            qdrant_repo = QdrantRepository(settings)

        Should be:
            qdrant_repo = QdrantRepository(qdrant_client)

        This test verifies the source code directly.
        """
        import inspect
        from pathlib import Path

        # Read the search.py source file
        search_file = Path("src/api/v1/search.py")
        source_code = search_file.read_text()

        # Check if the buggy line exists
        buggy_line = "QdrantRepository(settings)"

        # This will FAIL as long as the bug exists
        assert buggy_line not in source_code, \
            f"Found 'QdrantRepository(settings)' in search.py. " \
            f"Should use 'QdrantRepository(qdrant_client)' instead. " \
            f"QdrantRepository expects a QdrantClient instance, not Settings."

        # Also verify the correct pattern exists
        correct_import = "from qdrant_connection import qdrant_client"
        correct_usage = "QdrantRepository(qdrant_client)"

        assert correct_import in source_code, \
            f"Missing import: '{correct_import}' in search.py"

        assert correct_usage in source_code, \
            f"Missing correct usage: '{correct_usage}' in search.py"
