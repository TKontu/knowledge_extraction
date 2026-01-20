"""Tests for SchemaExtractionOrchestrator concurrency behavior.

These tests verify that chunk extraction uses continuous semaphore-based
concurrency instead of batch-and-wait, which keeps vLLM KV cache utilized.
"""

import asyncio
from unittest.mock import AsyncMock, Mock, patch
from uuid import uuid4

import pytest

from src.services.extraction.schema_orchestrator import SchemaExtractionOrchestrator
from src.services.extraction.field_groups import FieldGroup, FieldDefinition


@pytest.fixture
def mock_schema_extractor():
    """Mock SchemaExtractor."""
    return AsyncMock()


@pytest.fixture
def test_field_group():
    """Simple field group for testing."""
    return FieldGroup(
        name="test_group",
        description="Test group",
        fields=[
            FieldDefinition(name="test_field", field_type="text", description="Test field"),
        ],
        prompt_hint="Extract test field",
    )


@pytest.fixture
def orchestrator(mock_schema_extractor):
    """Create orchestrator with mocked extractor."""
    return SchemaExtractionOrchestrator(mock_schema_extractor)


class TestContinuousConcurrency:
    """Tests verifying continuous semaphore-based concurrency."""

    async def test_chunks_processed_with_continuous_flow(
        self, orchestrator, mock_schema_extractor, test_field_group
    ):
        """Verify requests start immediately when semaphore slot opens.

        With continuous flow: request N+1 starts as soon as ANY request completes.
        With batch-and-wait: request N+1 waits for ALL batch N requests to complete.

        This test verifies continuous flow by using varying delays:
        - Chunk 0: fast (0.02s)
        - Chunk 1: slow (0.10s)
        - With continuous flow, chunk 2 should start at ~0.02s when chunk 0 finishes
        - With batch-and-wait, chunk 2 would wait until 0.10s for both to finish
        """
        # Track when each extraction starts and completes
        extraction_events = []
        extraction_lock = asyncio.Lock()

        async def track_extraction(content, **kwargs):
            """Track extraction timing with varying delays."""
            chunk_id = content
            chunk_idx = int(chunk_id.split("_")[1])
            async with extraction_lock:
                extraction_events.append(("start", chunk_id, asyncio.get_event_loop().time()))

            # Alternating delays: even chunks fast (0.02s), odd chunks slow (0.10s)
            delay = 0.02 if chunk_idx % 2 == 0 else 0.10
            await asyncio.sleep(delay)

            async with extraction_lock:
                extraction_events.append(("end", chunk_id, asyncio.get_event_loop().time()))
            return {"test_field": f"result_{chunk_id}"}

        mock_schema_extractor.extract_field_group.side_effect = track_extraction

        # Create mock chunks
        chunks = [Mock(content=f"chunk_{i}") for i in range(6)]

        # Patch settings to use concurrency of 2
        with patch("src.services.extraction.schema_orchestrator.settings") as mock_settings:
            mock_settings.extraction_max_concurrent_chunks = 2
            mock_settings.llm_retry_backoff_min = 1
            mock_settings.llm_retry_backoff_max = 2

            results = await orchestrator._extract_chunks_batched(
                chunks=chunks,
                group=test_field_group,
                company_name="test_company",
            )

        # Verify all chunks processed
        assert len(results) == 6

        # Analyze timing: chunk_2 should start before chunk_1 ends
        # because chunk_0 finishes first (0.02s) while chunk_1 is still running (0.10s)
        starts = {e[1]: e[2] for e in extraction_events if e[0] == "start"}
        ends = {e[1]: e[2] for e in extraction_events if e[0] == "end"}

        # With continuous flow: chunk_2 starts when chunk_0 finishes (~0.02s)
        # before chunk_1 finishes (~0.10s)
        chunk_2_start = starts.get("chunk_2", 0)
        chunk_1_end = ends.get("chunk_1", 0)

        assert chunk_2_start < chunk_1_end, (
            f"Expected continuous flow: chunk_2 should start ({chunk_2_start}) "
            f"before chunk_1 ends ({chunk_1_end})"
        )

    async def test_max_concurrent_chunks_respected(
        self, orchestrator, mock_schema_extractor, test_field_group
    ):
        """Verify concurrency never exceeds extraction_max_concurrent_chunks."""
        max_concurrent_observed = 0
        current_concurrent = 0
        concurrent_lock = asyncio.Lock()

        async def track_concurrency(content, **kwargs):
            nonlocal max_concurrent_observed, current_concurrent
            async with concurrent_lock:
                current_concurrent += 1
                max_concurrent_observed = max(max_concurrent_observed, current_concurrent)
            await asyncio.sleep(0.02)  # Simulate work
            async with concurrent_lock:
                current_concurrent -= 1
            return {"test_field": "result"}

        mock_schema_extractor.extract_field_group.side_effect = track_concurrency

        # Create 10 chunks to process
        chunks = [Mock(content=f"chunk_{i}") for i in range(10)]

        # Set max concurrent to 3
        with patch("src.services.extraction.schema_orchestrator.settings") as mock_settings:
            mock_settings.extraction_max_concurrent_chunks = 3
            mock_settings.llm_retry_backoff_min = 1
            mock_settings.llm_retry_backoff_max = 2

            await orchestrator._extract_chunks_batched(
                chunks=chunks,
                group=test_field_group,
                company_name="test_company",
            )

        # Verify max concurrent never exceeded limit
        assert max_concurrent_observed <= 3, (
            f"Concurrency exceeded limit: observed {max_concurrent_observed}, max 3"
        )
        # Verify we actually achieved some parallelism
        assert max_concurrent_observed > 1, (
            "Expected some parallelism, but only 1 concurrent request observed"
        )

    async def test_all_chunks_complete_despite_varying_times(
        self, orchestrator, mock_schema_extractor, test_field_group
    ):
        """All chunks complete even with varying processing times."""
        completed_chunks = []

        async def varying_time_extraction(content, **kwargs):
            # Varying delays based on chunk index
            chunk_idx = int(content.split("_")[1])
            delay = 0.01 + (chunk_idx % 3) * 0.02  # 0.01, 0.03, 0.05 pattern
            await asyncio.sleep(delay)
            completed_chunks.append(content)
            return {"test_field": f"result_{content}"}

        mock_schema_extractor.extract_field_group.side_effect = varying_time_extraction

        chunks = [Mock(content=f"chunk_{i}") for i in range(8)]

        with patch("src.services.extraction.schema_orchestrator.settings") as mock_settings:
            mock_settings.extraction_max_concurrent_chunks = 4
            mock_settings.llm_retry_backoff_min = 1
            mock_settings.llm_retry_backoff_max = 2

            results = await orchestrator._extract_chunks_batched(
                chunks=chunks,
                group=test_field_group,
                company_name="test_company",
            )

        # All 8 chunks should complete
        assert len(results) == 8
        assert len(completed_chunks) == 8

    async def test_failed_chunks_dont_block_others(
        self, orchestrator, mock_schema_extractor, test_field_group
    ):
        """Failed extractions don't prevent other chunks from processing."""
        call_count = 0

        async def failing_extraction(content, **kwargs):
            nonlocal call_count
            call_count += 1
            chunk_idx = int(content.split("_")[1])
            if chunk_idx == 2:  # Chunk 2 always fails
                raise Exception("Simulated failure")
            await asyncio.sleep(0.01)
            return {"test_field": f"result_{content}"}

        mock_schema_extractor.extract_field_group.side_effect = failing_extraction

        chunks = [Mock(content=f"chunk_{i}") for i in range(5)]

        with patch("src.services.extraction.schema_orchestrator.settings") as mock_settings:
            mock_settings.extraction_max_concurrent_chunks = 2
            mock_settings.llm_retry_backoff_min = 0.01  # Fast retry for test
            mock_settings.llm_retry_backoff_max = 0.02

            results = await orchestrator._extract_chunks_batched(
                chunks=chunks,
                group=test_field_group,
                company_name="test_company",
            )

        # 4 successful + chunk_2 failed (with 3 retries)
        assert len(results) == 4  # chunk_2 failed after retries
        # Verify retries happened for the failing chunk
        assert call_count >= 7  # 4 success + 3 retries for chunk_2


