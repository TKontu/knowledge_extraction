# TODO: Extraction Pipeline Service

**Agent:** pipeline-service
**Branch:** `feat/extraction-pipeline-service`
**Priority:** HIGH
**Assigned:** 2026-01-11

## Context

The system has these components built but NOT integrated:
- `ExtractionOrchestrator` (`services/extraction/extractor.py`) - chunks documents, calls LLM, deduplicates exact matches
- `ExtractionDeduplicator` (`services/storage/deduplication.py`) - embedding similarity check (0.90 threshold)
- `EntityExtractor` (`services/knowledge/extractor.py`) - extracts entities from extraction data via LLM
- `ExtractionRepository` (`services/storage/repositories/extraction.py`) - stores extractions in PostgreSQL
- `QdrantRepository` (`services/storage/qdrant/repository.py`) - stores embeddings for vector search
- `EmbeddingService` (`services/storage/embedding.py`) - generates embeddings

**Problem:** The extraction API creates a Job but nothing processes it. The components exist in isolation.

**Solution:** Create `ExtractionPipelineService` that orchestrates the full extraction flow.

## Objective

Create a unified extraction pipeline service that processes extraction jobs end-to-end: fetching sources, extracting facts, checking for duplicates, storing results, extracting entities, and indexing for search.

## Tasks

### 1. Create ExtractionPipelineService class

**File:** `src/services/extraction/pipeline.py` (new file)

**Requirements:**
- Orchestrates the complete extraction pipeline
- Accepts dependencies via constructor (dependency injection)
- Processes a single source or batch of sources
- Returns detailed results including what was extracted, deduplicated, and stored

**Class structure:**
```python
from dataclasses import dataclass
from uuid import UUID
from typing import Optional
import structlog

from services.extraction.extractor import ExtractionOrchestrator
from services.storage.deduplication import ExtractionDeduplicator
from services.knowledge.extractor import EntityExtractor
from services.storage.repositories.extraction import ExtractionRepository
from services.storage.repositories.source import SourceRepository
from services.storage.qdrant.repository import QdrantRepository
from services.storage.embedding import EmbeddingService

logger = structlog.get_logger(__name__)

@dataclass
class PipelineResult:
    """Result from processing a single source."""
    source_id: UUID
    extractions_created: int
    extractions_deduplicated: int
    entities_extracted: int
    entities_deduplicated: int
    errors: list[str]

@dataclass
class BatchPipelineResult:
    """Result from processing multiple sources."""
    sources_processed: int
    sources_failed: int
    total_extractions: int
    total_deduplicated: int
    total_entities: int
    results: list[PipelineResult]

class ExtractionPipelineService:
    """Orchestrates the complete extraction pipeline."""

    def __init__(
        self,
        orchestrator: ExtractionOrchestrator,
        deduplicator: ExtractionDeduplicator,
        entity_extractor: EntityExtractor,
        extraction_repo: ExtractionRepository,
        source_repo: SourceRepository,
        qdrant_repo: QdrantRepository,
        embedding_service: EmbeddingService,
    ):
        """Initialize pipeline with all dependencies."""

    async def process_source(
        self,
        source_id: UUID,
        project_id: UUID,
        profile_name: str = "general",
    ) -> PipelineResult:
        """Process a single source through the full pipeline."""

    async def process_batch(
        self,
        source_ids: list[UUID],
        project_id: UUID,
        profile_name: str = "general",
    ) -> BatchPipelineResult:
        """Process multiple sources."""

    async def process_project_pending(
        self,
        project_id: UUID,
        profile_name: str = "general",
    ) -> BatchPipelineResult:
        """Process all pending sources for a project."""
```

**Test cases:**
- `test_init_accepts_all_dependencies`
- `test_process_source_returns_pipeline_result`
- `test_process_batch_returns_batch_result`

### 2. Implement process_source() method

**File:** `src/services/extraction/pipeline.py`

**Requirements:**
The method should execute this flow:
1. Fetch source from SourceRepository
2. Validate source has content (skip if empty)
3. Get project config for entity types
4. Call ExtractionOrchestrator.extract() to get facts
5. For each extracted fact:
   a. Check for duplicate via ExtractionDeduplicator.check_duplicate()
   b. If not duplicate:
      - Store in ExtractionRepository
      - Generate embedding via EmbeddingService
      - Store embedding in QdrantRepository
      - Call EntityExtractor.extract() to extract entities
6. Update source status to "extracted"
7. Return PipelineResult with counts

**Implementation sketch:**
```python
async def process_source(
    self,
    source_id: UUID,
    project_id: UUID,
    profile_name: str = "general",
) -> PipelineResult:
    errors = []
    extractions_created = 0
    extractions_deduplicated = 0
    entities_extracted = 0

    # 1. Fetch source
    source = await self._source_repo.get(source_id)
    if not source or not source.content:
        return PipelineResult(
            source_id=source_id,
            extractions_created=0,
            extractions_deduplicated=0,
            entities_extracted=0,
            entities_deduplicated=0,
            errors=["Source not found or empty"],
        )

    # 2. Get extraction profile
    profile = await self._get_profile(profile_name)

    # 3. Extract facts via orchestrator
    result = await self._orchestrator.extract(
        page_id=source_id,  # Note: orchestrator uses page_id terminology
        markdown=source.content,
        profile=profile,
    )

    # 4. Process each fact
    for fact in result.facts:
        try:
            # Check for duplicate
            dedup_result = await self._deduplicator.check_duplicate(
                project_id=project_id,
                source_group=source.source_group,
                text_content=fact.fact,
            )

            if dedup_result.is_duplicate:
                extractions_deduplicated += 1
                continue

            # Store extraction
            extraction = await self._extraction_repo.create(
                project_id=project_id,
                source_id=source_id,
                data={"fact_text": fact.fact, "category": fact.category},
                extraction_type=fact.category,
                source_group=source.source_group,
                confidence=fact.confidence,
                profile_used=profile_name,
            )
            extractions_created += 1

            # Generate and store embedding
            embedding = await self._embedding_service.embed(fact.fact)
            await self._qdrant_repo.upsert(
                extraction_id=extraction.id,
                embedding=embedding,
                payload={
                    "project_id": str(project_id),
                    "source_group": source.source_group,
                    "extraction_type": fact.category,
                },
            )

            # Extract entities
            # ... entity extraction logic

        except Exception as e:
            errors.append(f"Error processing fact: {str(e)}")

    # Update source status
    await self._source_repo.update_status(source_id, "extracted")

    return PipelineResult(...)
```

