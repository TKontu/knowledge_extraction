# TODO: Agent Export

**Agent ID**: `agent-export`
**Branch**: `feat/export-api`
**Priority**: 4

## Objective

Add CSV and JSON export endpoints for entities and extractions to enable data portability and external analysis.

## Context

- Entities are stored via `EntityRepository` in `src/services/storage/repositories/entity.py`
- Extractions are stored via `ExtractionRepository` in `src/services/storage/repositories/extraction.py`
- Existing API patterns in `src/api/v1/` use FastAPI routers with dependency injection
- Database session provided via `get_db` dependency
- API key auth required for all endpoints (handled by middleware)

## Tasks

### 1. Create export router

**File**: `src/api/v1/export.py` (new file)

```python
"""Export API endpoints for entities and extractions."""

import csv
import io
import json
from datetime import datetime
from typing import Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Query, HTTPException
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from database import get_db
from orm_models import Entity, Extraction

logger = structlog.get_logger(__name__)

router = APIRouter(prefix="/api/v1/projects/{project_id}/export", tags=["export"])


@router.get("/entities")
async def export_entities(
    project_id: UUID,
    format: Literal["csv", "json"] = Query(default="csv"),
    entity_type: str | None = Query(default=None),
    source_group: str | None = Query(default=None),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """
    Export entities for a project.

    Args:
        project_id: Project UUID
        format: Export format (csv or json)
        entity_type: Filter by entity type
        source_group: Filter by source group
    """
    # Build query
    query = db.query(Entity).filter(Entity.project_id == project_id)

    if entity_type:
        query = query.filter(Entity.entity_type == entity_type)
    if source_group:
        query = query.filter(Entity.source_group == source_group)

    entities = query.all()

    logger.info(
        "export_entities",
        project_id=str(project_id),
        format=format,
        count=len(entities),
    )

    if format == "json":
        return _export_entities_json(entities, project_id)
    else:
        return _export_entities_csv(entities, project_id)


@router.get("/extractions")
async def export_extractions(
    project_id: UUID,
    format: Literal["csv", "json"] = Query(default="csv"),
    extraction_type: str | None = Query(default=None),
    source_group: str | None = Query(default=None),
    min_confidence: float | None = Query(default=None, ge=0.0, le=1.0),
    db: Session = Depends(get_db),
) -> StreamingResponse:
    """
    Export extractions for a project.

    Args:
        project_id: Project UUID
        format: Export format (csv or json)
        extraction_type: Filter by extraction type
        source_group: Filter by source group
        min_confidence: Minimum confidence threshold
    """
    # Build query
    query = db.query(Extraction).filter(Extraction.project_id == project_id)

    if extraction_type:
        query = query.filter(Extraction.extraction_type == extraction_type)
    if source_group:
        query = query.filter(Extraction.source_group == source_group)
    if min_confidence is not None:
        query = query.filter(Extraction.confidence >= min_confidence)

    extractions = query.all()

    logger.info(
        "export_extractions",
        project_id=str(project_id),
        format=format,
        count=len(extractions),
    )

    if format == "json":
        return _export_extractions_json(extractions, project_id)
    else:
        return _export_extractions_csv(extractions, project_id)


def _export_entities_json(entities: list[Entity], project_id: UUID) -> StreamingResponse:
    """Generate JSON export for entities."""
    data = {
        "project_id": str(project_id),
        "exported_at": datetime.utcnow().isoformat(),
        "count": len(entities),
        "entities": [
            {
                "id": str(e.id),
                "entity_type": e.entity_type,
                "value": e.value,
                "normalized_value": e.normalized_value,
                "source_group": e.source_group,
                "attributes": e.attributes,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in entities
        ],
    }

    content = json.dumps(data, indent=2)

    return StreamingResponse(
        iter([content]),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="entities_{project_id}.json"'
        },
    )


def _export_entities_csv(entities: list[Entity], project_id: UUID) -> StreamingResponse:
    """Generate CSV export for entities."""
    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "id", "entity_type", "value", "normalized_value",
        "source_group", "attributes", "created_at"
    ])

    # Data rows
    for e in entities:
        writer.writerow([
            str(e.id),
            e.entity_type,
            e.value,
            e.normalized_value,
            e.source_group,
            json.dumps(e.attributes) if e.attributes else "",
            e.created_at.isoformat() if e.created_at else "",
        ])

    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="entities_{project_id}.csv"'
        },
    )


def _export_extractions_json(extractions: list[Extraction], project_id: UUID) -> StreamingResponse:
    """Generate JSON export for extractions."""
    data = {
        "project_id": str(project_id),
        "exported_at": datetime.utcnow().isoformat(),
        "count": len(extractions),
        "extractions": [
            {
                "id": str(e.id),
                "source_id": str(e.source_id) if e.source_id else None,
                "extraction_type": e.extraction_type,
                "data": e.data,
                "source_group": e.source_group,
                "confidence": e.confidence,
                "profile_used": e.profile_used,
                "created_at": e.created_at.isoformat() if e.created_at else None,
            }
            for e in extractions
        ],
    }

    content = json.dumps(data, indent=2)

    return StreamingResponse(
        iter([content]),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="extractions_{project_id}.json"'
        },
    )


def _export_extractions_csv(extractions: list[Extraction], project_id: UUID) -> StreamingResponse:
    """Generate CSV export for extractions."""
    output = io.StringIO()
    writer = csv.writer(output)

    # Header
    writer.writerow([
        "id", "source_id", "extraction_type", "data",
        "source_group", "confidence", "profile_used", "created_at"
    ])

    # Data rows
    for e in extractions:
        writer.writerow([
            str(e.id),
            str(e.source_id) if e.source_id else "",
            e.extraction_type,
            json.dumps(e.data) if e.data else "",
            e.source_group,
            e.confidence,
            e.profile_used,
            e.created_at.isoformat() if e.created_at else "",
        ])

    output.seek(0)

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="extractions_{project_id}.csv"'
        },
    )
```

