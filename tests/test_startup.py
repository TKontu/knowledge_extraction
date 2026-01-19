"""Tests for application startup and initialization."""

import pytest
from unittest.mock import AsyncMock, MagicMock, patch, Mock
from qdrant_client import QdrantClient
from uuid import uuid4


class TestQdrantInitializationWithRetry:
    """Test Qdrant initialization with retry logic for production robustness."""

    @pytest.mark.asyncio
    async def test_init_succeeds_on_first_try(self):
        """Should initialize collection successfully on first attempt."""
        from main import lifespan, app
        from qdrant_connection import qdrant_client

        # Mock init_collection to succeed immediately
        with patch("main.QdrantRepository") as mock_repo_class:
            mock_repo = Mock()
            mock_repo.init_collection = AsyncMock()
            mock_repo_class.return_value = mock_repo

            async with lifespan(app):
                pass

            # Should call init_collection exactly once
            mock_repo.init_collection.assert_called_once()

    @pytest.mark.asyncio
    async def test_init_retries_on_transient_failure(self):
        """Should retry initialization if Qdrant is temporarily unavailable.

        This tests the scenario where Qdrant container starts but isn't ready
        to accept connections yet.
        """
        from main import lifespan, app

        # Mock init_collection to fail first, then succeed
        with patch("main.QdrantRepository") as mock_repo_class:
            mock_repo = Mock()
            # Fail on first call, succeed on second
            mock_repo.init_collection = AsyncMock(
                side_effect=[
                    ConnectionError("Connection refused"),  # First attempt fails
                    None,  # Second attempt succeeds
                ]
            )
            mock_repo_class.return_value = mock_repo

            # This will FAIL until we add retry logic
            async with lifespan(app):
                pass

            # Should have retried and eventually succeeded
            assert mock_repo.init_collection.call_count == 2

    @pytest.mark.asyncio
    async def test_init_logs_warning_on_final_failure(self):
        """Should log warning but not crash if init fails after all retries.

        Application should start even if Qdrant is unavailable - collection
        will be created on first use.
        """
        from main import lifespan, app
        import structlog

        # Mock init_collection to always fail
        with patch("main.QdrantRepository") as mock_repo_class:
            mock_repo = Mock()
            mock_repo.init_collection = AsyncMock(
                side_effect=ConnectionError("Connection refused")
            )
            mock_repo_class.return_value = mock_repo

            # Mock logger to verify warning is logged
            with patch("main.logger") as mock_logger:
                # This should NOT crash, just log warning
                async with lifespan(app):
                    pass

                # Should have logged a warning about the failure
                # This will FAIL until we add error handling
                warning_calls = [
                    call for call in mock_logger.warning.call_args_list
                    if "qdrant" in str(call).lower()
                ]
                assert len(warning_calls) > 0, "Should log warning about Qdrant init failure"

    @pytest.mark.asyncio
    async def test_init_uses_exponential_backoff(self):
        """Should use exponential backoff between retries (2^attempt seconds)."""
        from main import lifespan, app
        import asyncio

        # Mock init_collection to fail multiple times
        with patch("main.QdrantRepository") as mock_repo_class:
            mock_repo = Mock()
            mock_repo.init_collection = AsyncMock(
                side_effect=[
                    ConnectionError("Connection refused"),
                    ConnectionError("Connection refused"),
                    None,  # Succeed on third attempt
                ]
            )
            mock_repo_class.return_value = mock_repo

            # Mock asyncio.sleep to verify backoff timing
            with patch("main.asyncio.sleep", new_callable=AsyncMock) as mock_sleep:
                async with lifespan(app):
                    pass

                # Should have slept with exponential backoff
                # This will FAIL until we implement backoff
                sleep_calls = [call[0][0] for call in mock_sleep.call_args_list]

                # Expect: 1 second after first failure, 2 seconds after second failure
                assert len(sleep_calls) >= 2, "Should sleep between retries"
                assert sleep_calls[0] == 1, "First retry should wait 1 second"
                assert sleep_calls[1] == 2, "Second retry should wait 2 seconds"


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
