"""Tests for QdrantRepository."""

from uuid import uuid4

import pytest
from qdrant_client import QdrantClient
from qdrant_client.models import Distance

from services.storage.qdrant.repository import (
    EmbeddingItem,
    QdrantRepository,
    SearchResult,
)


@pytest.fixture
def qdrant_client():
    """Create a Qdrant client connected to the test instance."""
    from config import settings

    client = QdrantClient(url=settings.qdrant_url, timeout=5.0)
    return client


@pytest.fixture
def qdrant_repo(qdrant_client):
    """Create QdrantRepository instance."""
    repo = QdrantRepository(qdrant_client)
    # Clean up any existing test collection
    try:
        qdrant_client.delete_collection(repo.collection_name)
    except Exception:
        pass
    return repo


class TestQdrantRepositoryInitCollection:
    """Test QdrantRepository.init_collection() method."""

    async def test_creates_collection_if_not_exists(self, qdrant_repo, qdrant_client):
        """Should create collection with correct configuration if it doesn't exist."""
        # Ensure collection doesn't exist
        collections = qdrant_client.get_collections().collections
        assert not any(c.name == qdrant_repo.collection_name for c in collections)

        # Initialize collection
        await qdrant_repo.init_collection()

        # Verify collection was created
        collections = qdrant_client.get_collections().collections
        assert any(c.name == qdrant_repo.collection_name for c in collections)

        # Verify collection configuration
        collection_info = qdrant_client.get_collection(qdrant_repo.collection_name)
        assert collection_info.config.params.vectors.size == 1024  # BGE-large-en
        assert collection_info.config.params.vectors.distance == Distance.COSINE

    async def test_init_collection_is_idempotent(self, qdrant_repo, qdrant_client):
        """Should not fail when called multiple times."""
        # Create collection first time
        await qdrant_repo.init_collection()

        # Verify collection exists
        collections = qdrant_client.get_collections().collections
        assert any(c.name == qdrant_repo.collection_name for c in collections)

        # Call again - should not raise error
        await qdrant_repo.init_collection()

        # Collection should still exist with same configuration
        collection_info = qdrant_client.get_collection(qdrant_repo.collection_name)
        assert collection_info.config.params.vectors.size == 1024


class TestQdrantRepositoryUpsert:
    """Test QdrantRepository.upsert() method."""

    async def test_upsert_new_embedding(self, qdrant_repo, qdrant_client):
        """Should insert new embedding with payload."""
        # Initialize collection first
        await qdrant_repo.init_collection()

        # Create test data
        extraction_id = uuid4()
        embedding = [0.1] * 1024  # 1024-dimensional vector
        payload = {
            "project_id": str(uuid4()),
            "source_group": "test_company",
            "extraction_type": "technical_fact",
            "confidence": 0.9,
        }

        # Upsert embedding
        point_id = await qdrant_repo.upsert(
            extraction_id=extraction_id,
            embedding=embedding,
            payload=payload,
        )

        # Should return extraction_id as string
        assert point_id == str(extraction_id)

        # Verify point was stored
        points = qdrant_client.retrieve(
            collection_name=qdrant_repo.collection_name,
            ids=[str(extraction_id)],
        )
        assert len(points) == 1
        assert points[0].id == str(extraction_id)
        assert points[0].payload == payload

    async def test_upsert_updates_existing_embedding(self, qdrant_repo, qdrant_client):
        """Should update existing embedding when called with same extraction_id."""
        # Initialize collection
        await qdrant_repo.init_collection()

        extraction_id = uuid4()
        embedding = [0.1] * 1024
        original_payload = {"source_group": "company_a", "confidence": 0.8}

        # Insert first time
        await qdrant_repo.upsert(extraction_id, embedding, original_payload)

        # Update with new payload
        updated_payload = {"source_group": "company_b", "confidence": 0.95}
        await qdrant_repo.upsert(extraction_id, embedding, updated_payload)

        # Verify only one point exists with updated payload
        points = qdrant_client.retrieve(
            collection_name=qdrant_repo.collection_name,
            ids=[str(extraction_id)],
        )
        assert len(points) == 1
        assert points[0].payload == updated_payload


class TestQdrantRepositoryUpsertBatch:
    """Test QdrantRepository.upsert_batch() method."""

    async def test_upsert_batch_inserts_multiple_embeddings(
        self, qdrant_repo, qdrant_client
    ):
        """Should insert multiple embeddings in one batch."""
        # Initialize collection
        await qdrant_repo.init_collection()

        # Create batch items
        items = [
            EmbeddingItem(
                extraction_id=uuid4(),
                embedding=[0.1] * 1024,
                payload={"source_group": "company_a", "confidence": 0.8},
            ),
            EmbeddingItem(
                extraction_id=uuid4(),
                embedding=[0.2] * 1024,
                payload={"source_group": "company_b", "confidence": 0.9},
            ),
            EmbeddingItem(
                extraction_id=uuid4(),
                embedding=[0.3] * 1024,
                payload={"source_group": "company_c", "confidence": 0.95},
            ),
        ]

        # Batch upsert
        point_ids = await qdrant_repo.upsert_batch(items)

        # Should return list of point IDs
        assert len(point_ids) == 3
        assert all(isinstance(pid, str) for pid in point_ids)

        # Verify all points were stored
        points = qdrant_client.retrieve(
            collection_name=qdrant_repo.collection_name,
            ids=point_ids,
        )
        assert len(points) == 3

    async def test_upsert_batch_with_empty_list(self, qdrant_repo, qdrant_client):
        """Should handle empty list gracefully."""
        await qdrant_repo.init_collection()

        # Batch upsert with empty list
        point_ids = await qdrant_repo.upsert_batch([])

        # Should return empty list
        assert point_ids == []