**Test cases:**
- `test_process_source_extracts_facts` - Mocked orchestrator returns facts, they get stored
- `test_process_source_deduplicates` - Duplicate facts are skipped
- `test_process_source_stores_embeddings` - Embeddings stored in Qdrant
- `test_process_source_extracts_entities` - EntityExtractor called for each extraction
- `test_process_source_updates_source_status` - Source marked as "extracted"
- `test_process_source_handles_empty_source` - Returns gracefully for empty content
- `test_process_source_handles_errors` - Errors captured in result, processing continues

### 3. Implement process_batch() method

**File:** `src/services/extraction/pipeline.py`

**Requirements:**
- Iterate through source_ids and call process_source() for each
- Aggregate results into BatchPipelineResult
- Continue processing even if individual sources fail
- Log progress for long batches

**Test cases:**
- `test_process_batch_processes_all_sources`
- `test_process_batch_aggregates_results`
- `test_process_batch_continues_on_failure`

### 4. Implement process_project_pending() method

**File:** `src/services/extraction/pipeline.py`

**Requirements:**
- Query SourceRepository for all sources with status="pending" for the project
- Call process_batch() with the source IDs
- Return BatchPipelineResult

**Test cases:**
- `test_process_project_pending_finds_pending_sources`
- `test_process_project_pending_processes_all`

### 5. Create extraction worker

**File:** `src/services/extraction/worker.py` (new file)

**Requirements:**
- Background worker that processes extraction jobs from the database
- Similar pattern to `services/scraper/worker.py`
- Polls for jobs with type="extract" and status="queued"
- Initializes ExtractionPipelineService with all dependencies
- Calls process_project_pending() or process specific sources based on job payload

**Class structure:**
```python
class ExtractionWorker:
    """Background worker for processing extraction jobs."""

    def __init__(self, db_session_factory, settings):
        """Initialize worker with session factory and settings."""

    async def process_job(self, job: Job) -> dict:
        """Process a single extraction job."""

    async def run(self):
        """Main worker loop - poll for jobs and process."""
```

**Test cases:**
- `test_worker_processes_queued_jobs`
- `test_worker_updates_job_status`
- `test_worker_handles_job_failure`

### 6. Register worker in scheduler

**File:** `src/services/scraper/scheduler.py` (modify existing)

**Requirements:**
- Import ExtractionWorker
- Add extraction worker to the scheduler alongside scraper worker
- Both workers run concurrently

**Test cases:**
- `test_scheduler_runs_extraction_worker`

### 7. Create comprehensive test suite

**File:** `tests/test_extraction_pipeline.py` (new file)

**Requirements:**
- Mock all external dependencies (LLM, Qdrant, embedding service)
- Test the full pipeline flow
- Test error handling and edge cases
- Use pytest fixtures for common setup

## Constraints

- Do NOT modify ExtractionOrchestrator's core logic (just use it)
- Do NOT modify ExtractionDeduplicator (just use it)
- Do NOT modify EntityExtractor (just use it)
- Do NOT modify repository classes (just use them)
- Do NOT add new dependencies to requirements.txt
- Use TDD: write tests first, then implement
- All new code must have type hints

## Verification

Before creating PR, confirm:
- [ ] All 7 tasks above completed
- [ ] `pytest tests/test_extraction_pipeline.py -v` - All tests pass
- [ ] `pytest` - All 493+ tests still pass
- [ ] `ruff check src/` clean
- [ ] `ruff format src/` applied
- [ ] No new warnings
- [ ] Worker processes test job successfully (manual test)

## Notes

**Profile Loading:**
```python
from services.extraction.profiles import ProfileRepository

profile_repo = ProfileRepository(db)
profile = await profile_repo.get_by_name(profile_name)
if not profile:
    profile = await profile_repo.get_by_name("general")
```

**ExtractionRepository.create() signature:**
```python
async def create(
    self,
    project_id: UUID,
    source_id: UUID,
    data: dict,
    extraction_type: str,
    source_group: str,
    confidence: float | None = None,
    profile_used: str | None = None,
    chunk_index: int | None = None,
    chunk_context: dict | None = None,
) -> Extraction
```

**Entity Extraction Integration:**
```python
from services.knowledge.extractor import EntityExtractor

# Get entity types from project config
project = await project_repo.get(project_id)
entity_types = project.entity_types  # JSONB list of entity type defs

# Extract entities for each stored extraction
entities = await entity_extractor.extract(
    extraction_id=extraction.id,
    extraction_data={"fact_text": fact.fact, "category": fact.category},
    project_id=project_id,
    entity_types=entity_types,
    source_group=source.source_group,
)
```
