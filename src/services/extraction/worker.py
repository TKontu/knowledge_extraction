"""Background worker for processing extraction jobs."""

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from sqlalchemy.orm import Session

from constants import JobStatus
from orm_models import Job, Project
from services.extraction.pipeline import (
    CheckpointCallback,
    SchemaExtractionPipeline,
    SchemaPipelineResult,
)
from services.storage.repositories.job import JobRepository

if TYPE_CHECKING:
    from config import ClassificationConfig, ExtractionConfig, LLMConfig
    from services.extraction.embedding_pipeline import ExtractionEmbeddingService
    from services.llm.queue import LLMRequestQueue
    from services.storage.embedding import EmbeddingService

logger = structlog.get_logger(__name__)


class ExtractionWorker:
    """Background worker for processing extraction jobs.

    Handles queued extraction jobs by:
    1. Updating job status to "running"
    2. Using SchemaExtractionPipeline for template-based field group extraction
    3. Updating job with results and completion status

    Args:
        db: Database session for persistence.
        llm: LLM configuration (required for schema extraction).
        extraction: Extraction configuration facade.
        classification: Classification configuration facade.
        embedding_service: Shared embedding service (from ServiceContainer).
        extraction_embedding: Shared extraction embedding service (from ServiceContainer).
        request_timeout: Timeout in seconds for queued LLM requests.
        llm_queue: Optional LLM request queue for schema extraction.

    Example:
        worker = ExtractionWorker(
            db=db,
            llm=settings.llm,
            extraction=settings.extraction,
            classification=settings.classification,
            embedding_service=container.embedding_service,
            extraction_embedding=container.extraction_embedding,
            llm_queue=llm_queue,
        )
        await worker.process_job(job)
    """

    def __init__(
        self,
        db: Session,
        *,
        llm: "LLMConfig | None" = None,
        extraction: "ExtractionConfig | None" = None,
        classification: "ClassificationConfig | None" = None,
        embedding_service: "EmbeddingService | None" = None,
        extraction_embedding: "ExtractionEmbeddingService | None" = None,
        request_timeout: int = 300,
        llm_queue: "LLMRequestQueue | None" = None,
    ) -> None:
        """Initialize ExtractionWorker.

        Args:
            db: Database session.
            llm: LLM configuration (required for schema extraction).
            extraction: Extraction configuration facade.
            classification: Classification configuration facade.
            embedding_service: Shared embedding service (from ServiceContainer).
            extraction_embedding: Shared extraction embedding service (from ServiceContainer).
            request_timeout: Timeout in seconds for queued LLM requests.
            llm_queue: Optional LLM queue for schema extraction.
        """
        self.db = db
        self._llm = llm
        self._extraction = extraction
        self._classification = classification
        self._embedding_service = embedding_service
        self._extraction_embedding = extraction_embedding
        self._request_timeout = request_timeout
        self.llm_queue = llm_queue
        self.job_repo = JobRepository(db)

    def _create_checkpoint_callback(self, job: Job) -> CheckpointCallback:
        """Create a checkpoint callback that saves progress to job.payload.

        Args:
            job: Job instance to update with checkpoint data.

        Returns:
            Callback function that persists checkpoint state.
        """

        def callback(processed_ids: list[str], extractions: int, entities: int) -> None:
            checkpoint = {
                "processed_source_ids": processed_ids,
                "last_checkpoint_at": datetime.now(UTC).isoformat(),
                "total_extractions": extractions,
                "total_entities": entities,
            }
            payload = job.payload or {}
            payload["checkpoint"] = checkpoint
            job.payload = payload
            # Note: No commit here - pipeline.extract_project already commits
            # after each chunk, which includes this payload update
            logger.debug(
                "checkpoint_saved",
                job_id=str(job.id),
                processed_count=len(processed_ids),
                extractions=extractions,
            )

        return callback

    def _get_resume_state(self, job: Job) -> set[str] | None:
        """Get the set of already-processed source IDs from job checkpoint.

        Args:
            job: Job instance to check for checkpoint data.

        Returns:
            Set of source ID strings if checkpoint exists, None otherwise.
        """
        if not job.payload:
            return None
        checkpoint = job.payload.get("checkpoint")
        if not checkpoint:
            return None
        processed = checkpoint.get("processed_source_ids", [])
        if not processed:
            return None
        logger.info(
            "resuming_from_checkpoint",
            job_id=str(job.id),
            already_processed=len(processed),
            last_checkpoint=checkpoint.get("last_checkpoint_at"),
        )
        return set(processed)

    async def _create_schema_pipeline(
        self, project: Project | None = None
    ) -> SchemaExtractionPipeline:
        """Create a SchemaExtractionPipeline for schema-based extraction.

        Uses shared embedding services injected at construction time
        (from ServiceContainer) rather than creating per-job instances.

        Args:
            project: Optional project to extract classification_config from.
        """
        from redis_client import get_async_redis
        from services.extraction.schema_adapter import (
            ClassificationConfig,
        )
        from services.extraction.schema_extractor import SchemaExtractor
        from services.extraction.schema_orchestrator import SchemaExtractionOrchestrator
        from services.extraction.smart_classifier import SmartClassifier

        if not self._llm:
            raise ValueError("llm config required for schema extraction")

        extractor = SchemaExtractor(
            self._llm,
            llm_queue=self.llm_queue,
            content_limit=self._extraction.content_limit if self._extraction else 20000,
            source_quoting=self._extraction.source_quoting_enabled
            if self._extraction
            else True,
            request_timeout=self._request_timeout,
        )

        # Extract classification_config from project's extraction_schema
        classification_config = None
        if project and project.extraction_schema:
            classification_config = ClassificationConfig.from_dict(
                project.extraction_schema.get("classification_config")
            )
            # Validate and log warnings for invalid patterns
            if classification_config:
                is_valid, config_errors = classification_config.validate()
                if not is_valid:
                    logger.warning(
                        "invalid_classification_config",
                        project_id=str(project.id),
                        errors=config_errors,
                    )
                    # Reset to None to use defaults instead of failing
                    classification_config = None

        # Create smart classifier if enabled (reuses shared embedding service)
        smart_classifier = None
        if (
            self._classification
            and self._classification.smart_enabled
            and self._embedding_service
        ):
            async_redis = await get_async_redis()
            smart_classifier = SmartClassifier(
                embedding_service=self._embedding_service,
                redis_client=async_redis,
                app_config=self._classification,
                classification_config=classification_config,
                embedding_model_name=self._embedding_service.model,
            )

        # Create grounding verifier for LLM rescue of borderline fields
        grounding_verifier = None
        if self._llm:
            from services.extraction.llm_grounding import LLMGroundingVerifier
            from services.llm.client import LLMClient

            llm_client = LLMClient(
                self._llm,
                llm_queue=self.llm_queue,
                request_timeout=self._request_timeout,
            )
            grounding_verifier = LLMGroundingVerifier(llm_client=llm_client)

        # Create LLM skip-gate if enabled
        skip_gate = None
        if (
            self._classification
            and self._classification.skip_gate_enabled
            and self._llm
        ):
            from dataclasses import replace as dc_replace

            from services.extraction.llm_skip_gate import LLMSkipGate
            from services.llm.client import LLMClient as LLMClientCls

            gate_llm = self._llm
            if self._classification.skip_gate_model:
                gate_llm = dc_replace(
                    self._llm,
                    model=self._classification.skip_gate_model,
                )

            gate_client = LLMClientCls(
                gate_llm,
                llm_queue=self.llm_queue,
                request_timeout=self._request_timeout,
            )
            skip_gate = LLMSkipGate(
                llm_client=gate_client,
                content_limit=self._classification.skip_gate_content_limit,
            )

        # Extract context (entity_id_fields, source_label, etc.) from schema
        extraction_context = None
        if project and project.extraction_schema:
            from services.extraction.schema_adapter import ExtractionContext

            extraction_context = ExtractionContext.from_dict(
                project.extraction_schema.get("extraction_context")
            )

        orchestrator = SchemaExtractionOrchestrator(
            extractor,
            extraction_config=self._extraction,
            classification_config=self._classification,
            context=extraction_context,
            smart_classifier=smart_classifier,
            grounding_verifier=grounding_verifier,
            skip_gate=skip_gate,
            extraction_schema=project.extraction_schema if project else None,
        )

        # Use shared extraction embedding service if schema embedding is enabled
        extraction_embedding = None
        if self._extraction and self._extraction.schema_embedding_enabled:
            extraction_embedding = self._extraction_embedding

        return SchemaExtractionPipeline(
            orchestrator,
            self.db,
            extraction_embedding=extraction_embedding,
            extraction_config=self._extraction,
        )

    async def _process_with_schema_pipeline(
        self,
        project_id: UUID,
        source_ids: list[UUID] | None,
        source_groups: list[str] | None = None,
        force: bool = False,
        field_groups_filter: list[str] | None = None,
        cancellation_check=None,
        project: Project | None = None,
        job: Job | None = None,
    ) -> SchemaPipelineResult:
        """Process extraction using schema-based pipeline.

        Args:
            project_id: Project UUID.
            source_ids: Optional specific source IDs to process.
            source_groups: Optional filter by source groups.
            force: If True, re-extract already extracted sources.
            cancellation_check: Optional async callback that returns True if
                              processing should be cancelled.
            project: Optional project for classification_config extraction.
            job: Optional job for checkpoint callback and resume support.

        Returns:
            SchemaPipelineResult with extraction counts.
        """
        pipeline = await self._create_schema_pipeline(project=project)

        # Get checkpoint callback and resume state if job provided
        checkpoint_callback = None
        resume_from = None
        if job:
            checkpoint_callback = self._create_checkpoint_callback(job)
            resume_from = self._get_resume_state(job)

        # Schema pipeline processes sources for the project
        # skip_extracted=False when force=True to re-extract
        return await pipeline.extract_project(
            project_id=project_id,
            source_ids=source_ids,
            source_groups=source_groups,
            skip_extracted=not force,
            field_groups_filter=field_groups_filter,
            cancellation_check=cancellation_check,
            checkpoint_callback=checkpoint_callback,
            resume_from=resume_from,
        )

    async def process_job(self, job: Job) -> None:
        """Process an extraction job using SchemaExtractionPipeline.

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

            if not self._llm:
                raise ValueError("LLM config required for extraction")

            # Update job status to running
            job.status = JobStatus.RUNNING
            job.started_at = datetime.now(UTC)
            self.db.commit()

            # Extract payload data
            payload = job.payload or {}
            project_id = payload.get("project_id")
            source_ids = payload.get("source_ids")
            force = payload.get("force", False)
            source_groups = payload.get("source_groups")
            field_groups_filter = payload.get("field_groups")

            if not project_id:
                raise ValueError("project_id is required in job payload")

            # Convert project_id to UUID if string
            if isinstance(project_id, str):
                project_id = UUID(project_id)

            # Load project for classification_config
            project = self.db.query(Project).filter(Project.id == project_id).first()

            # Create cancellation check callback with time-throttle (skip DB check if <5s since last)
            _last_cancel_check = 0.0
            _last_cancel_result = False

            async def check_cancellation() -> bool:
                nonlocal _last_cancel_check, _last_cancel_result
                import time

                now = time.monotonic()
                if now - _last_cancel_check < 5.0:
                    return _last_cancel_result
                _last_cancel_check = now
                _last_cancel_result = self.job_repo.is_cancellation_requested(job.id)
                return _last_cancel_result

            logger.info(
                "extraction_job_using_schema",
                job_id=str(job.id),
                project_id=str(project_id),
                source_count=len(source_ids) if source_ids else "all_pending",
                force=force,
            )

            result = await self._process_with_schema_pipeline(
                project_id=project_id,
                source_ids=[UUID(sid) for sid in source_ids] if source_ids else None,
                source_groups=source_groups,
                force=force,
                field_groups_filter=field_groups_filter,
                cancellation_check=check_cancellation,
                project=project,
                job=job,
            )

            # Handle schema pipeline error results (e.g. invalid schema, project not found)
            if isinstance(result, SchemaPipelineResult) and result.error:
                job.status = JobStatus.FAILED
                job.error = result.error
                job.completed_at = datetime.now(UTC)
                self.db.commit()
                logger.error(
                    "extraction_job_pipeline_error",
                    job_id=str(job.id),
                    error=result.error,
                )
                return

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
            if (
                result.sources_failed > 0
                and result.sources_processed == result.sources_failed
            ):
                # All sources failed - mark job as failed
                job.status = JobStatus.FAILED
                job.error = f"All {result.sources_failed} sources failed to process"
                logger.error(
                    "extraction_job_failed",
                    job_id=str(job.id),
                    error=job.error,
                )
            else:
                job.status = JobStatus.COMPLETED
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
            job.status = JobStatus.FAILED
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
