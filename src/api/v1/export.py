"""Export API endpoints for entities and extractions."""

import csv
import io
import json
from datetime import datetime
from typing import Literal
from uuid import UUID

import structlog
from fastapi import APIRouter, Depends, Query
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
