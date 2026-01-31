"""Extraction pipeline service for orchestrating the complete extraction flow."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID

import structlog

from config import settings as app_settings
from models import ExtractionProfile
from services.alerting import get_alert_service

if TYPE_CHECKING:
    from services.llm.queue import LLMRequestQueue


class QueueFullError(Exception):
    """Raised when LLM queue is persistently full and cannot accept new requests."""

    pass


# Backpressure constants
BACKPRESSURE_WAIT_BASE = 2.0  # Base wait time in seconds
MAX_BACKPRESSURE_RETRIES = 10  # Max retries before raising QueueFullError


from services.extraction.extractor import ExtractionOrchestrator
from services.extraction.profiles import ProfileRepository
from services.extraction.schema_adapter import SchemaAdapter
from services.knowledge.extractor import EntityExtractor
from services.projects.repository import ProjectRepository
from services.projects.templates import DEFAULT_EXTRACTION_TEMPLATE
from services.storage.deduplication import ExtractionDeduplicator
from services.storage.embedding import EmbeddingService
from services.storage.qdrant.repository import EmbeddingItem, QdrantRepository
from services.storage.repositories.extraction import ExtractionRepository
from services.storage.repositories.source import SourceRepository

# Default fallback profile when database profile not found
DEFAULT_PROFILE = ExtractionProfile(
    name="general",
    categories=["general", "features", "technical", "integration"],
    prompt_focus="General technical facts about the product, features, integrations, and capabilities",
    depth="detailed",
    is_builtin=True,
)

logger = structlog.get_logger(__name__)


@dataclass
class PipelineResult:
    """Result from processing a single source."""

    source_id: UUID
    extractions_created: int
    extractions_deduplicated: int
    entities_extracted: int
    entities_deduplicated: int
    errors: list[str] = field(default_factory=list)


@dataclass
class BatchPipelineResult:
    """Result from processing multiple sources."""

    sources_processed: int
    sources_failed: int
    total_extractions: int
    total_deduplicated: int
    total_entities: int
    results: list[PipelineResult] = field(default_factory=list)


class ExtractionPipelineService:
    """Orchestrates the complete extraction pipeline."""

    def __init__(
        self,
        orchestrator: ExtractionOrchestrator,
        deduplicator: ExtractionDeduplicator,
        entity_extractor: EntityExtractor,
        extraction_repo: ExtractionRepository,
        source_repo: SourceRepository,
        project_repo: ProjectRepository,
        qdrant_repo: QdrantRepository,
        embedding_service: EmbeddingService,
        profile_repo: ProfileRepository | None = None,
        llm_queue: "LLMRequestQueue | None" = None,
    ):
        """Initialize pipeline with all dependencies."""
        self._orchestrator = orchestrator
        self._deduplicator = deduplicator
        self._entity_extractor = entity_extractor
        self._extraction_repo = extraction_repo
        self._source_repo = source_repo
        self._project_repo = project_repo
        self._qdrant_repo = qdrant_repo
        self._embedding_service = embedding_service
        self._profile_repo = profile_repo
        self._llm_queue = llm_queue

    async def process_source(
        self,
        source_id: UUID,
        project_id: UUID,
        profile_name: str = "general",
    ) -> PipelineResult:
        """Process a single source through the full pipeline."""
        errors = []
        extractions_created = 0
        extractions_deduplicated = 0
        entities_extracted = 0
        entities_deduplicated = 0

        # Fetch source
        source = self._source_repo.get(source_id)
        if not source or not source.content:
            return PipelineResult(
                source_id=source_id,
                extractions_created=0,
                extractions_deduplicated=0,
                entities_extracted=0,
                entities_deduplicated=0,
                errors=["Source not found or empty"],
            )

        # Get project for entity types
        project = self._project_repo.get(project_id)
        entity_types = project.entity_types if project else []

        # Load extraction profile
        profile = None
        if self._profile_repo:
            profile = self._profile_repo.get_by_name(profile_name)
        if profile is None:
            profile = DEFAULT_PROFILE

        # Extract facts via orchestrator
        result = await self._orchestrator.extract(
            page_id=source_id,
            markdown=source.content,
            profile=profile,
        )

        # Phase 1: Deduplicate and collect facts to embed
        facts_to_embed = []
        fact_extractions = []  # Track (fact, extraction) pairs

        for fact in result.facts:
            try:
                # Check for duplicate
                dedup_result = await self._deduplicator.check_duplicate(
                    project_id=project_id,
                    source_group=source.source_group,
                    text_content=fact.fact,
                )

                if dedup_result.is_duplicate:
                    extractions_deduplicated += 1
                    continue

                # Store extraction
                extraction = self._extraction_repo.create(
                    project_id=project_id,
                    source_id=source_id,
                    data={"fact_text": fact.fact, "category": fact.category},
                    extraction_type=fact.category,
                    source_group=source.source_group,
                    confidence=fact.confidence,
                    profile_used=profile_name,
                )
                extractions_created += 1

                # Collect for batch embedding
                facts_to_embed.append(fact.fact)
                fact_extractions.append((fact, extraction))

            except Exception as e:
                errors.append(f"Error processing fact: {str(e)}")
                logger.error("fact_processing_failed", error=str(e), fact=fact.fact)

        # Phase 2: Batch embed and upsert
        embeddings_succeeded = False
        if facts_to_embed:
            try:
                # Batch embed all facts at once
                embeddings = await self._embedding_service.embed_batch(facts_to_embed)

                # Phase 3: Batch upsert to Qdrant
                items = [
                    EmbeddingItem(
                        extraction_id=extraction.id,
                        embedding=embedding,
                        payload={
                            "project_id": str(project_id),
                            "source_group": source.source_group,
                            "extraction_type": fact.category,
                        },
                    )
                    for (fact, extraction), embedding in zip(
                        fact_extractions, embeddings, strict=True
                    )
                ]
                await self._qdrant_repo.upsert_batch(items)

                # Phase 3b: Update extraction records with embedding_id
                # This tracks which extractions have embeddings in Qdrant
                extraction_ids = [extraction.id for _, extraction in fact_extractions]
                self._extraction_repo.update_embedding_ids_batch(extraction_ids)

                embeddings_succeeded = True

            except Exception as e:
                errors.append(f"Error batch embedding: {str(e)}")
                logger.error(
                    "batch_embedding_failed",
                    error=str(e),
                    extractions_affected=len(fact_extractions),
                    source_id=str(source_id),
                )
                # Skip entity extraction - extractions exist but aren't searchable
                # entities_extracted will remain False, signaling incomplete processing

                # Alert on partial failure (PG succeeded, Qdrant failed)
                try:
                    alert_service = get_alert_service()
                    await alert_service.alert_embedding_failure(
                        project_id=project_id,
                        source_id=source_id,
                        extractions_affected=len(fact_extractions),
                        error=str(e),
                    )
                except Exception as alert_err:
                    # Don't let alerting failure break the pipeline
                    logger.warning(
                        "alert_delivery_failed",
                        error=str(alert_err),
                    )

        # Phase 4: Entity extraction (only if embeddings succeeded)
        if not embeddings_succeeded and fact_extractions:
            logger.warning(
                "skipping_entity_extraction",
                reason="embeddings_failed",
                source_id=str(source_id),
                extractions_count=len(fact_extractions),
            )

        for fact, extraction in fact_extractions if embeddings_succeeded else []:
            try:
                entities = await self._entity_extractor.extract(
                    extraction_id=extraction.id,
                    extraction_data={"fact_text": fact.fact, "category": fact.category},
                    project_id=project_id,
                    entity_types=entity_types,
                    source_group=source.source_group,
                )
                entities_extracted += len(entities)

                # Mark extraction as having entities extracted
                self._extraction_repo.update_entities_extracted(
                    extraction_id=extraction.id,
                    entities_extracted=True,
                )

            except Exception as e:
                errors.append(f"Error extracting entities: {str(e)}")
                logger.error(
                    "entity_extraction_failed",
                    error=str(e),
                    error_type=type(e).__name__,
                    source_id=str(source_id),
                    source_url=source.uri if hasattr(source, "uri") else None,
                    source_group=source.source_group,
                    fact_preview=fact.fact[:500] if fact.fact else None,
                    fact_category=fact.category,
                    fact_confidence=fact.confidence,
                    exc_info=True,
                )

        # Update source status
        self._source_repo.update_status(source_id, "extracted")

        return PipelineResult(
            source_id=source_id,
            extractions_created=extractions_created,
            extractions_deduplicated=extractions_deduplicated,
            entities_extracted=entities_extracted,
            entities_deduplicated=entities_deduplicated,
            errors=errors,
        )

    async def _wait_for_queue_capacity(self) -> None:
        """Wait for LLM queue to have capacity.

        Uses exponential backoff to poll queue status.

        Raises:
            QueueFullError: If queue remains full after max retries.
        """
        if self._llm_queue is None:
            return

        for attempt in range(MAX_BACKPRESSURE_RETRIES):
            status = await self._llm_queue.get_backpressure_status()

            if not status.get("should_wait", False):
                return

            wait_time = BACKPRESSURE_WAIT_BASE * (1.5**attempt)
            logger.info(
                "pipeline_backpressure_wait",
                attempt=attempt + 1,
                max_retries=MAX_BACKPRESSURE_RETRIES,
                wait_seconds=wait_time,
                queue_depth=status.get("queue_depth"),
                pressure=status.get("pressure"),
            )
            await asyncio.sleep(wait_time)

        # All retries exhausted
        raise QueueFullError(
            f"LLM queue persistently full after {MAX_BACKPRESSURE_RETRIES} retries"
        )

    async def process_batch(
        self,
        source_ids: list[UUID],
        project_id: UUID,
        profile_name: str = "general",
        max_concurrent: int = 10,
        chunk_size: int | None = None,
        cancellation_check: Callable[[], Awaitable[bool]] | None = None,
    ) -> BatchPipelineResult:
        """Process multiple sources in parallel.

        Args:
            source_ids: List of source UUIDs to process.
            project_id: Project UUID.
            profile_name: Extraction profile name.
            max_concurrent: Maximum concurrent source extractions.
            chunk_size: Optional chunk size for processing in batches.
                       If provided, backpressure is checked between chunks.
            cancellation_check: Optional async callback that returns True if
                              processing should be cancelled.

        Returns:
            BatchPipelineResult with aggregated results.

        Raises:
            QueueFullError: If LLM queue is persistently full.
        """
        # Check backpressure before starting
        await self._wait_for_queue_capacity()

        semaphore = asyncio.Semaphore(max_concurrent)

        async def process_with_limit(source_id: UUID) -> PipelineResult:
            """Process a single source with concurrency control."""
            async with semaphore:
                return await self.process_source(source_id, project_id, profile_name)

        # Process in chunks if chunk_size is specified
        if chunk_size and len(source_ids) > chunk_size:
            all_results = []
            for i in range(0, len(source_ids), chunk_size):
                # Check for cancellation between chunks
                if cancellation_check and await cancellation_check():
                    logger.info(
                        "batch_processing_cancelled",
                        processed=i,
                        remaining=len(source_ids) - i,
                    )
                    break

                chunk = source_ids[i : i + chunk_size]

                # Check backpressure between chunks
                if i > 0:
                    await self._wait_for_queue_capacity()

                chunk_results = await asyncio.gather(
                    *[process_with_limit(sid) for sid in chunk],
                    return_exceptions=True,
                )
                all_results.extend(chunk_results)
            results = all_results
        else:
            # Check cancellation before starting non-chunked batch
            if cancellation_check and await cancellation_check():
                logger.info(
                    "batch_processing_cancelled_before_start",
                    total_sources=len(source_ids),
                )
                results = []
            else:
                # Process all sources in parallel with bounded concurrency
                results = await asyncio.gather(
                    *[process_with_limit(sid) for sid in source_ids],
                    return_exceptions=True,
                )

        # Handle exceptions in results
        processed_results = []
        for i, result in enumerate(results):
            if isinstance(result, Exception):
                logger.error(
                    "source_processing_failed",
                    source_id=str(source_ids[i]),
                    error=str(result),
                )
                processed_results.append(
                    PipelineResult(
                        source_id=source_ids[i],
                        extractions_created=0,
                        extractions_deduplicated=0,
                        entities_extracted=0,
                        entities_deduplicated=0,
                        errors=[f"Processing failed: {str(result)}"],
                    )
                )
            else:
                processed_results.append(result)

        # Aggregate results
        sources_failed = sum(1 for r in processed_results if r.errors)
        total_extractions = sum(r.extractions_created for r in processed_results)
        total_deduplicated = sum(r.extractions_deduplicated for r in processed_results)
        total_entities = sum(r.entities_extracted for r in processed_results)

        return BatchPipelineResult(
            sources_processed=len(source_ids),
            sources_failed=sources_failed,
            total_extractions=total_extractions,
            total_deduplicated=total_deduplicated,
            total_entities=total_entities,
            results=processed_results,
        )

    async def process_project_pending(
        self,
        project_id: UUID,
        profile_name: str = "general",
        cancellation_check: Callable[[], Awaitable[bool]] | None = None,
    ) -> BatchPipelineResult:
        """Process all pending sources for a project.

        Args:
            project_id: Project UUID.
            profile_name: Extraction profile name.
            cancellation_check: Optional async callback that returns True if
                              processing should be cancelled.

        Returns:
            BatchPipelineResult with aggregated results.
        """
        # Query for pending sources
        pending_sources = self._source_repo.get_by_project_and_status(
            project_id, "pending"
        )

        # Extract source IDs
        source_ids = [source.id for source in pending_sources]

        # Process batch with cancellation support
        return await self.process_batch(
            source_ids,
            project_id,
            profile_name,
            cancellation_check=cancellation_check,
        )


class SchemaExtractionPipeline:
    """Runs schema extraction on sources and stores results."""

    def __init__(
        self,
        orchestrator,  # SchemaExtractionOrchestrator
        db_session,
    ):
        self._orchestrator = orchestrator
        self._db = db_session

    async def extract_source(
        self,
        source,  # Source ORM object
        source_context: str | None = None,
        company_name: str | None = None,  # Deprecated, backward compat
        field_groups: list | None = None,
        extraction_context: "ExtractionContext | None" = None,
        schema_name: str = "unknown",
    ) -> list:  # list[Extraction]
        """Extract all field groups from a source.

        Args:
            source: Source ORM object with markdown content.
            source_context: Source context (e.g., company name, website name).
            company_name: DEPRECATED. Use source_context instead.
            field_groups: Pre-converted FieldGroup objects (required).
            extraction_context: Optional extraction context for prompt customization.
            schema_name: Name of the schema used for extraction (for tracking).

        Returns:
            List of created Extraction objects.
        """
        from orm_models import Extraction

        # Backward compatibility: use company_name if source_context not provided
        context_value = source_context if source_context is not None else company_name

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

        # Run extraction for all field groups (with classification)
        results, classification = await self._orchestrator.extract_all_groups(
            source_id=source.id,
            markdown=source.content,
            source_context=context_value,
            field_groups=field_groups,
            source_url=source.uri,
            source_title=source.title,
        )

        # Store classification result on source if available
        if classification:
            source.page_type = classification.page_type
            source.relevant_field_groups = classification.relevant_groups
            source.classification_method = classification.method.value
            source.classification_confidence = classification.confidence

        # Store each result as an extraction
        extractions = []
        for result in results:
            extraction = Extraction(
                project_id=source.project_id,
                source_id=source.id,
                data=result["data"],
                extraction_type=result["extraction_type"],
                source_group=context_value,
                confidence=result.get("confidence"),
                profile_used=schema_name,
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
        cancellation_check: Callable[[], Awaitable[bool]] | None = None,
    ) -> dict:
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

        Returns:
            Summary dict with extraction counts including sources_failed.
        """
        from orm_models import Project, Source

        # Load project to get extraction_schema
        project = self._db.query(Project).filter(Project.id == project_id).first()
        if not project:
            logger.error("project_not_found", project_id=str(project_id))
            return {"error": "Project not found", "project_id": str(project_id)}

        # Convert project schema to field groups
        adapter = SchemaAdapter()
        schema = project.extraction_schema

        # Fallback to default if schema is missing or invalid
        if not schema:
            logger.warning(
                "project_missing_schema_using_default",
                project_id=str(project_id),
            )
            schema = DEFAULT_EXTRACTION_TEMPLATE["extraction_schema"]

        validation = adapter.validate_extraction_schema(schema)
        if not validation.is_valid:
            logger.error(
                "invalid_extraction_schema_using_default",
                project_id=str(project_id),
                errors=validation.errors,
            )
            schema = DEFAULT_EXTRACTION_TEMPLATE["extraction_schema"]

        field_groups = adapter.convert_to_field_groups(schema)

        logger.info(
            "using_project_schema",
            project_id=str(project_id),
            schema_name=schema.get("name", "unknown"),
            field_groups_count=len(field_groups),
        )

        # Build query based on whether specific source_ids are provided
        if source_ids:
            # When specific source_ids provided, extract those regardless of status
            query = self._db.query(Source).filter(
                Source.project_id == project_id,
                Source.id.in_(source_ids),
                Source.content.isnot(None),
            )
        else:
            # Build list of allowed statuses based on skip_extracted flag
            allowed_statuses = ["ready", "pending"]
            if not skip_extracted:
                allowed_statuses.append("extracted")

            # Include sources that are ready (and optionally extracted)
            query = self._db.query(Source).filter(
                Source.project_id == project_id,
                Source.status.in_(allowed_statuses),
                Source.content.isnot(None),
            )

        if source_groups:
            query = query.filter(Source.source_group.in_(source_groups))

        sources = query.all()

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
            return {
                "project_id": str(project_id),
                "sources_processed": 0,
                "sources_failed": 0,
                "extractions_created": 0,
                "field_groups": len(field_groups),
                "schema_name": schema.get("name", "unknown"),
                "cancelled": True,
            }

        # Process sources in parallel with cancellation support
        # Use chunked processing to allow cancellation checks between batches
        semaphore = asyncio.Semaphore(app_settings.extraction_max_concurrent_sources)
        chunk_size = 20  # Check cancellation every 20 sources

        async def extract_with_limit(source) -> tuple[int, bool]:
            """Extract source and return (extraction_count, success)."""
            async with semaphore:
                try:
                    extractions = await self.extract_source(
                        source=source,
                        source_context=source.source_group,
                        field_groups=field_groups,
                        schema_name=schema_name,
                    )
                    # Update source status based on classification result
                    if source.page_type == "skip":
                        source.status = "skipped"
                    else:
                        source.status = "extracted"
                    return len(extractions), True
                except Exception as e:
                    logger.error(
                        "schema_extraction_failed",
                        source_id=str(source.id),
                        error=str(e),
                        exc_info=True,
                    )
                    return 0, False

        # Process in chunks to allow cancellation checks
        all_results = []
        cancelled = False
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
            chunk_results = await asyncio.gather(
                *[extract_with_limit(s) for s in chunk],
            )
            all_results.extend(chunk_results)

        # Count successes and failures
        total_extractions = sum(count for count, _ in all_results)
        sources_failed = sum(1 for _, success in all_results if not success)
        sources_processed = len(all_results)

        # Commit any changes made during processing
        if sources_processed > 0:
            self._db.commit()

        result = {
            "project_id": str(project_id),
            "sources_processed": sources_processed,
            "sources_failed": sources_failed,
            "extractions_created": total_extractions,
            "field_groups": len(field_groups),
            "schema_name": schema.get("name", "unknown"),
        }

        if cancelled:
            result["cancelled"] = True

        return result
