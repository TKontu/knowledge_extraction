"""Qdrant connection and client management."""

from collections.abc import Generator

from qdrant_client import QdrantClient
from qdrant_client.http.exceptions import ResponseHandlingException, UnexpectedResponse

from config import settings

# Create Qdrant client
qdrant_client = QdrantClient(
    url=settings.qdrant_url,
    timeout=5.0,
)


def get_qdrant() -> Generator[QdrantClient, None, None]:
    """
    Dependency for getting Qdrant client.

    Yields:
        Qdrant client instance.
    """
    yield qdrant_client


def check_qdrant_connection() -> bool:
    """
    Check if Qdrant connection is working.

    Returns:
        True if connection succeeds, False otherwise.
    """
    try:
        # Check health to verify connection
        qdrant_client.get_collections()
        return True
    except (UnexpectedResponse, ResponseHandlingException, ConnectionError, OSError):
        return False
