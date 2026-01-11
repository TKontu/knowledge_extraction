# TODO: Jobs API + Prometheus Metrics

**Agent:** jobs-metrics
**Branch:** `feat/jobs-metrics-api`
**Priority:** MEDIUM-HIGH
**Assigned:** 2026-01-11

## Context

The system creates jobs (scrape, extract) but there's no way to list all jobs or monitor system health via metrics.

**Existing infrastructure:**
- Job ORM model (`orm_models.py`) with id, type, status, payload, result, timestamps
- Jobs table in database
- Individual job status endpoint exists: `GET /api/v1/scrape/{job_id}`
- No job listing endpoint
- No Prometheus metrics

**Needed for operations:**
1. List all jobs with filtering (for admin dashboard)
2. Prometheus metrics for monitoring (Grafana integration)

## Objective

Create Jobs API for listing/filtering jobs and a Prometheus-compatible metrics endpoint for system monitoring.

## Tasks

### 1. Create job list response models

**File:** `src/models.py` (add to existing file)

**Requirements:**
Add these Pydantic models:

```python
class JobSummary(BaseModel):
    """Summary of a job for list views."""
    id: str
    type: str
    status: str
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
    error: str | None = None

class JobListResponse(BaseModel):
    """Response for job list endpoint."""
    jobs: list[JobSummary]
    total: int
    limit: int
    offset: int

class JobDetailResponse(BaseModel):
    """Detailed job information."""
    id: str
    type: str
    status: str
    payload: dict
    result: dict | None = None
    error: str | None = None
    created_at: str
    started_at: str | None = None
    completed_at: str | None = None
```

**Test cases:**
- `test_job_summary_serialization`
- `test_job_list_response_pagination`
- `test_job_detail_response_includes_payload`

### 2. Create jobs API endpoint

**File:** `src/api/v1/jobs.py` (new file)

**Requirements:**
- GET /api/v1/jobs - List all jobs with filtering
- GET /api/v1/jobs/{job_id} - Get detailed job info
- Filter by: type, status, created date range
- Pagination support
- Sort by created_at descending (newest first)

```python
from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.orm import Session
from sqlalchemy import select, desc
from datetime import datetime
from uuid import UUID

from database import get_db
from models import JobListResponse, JobDetailResponse, JobSummary
from orm_models import Job

router = APIRouter(prefix="/api/v1", tags=["jobs"])

@router.get("/jobs", status_code=status.HTTP_200_OK)
async def list_jobs(
    type: str | None = Query(default=None, description="Filter by job type (scrape, extract)"),
    status_filter: str | None = Query(default=None, alias="status", description="Filter by status"),
    created_after: datetime | None = Query(default=None, description="Filter by creation date"),
    created_before: datetime | None = Query(default=None, description="Filter by creation date"),
    limit: int = Query(default=50, ge=1, le=100),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> JobListResponse:
    """List all jobs with optional filtering."""

@router.get("/jobs/{job_id}", status_code=status.HTTP_200_OK)
async def get_job(
    job_id: str,
    db: Session = Depends(get_db),
) -> JobDetailResponse:
    """Get detailed information about a specific job."""
```

**Test cases:**
- `test_list_jobs_returns_all` - Returns all jobs
- `test_list_jobs_filter_by_type` - Filter by scrape/extract
- `test_list_jobs_filter_by_status` - Filter by queued/running/completed/failed
- `test_list_jobs_filter_by_date_range` - Date filtering works
- `test_list_jobs_pagination` - Pagination works correctly
- `test_list_jobs_sorted_newest_first` - Most recent jobs first
- `test_get_job_returns_details` - Returns full job info
- `test_get_job_not_found` - 404 for missing job
- `test_get_job_invalid_uuid` - 422 for invalid UUID

### 3. Create metrics collector

**File:** `src/services/metrics/collector.py` (new file)

**Requirements:**
- Collect system metrics from database
- Job counts by type and status
- Source counts by status
- Extraction counts
- Entity counts

```python
from dataclasses import dataclass
from sqlalchemy.orm import Session
from sqlalchemy import select, func

from orm_models import Job, Source, Extraction, Entity

@dataclass
class SystemMetrics:
    """System metrics for Prometheus."""
    jobs_total: int
    jobs_by_type: dict[str, int]  # {"scrape": 10, "extract": 5}
    jobs_by_status: dict[str, int]  # {"queued": 2, "completed": 13}
    sources_total: int
    sources_by_status: dict[str, int]
    extractions_total: int
    entities_total: int

class MetricsCollector:
    """Collects system metrics from database."""

    def __init__(self, db: Session):
        self._db = db

    async def collect(self) -> SystemMetrics:
        """Collect all system metrics."""

    async def _count_jobs_by_type(self) -> dict[str, int]:
        """Count jobs grouped by type."""

    async def _count_jobs_by_status(self) -> dict[str, int]:
        """Count jobs grouped by status."""

    async def _count_sources_by_status(self) -> dict[str, int]:
        """Count sources grouped by status."""
```

**Test cases:**
- `test_collect_returns_metrics`
- `test_count_jobs_by_type`
- `test_count_jobs_by_status`
- `test_count_sources_by_status`

### 4. Create Prometheus metrics formatter

**File:** `src/services/metrics/prometheus.py` (new file)

**Requirements:**
- Format metrics in Prometheus text exposition format
- Include HELP and TYPE annotations
- Use standard metric naming conventions

