# TODO: Extraction Deduplicator

**Agent:** extraction-dedup
**Branch:** `feat/extraction-deduplicator`
**Priority:** medium
**Assigned:** 2026-01-11

## Context

The system extracts facts/extractions from documents, but the same information may be extracted multiple times from different sources or re-scrapes. We need a deduplication layer that uses embedding similarity to detect and prevent duplicate extractions.

**Existing infrastructure:**
- `EmbeddingService` (`services/storage/embedding.py`) - Generates embeddings via BGE-large-en
- `QdrantRepository` (`services/storage/qdrant/repository.py`) - Vector storage and search
- `ExtractionRepository` (`services/storage/repositories/extraction.py`) - Extraction CRUD

**Design decision:** Single similarity threshold (0.90) for MVP. Same-source_group deduplication only.

## Objective

Implement an ExtractionDeduplicator class that checks for existing similar extractions before allowing new ones to be stored, preventing duplicate information in the knowledge base.

## Tasks

### 1. Create ExtractionDeduplicator class

**File(s):** `src/services/storage/deduplication.py` (new file)

**Requirements:**
- Class that checks extraction similarity before storage
- Uses EmbeddingService to generate embedding for new extraction
- Uses QdrantRepository to search for similar existing extractions
- Configurable similarity threshold (default 0.90)
- Scoped to same project_id and source_group (don't dedupe across groups)

**Class structure:**
```python
from dataclasses import dataclass
from uuid import UUID
from typing import Optional

@dataclass
class DeduplicationResult:
    """Result of deduplication check."""
    is_duplicate: bool
    similar_extraction_id: Optional[UUID] = None
    similarity_score: Optional[float] = None

class ExtractionDeduplicator:
    """Checks for duplicate extractions using embedding similarity."""

    DEFAULT_THRESHOLD = 0.90

    def __init__(
        self,
        embedding_service: EmbeddingService,
        qdrant_repo: QdrantRepository,
        threshold: float = DEFAULT_THRESHOLD,
    ):
        """Initialize deduplicator."""

    async def check_duplicate(
        self,
        project_id: UUID,
        source_group: str,
        text_content: str,
    ) -> DeduplicationResult:
        """Check if similar extraction already exists."""

    async def get_text_from_extraction_data(self, data: dict) -> str:
        """Extract text content from extraction data dict."""
```

**Test cases to cover:**
- `test_init_with_default_threshold` - Default threshold is 0.90
- `test_init_with_custom_threshold` - Can set custom threshold

### 2. Implement `check_duplicate()` method

**File(s):** `src/services/storage/deduplication.py`

**Requirements:**
- Generate embedding for the input text
- Search Qdrant for similar vectors with filters:
  - `project_id` must match
  - `source_group` must match
- Return DeduplicationResult with:
  - `is_duplicate=True` if similarity >= threshold
  - `similar_extraction_id` of the most similar match
  - `similarity_score` of the best match
- Return `is_duplicate=False` if no matches above threshold

**Test cases to cover:**
- `test_check_duplicate_finds_similar` - Returns is_duplicate=True when similar exists
- `test_check_duplicate_no_match` - Returns is_duplicate=False when no similar
- `test_check_duplicate_below_threshold` - 0.89 similarity returns is_duplicate=False
- `test_check_duplicate_at_threshold` - 0.90 similarity returns is_duplicate=True
- `test_check_duplicate_scoped_to_project` - Different project not considered duplicate
- `test_check_duplicate_scoped_to_source_group` - Different source_group not duplicate
- `test_check_duplicate_returns_best_match` - Returns highest similarity match

### 3. Implement `get_text_from_extraction_data()` method

**File(s):** `src/services/storage/deduplication.py`

**Requirements:**
- Extract searchable text from extraction data dictionary
- Check common fields in order: `fact_text`, `text`, `content`, `summary`
- If none found, JSON serialize the whole dict
- Return string suitable for embedding

**Test cases to cover:**
- `test_get_text_from_fact_text_field` - Uses fact_text if present
- `test_get_text_from_text_field` - Falls back to text field
- `test_get_text_from_content_field` - Falls back to content field
- `test_get_text_serializes_dict` - Falls back to JSON serialization

### 4. Add convenience method `check_extraction_data()`

**File(s):** `src/services/storage/deduplication.py`

**Requirements:**
- Wrapper that accepts extraction data dict directly
- Extracts text using `get_text_from_extraction_data()`
- Calls `check_duplicate()` with extracted text

**Signature:**
```python
async def check_extraction_data(
    self,
    project_id: UUID,
    source_group: str,
    extraction_data: dict,
) -> DeduplicationResult:
    """Check if extraction data is a duplicate."""
```

**Test cases to cover:**
- `test_check_extraction_data_extracts_text` - Calls get_text internally
- `test_check_extraction_data_delegates_to_check_duplicate` - Uses check_duplicate

### 5. Create comprehensive test suite

**File(s):** `tests/test_extraction_deduplicator.py` (new file)

**Requirements:**
- Use pytest-asyncio
- Mock EmbeddingService and QdrantRepository
- Test threshold boundary conditions
- Test scoping (project_id, source_group)
- Follow TDD: write tests before implementation

**Test structure:**
```python
import pytest
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4
from services.storage.deduplication import (
    ExtractionDeduplicator,
    DeduplicationResult,
)

class TestExtractionDeduplicatorInit:
    """Tests for ExtractionDeduplicator initialization."""

class TestCheckDuplicate:
    """Tests for check_duplicate() method."""

class TestGetTextFromExtractionData:
    """Tests for get_text_from_extraction_data() method."""

class TestCheckExtractionData:
    """Tests for check_extraction_data() convenience method."""
```

## Constraints

- Do NOT modify `EmbeddingService` - it's complete and tested
- Do NOT modify `QdrantRepository` - it's complete and tested
- Do NOT modify `ExtractionRepository` - this is a standalone component
- Only create new files: `services/storage/deduplication.py`, `tests/test_extraction_deduplicator.py`
- Do NOT integrate with extraction pipeline yet (that's a separate task)
- Single threshold for MVP (no per-type thresholds)
- Use TDD: write tests first, then implement

## Verification

Before creating PR, confirm:
- [ ] All 5 tasks above completed
- [ ] `pytest tests/test_extraction_deduplicator.py -v` - All tests pass
- [ ] `pytest` - All 417+ tests still pass
- [ ] `ruff check src/` clean
- [ ] `ruff format src/` applied
- [ ] No new warnings

## Notes

**Embedding Service Usage:**
```python
# Generate embedding for text
embedding = await self._embedding_service.embed(text_content)
# Returns list[float] of length 1024 (BGE-large-en dimension)
```

**Qdrant Search Pattern:**
```python
from services.storage.qdrant.repository import SearchResult

results: list[SearchResult] = await self._qdrant_repo.search(
    query_embedding=embedding,
    limit=1,  # Only need best match
    filters={
        "project_id": str(project_id),
        "source_group": source_group,
    },
)

if results and results[0].score >= self._threshold:
    return DeduplicationResult(
        is_duplicate=True,
        similar_extraction_id=results[0].extraction_id,
        similarity_score=results[0].score,
    )
```

**Threshold Rationale:**
- 0.90 is conservative - will only mark as duplicate if very similar
- Prevents false positives (marking different facts as duplicates)
- Can be tuned later based on production experience
