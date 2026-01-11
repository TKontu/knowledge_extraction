# TODO: Entity Query API Endpoints

**Agent:** entity-api
**Branch:** `feat/entity-api-endpoints`
**Priority:** medium
**Assigned:** 2026-01-11

## Context

The EntityRepository (`services/storage/repositories/entity.py`) provides full CRUD operations for entities with deduplication. You need to expose entity query functionality via REST API endpoints for browsing and filtering extracted entities.

**Existing infrastructure:**
- `EntityRepository` - `list()`, `list_by_type()`, `get()`, `get_entities_for_extraction()`
- `EntityFilters` dataclass for filtering
- Entity model with: id, project_id, source_group, entity_type, value, normalized_value, attributes

**API pattern to follow:** See `api/v1/extraction.py` and `api/v1/projects.py` for endpoint structure.

## Objective

Create Entity API endpoints that allow querying and browsing entities by project, type, and source_group, enabling structured queries like "Which companies support SSO?"

## Tasks

### 1. Create request/response models

**File(s):** `src/models.py` (add to existing file)

**Requirements:**
Add these Pydantic models to the existing `models.py`:

```python
class EntityResponse(BaseModel):
    """Single entity in response."""
    id: str
    entity_type: str
    value: str
    normalized_value: str
    source_group: str
    attributes: dict[str, Any]
    created_at: str

class EntityListResponse(BaseModel):
    """Response for entity list endpoint."""
    entities: list[EntityResponse]
    total: int
    limit: int
    offset: int

class EntityTypeCount(BaseModel):
    """Count of entities per type."""
    entity_type: str
    count: int

class EntityTypesResponse(BaseModel):
    """Response for entity types summary."""
    types: list[EntityTypeCount]
    total_entities: int
```

**Test cases to cover:**
- `test_entity_response_serialization` - Can serialize entity
- `test_entity_list_response_with_pagination` - Includes pagination info

### 2. Create entity list endpoint

**File(s):** `src/api/v1/entities.py` (new file)

**Requirements:**
- Create new router with prefix `/api/v1` and tag `entities`
- Implement `GET /api/v1/projects/{project_id}/entities` endpoint
- Query parameters for filtering:
  - `entity_type` (optional) - Filter by entity type
  - `source_group` (optional) - Filter by source group
  - `limit` (default 50, max 100)
  - `offset` (default 0)
- Validate project exists (404 if not)
- Validate project_id UUID format (422 if invalid)
- Return EntityListResponse with pagination

**Endpoint signature:**
```python
@router.get("/projects/{project_id}/entities", status_code=status.HTTP_200_OK)
async def list_entities(
    project_id: str,
    entity_type: str | None = Query(default=None),
    source_group: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> EntityListResponse:
```

**Test cases to cover:**
- `test_list_entities_returns_all` - Returns all entities for project
- `test_list_entities_project_not_found` - 404 for missing project
- `test_list_entities_invalid_project_id` - 422 for invalid UUID
- `test_list_entities_filter_by_type` - Filters by entity_type
- `test_list_entities_filter_by_source_group` - Filters by source_group
- `test_list_entities_combined_filters` - Both type and source_group
- `test_list_entities_pagination` - Respects limit and offset
- `test_list_entities_empty` - Returns empty list when no entities

### 3. Create entity types summary endpoint

**File(s):** `src/api/v1/entities.py`

**Requirements:**
- Implement `GET /api/v1/projects/{project_id}/entities/types` endpoint
- Returns count of entities per type for the project
- Optionally filter by source_group
- Useful for UI showing "5 plans, 12 features, 3 limits"

**Endpoint signature:**
```python
@router.get("/projects/{project_id}/entities/types", status_code=status.HTTP_200_OK)
async def get_entity_types(
    project_id: str,
    source_group: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> EntityTypesResponse:
```

**Test cases to cover:**
- `test_get_entity_types_counts` - Returns counts per type
- `test_get_entity_types_project_not_found` - 404 for missing project
- `test_get_entity_types_filter_by_source_group` - Filters counts by source_group
- `test_get_entity_types_empty` - Returns empty when no entities

### 4. Create single entity endpoint

**File(s):** `src/api/v1/entities.py`

**Requirements:**
- Implement `GET /api/v1/projects/{project_id}/entities/{entity_id}` endpoint
- Returns full entity details
- 404 if entity not found or doesn't belong to project

