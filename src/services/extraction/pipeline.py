"""Extraction pipeline service for orchestrating the complete extraction flow."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from uuid import UUID

import structlog

from constants import SourceStatus
from services.extraction.content_selector import get_extraction_content
from services.extraction.embedding_pipeline import ExtractionEmbeddingService
from services.extraction.schema_adapter import SchemaAdapter
from services.projects.repository import ProjectRepository
from services.projects.templates import DEFAULT_EXTRACTION_TEMPLATE

logger = structlog.get_logger(__name__)

# Type alias for checkpoint callback
# Args: (processed_source_ids, total_extractions, total_entities)
type CheckpointCallback = Callable[[list[str], int, int], None]


@dataclass
class SchemaPipelineResult:
    """Result from schema-based extraction pipeline.

    Note: total_deduplicated and total_entities are always 0 for schema pipeline.
    Schema extraction uses merge_dedupe within field groups rather than a
    separate dedup/entity step. Kept for interface compatibility with BatchPipelineResult.
    """

    project_id: str
    sources_processed: int
    sources_failed: int
    total_extractions: int
    field_groups: int
    schema_name: str
    sources_skipped: int = 0
    sources_no_content: int = 0
    total_embedded: int = 0
    embedding_errors: int = 0
    total_deduplicated: int = 0
    total_entities: int = 0
    cancelled: bool = False
    error: str | None = None


class SchemaExtractionPipeline:
    """Runs schema extraction on sources and stores results."""

    def __init__(
        self,
        orchestrator,  # SchemaExtractionOrchestrator
        db_session,
        extraction_embedding: ExtractionEmbeddingService | None = None,
        extraction_config=None,
        project_repo: "ProjectRepository | None" = None,
    ):
        from config import settings

        self._orchestrator = orchestrator
        self._db = db_session
        self._extraction_embedding = extraction_embedding
        self._extraction = extraction_config or settings.extraction
        self._project_repo = project_repo

    async def extract_source(
        self,
        source,  # Source ORM object
        source_context: str | None = None,
        field_groups: list | None = None,
        schema_name: str = "unknown",
        update_classification: bool = True,
    ) -> list:  # list[Extraction]
        """Extract all field groups from a source.

        Args:
            source: Source ORM object with markdown content.
            source_context: Source context (e.g., company name, website name).
            field_groups: Pre-converted FieldGroup objects (required).
            schema_name: Name of the schema used for extraction (for tracking).
            update_classification: If True, store classification result on source.
                Set to False for partial field_groups extraction to preserve
                existing classification from a prior full extraction.

        Returns:
            List of created Extraction objects.
        """
        from orm_models import Extraction

        context_value = source_context

        if not source.content:
            logger.warning("source_has_no_content", source_id=str(source.id))
            return []

        # Use provided field_groups or require caller to provide them
        if not field_groups:
            logger.error(
                "extract_source_no_field_groups",
                source_id=str(source.id),
                message="field_groups must be provided",
            )
            return []

        # Run extraction for all field groups (prefer domain-deduped content)
        dedup_content = get_extraction_content(
            source, domain_dedup_enabled=self._extraction.domain_dedup_enabled
        )
        results, classification = await self._orchestrator.extract_all_groups(
            source_id=source.id,
            markdown=dedup_content,
            source_context=context_value,
            field_groups=field_groups,
            source_url=source.uri,
            source_title=source.title,
        )

        # Store classification result on source if available
        # Skip when doing partial field_groups extraction to preserve
        # classification from prior full extraction
        if classification and update_classification:
            source.page_type = classification.page_type
            source.relevant_field_groups = classification.relevant_groups
            source.classification_method = classification.method.value
            source.classification_confidence = classification.confidence

        # Store each result as an extraction
        extractions = []
        for result in results:
            # Record truncation in chunk_context if any chunk was truncated
            chunk_context = None
            if result.get("data", {}).pop("_truncated", False):
                chunk_context = {"truncated": True}
                logger.warning(
                    "extraction_truncated",
                    source_id=str(source.id),
                    extraction_type=result["extraction_type"],
                )

            data_version = result.get("data_version", 1)
            extraction = Extraction(
                project_id=source.project_id,
                source_id=source.id,
                data=result["data"],
                data_version=data_version,
                extraction_type=result["extraction_type"],
                source_group=context_value,
                confidence=result.get("confidence"),
                grounding_scores=result.get("grounding_scores") if data_version < 2 else None,
                profile_used=schema_name,
                chunk_context=chunk_context,
            )
            self._db.add(extraction)
            extractions.append(extraction)

        self._db.flush()
        return extractions

    async def extract_project(
        self,
        project_id: UUID,
        source_ids: list[UUID] | None = None,
        source_groups: list[str] | None = None,
        skip_extracted: bool = True,
        field_groups_filter: list[str] | None = None,
        cancellation_check: Callable[[], Awaitable[bool]] | None = None,
        checkpoint_callback: CheckpointCallback | None = None,
        resume_from: set[str] | None = None,
    ) -> SchemaPipelineResult:
        """Extract all sources in a project.

        Args:
            project_id: Project UUID.
            source_ids: Optional specific source IDs to process. If provided,
                        only these sources are extracted (ignores skip_extracted).
            source_groups: Optional filter by company names.
            skip_extracted: If True, skip sources with 'extracted' status.
                           Ignored when source_ids is provided.
            cancellation_check: Optional async callback that returns True if
                              processing should be cancelled.
            checkpoint_callback: Optional callback invoked after each chunk commit.
                               Called with (processed_source_ids, total_extractions, total_entities).
            resume_from: Optional set of source IDs to skip (already processed in prior run).

        Returns:
            Summary dict with extraction counts including sources_failed.
        """
        from sqlalchemy import select

        from orm_models import Source

        # Load project to get extraction_schema
        project_repo = self._project_repo or ProjectRepository(self._db)
        project = project_repo.get(project_id)
        if not project:
            logger.error("project_not_found", project_id=str(project_id))
            return SchemaPipelineResult(
                project_id=str(project_id),
                sources_processed=0,
                sources_failed=0,
                total_extractions=0,
                field_groups=0,
                schema_name="unknown",
                error="Project not found",
            )

        # Convert project schema to field groups
        adapter = SchemaAdapter()
        schema = project.extraction_schema

        # Fallback to default if schema is missing or has no valid field_groups
        if not schema or not isinstance(schema.get("field_groups"), list):
            logger.warning(
                "project_missing_schema_using_default",
                project_id=str(project_id),
            )
            schema = DEFAULT_EXTRACTION_TEMPLATE["extraction_schema"]

        validation = adapter.validate_extraction_schema(schema)
        if not validation.is_valid:
            logger.error(
                "invalid_extraction_schema",
                project_id=str(project_id),
                errors=validation.errors,
            )
            return SchemaPipelineResult(
                project_id=str(project_id),
                sources_processed=0,
                sources_failed=0,
                total_extractions=0,
                field_groups=0,
                schema_name="invalid",
                error=f"Invalid extraction schema: {'; '.join(validation.errors)}",
            )

        field_groups = adapter.convert_to_field_groups(schema)

        if field_groups_filter:
            all_names = {g.name for g in field_groups}
            filter_set = set(field_groups_filter)
            unmatched = filter_set - all_names
            field_groups = [g for g in field_groups if g.name in filter_set]

            if unmatched:
                if not field_groups:
                    error_msg = (
                        f"No field groups matched filter {sorted(unmatched)}. "
                        f"Available: {sorted(all_names)}"
                    )
                    logger.error(
                        "field_groups_filter_no_match",
                        project_id=str(project_id),
                        requested=field_groups_filter,
                        available=sorted(all_names),
                    )
                    return SchemaPipelineResult(
                        project_id=str(project_id),
                        sources_processed=0,
                        sources_failed=0,
                        total_extractions=0,
                        field_groups=0,
                        schema_name=schema.get("name", "unknown"),
                        error=error_msg,
                    )
                logger.warning(
                    "field_groups_filter_partial_match",
                    project_id=str(project_id),
                    matched=[g.name for g in field_groups],
                    unmatched=sorted(unmatched),
                )

            logger.info(
                "field_groups_filtered",
                project_id=str(project_id),
                requested=field_groups_filter,
                matched=[g.name for g in field_groups],
            )

        logger.info(
            "using_project_schema",
            project_id=str(project_id),
            schema_name=schema.get("name", "unknown"),
            field_groups_count=len(field_groups),
        )

        # Build query based on whether specific source_ids are provided
        if source_ids:
            # When specific source_ids provided, extract those regardless of status
            stmt = select(Source).where(
                Source.project_id == project_id,
                Source.id.in_(source_ids),
                Source.content.isnot(None),
            )
        else:
            # Build list of allowed statuses based on skip_extracted flag
            allowed_statuses = [SourceStatus.READY, SourceStatus.PENDING]
            if not skip_extracted:
                allowed_statuses.append(SourceStatus.EXTRACTED)

            # Include sources that are ready (and optionally extracted)
            stmt = select(Source).where(
                Source.project_id == project_id,
                Source.status.in_(allowed_statuses),
                Source.content.isnot(None),
            )

        if source_groups:
            stmt = stmt.where(Source.source_group.in_(source_groups))

        sources = list(self._db.execute(stmt).scalars().all())

        logger.info(
            "project_extraction_started",
            project_id=str(project_id),
            source_count=len(sources),
            field_groups_count=len(field_groups),
        )

        # Get schema name for tracking
        schema_name = schema.get("name", "unknown")

        # Check for cancellation before starting
        if cancellation_check and await cancellation_check():
            logger.info(
                "schema_extraction_cancelled_before_start",
                project_id=str(project_id),
                source_count=len(sources),
            )
            return SchemaPipelineResult(
                project_id=str(project_id),
                sources_processed=0,
                sources_failed=0,
                total_extractions=0,
                field_groups=len(field_groups),
                schema_name=schema.get("name", "unknown"),
                cancelled=True,
            )

        # Process sources in parallel with cancellation support
        # Use chunked processing to allow cancellation checks between batches
        semaphore = asyncio.Semaphore(self._extraction.max_concurrent_sources)
        chunk_size = self._extraction.extraction_batch_size

        # Track whether embedding is available for this run
        embed_enabled = (
            self._extraction.schema_embedding_enabled
            and self._extraction_embedding is not None
        )

        # Collect extractions per source for batch embedding
        chunk_extractions: list = []  # Extraction ORM objects from current chunk

        async def extract_with_limit(source) -> tuple[int, bool, str]:
            """Extract source and return (extraction_count, success, status).

            Status is one of: "extracted", "skipped", "no_content", "failed".
            """
            async with semaphore:
                try:
                    extractions = await self.extract_source(
                        source=source,
                        source_context=source.source_group,
                        field_groups=field_groups,
                        schema_name=schema_name,
                        update_classification=not bool(field_groups_filter),
                    )
                    # Update source status based on classification result
                    if source.page_type == "skip":
                        source.status = SourceStatus.SKIPPED
                        return len(extractions), True, "skipped"
                    source.status = SourceStatus.EXTRACTED
                    # Collect for batch embedding
                    if embed_enabled:
                        chunk_extractions.extend(extractions)
                    if not extractions and not source.content:
                        return 0, True, "no_content"
                    return len(extractions), True, "extracted"
                except Exception as e:
                    logger.error(
                        "schema_extraction_failed",
                        source_id=str(source.id),
                        error=str(e),
                        exc_info=True,
                    )
                    return 0, False, "failed"

        # Process in chunks to allow cancellation checks and batch commits
        all_results = []
        all_processed_ids: list[str] = []
        total_embedded = 0
        total_embedding_errors = 0
        cancelled = False
        chunk_idx = 0

        for i in range(0, len(sources), chunk_size):
            # Check for cancellation between chunks
            if cancellation_check and await cancellation_check():
                logger.info(
                    "schema_extraction_cancelled",
                    project_id=str(project_id),
                    processed=i,
                    remaining=len(sources) - i,
                )
                cancelled = True
                break

            chunk = sources[i : i + chunk_size]

            # Filter already-processed sources when resuming
            if resume_from:
                chunk = [s for s in chunk if str(s.id) not in resume_from]
                if not chunk:
                    chunk_idx += 1
                    continue

            # Reset chunk extraction collector
            chunk_extractions.clear()

            chunk_results = await asyncio.gather(
                *[extract_with_limit(s) for s in chunk],
            )
            all_results.extend(chunk_results)

            # Track only successfully processed source IDs for checkpoint
            # Failed sources keep their original status and can be retried
            chunk_processed_ids = [
                str(s.id)
                for s, (_, success, _status) in zip(chunk, chunk_results, strict=True)
                if success
            ]
            all_processed_ids.extend(chunk_processed_ids)

            # Flush to ensure extraction IDs are assigned before embedding
            self._db.flush()

            # Embed extractions from this chunk (after flush, before commit)
            if embed_enabled and chunk_extractions:
                embed_result = await self._extraction_embedding.embed_and_upsert(
                    chunk_extractions
                )
                total_embedded += embed_result.embedded_count
                if embed_result.errors:
                    total_embedding_errors += embed_result.failed_count or 1
                    logger.error(
                        "chunk_embedding_failed",
                        errors=embed_result.errors,
                        chunk=chunk_idx + 1,
                    )
                # Mark extractions as embedded when the batch succeeded
                if embed_result.embedded_count > 0 and not embed_result.errors:
                    for e in chunk_extractions:
                        e.embedded = True

            # Call checkpoint callback to update job payload before commit
            if checkpoint_callback:
                total_extractions_so_far = sum(count for count, _, _ in all_results)
                checkpoint_callback(all_processed_ids, total_extractions_so_far, 0)

            # Commit after each chunk for durability (includes checkpoint update)
            self._db.commit()
            logger.info(
                "chunk_committed",
                chunk=chunk_idx + 1,
                chunk_sources=len(chunk),
                total_processed=len(all_processed_ids),
                embedded=total_embedded if embed_enabled else None,
            )

            chunk_idx += 1

        # Count successes, failures, and categories
        total_extractions = sum(count for count, _, _ in all_results)
        sources_failed = sum(1 for _, success, _ in all_results if not success)
        sources_skipped = sum(
            1 for _, success, st in all_results if success and st == "skipped"
        )
        sources_no_content = sum(
            1 for _, success, st in all_results if success and st == "no_content"
        )
        sources_processed = len(all_results)

        return SchemaPipelineResult(
            project_id=str(project_id),
            sources_processed=sources_processed,
            sources_failed=sources_failed,
            total_extractions=total_extractions,
            field_groups=len(field_groups),
            schema_name=schema.get("name", "unknown"),
            sources_skipped=sources_skipped,
            sources_no_content=sources_no_content,
            total_embedded=total_embedded,
            embedding_errors=total_embedding_errors,
            cancelled=cancelled,
        )
