"""Consolidation service: DB integration layer.

Reads raw extractions + grounding_scores from DB, delegates to pure
consolidation functions, writes results to consolidated_extractions table.
"""

from __future__ import annotations

from uuid import UUID

import structlog
from sqlalchemy import delete, distinct, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from orm_models import ConsolidatedExtraction, Extraction
from services.extraction.consolidation import (
    ConsolidatedRecord,
    consolidate_extractions,
)
from services.projects.repository import ProjectRepository

logger = structlog.get_logger(__name__)


class ConsolidationService:
    """Orchestrates consolidation from raw extractions to consolidated records."""

    def __init__(self, session: Session, project_repo: ProjectRepository):
        self._session = session
        self._project_repo = project_repo

    def consolidate_source_group(
        self,
        project_id: UUID,
        source_group: str,
    ) -> list[ConsolidatedRecord]:
        """Consolidate all extraction types for one source group.

        1. Load all extractions for (project_id, source_group)
        2. Group by extraction_type
        3. Load field definitions from project schema
        4. Call consolidate_extractions() for each type
        5. Upsert into consolidated_extractions table
        """
        project = self._project_repo.get(project_id)
        if not project:
            return []

        schema = project.extraction_schema
        if not schema or not schema.get("field_groups"):
            return []

        field_defs_by_group = _extract_field_definitions(schema)

        # Delete existing consolidated records for this source group so that
        # removed extraction types don't leave stale rows behind.
        self._session.execute(
            delete(ConsolidatedExtraction).where(
                ConsolidatedExtraction.project_id == project_id,
                ConsolidatedExtraction.source_group == source_group,
            )
        )

        # Load all extractions for this source group
        extractions = (
            self._session.execute(
                select(Extraction).where(
                    Extraction.project_id == project_id,
                    Extraction.source_group == source_group,
                )
            )
            .scalars()
            .all()
        )

        if not extractions:
            return []

        # Group by extraction_type
        by_type: dict[str, list[Extraction]] = {}
        for ext in extractions:
            by_type.setdefault(ext.extraction_type, []).append(ext)

        records: list[ConsolidatedRecord] = []
        for ext_type, type_extractions in by_type.items():
            field_defs = field_defs_by_group.get(ext_type, [])
            if not field_defs:
                continue

            # Convert ORM objects to dicts for pure function
            ext_dicts = [
                {
                    "data": ext.data,
                    "confidence": ext.confidence or 0.5,
                    "grounding_scores": ext.grounding_scores or {},
                    "source_id": str(ext.source_id),
                }
                for ext in type_extractions
            ]

            record = consolidate_extractions(
                ext_dicts, field_defs, source_group, ext_type
            )
            records.append(record)

            # Upsert to DB
            self._upsert_record(project_id, record, len(type_extractions))

        return records

    def consolidate_project(
        self,
        project_id: UUID,
    ) -> dict[str, int]:
        """Consolidate all source groups in a project.

        Returns: {"source_groups": N, "records_created": M, "errors": E}
        """
        # Get distinct source groups
        source_groups = (
            self._session.execute(
                select(distinct(Extraction.source_group)).where(
                    Extraction.project_id == project_id,
                )
            )
            .scalars()
            .all()
        )

        return self._process_source_groups(project_id, source_groups)

    def reconsolidate(
        self,
        project_id: UUID,
        source_groups: list[str] | None = None,
    ) -> dict[str, int]:
        """Re-run consolidation (idempotent). Upserts replace old records."""
        if source_groups:
            return self._process_source_groups(project_id, source_groups)
        return self.consolidate_project(project_id)

    def _process_source_groups(
        self,
        project_id: UUID,
        source_groups: list[str],
    ) -> dict[str, int]:
        """Process a list of source groups with per-group error isolation.

        Uses SAVEPOINTs so that a failure in one group does not roll back
        previously successful groups within the same outer transaction.
        """
        total_records = 0
        errors = 0

        for sg in source_groups:
            savepoint = self._session.begin_nested()
            try:
                records = self.consolidate_source_group(project_id, sg)
                savepoint.commit()
                total_records += len(records)
            except Exception:
                savepoint.rollback()
                logger.exception(
                    "consolidation_error",
                    project_id=str(project_id),
                    source_group=sg,
                )
                errors += 1

        return {
            "source_groups": len(source_groups),
            "records_created": total_records,
            "errors": errors,
        }

    def _upsert_record(
        self,
        project_id: UUID,
        record: ConsolidatedRecord,
        source_count: int,
    ) -> None:
        """Upsert a consolidated record into the DB."""
        # Build data and provenance dicts from ConsolidatedRecord
        data: dict = {}
        provenance: dict = {}
        total_grounded = 0

        for field_name, field in record.fields.items():
            data[field_name] = field.value
            provenance[field_name] = {
                "strategy": field.strategy,
                "source_count": field.source_count,
                "grounded_count": field.grounded_count,
                "agreement": field.agreement,
                "top_sources": field.top_sources,
            }
            # Record-level grounded_count = max across fields. Per-field
            # breakdown is in the provenance JSONB for detailed queries.
            total_grounded = max(total_grounded, field.grounded_count)

        # PostgreSQL upsert (INSERT ... ON CONFLICT UPDATE)
        stmt = pg_insert(ConsolidatedExtraction).values(
            project_id=project_id,
            source_group=record.source_group,
            extraction_type=record.extraction_type,
            data=data,
            provenance=provenance,
            source_count=source_count,
            grounded_count=total_grounded,
        )
        stmt = stmt.on_conflict_do_update(
            constraint="uq_consolidated_project_sg_type",
            set_={
                "data": stmt.excluded.data,
                "provenance": stmt.excluded.provenance,
                "source_count": stmt.excluded.source_count,
                "grounded_count": stmt.excluded.grounded_count,
                "updated_at": func.now(),
            },
        )
        self._session.execute(stmt)
        self._session.flush()


def _extract_field_definitions(schema: dict) -> dict[str, list[dict]]:
    """Extract field definitions from extraction_schema, keyed by group name."""
    result: dict[str, list[dict]] = {}
    for fg in schema.get("field_groups", []):
        name = fg.get("name", "")
        if name:
            result[name] = fg.get("fields", [])
    return result