**Endpoint signature:**
```python
@router.get("/projects/{project_id}/entities/{entity_id}", status_code=status.HTTP_200_OK)
async def get_entity(
    project_id: str,
    entity_id: str,
    db: Session = Depends(get_db),
) -> EntityResponse:
```

**Test cases to cover:**
- `test_get_entity_returns_entity` - Returns entity details
- `test_get_entity_not_found` - 404 for missing entity
- `test_get_entity_wrong_project` - 404 if entity belongs to different project
- `test_get_entity_invalid_ids` - 422 for invalid UUID format

### 5. Create source_groups with entity endpoint

**File(s):** `src/api/v1/entities.py`

**Requirements:**
- Implement `GET /api/v1/projects/{project_id}/entities/by-value` endpoint
- Find source_groups that have an entity with specific type and value
- Answers queries like "Which companies support SSO?"

**Endpoint signature:**
```python
@router.get("/projects/{project_id}/entities/by-value", status_code=status.HTTP_200_OK)
async def get_source_groups_by_entity(
    project_id: str,
    entity_type: str = Query(..., description="Entity type to search"),
    value: str = Query(..., description="Entity value to match (case-insensitive)"),
    db: Session = Depends(get_db),
) -> dict:
    """Find source_groups that have an entity with the given type and value."""
```

**Response format:**
```json
{
  "entity_type": "feature",
  "value": "sso",
  "source_groups": ["acme_corp", "globex_inc", "initech"],
  "total": 3
}
```

**Test cases to cover:**
- `test_get_source_groups_by_entity_finds_matches` - Returns matching source_groups
- `test_get_source_groups_by_entity_case_insensitive` - "SSO" matches "sso"
- `test_get_source_groups_by_entity_no_matches` - Returns empty list
- `test_get_source_groups_by_entity_missing_params` - 422 if params missing

### 6. Register router in main app

**File(s):** `src/main.py`

**Requirements:**
- Import the entities router
- Include router in app

**Add these lines:**
```python
from api.v1.entities import router as entities_router
# ... in app setup
app.include_router(entities_router)
```

### 7. Create comprehensive test suite

**File(s):** `tests/test_entity_endpoint.py` (new file)

**Requirements:**
- Use pytest-asyncio
- Create test fixtures for projects and entities
- Test all error cases
- Test filtering and pagination
- Follow existing test patterns

## Constraints

- Do NOT modify `EntityRepository` - it's complete and tested
- Do NOT modify existing API endpoints
- Only modify: `models.py` (add models), `main.py` (register router), create new files
- Use TDD: write tests first, then implement
- Use normalized_value for case-insensitive matching in by-value endpoint

## Verification

Before creating PR, confirm:
- [ ] All 7 tasks above completed
- [ ] `pytest tests/test_entity_endpoint.py -v` - All tests pass
- [ ] `pytest` - All 417+ tests still pass
- [ ] `ruff check src/` clean
- [ ] `ruff format src/` applied
- [ ] No new warnings
- [ ] All endpoints appear in OpenAPI docs (`/docs`)

## Notes

**EntityRepository Usage:**
```python
from services.storage.repositories.entity import EntityRepository, EntityFilters

entity_repo = EntityRepository(db)

# List with filters
filters = EntityFilters(
    project_id=project_uuid,
    source_group=source_group,
    entity_type=entity_type,
)
entities = await entity_repo.list(filters)

# Get by ID
entity = await entity_repo.get(entity_id)
```

**Counting Entity Types:**
Since EntityRepository doesn't have a count method, you'll need to:
1. Fetch all entities for the project
2. Group by entity_type in Python
3. Count each group

```python
from collections import Counter

entities = await entity_repo.list(EntityFilters(project_id=project_uuid))
type_counts = Counter(e.entity_type for e in entities)
```

**Case-Insensitive Matching:**
Use the `normalized_value` field which is already lowercase:
```python
# User searches for "SSO", normalize to "sso" for matching
search_normalized = value.lower().strip()
entities = await entity_repo.list(EntityFilters(
    project_id=project_uuid,
    entity_type=entity_type,
))
matching = [e for e in entities if e.normalized_value == search_normalized]
source_groups = list(set(e.source_group for e in matching))
```