class TestQdrantRepositorySearch:
    """Test QdrantRepository.search() method."""

    async def test_search_finds_similar_embeddings(self, qdrant_repo, qdrant_client):
        """Should return similar embeddings ordered by score."""
        # Initialize collection and add test data
        await qdrant_repo.init_collection()

        # Insert test embeddings
        items = [
            EmbeddingItem(
                extraction_id=uuid4(),
                embedding=[0.9] + [0.1] * 1023,  # Similar to query
                payload={"source_group": "company_a", "confidence": 0.9},
            ),
            EmbeddingItem(
                extraction_id=uuid4(),
                embedding=[0.1] * 1024,  # Different from query
                payload={"source_group": "company_b", "confidence": 0.8},
            ),
            EmbeddingItem(
                extraction_id=uuid4(),
                embedding=[0.85] + [0.15] * 1023,  # Similar to query
                payload={"source_group": "company_c", "confidence": 0.95},
            ),
        ]
        await qdrant_repo.upsert_batch(items)

        # Search with query similar to first and third items
        query_embedding = [1.0] + [0.0] * 1023
        results = await qdrant_repo.search(
            query_embedding=query_embedding,
            limit=2,
        )

        # Should return 2 most similar results
        assert len(results) == 2
        assert all(isinstance(r, SearchResult) for r in results)

        # Results should be ordered by similarity (highest score first)
        assert results[0].score > results[1].score

        # Should have extraction IDs
        assert all(r.extraction_id is not None for r in results)

    async def test_search_with_filters(self, qdrant_repo, qdrant_client):
        """Should filter results by payload fields."""
        await qdrant_repo.init_collection()

        # Insert test embeddings with different source_groups
        items = [
            EmbeddingItem(
                extraction_id=uuid4(),
                embedding=[0.9] * 1024,
                payload={"source_group": "company_a", "extraction_type": "api"},
            ),
            EmbeddingItem(
                extraction_id=uuid4(),
                embedding=[0.9] * 1024,  # Same similarity
                payload={"source_group": "company_b", "extraction_type": "pricing"},
            ),
        ]
        await qdrant_repo.upsert_batch(items)

        # Search with filter for company_a
        query_embedding = [1.0] * 1024
        results = await qdrant_repo.search(
            query_embedding=query_embedding,
            limit=10,
            filters={"source_group": "company_a"},
        )

        # Should only return company_a results
        assert len(results) == 1
        assert results[0].payload["source_group"] == "company_a"

    async def test_search_returns_payload(self, qdrant_repo, qdrant_client):
        """Should include payload in search results."""
        await qdrant_repo.init_collection()

        extraction_id = uuid4()
        payload = {
            "source_group": "test_company",
            "extraction_type": "feature",
            "confidence": 0.95,
        }

        await qdrant_repo.upsert(
            extraction_id=extraction_id,
            embedding=[0.5] * 1024,
            payload=payload,
        )

        # Search
        results = await qdrant_repo.search(
            query_embedding=[0.5] * 1024,
            limit=1,
        )

        # Should include payload
        assert len(results) == 1
        assert results[0].payload == payload
        assert results[0].extraction_id == extraction_id


class TestQdrantRepositoryDelete:
    """Test QdrantRepository.delete() method."""

    async def test_delete_existing_embedding(self, qdrant_repo, qdrant_client):
        """Should delete existing embedding and return True."""
        # Initialize collection and insert embedding
        await qdrant_repo.init_collection()

        extraction_id = uuid4()
        await qdrant_repo.upsert(
            extraction_id=extraction_id,
            embedding=[0.5] * 1024,
            payload={"test": "data"},
        )

        # Verify it exists
        points = qdrant_client.retrieve(
            collection_name=qdrant_repo.collection_name,
            ids=[str(extraction_id)],
        )
        assert len(points) == 1

        # Delete it
        success = await qdrant_repo.delete(extraction_id)

        # Should return True
        assert success is True

        # Verify it was deleted
        points = qdrant_client.retrieve(
            collection_name=qdrant_repo.collection_name,
            ids=[str(extraction_id)],
        )
        assert len(points) == 0

    async def test_delete_nonexistent_embedding(self, qdrant_repo, qdrant_client):
        """Should return True even if embedding doesn't exist (idempotent)."""
        await qdrant_repo.init_collection()

        # Try to delete non-existent embedding
        nonexistent_id = uuid4()
        success = await qdrant_repo.delete(nonexistent_id)

        # Should still return True (idempotent operation)
        assert success is True

    async def test_delete_does_not_affect_other_embeddings(
        self, qdrant_repo, qdrant_client
    ):
        """Should only delete specified embedding, not others."""
        await qdrant_repo.init_collection()

        # Insert two embeddings
        id1 = uuid4()
        id2 = uuid4()

        await qdrant_repo.upsert(id1, [0.1] * 1024, {"name": "first"})
        await qdrant_repo.upsert(id2, [0.2] * 1024, {"name": "second"})

        # Delete first one
        await qdrant_repo.delete(id1)

        # Verify first is deleted
        points = qdrant_client.retrieve(
            collection_name=qdrant_repo.collection_name,
            ids=[str(id1)],
        )
        assert len(points) == 0

        # Verify second still exists
        points = qdrant_client.retrieve(
            collection_name=qdrant_repo.collection_name,
            ids=[str(id2)],
        )
        assert len(points) == 1
        assert points[0].payload["name"] == "second"
