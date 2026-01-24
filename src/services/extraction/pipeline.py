"""Extraction pipeline service for orchestrating the complete extraction flow."""

import asyncio
from dataclasses import dataclass, field
from typing import TYPE_CHECKING
from uuid import UUID

import structlog

from models import ExtractionProfile

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
from services.storage.qdrant.repository import QdrantRepository
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
        source = await self._source_repo.get(source_id)
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
        project = await self._project_repo.get(project_id)
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

        # Process each fact
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
                extraction = await self._extraction_repo.create(
                    project_id=project_id,
                    source_id=source_id,
                    data={"fact_text": fact.fact, "category": fact.category},
                    extraction_type=fact.category,
                    source_group=source.source_group,
                    confidence=fact.confidence,
                    profile_used=profile_name,
                )
                extractions_created += 1

                # Generate and store embedding
                embedding = await self._embedding_service.embed(fact.fact)
                await self._qdrant_repo.upsert(
                    extraction_id=extraction.id,
                    embedding=embedding,
                    payload={
                        "project_id": str(project_id),
                        "source_group": source.source_group,
                        "extraction_type": fact.category,
                    },
                )

                # Extract entities
                entities = await self._entity_extractor.extract(
                    extraction_id=extraction.id,
                    extraction_data={"fact_text": fact.fact, "category": fact.category},
                    project_id=project_id,
                    entity_types=entity_types,
                    source_group=source.source_group,
                )
                entities_extracted += len(entities)

            except Exception as e:
                errors.append(f"Error processing fact: {str(e)}")
                logger.error("fact_processing_failed", error=str(e), fact=fact.fact)

        # Update source status
        await self._source_repo.update_status(source_id, "extracted")

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

            wait_time = BACKPRESSURE_WAIT_BASE * (1.5 ** attempt)
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
    ) -> BatchPipelineResult:
        """Process multiple sources in parallel.

        Args:
            source_ids: List of source UUIDs to process.
            project_id: Project UUID.
            profile_name: Extraction profile name.
            max_concurrent: Maximum concurrent source extractions.
            chunk_size: Optional chunk size for processing in batches.
                       If provided, backpressure is checked between chunks.

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
    ) -> BatchPipelineResult:
        """Process all pending sources for a project."""
        # Query for pending sources
        pending_sources = await self._source_repo.get_by_project_and_status(
            project_id, "pending"
        )

        # Extract source IDs
        source_ids = [source.id for source in pending_sources]

        # Process batch
        return await self.process_batch(source_ids, project_id, profile_name)


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
        company_name: str,
        field_groups: list | None = None,
        schema_name: str = "unknown",
    ) -> list:  # list[Extraction]
        """Extract all field groups from a source.

        Args:
            source: Source ORM object with markdown content.
            company_name: Company name (source_group).
            field_groups: Pre-converted FieldGroup objects (required).
            schema_name: Name of the schema used for extraction (for tracking).

        Returns:
            List of created Extraction objects.
        """
        from orm_models import Extraction

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

        # Run extraction for all field groups
        results = await self._orchestrator.extract_all_groups(
            source_id=source.id,
            markdown=source.content,
            company_name=company_name,
            field_groups=field_groups,
        )

        # Store each result as an extraction
        extractions = []
        for result in results:
            extraction = Extraction(
                project_id=source.project_id,
                source_id=source.id,
                data=result["data"],
                extraction_type=result["extraction_type"],
                source_group=company_name,
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
        source_groups: list[str] | None = None,
        skip_extracted: bool = True,
    ) -> dict:
        """Extract all sources in a project.

        Args:
            project_id: Project UUID.
            source_groups: Optional filter by company names.
            skip_extracted: If True, skip sources with 'extracted' status.

        Returns:
            Summary dict with extraction counts.
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

        # Process sources in parallel
        semaphore = asyncio.Semaphore(10)

        async def extract_with_limit(source) -> int:
            async with semaphore:
                extractions = await self.extract_source(
                    source=source,
                    company_name=source.source_group,
                    field_groups=field_groups,
                    schema_name=schema_name,
                )
                return len(extractions)

        extraction_counts = await asyncio.gather(
            *[extract_with_limit(s) for s in sources],
            return_exceptions=True,
        )

        total_extractions = sum(
            c for c in extraction_counts if isinstance(c, int)
        )

        for i, result in enumerate(extraction_counts):
            if isinstance(result, Exception):
                logger.error(
                    "schema_extraction_failed",
                    source_id=str(sources[i].id),
                    error=str(result),
                )

        self._db.commit()

        return {
            "project_id": str(project_id),
            "sources_processed": len(sources),
            "extractions_created": total_extractions,
            "field_groups": len(field_groups),
            "schema_name": schema.get("name", "unknown"),
        }
