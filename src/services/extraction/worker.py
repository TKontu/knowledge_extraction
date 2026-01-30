"""Background worker for processing extraction jobs."""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from sqlalchemy.orm import Session

from orm_models import Job, Project
from services.extraction.pipeline import ExtractionPipelineService, SchemaExtractionPipeline
from services.storage.repositories.job import JobRepository

if TYPE_CHECKING:
    from services.llm.queue import LLMRequestQueue

logger = structlog.get_logger(__name__)


@dataclass
class SchemaExtractionResult:
    """Result from schema-based extraction to match BatchPipelineResult interface."""

    sources_processed: int
    sources_failed: int
    total_extractions: int
    total_deduplicated: int = 0
    total_entities: int = 0


class ExtractionWorker:
    """Background worker for processing extraction jobs.

    Handles queued extraction jobs by:
    1. Updating job status to "running"
    2. Checking if project has extraction_schema
    3. Using SchemaExtractionPipeline if schema exists, else ExtractionPipelineService
    4. Updating job with results and completion status

    The worker automatically selects the appropriate extraction strategy:
    - Projects WITH extraction_schema: Uses template-based field group extraction
    - Projects WITHOUT extraction_schema: Uses generic fact extraction

    Args:
        db: Database session for persistence.
        pipeline_service: Pipeline service for generic extraction (fallback).
        settings: Application settings for creating schema pipeline.
        llm_queue: Optional LLM request queue for schema extraction.

    Example:
        pipeline = ExtractionPipelineService(...)
        worker = ExtractionWorker(
            db=db,
            pipeline_service=pipeline,
            settings=settings,
            llm_queue=llm_queue,
        )
        await worker.process_job(job)
    """

    def __init__(
        self,
        db: Session,
        pipeline_service: ExtractionPipelineService,
        settings=None,
        llm_queue: "LLMRequestQueue | None" = None,
    ) -> None:
        """Initialize ExtractionWorker.

        Args:
            db: Database session.
            pipeline_service: Generic extraction pipeline service.
            settings: Application settings (required for schema extraction).
            llm_queue: Optional LLM queue for schema extraction.
        """
        self.db = db
        self.pipeline_service = pipeline_service
        self.settings = settings
        self.llm_queue = llm_queue
        self.job_repo = JobRepository(db)

    def _has_extraction_schema(self, project_id: UUID) -> tuple[bool, Project | None]:
        """Check if project has an extraction_schema defined.

        Returns:
            Tuple of (has_schema, project)
        """
        project = self.db.query(Project).filter(Project.id == project_id).first()
        if not project:
            return False, None

        # Check if extraction_schema exists and has field_groups
        schema = project.extraction_schema
        if schema and isinstance(schema, dict) and schema.get("field_groups"):
            return True, project
        return False, project

    def _create_schema_pipeline(self) -> SchemaExtractionPipeline:
        """Create a SchemaExtractionPipeline for schema-based extraction."""
        from services.extraction.schema_extractor import SchemaExtractor
        from services.extraction.schema_orchestrator import SchemaExtractionOrchestrator

        if not self.settings:
            raise ValueError("settings required for schema extraction")

        extractor = SchemaExtractor(self.settings, llm_queue=self.llm_queue)
        orchestrator = SchemaExtractionOrchestrator(extractor)
        return SchemaExtractionPipeline(orchestrator, self.db)

    async def _process_with_schema_pipeline(
        self,
        project_id: UUID,
        source_ids: list[UUID] | None,
        source_groups: list[str] | None = None,
        force: bool = False,
        cancellation_check=None,
    ) -> SchemaExtractionResult:
        """Process extraction using schema-based pipeline.

        Args:
            project_id: Project UUID.
            source_ids: Optional specific source IDs to process.
            source_groups: Optional filter by source groups.
            force: If True, re-extract already extracted sources.
            cancellation_check: Optional async callback that returns True if
                              processing should be cancelled.

        Returns:
            SchemaExtractionResult with extraction counts.
        """
        pipeline = self._create_schema_pipeline()

        # Schema pipeline processes sources for the project
        # skip_extracted=False when force=True to re-extract
        result = await pipeline.extract_project(
            project_id=project_id,
            source_ids=source_ids,
            source_groups=source_groups,
            skip_extracted=not force,
            cancellation_check=cancellation_check,
        )

        # Handle error case
        if "error" in result:
            return SchemaExtractionResult(
                sources_processed=0,
                sources_failed=1,
                total_extractions=0,
            )

        return SchemaExtractionResult(
            sources_processed=result.get("sources_processed", 0),
            sources_failed=result.get("sources_failed", 0),
            total_extractions=result.get("extractions_created", 0),
            total_deduplicated=0,
            total_entities=0,
        )

    async def process_job(self, job: Job) -> None:
        """Process an extraction job.

        Automatically selects the appropriate extraction strategy based on
        whether the project has an extraction_schema defined:
        - WITH schema: Uses SchemaExtractionPipeline (template-based)
        - WITHOUT schema: Uses ExtractionPipelineService (generic facts)

        Args:
            job: Job instance to process.

        Raises:
            None: All exceptions are caught and stored in job.error.
        """
        logger.info("extraction_job_started", job_id=str(job.id))
        try:
            # Check for cancellation before starting
            if self.job_repo.is_cancellation_requested(job.id):
                logger.info("extraction_job_cancelled_early", job_id=str(job.id))
                self.job_repo.mark_cancelled(job.id)
                self.db.commit()
                return

            # Update job status to running
            job.status = "running"
            job.started_at = datetime.now(UTC)
            self.db.commit()

            # Extract payload data
            payload = job.payload or {}
            project_id = payload.get("project_id")
            source_ids = payload.get("source_ids")
            profile_name = payload.get("profile", "general")
            force = payload.get("force", False)

            if not project_id:
                raise ValueError("project_id is required in job payload")

            # Convert project_id to UUID if string
            if isinstance(project_id, str):
                project_id = UUID(project_id)

            # Check if project has extraction schema
            has_schema, project = self._has_extraction_schema(project_id)

            # Create cancellation check callback for both pipelines
            async def check_cancellation() -> bool:
                return self.job_repo.is_cancellation_requested(job.id)

            if has_schema and self.settings:
                # Use schema-based extraction
                schema_name = project.extraction_schema.get("name", "unknown")
                logger.info(
                    "extraction_job_using_schema",
                    job_id=str(job.id),
                    project_id=str(project_id),
                    schema_name=schema_name,
                    source_count=len(source_ids) if source_ids else "all_pending",
                    force=force,
                )

                result = await self._process_with_schema_pipeline(
                    project_id=project_id,
                    source_ids=[UUID(sid) for sid in source_ids] if source_ids else None,
                    force=force,
                    cancellation_check=check_cancellation,
                )
            else:
                # Use generic fact extraction (original behavior)
                if has_schema and not self.settings:
                    logger.warning(
                        "extraction_job_schema_fallback",
                        job_id=str(job.id),
                        reason="settings not provided, falling back to generic extraction",
                    )

                logger.info(
                    "extraction_job_using_generic",
                    job_id=str(job.id),
                    project_id=str(project_id),
                    profile=profile_name,
                    source_count=len(source_ids) if source_ids else "all_pending",
                )

                # Process sources with generic pipeline
                if source_ids:
                    # Process specific sources
                    if isinstance(source_ids[0], str):
                        source_ids = [UUID(sid) for sid in source_ids]
                    result = await self.pipeline_service.process_batch(
                        source_ids=source_ids,
                        project_id=project_id,
                        profile_name=profile_name,
                        cancellation_check=check_cancellation,
                    )
                else:
                    # Process all pending sources for project
                    result = await self.pipeline_service.process_project_pending(
                        project_id=project_id,
                        profile_name=profile_name,
                        cancellation_check=check_cancellation,
                    )

            # Check if job was cancelled during processing
            if self.job_repo.is_cancellation_requested(job.id):
                logger.info(
                    "extraction_job_cancelled_during_processing",
                    job_id=str(job.id),
                    sources_processed=result.sources_processed,
                )
                self.job_repo.mark_cancelled(job.id)
                job.result = {
                    "sources_processed": result.sources_processed,
                    "sources_failed": result.sources_failed,
                    "total_extractions": result.total_extractions,
                    "total_deduplicated": result.total_deduplicated,
                    "total_entities": result.total_entities,
                    "cancelled": True,
                }
                self.db.commit()
                return

            # Update job with results
            if result.sources_failed > 0 and result.sources_processed == result.sources_failed:
                # All sources failed - mark job as failed
                job.status = "failed"
                job.error = f"All {result.sources_failed} sources failed to process"
                logger.error(
                    "extraction_job_failed",
                    job_id=str(job.id),
                    error=job.error,
                )
            else:
                job.status = "completed"
                logger.info(
                    "extraction_job_completed",
                    job_id=str(job.id),
                    sources_processed=result.sources_processed,
                    total_extractions=result.total_extractions,
                    total_entities=result.total_entities,
                )

            job.completed_at = datetime.now(UTC)
            job.result = {
                "sources_processed": result.sources_processed,
                "sources_failed": result.sources_failed,
                "total_extractions": result.total_extractions,
                "total_deduplicated": result.total_deduplicated,
                "total_entities": result.total_entities,
            }
            self.db.commit()

        except Exception as e:
            # Handle unexpected errors
            self.db.rollback()  # Rollback any partial changes
            job.status = "failed"
            job.error = f"{type(e).__name__}: {str(e)}"
            job.completed_at = datetime.now(UTC)
            self.db.commit()
            logger.error(
                "extraction_job_error",
                job_id=str(job.id),
                error=str(e),
                error_type=type(e).__name__,
                exc_info=True,
            )
