"""Consolidation service: DB integration layer.

Reads raw extractions + grounding_scores from DB, delegates to pure
consolidation functions, writes results to consolidated_extractions table.

Supports optional LLM post-processing for fields with strategy="llm_summarize".
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from sqlalchemy import delete, distinct, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from orm_models import ConsolidatedExtraction, Extraction
from services.extraction.consolidation import (
    ConsolidatedField,
    ConsolidatedRecord,
    WeightedValue,
    consolidate_extractions,
    effective_weight,
    get_llm_summarize_candidates,
)
from services.extraction.extraction_items import safe_data_version
from services.projects.repository import ProjectRepository

if TYPE_CHECKING:
    from services.llm.client import LLMClient

logger = structlog.get_logger(__name__)

_SUMMARIZE_PROMPT = """\
Synthesize these summaries for '{field_name}' into one accurate, concise paragraph.
Only include claims that appear in multiple summaries or have high confidence.
Do NOT add information not present in the summaries.

{candidates}"""


class ConsolidationService:
    """Orchestrates consolidation from raw extractions to consolidated records."""

    def __init__(self, session: Session, project_repo: ProjectRepository):
        self._session = session
        self._project_repo = project_repo

    async def consolidate_source_group(
        self,
        project_id: UUID,
        source_group: str,
        *,
        llm_client: LLMClient | None = None,
    ) -> list[ConsolidatedRecord]:
        """Consolidate all extraction types for one source group.

        1. Load all extractions for (project_id, source_group)
        2. Group by extraction_type
        3. Load field definitions from project schema
        4. Call consolidate_extractions() for each type
        5. Optionally run LLM post-processing for llm_summarize fields
        6. Upsert into consolidated_extractions table
        """
        project = self._project_repo.get(project_id)
        if not project:
            return []

        schema = project.extraction_schema
        if not schema or not schema.get("field_groups"):
            return []

        field_defs_by_group, entity_list_groups = _extract_field_definitions(schema)

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
                    "data_version": safe_data_version(ext),
                    "confidence": ext.confidence if ext.confidence is not None else 0.5,
                    "grounding_scores": ext.grounding_scores or {},
                    "source_id": str(ext.source_id),
                }
                for ext in type_extractions
            ]

            is_entity_list = ext_type in entity_list_groups
            record = consolidate_extractions(
                ext_dicts,
                field_defs,
                source_group,
                ext_type,
                entity_list_key=ext_type if is_entity_list else None,
            )

            # LLM post-processing for llm_summarize fields
            if llm_client and not is_entity_list:
                record = await self._llm_post_process(
                    record,
                    ext_dicts,
                    field_defs,
                    llm_client,
                )

            records.append(record)

            # Upsert to DB
            self._upsert_record(project_id, record, len(type_extractions))

        return records

    async def consolidate_project(
        self,
        project_id: UUID,
        *,
        llm_client: LLMClient | None = None,
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

        return await self._process_source_groups(
            project_id,
            source_groups,
            llm_client=llm_client,
        )

    async def reconsolidate(
        self,
        project_id: UUID,
        source_groups: list[str] | None = None,
        *,
        llm_client: LLMClient | None = None,
    ) -> dict[str, int]:
        """Re-run consolidation (idempotent). Upserts replace old records."""
        if source_groups:
            return await self._process_source_groups(
                project_id,
                source_groups,
                llm_client=llm_client,
            )
        return await self.consolidate_project(project_id, llm_client=llm_client)

    async def _process_source_groups(
        self,
        project_id: UUID,
        source_groups: list[str],
        *,
        llm_client: LLMClient | None = None,
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
                records = await self.consolidate_source_group(
                    project_id,
                    sg,
                    llm_client=llm_client,
                )
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

    async def _llm_post_process(
        self,
        record: ConsolidatedRecord,
        ext_dicts: list[dict],
        field_defs: list[dict],
        llm_client: LLMClient,
    ) -> ConsolidatedRecord:
        """Replace llm_summarize fields with LLM-synthesized text.

        Collects candidate values from extractions, builds a synthesis prompt,
        and replaces the longest_top_k fallback with the LLM result.
        Falls back to the existing value on LLM failure.
        """
        from services.extraction.grounding import GROUNDING_DEFAULTS

        summarize_fields: list[dict] = [
            fd
            for fd in field_defs
            if fd.get("consolidation_strategy") == "llm_summarize"
        ]
        if not summarize_fields:
            return record

        for field_def in summarize_fields:
            field_name = field_def["name"]
            if field_name not in record.fields:
                continue

            field_type = field_def.get("field_type", "text")
            grounding_mode = field_def.get("grounding_mode") or GROUNDING_DEFAULTS.get(
                field_type, "required"
            )

            # Build weighted values from extractions (same logic as consolidate_extractions)
            weighted_values: list[WeightedValue] = []
            for ext in ext_dicts:
                data = ext.get("data", {})
                data_version = ext.get("data_version", 1)
                source_id = ext.get("source_id", "")

                if data_version >= 2:
                    field_data = data.get(field_name)
                    if not isinstance(field_data, dict):
                        continue
                    value = field_data.get("value")
                    if value is None or not isinstance(value, str):
                        continue
                    confidence = float(field_data.get("confidence", 0.5))
                    grounding_score = float(field_data.get("grounding", 1.0))
                    ext_confidence = float(ext.get("confidence", 0.5))
                    weight = effective_weight(
                        confidence, grounding_score, grounding_mode
                    )
                    weight = min(weight, max(ext_confidence, 0.3))
                else:
                    value = data.get(field_name)
                    if value is None or not isinstance(value, str):
                        continue
                    confidence = ext.get("confidence", 0.5)
                    grounding_scores = ext.get("grounding_scores") or {}
                    grounding_score = grounding_scores.get(field_name)
                    weight = effective_weight(
                        confidence, grounding_score, grounding_mode
                    )

                weighted_values.append(WeightedValue(value, weight, str(source_id)))

            candidates = get_llm_summarize_candidates(weighted_values)
            if len(candidates) < 2:
                # Not enough distinct values to synthesize — keep fallback
                continue

            # Build synthesis prompt
            candidate_lines = "\n".join(
                f'{i + 1}. (weight: {w}) "{text}"'
                for i, (text, w) in enumerate(candidates)
            )
            prompt = _SUMMARIZE_PROMPT.format(
                field_name=field_name,
                candidates=candidate_lines,
            )

            try:
                response = await llm_client.complete(
                    system_prompt="You are a concise information synthesizer.",
                    user_prompt=prompt,
                )
                synthesized = response.get("text", "").strip()
                if synthesized:
                    existing = record.fields[field_name]
                    record.fields[field_name] = ConsolidatedField(
                        value=synthesized,
                        strategy="llm_summarize",
                        source_count=existing.source_count,
                        grounded_count=existing.grounded_count,
                        agreement=existing.agreement,
                        winning_weight=existing.winning_weight,
                        top_sources=existing.top_sources,
                    )
                    logger.info(
                        "llm_summarize_success",
                        field=field_name,
                        source_group=record.source_group,
                        candidates=len(candidates),
                    )
            except Exception:
                logger.warning(
                    "llm_summarize_failed",
                    field=field_name,
                    source_group=record.source_group,
                    exc_info=True,
                )
                # Keep longest_top_k fallback value

        return record

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
            prov_entry: dict = {
                "strategy": field.strategy,
                "source_count": field.source_count,
                "grounded_count": field.grounded_count,
                "agreement": field.agreement,
                "winning_weight": field.winning_weight,
                "top_sources": field.top_sources,
            }
            if field.entity_provenance is not None:
                prov_entry["entity_provenance"] = field.entity_provenance
            provenance[field_name] = prov_entry
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


def _extract_field_definitions(
    schema: dict,
) -> tuple[dict[str, list[dict]], set[str]]:
    """Extract field definitions from extraction_schema, keyed by group name.

    Returns:
        Tuple of (field_defs_by_group, entity_list_groups).
        entity_list_groups is the set of group names with is_entity_list=True.
    """
    result: dict[str, list[dict]] = {}
    entity_list_groups: set[str] = set()
    for fg in schema.get("field_groups", []):
        name = fg.get("name", "")
        if name:
            result[name] = fg.get("fields", [])
            if fg.get("is_entity_list", False):
                entity_list_groups.add(name)
    return result, entity_list_groups