class TestRetryBehavior:
    """Tests for retry behavior within concurrency control."""

    async def test_retry_within_semaphore(
        self, orchestrator, mock_schema_extractor, test_field_group
    ):
        """Retries should happen within the same semaphore slot."""
        attempt_counts = {}

        async def retry_extraction(content, **kwargs):
            chunk_id = content
            attempt_counts[chunk_id] = attempt_counts.get(chunk_id, 0) + 1
            if chunk_id == "chunk_1" and attempt_counts[chunk_id] < 3:
                raise Exception("Temporary failure")
            return {"test_field": f"result_{content}"}

        mock_schema_extractor.extract_field_group.side_effect = retry_extraction

        chunks = [Mock(content=f"chunk_{i}") for i in range(3)]

        with patch("src.services.extraction.schema_orchestrator.settings") as mock_settings:
            mock_settings.extraction_max_concurrent_chunks = 2
            mock_settings.llm_retry_backoff_min = 0.01
            mock_settings.llm_retry_backoff_max = 0.02

            results = await orchestrator._extract_chunks_batched(
                chunks=chunks,
                group=test_field_group,
                company_name="test_company",
            )

        # All chunks should succeed (chunk_1 after 3 attempts)
        assert len(results) == 3
        assert attempt_counts["chunk_1"] == 3  # 2 failures + 1 success
