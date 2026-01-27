# TODO: Dead Letter Queue for Scrape/Extraction Jobs

**Agent:** agent-dlq
**Branch:** `feat/scrape-extraction-dlq`
**Priority:** high

## Context

LLM requests have a Dead Letter Queue (Redis `llm:dlq`), but failed scrape/extraction jobs have NO recovery mechanism. Failed sources stay in `pending` status forever with no visibility.

**Existing DLQ pattern in `src/services/llm/worker.py`:**
- Uses Redis list `llm:dlq`
- Has `get_dlq_stats()` and `reprocess_dlq_item()` methods
- Stores JSON with error context

## Objective

Add DLQ support for scraping and extraction failures, following the existing LLM DLQ pattern, with API endpoints to list and retry failed items.

## Tasks

### 1. Create DLQ Service Module

**File:** `src/services/dlq/__init__.py` (new file)

**Requirements:**
- Create empty `__init__.py`

### 2. Create DLQ Service

**File:** `src/services/dlq/service.py` (new file)

**Requirements:**
- Define Redis keys: `SCRAPE_DLQ_KEY = "scrape:dlq"` and `EXTRACTION_DLQ_KEY = "extraction:dlq"`
- Implement `DLQService` class with methods:

```python
from dataclasses import dataclass
from datetime import datetime, UTC
from uuid import UUID
import json
from redis.asyncio import Redis

SCRAPE_DLQ_KEY = "scrape:dlq"
EXTRACTION_DLQ_KEY = "extraction:dlq"

@dataclass
class DLQItem:
    id: str  # UUID string
    source_id: str
    job_id: str | None
    error: str
    failed_at: str  # ISO format
    retry_count: int
    dlq_type: str  # "scrape" or "extraction"

class DLQService:
    def __init__(self, redis: Redis):
        self._redis = redis

    async def push_scrape_failure(
        self,
        source_id: UUID,
        error: str,
        job_id: UUID | None = None,
        retry_count: int = 0,
    ) -> None:
        """Push failed scrape to DLQ."""

    async def push_extraction_failure(
        self,
        source_id: UUID,
        error: str,
        job_id: UUID | None = None,
        retry_count: int = 0,
    ) -> None:
        """Push failed extraction to DLQ."""

    async def get_scrape_dlq(self, limit: int = 100) -> list[DLQItem]:
        """Get items from scrape DLQ."""

    async def get_extraction_dlq(self, limit: int = 100) -> list[DLQItem]:
        """Get items from extraction DLQ."""

    async def get_dlq_stats(self) -> dict:
        """Get counts for both DLQs."""
        # Returns {"scrape": count, "extraction": count}

    async def pop_scrape_item(self, item_id: str) -> DLQItem | None:
        """Remove and return item from scrape DLQ for retry."""

    async def pop_extraction_item(self, item_id: str) -> DLQItem | None:
        """Remove and return item from extraction DLQ for retry."""
```

**Implementation notes:**
- Use `lpush` to add items (newest first)
- Use `lrange` to list items
- Use `lrem` to remove specific item by value when popping
- Store items as JSON strings with UUID as `id` field
- Generate UUID for each DLQ entry to identify it

### 3. Create DLQ API Endpoints

**File:** `src/api/v1/dlq.py` (new file)

**Requirements:**
- Create FastAPI router with prefix `/dlq` and tag `dlq`
- Implement endpoints:

```python
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

router = APIRouter(prefix="/dlq", tags=["dlq"])

class DLQItemResponse(BaseModel):
    id: str
    source_id: str
    job_id: str | None
    error: str
    failed_at: str
    retry_count: int
    dlq_type: str

class DLQStatsResponse(BaseModel):
    scrape: int
    extraction: int

@router.get("/stats", response_model=DLQStatsResponse)
async def get_dlq_stats(...):
    """Get DLQ statistics."""

@router.get("/scrape", response_model=list[DLQItemResponse])
async def list_scrape_dlq(limit: int = 100, ...):
    """List failed scrape items."""

@router.get("/extraction", response_model=list[DLQItemResponse])
async def list_extraction_dlq(limit: int = 100, ...):
    """List failed extraction items."""

@router.post("/scrape/{item_id}/retry")
async def retry_scrape_item(item_id: str, ...):
    """Pop item from DLQ and re-queue for processing."""
    # For now, just pop and return the item
    # Actual re-queueing can be added later

@router.post("/extraction/{item_id}/retry")
async def retry_extraction_item(item_id: str, ...):
    """Pop item from DLQ and re-queue for processing."""
```

### 4. Register DLQ Router

**File:** `src/main.py`

**Requirements:**
- Import the DLQ router
- Add `app.include_router(dlq.router, prefix="/api/v1")`
- Follow the existing pattern for other routers in the file

### 5. Add DLQ Dependency

**File:** `src/api/dependencies.py`

**Requirements:**
- Add `get_dlq_service` dependency function that creates `DLQService` with Redis connection
- Follow existing patterns for dependencies in this file

### 6. Write Tests

**File:** `tests/test_dlq_service.py` (new file)

**Requirements:**
- Test `push_scrape_failure` adds item to Redis
- Test `push_extraction_failure` adds item to Redis
- Test `get_scrape_dlq` returns items in order
- Test `get_extraction_dlq` returns items in order
- Test `get_dlq_stats` returns correct counts
- Test `pop_scrape_item` removes and returns item
- Test `pop_extraction_item` removes and returns item
- Mock Redis for unit tests

**File:** `tests/test_dlq_api.py` (new file)

**Requirements:**
- Test GET `/api/v1/dlq/stats` returns stats
- Test GET `/api/v1/dlq/scrape` returns list
- Test GET `/api/v1/dlq/extraction` returns list
- Test POST `/api/v1/dlq/scrape/{id}/retry` pops item
- Test POST `/api/v1/dlq/extraction/{id}/retry` pops item
- Test 404 when item not found

## Constraints

- Do NOT modify existing workers to push to DLQ yet - that's a separate task
- Do NOT implement actual re-queueing logic in retry endpoints - just pop and return
- Do NOT run full test suite - only run tests in Test Scope below
- Do NOT lint entire codebase - only lint files in Lint Scope below
- Follow existing code patterns in the codebase

## Test Scope

**ONLY run these tests - do NOT run `pytest` without arguments:**

```bash
pytest tests/test_dlq_service.py -v
pytest tests/test_dlq_api.py -v
```

## Lint Scope

**ONLY lint these files - do NOT run `ruff check src/`:**

```bash
ruff check src/services/dlq/ src/api/v1/dlq.py
ruff format src/services/dlq/ src/api/v1/dlq.py
```

## Verification

Before creating PR:

1. `pytest tests/test_dlq_service.py -v` - Must pass
2. `pytest tests/test_dlq_api.py -v` - Must pass
3. `ruff check src/services/dlq/ src/api/v1/dlq.py` - Must be clean

## Definition of Done

- [ ] `DLQService` class created with all methods
- [ ] DLQ API endpoints created and registered
- [ ] Dependency function added
- [ ] Unit tests for service
- [ ] API tests for endpoints
- [ ] All scoped tests pass
- [ ] Lint clean (scoped)
- [ ] PR created with title: `feat: add DLQ for scrape and extraction failures`