```python
def format_prometheus(metrics: SystemMetrics) -> str:
    """Format metrics in Prometheus text exposition format."""
    lines = []

    # Jobs total
    lines.append("# HELP scristill_jobs_total Total number of jobs")
    lines.append("# TYPE scristill_jobs_total gauge")
    lines.append(f"scristill_jobs_total {metrics.jobs_total}")

    # Jobs by type
    lines.append("# HELP scristill_jobs_by_type Number of jobs by type")
    lines.append("# TYPE scristill_jobs_by_type gauge")
    for job_type, count in metrics.jobs_by_type.items():
        lines.append(f'scristill_jobs_by_type{{type="{job_type}"}} {count}')

    # Jobs by status
    lines.append("# HELP scristill_jobs_by_status Number of jobs by status")
    lines.append("# TYPE scristill_jobs_by_status gauge")
    for status, count in metrics.jobs_by_status.items():
        lines.append(f'scristill_jobs_by_status{{status="{status}"}} {count}')

    # Sources
    lines.append("# HELP scristill_sources_total Total number of sources")
    lines.append("# TYPE scristill_sources_total gauge")
    lines.append(f"scristill_sources_total {metrics.sources_total}")

    # Sources by status
    lines.append("# HELP scristill_sources_by_status Number of sources by status")
    lines.append("# TYPE scristill_sources_by_status gauge")
    for status, count in metrics.sources_by_status.items():
        lines.append(f'scristill_sources_by_status{{status="{status}"}} {count}')

    # Extractions
    lines.append("# HELP scristill_extractions_total Total number of extractions")
    lines.append("# TYPE scristill_extractions_total gauge")
    lines.append(f"scristill_extractions_total {metrics.extractions_total}")

    # Entities
    lines.append("# HELP scristill_entities_total Total number of entities")
    lines.append("# TYPE scristill_entities_total gauge")
    lines.append(f"scristill_entities_total {metrics.entities_total}")

    return "\n".join(lines) + "\n"
```

**Test cases:**
- `test_format_prometheus_includes_help`
- `test_format_prometheus_includes_type`
- `test_format_prometheus_formats_labels`
- `test_format_prometheus_valid_format`

### 5. Create metrics API endpoint

**File:** `src/api/v1/metrics.py` (new file)

**Requirements:**
- GET /metrics - Returns Prometheus-format metrics
- Content-Type: text/plain
- No authentication required (for Prometheus scraping)

```python
from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse
from sqlalchemy.orm import Session

from database import get_db
from services.metrics.collector import MetricsCollector
from services.metrics.prometheus import format_prometheus

router = APIRouter(tags=["metrics"])

@router.get("/metrics", response_class=PlainTextResponse)
async def get_metrics(db: Session = Depends(get_db)) -> str:
    """
    Get Prometheus-format metrics.

    Returns system metrics in Prometheus text exposition format.
    """
    collector = MetricsCollector(db)
    metrics = await collector.collect()
    return format_prometheus(metrics)
```

**Test cases:**
- `test_metrics_endpoint_returns_text`
- `test_metrics_endpoint_content_type`
- `test_metrics_endpoint_valid_prometheus_format`

### 6. Register routers in main app

**File:** `src/main.py`

**Requirements:**
- Import jobs router
- Import metrics router
- Include both routers in app

```python
from api.v1.jobs import router as jobs_router
from api.v1.metrics import router as metrics_router
# ...
app.include_router(jobs_router)
app.include_router(metrics_router)
```

### 7. Create comprehensive test suite

**File:** `tests/test_jobs_endpoint.py` (new file)
**File:** `tests/test_metrics.py` (new file)

**Requirements:**
- Test all job filtering scenarios
- Test metrics collection accuracy
- Test Prometheus format validity
- Create fixtures for jobs with various states

## Constraints

- Do NOT modify existing job-related code
- Do NOT add authentication to /metrics (Prometheus needs open access)
- Do NOT add request-level metrics (like latency histograms) - MVP is database counts only
- Metrics endpoint should be fast (<100ms)
- Use TDD: write tests first, then implement

## Verification

Before creating PR, confirm:
- [ ] All 7 tasks above completed
- [ ] `pytest tests/test_jobs_endpoint.py tests/test_metrics.py -v` - All tests pass
- [ ] `pytest` - All 493+ tests still pass
- [ ] `ruff check src/` clean
- [ ] `ruff format src/` applied
- [ ] No new warnings
- [ ] Jobs endpoint appears in OpenAPI docs (`/docs`)
- [ ] Metrics endpoint returns valid Prometheus format
- [ ] `curl http://localhost:8000/metrics` works

## Notes

**SQLAlchemy Count Queries:**
```python
from sqlalchemy import select, func

# Count all jobs
result = db.execute(select(func.count(Job.id)))
total = result.scalar()

# Count by status
result = db.execute(
    select(Job.status, func.count(Job.id))
    .group_by(Job.status)
)
counts = {row[0]: row[1] for row in result.all()}
```

**Job Model Reference:**
```python
class Job(Base):
    __tablename__ = "jobs"
    id: UUID
    type: str  # "scrape", "extract"
    status: str  # "queued", "running", "completed", "failed"
    payload: dict  # JSONB
    result: dict | None  # JSONB
    error: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
```

**Prometheus Format Reference:**
```
# HELP metric_name Description
# TYPE metric_name gauge
metric_name 123
metric_name{label="value"} 456
```

**File Structure:**
```
src/
├── services/
│   └── metrics/
│       ├── __init__.py
│       ├── collector.py     # MetricsCollector
│       └── prometheus.py    # format_prometheus()
└── api/
    └── v1/
        ├── jobs.py          # Jobs API
        └── metrics.py       # Metrics endpoint
```
