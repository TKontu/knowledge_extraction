# TODO: Search API Endpoint

**Agent:** search-api
**Branch:** `feat/search-api-endpoint`
**Priority:** high
**Assigned:** 2026-01-11

## Context

The SearchService (`services/storage/search.py`) is fully implemented with hybrid semantic + structured search. It combines Qdrant vector search with PostgreSQL JSONB filtering. You need to expose this functionality via a REST API endpoint.

**Existing infrastructure:**
- `SearchService` - `search()` method returns `list[ExtractionSearchResult]`
- `EmbeddingService` - Generates embeddings via BGE-large-en
- `QdrantRepository` - Vector similarity search
- `ExtractionRepository` - JSONB filtering

**API pattern to follow:** See `api/v1/extraction.py` for endpoint structure, error handling, and response patterns.

## Objective

Create a Search API endpoint that exposes the SearchService's hybrid search capabilities with proper request validation, error handling, and response formatting.

## Tasks

### 1. Create request/response models

**File(s):** `src/models.py` (add to existing file)

**Requirements:**
Add these Pydantic models to the existing `models.py`:

```python
class SearchRequest(BaseModel):
    """Request body for search endpoint."""
    query: str = Field(..., min_length=1, max_length=1000, description="Search query")
    limit: int = Field(default=10, ge=1, le=100, description="Max results")
    source_groups: list[str] | None = Field(default=None, description="Filter by source groups")
    filters: dict[str, Any] | None = Field(default=None, description="JSONB filters")

class SearchResultItem(BaseModel):
    """Single search result."""
    extraction_id: str
    score: float
    data: dict[str, Any]
    source_group: str
    source_uri: str
    confidence: float | None

class SearchResponse(BaseModel):
    """Response for search endpoint."""
    results: list[SearchResultItem]
    query: str
    total: int
```

**Test cases to cover:**
- `test_search_request_validates_query_length` - Empty query rejected
- `test_search_request_limit_bounds` - limit must be 1-100
- `test_search_response_serialization` - Can serialize results

### 2. Create search endpoint

**File(s):** `src/api/v1/search.py` (new file)

**Requirements:**
- Create new router with prefix `/api/v1` and tag `search`
- Implement `POST /api/v1/projects/{project_id}/search` endpoint
- Validate project exists (404 if not)
- Validate project_id UUID format (422 if invalid)
- Initialize dependencies: EmbeddingService, QdrantRepository, ExtractionRepository, SearchService
- Call SearchService.search() with request parameters
- Return SearchResponse with results

**Endpoint signature:**
```python
@router.post("/projects/{project_id}/search", status_code=status.HTTP_200_OK)
async def search_extractions(
    project_id: str,
    request: SearchRequest,
    db: Session = Depends(get_db),
) -> SearchResponse:
```

**Test cases to cover:**
- `test_search_returns_results` - Valid search returns results
- `test_search_project_not_found` - 404 for missing project
- `test_search_invalid_project_id` - 422 for invalid UUID
- `test_search_with_source_group_filter` - Filters by source_group
- `test_search_with_jsonb_filters` - Filters by JSONB data
- `test_search_empty_results` - Returns empty list when no matches
- `test_search_respects_limit` - Honors limit parameter

### 3. Register router in main app

**File(s):** `src/main.py`

**Requirements:**
- Import the search router
- Include router in app (follow existing pattern for other routers)

**Add these lines:**
```python
from api.v1.search import router as search_router
# ... in app setup
app.include_router(search_router)
```

**Test cases to cover:**
- `test_search_endpoint_registered` - GET /docs shows search endpoint

### 4. Create comprehensive test suite

**File(s):** `tests/test_search_endpoint.py` (new file)

**Requirements:**
- Use pytest-asyncio
- Mock external services (EmbeddingService, QdrantRepository)
- Test all error cases
- Test filtering scenarios
- Follow existing test patterns from `test_extraction_endpoint.py`

**Test structure:**
```python
import pytest
from httpx import AsyncClient, ASGITransport
from unittest.mock import AsyncMock, patch
from main import app

class TestSearchEndpoint:
    """Tests for POST /api/v1/projects/{project_id}/search"""

    @pytest.fixture
    async def client(self):
        async with AsyncClient(
            transport=ASGITransport(app=app),
            base_url="http://test",
            headers={"X-API-Key": "test-key"},
        ) as ac:
            yield ac
```

## Constraints

- Do NOT modify `SearchService` - it's complete and tested
- Do NOT modify `EmbeddingService` or `QdrantRepository`
- Do NOT modify existing API endpoints
- Only modify: `models.py` (add models), `main.py` (register router), create new files
- Mock external HTTP calls in tests (embedding service calls BGE-large-en API)
- Use TDD: write tests first, then implement

## Verification

Before creating PR, confirm:
- [ ] All 4 tasks above completed
- [ ] `pytest tests/test_search_endpoint.py -v` - All tests pass
- [ ] `pytest` - All 417+ tests still pass
- [ ] `ruff check src/` clean
- [ ] `ruff format src/` applied
- [ ] No new warnings
- [ ] Endpoint appears in OpenAPI docs (`/docs`)

## Notes

**Dependency Initialization Pattern:**
The SearchService requires multiple dependencies. Initialize them in the endpoint:

```python
from services.storage.embedding import EmbeddingService
from services.storage.qdrant.repository import QdrantRepository
from services.storage.repositories.extraction import ExtractionRepository
from services.storage.search import SearchService
from config import get_settings

async def search_extractions(...):
    settings = get_settings()

    # Initialize services
    embedding_service = EmbeddingService(settings)
    qdrant_repo = QdrantRepository(settings)
    extraction_repo = ExtractionRepository(db)

    search_service = SearchService(
        embedding_service=embedding_service,
        qdrant_repo=qdrant_repo,
        extraction_repo=extraction_repo,
    )

    results = await search_service.search(
        project_id=project_uuid,
        query=request.query,
        limit=request.limit,
        source_groups=request.source_groups,
        jsonb_filters=request.filters,
    )
```

**Mocking External Services:**
For tests, mock the external HTTP calls:

```python
@patch("services.storage.embedding.EmbeddingService.embed")
@patch("services.storage.qdrant.repository.QdrantRepository.search")
async def test_search_returns_results(mock_qdrant, mock_embed, client):
    mock_embed.return_value = [0.1] * 1024  # BGE-large-en dimension
    mock_qdrant.return_value = [...]  # Mock vector results
```
