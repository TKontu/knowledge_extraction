# TODO: Report Generation MVP

**Agent:** reports
**Branch:** `feat/report-generation`
**Priority:** HIGH
**Assigned:** 2026-01-11

## Context

The system can extract facts and entities, and search them via hybrid search. Now we need to generate human-readable reports from this data.

**Existing infrastructure:**
- `ExtractionRepository` - Query extractions with filtering
- `EntityRepository` - Query entities for structured data
- `SearchService` - Hybrid semantic search
- `LLMClient` (`services/llm/client.py`) - For report synthesis
- Report ORM model exists (`orm_models.py`)
- Report table exists in database

**Report types needed (MVP):**
1. **Single** - All facts for one source_group (e.g., one company)
2. **Comparison** - Side-by-side comparison of multiple source_groups

## Objective

Create a ReportService that generates markdown reports from extracted data, with entity-based comparison tables for accurate structured comparisons.

## Tasks

### 1. Create report request/response models

**File:** `src/models.py` (add to existing file)

**Requirements:**
Add these Pydantic models:

```python
from enum import Enum

class ReportType(str, Enum):
    """Types of reports that can be generated."""
    SINGLE = "single"
    COMPARISON = "comparison"

class ReportRequest(BaseModel):
    """Request to generate a report."""
    type: ReportType
    source_groups: list[str] = Field(..., min_length=1, description="Source groups to include")
    entity_types: list[str] | None = Field(default=None, description="Entity types for comparison tables")
    categories: list[str] | None = Field(default=None, description="Filter by extraction categories")
    title: str | None = Field(default=None, description="Custom report title")
    max_extractions: int = Field(default=50, ge=1, le=200, description="Max extractions per source_group")

    @field_validator("source_groups")
    @classmethod
    def validate_comparison_needs_multiple(cls, v, info):
        if info.data.get("type") == ReportType.COMPARISON and len(v) < 2:
            raise ValueError("Comparison reports require at least 2 source_groups")
        return v

class ReportResponse(BaseModel):
    """Response with generated report."""
    id: str
    type: str
    title: str
    content: str  # Markdown content
    source_groups: list[str]
    extraction_count: int
    entity_count: int
    generated_at: str

class ReportJobResponse(BaseModel):
    """Response when report job is created."""
    job_id: str
    status: str
    report_id: str | None = None
```

**Test cases:**
- `test_report_request_validates_source_groups`
- `test_report_request_comparison_needs_multiple`
- `test_report_response_serialization`

### 2. Create ReportService class

**File:** `src/services/reports/service.py` (new file)

**Requirements:**
- Main service class for report generation
- Takes repositories and LLM client as dependencies
- Generates markdown reports

**Class structure:**
```python
from uuid import UUID
from dataclasses import dataclass

@dataclass
class ReportData:
    """Aggregated data for report generation."""
    extractions_by_group: dict[str, list[dict]]
    entities_by_group: dict[str, dict[str, list[dict]]]  # group -> type -> entities
    source_groups: list[str]

class ReportService:
    """Service for generating reports from extracted data."""

    def __init__(
        self,
        extraction_repo: ExtractionRepository,
        entity_repo: EntityRepository,
        llm_client: LLMClient,
        db_session,
    ):
        """Initialize with dependencies."""

    async def generate(
        self,
        project_id: UUID,
        request: ReportRequest,
    ) -> Report:
        """Generate a report based on the request."""

    async def _gather_data(
        self,
        project_id: UUID,
        source_groups: list[str],
        categories: list[str] | None,
        entity_types: list[str] | None,
        max_extractions: int,
    ) -> ReportData:
        """Gather extractions and entities for the report."""

    async def _generate_single_report(
        self,
        data: ReportData,
        title: str | None,
    ) -> str:
        """Generate markdown for single source_group report."""

    async def _generate_comparison_report(
        self,
        data: ReportData,
        title: str | None,
    ) -> str:
        """Generate markdown for comparison report with entity tables."""

    def _build_entity_table(
        self,
        entity_type: str,
        entities_by_group: dict[str, list[dict]],
    ) -> str:
        """Build markdown table from entity data."""
```

**Test cases:**
- `test_init_with_dependencies`
- `test_generate_returns_report`
- `test_gather_data_queries_extractions`
- `test_gather_data_queries_entities`

### 3. Implement _gather_data() method

**File:** `src/services/reports/service.py`

**Requirements:**
- Query ExtractionRepository for extractions matching filters
- Query EntityRepository for entities in the source_groups
- Group results by source_group
- Respect max_extractions limit per group
- Sort extractions by confidence (highest first)

**Test cases:**
- `test_gather_data_filters_by_category`
- `test_gather_data_respects_max_extractions`
- `test_gather_data_sorts_by_confidence`
- `test_gather_data_groups_by_source_group`

### 4. Implement _generate_single_report() method

**File:** `src/services/reports/service.py`

**Requirements:**
- Generate markdown report for a single source_group
- Sections by category
- Include extraction facts with confidence
- Use LLM for executive summary (optional)

**Output format:**
```markdown
# {source_group} - Extraction Report

Generated: {date}
Extractions: {count}

## Summary
{llm_generated_summary}

## Technical Specifications
- {fact_1} (confidence: 0.95)
- {fact_2} (confidence: 0.90)

## API & Integration
- {fact_3}
...

## Sources
Based on {source_count} sources.
```

**Test cases:**
- `test_generate_single_report_has_title`
- `test_generate_single_report_groups_by_category`
- `test_generate_single_report_includes_confidence`

### 5. Implement _generate_comparison_report() method

**File:** `src/services/reports/service.py`