### 2. Register router in main.py

**File**: `src/main.py`

Add import and include router:

```python
from api.v1.export import router as export_router

# Add with other routers
app.include_router(export_router)
```

### 3. Write tests

**File**: `tests/test_export_api.py`

```python
import pytest
import json
import csv
import io
from uuid import uuid4
from unittest.mock import patch, MagicMock

from fastapi.testclient import TestClient


@pytest.fixture
def mock_entities():
    """Create mock entity objects."""
    entity = MagicMock()
    entity.id = uuid4()
    entity.entity_type = "feature"
    entity.value = "SSO Support"
    entity.normalized_value = "sso_support"
    entity.source_group = "company_a"
    entity.attributes = {"priority": "high"}
    entity.created_at = None
    return [entity]


@pytest.fixture
def mock_extractions():
    """Create mock extraction objects."""
    extraction = MagicMock()
    extraction.id = uuid4()
    extraction.source_id = uuid4()
    extraction.extraction_type = "feature"
    extraction.data = {"fact_text": "Supports SSO"}
    extraction.source_group = "company_a"
    extraction.confidence = 0.95
    extraction.profile_used = "general"
    extraction.created_at = None
    return [extraction]


class TestExportEntities:
    def test_export_entities_csv(self, client, mock_entities):
        """Export entities as CSV."""
        project_id = uuid4()

        with patch("src.api.v1.export.get_db") as mock_db:
            mock_session = MagicMock()
            mock_query = MagicMock()
            mock_query.filter.return_value = mock_query
            mock_query.all.return_value = mock_entities
            mock_session.query.return_value = mock_query
            mock_db.return_value = mock_session

            response = client.get(
                f"/api/v1/projects/{project_id}/export/entities?format=csv",
                headers={"X-API-Key": "test-key"},
            )

            assert response.status_code == 200
            assert response.headers["content-type"] == "text/csv; charset=utf-8"
            assert "attachment" in response.headers["content-disposition"]

            # Parse CSV
            reader = csv.reader(io.StringIO(response.text))
            rows = list(reader)
            assert rows[0][0] == "id"  # Header
            assert len(rows) == 2  # Header + 1 data row

    def test_export_entities_json(self, client, mock_entities):
        """Export entities as JSON."""
        project_id = uuid4()

        with patch("src.api.v1.export.get_db") as mock_db:
            mock_session = MagicMock()
            mock_query = MagicMock()
            mock_query.filter.return_value = mock_query
            mock_query.all.return_value = mock_entities
            mock_session.query.return_value = mock_query
            mock_db.return_value = mock_session

            response = client.get(
                f"/api/v1/projects/{project_id}/export/entities?format=json",
                headers={"X-API-Key": "test-key"},
            )

            assert response.status_code == 200
            assert "application/json" in response.headers["content-type"]

            data = response.json()
            assert data["count"] == 1
            assert "entities" in data
            assert data["entities"][0]["entity_type"] == "feature"

    def test_export_entities_filter_by_type(self, client, mock_entities):
        """Filter entities by type."""
        project_id = uuid4()

        with patch("src.api.v1.export.get_db") as mock_db:
            mock_session = MagicMock()
            mock_query = MagicMock()
            mock_query.filter.return_value = mock_query
            mock_query.all.return_value = mock_entities
            mock_session.query.return_value = mock_query
            mock_db.return_value = mock_session

            response = client.get(
                f"/api/v1/projects/{project_id}/export/entities?entity_type=feature",
                headers={"X-API-Key": "test-key"},
            )

            assert response.status_code == 200
            # Verify filter was called
            assert mock_query.filter.called


class TestExportExtractions:
    def test_export_extractions_csv(self, client, mock_extractions):
        """Export extractions as CSV."""
        project_id = uuid4()

        with patch("src.api.v1.export.get_db") as mock_db:
            mock_session = MagicMock()
            mock_query = MagicMock()
            mock_query.filter.return_value = mock_query
            mock_query.all.return_value = mock_extractions
            mock_session.query.return_value = mock_query
            mock_db.return_value = mock_session

            response = client.get(
                f"/api/v1/projects/{project_id}/export/extractions?format=csv",
                headers={"X-API-Key": "test-key"},
            )

            assert response.status_code == 200
            assert response.headers["content-type"] == "text/csv; charset=utf-8"

    def test_export_extractions_json(self, client, mock_extractions):
        """Export extractions as JSON."""
        project_id = uuid4()

        with patch("src.api.v1.export.get_db") as mock_db:
            mock_session = MagicMock()
            mock_query = MagicMock()
            mock_query.filter.return_value = mock_query
            mock_query.all.return_value = mock_extractions
            mock_session.query.return_value = mock_query
            mock_db.return_value = mock_session

            response = client.get(
                f"/api/v1/projects/{project_id}/export/extractions?format=json",
                headers={"X-API-Key": "test-key"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["count"] == 1
            assert "extractions" in data

    def test_export_extractions_filter_by_confidence(self, client, mock_extractions):
        """Filter extractions by minimum confidence."""
        project_id = uuid4()

        with patch("src.api.v1.export.get_db") as mock_db:
            mock_session = MagicMock()
            mock_query = MagicMock()
            mock_query.filter.return_value = mock_query
            mock_query.all.return_value = mock_extractions
            mock_session.query.return_value = mock_query
            mock_db.return_value = mock_session

            response = client.get(
                f"/api/v1/projects/{project_id}/export/extractions?min_confidence=0.8",
                headers={"X-API-Key": "test-key"},
            )

            assert response.status_code == 200


class TestExportEmpty:
    def test_export_empty_entities(self, client):
        """Export with no entities returns empty result."""
        project_id = uuid4()

        with patch("src.api.v1.export.get_db") as mock_db:
            mock_session = MagicMock()
            mock_query = MagicMock()
            mock_query.filter.return_value = mock_query
            mock_query.all.return_value = []
            mock_session.query.return_value = mock_query
            mock_db.return_value = mock_session

            response = client.get(
                f"/api/v1/projects/{project_id}/export/entities?format=json",
                headers={"X-API-Key": "test-key"},
            )

            assert response.status_code == 200
            data = response.json()
            assert data["count"] == 0
            assert data["entities"] == []
```

## Constraints

- Do NOT add pagination to export (export returns all matching records)
- Do NOT add new dependencies
- MUST use StreamingResponse for memory efficiency
- MUST include Content-Disposition header for downloads
- MUST handle None values gracefully in exports
- CSV MUST properly escape special characters (handled by csv module)

## Verification

1. `pytest tests/test_export_api.py -v` passes
2. `pytest tests/ -v` - all existing tests still pass
3. `ruff check src/api/v1/export.py` - no lint errors
4. Manual test: Export entities/extractions, verify valid CSV/JSON

## Definition of Done

- [ ] `src/api/v1/export.py` created with 2 endpoints
- [ ] Router registered in main.py
- [ ] CSV export works with proper headers
- [ ] JSON export works with proper structure
- [ ] Filtering by type, source_group, confidence works
- [ ] Tests written and passing
- [ ] PR created with title: `feat: add CSV/JSON export endpoints`