**Requirements:**
- Generate side-by-side comparison of multiple source_groups
- **Entity tables built from actual entity data (not LLM inference)**
- LLM used only for analysis/insights section
- Tables for: limits, pricing, features (based on entity_types param)

**Output format:**
```markdown
# Comparison: {group_a} vs {group_b}

Generated: {date}

## Rate Limits
| Limit | {group_a} | {group_b} |
|-------|-----------|-----------|
| API calls/min | 10,000 | 5,000 |
| Storage | 100GB | 50GB |

## Pricing
| Plan | {group_a} | {group_b} |
|------|-----------|-----------|
| Pro | $99/month | $79/month |

## Features
| Feature | {group_a} | {group_b} |
|---------|-----------|-----------|
| SSO | Yes | Yes |
| Webhooks | Yes | No |

## Analysis
{llm_generated_insights}

## Detailed Findings

### {group_a}
- {fact_1}
- {fact_2}

### {group_b}
- {fact_3}
```

**Test cases:**
- `test_generate_comparison_report_has_tables`
- `test_generate_comparison_report_tables_from_entities`
- `test_generate_comparison_report_handles_missing_data`
- `test_generate_comparison_report_includes_analysis`

### 6. Implement _build_entity_table() method

**File:** `src/services/reports/service.py`

**Requirements:**
- Build markdown table from entity data
- Rows are unique entity values (e.g., "API calls/min")
- Columns are source_groups
- Show "N/A" for missing values
- Sort rows alphabetically

**Test cases:**
- `test_build_entity_table_creates_markdown`
- `test_build_entity_table_handles_missing_values`
- `test_build_entity_table_sorts_rows`

### 7. Create report API endpoints

**File:** `src/api/v1/reports.py` (new file)

**Requirements:**
- POST /api/v1/projects/{project_id}/reports - Create report (sync for MVP)
- GET /api/v1/projects/{project_id}/reports - List reports
- GET /api/v1/projects/{project_id}/reports/{report_id} - Get report

```python
router = APIRouter(prefix="/api/v1", tags=["reports"])

@router.post("/projects/{project_id}/reports", status_code=status.HTTP_201_CREATED)
async def create_report(
    project_id: str,
    request: ReportRequest,
    db: Session = Depends(get_db),
) -> ReportResponse:
    """Generate a report for a project."""

@router.get("/projects/{project_id}/reports", status_code=status.HTTP_200_OK)
async def list_reports(
    project_id: str,
    limit: int = Query(default=20, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> dict:
    """List reports for a project."""

@router.get("/projects/{project_id}/reports/{report_id}", status_code=status.HTTP_200_OK)
async def get_report(
    project_id: str,
    report_id: str,
    db: Session = Depends(get_db),
) -> ReportResponse:
    """Get a specific report."""
```

**Test cases:**
- `test_create_report_single_type`
- `test_create_report_comparison_type`
- `test_create_report_project_not_found`
- `test_list_reports_returns_list`
- `test_get_report_returns_report`
- `test_get_report_not_found`

### 8. Register router in main app

**File:** `src/main.py`

**Requirements:**
- Import reports router
- Include router in app

```python
from api.v1.reports import router as reports_router
# ...
app.include_router(reports_router)
```

### 9. Create comprehensive test suite

**File:** `tests/test_report_service.py` (new file)
**File:** `tests/test_report_endpoint.py` (new file)

**Requirements:**
- Mock LLM client for deterministic tests
- Test entity table building thoroughly
- Test markdown output format
- Test API endpoints

## Constraints

- Do NOT modify ExtractionRepository or EntityRepository
- Do NOT add PDF generation (post-MVP)
- Do NOT add async job processing (sync for MVP)
- Reports are generated synchronously for MVP (can be <30s)
- Entity tables MUST come from actual entity data, NOT LLM inference
- Use TDD: write tests first, then implement

## Verification

Before creating PR, confirm:
- [ ] All 9 tasks above completed
- [ ] `pytest tests/test_report_service.py tests/test_report_endpoint.py -v` - All tests pass
- [ ] `pytest` - All 493+ tests still pass
- [ ] `ruff check src/` clean
- [ ] `ruff format src/` applied
- [ ] No new warnings
- [ ] Report endpoints appear in OpenAPI docs (`/docs`)
- [ ] Can generate a comparison report via API (manual test)

## Notes

**LLM Client Usage for Summary:**
```python
# Use extract_facts for generating summary (it handles JSON mode)
# Or access client directly for text generation:
response = await self._llm_client.client.chat.completions.create(
    model=self._llm_client.model,
    messages=[
        {"role": "system", "content": "Generate a brief executive summary..."},
        {"role": "user", "content": f"Facts:\n{facts_text}"},
    ],
    temperature=0.3,
    max_tokens=500,
)
summary = response.choices[0].message.content
```

**EntityRepository Usage:**
```python
from services.storage.repositories.entity import EntityRepository, EntityFilters

entity_repo = EntityRepository(db)
filters = EntityFilters(
    project_id=project_uuid,
    source_group=source_group,
    entity_type="limit",
)
entities = await entity_repo.list(filters)
```

**Storing Report:**
```python
from orm_models import Report

report = Report(
    project_id=project_id,
    type=request.type.value,
    title=title,
    content=markdown_content,
    source_groups=request.source_groups,
    categories=request.categories or [],
    extraction_ids=[],  # Can populate if tracking
    format="md",
)
db.add(report)
db.commit()
```

**File Structure:**
```
src/
├── services/
│   └── reports/
│       ├── __init__.py
│       └── service.py       # ReportService
└── api/
    └── v1/
        └── reports.py       # API endpoints
```
