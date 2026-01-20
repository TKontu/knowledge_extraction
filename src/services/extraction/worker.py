"""Background worker for processing extraction jobs."""

from datetime import UTC, datetime
from uuid import UUID

import structlog
from sqlalchemy.orm import Session

from orm_models import Job
from services.extraction.pipeline import ExtractionPipelineService

logger = structlog.get_logger(__name__)


class ExtractionWorker:
    """Background worker for processing extraction jobs.

    Handles queued extraction jobs by:
    1. Updating job status to "running"
    2. Processing pending sources via ExtractionPipelineService
    3. Updating job with results and completion status

    Args:
        db: Database session for persistence.
        pipeline_service: Pipeline service for extraction orchestration.

    Example:
        pipeline = ExtractionPipelineService(...)
        worker = ExtractionWorker(db=db, pipeline_service=pipeline)
        await worker.process_job(job)
    """

    def __init__(
        self,
        db: Session,
        pipeline_service: ExtractionPipelineService,
    ) -> None:
        """Initialize ExtractionWorker.

        Args:
            db: Database session.
            pipeline_service: Extraction pipeline service.
        """
        self.db = db
        self.pipeline_service = pipeline_service

    async def process_job(self, job: Job) -> None:
        """Process an extraction job.

        Updates job status, processes sources, and marks job complete.

        Args:
            job: Job instance to process.

        Raises:
            None: All exceptions are caught and stored in job.error.
        """
        logger.info("extraction_job_started", job_id=str(job.id))
        try:
            # Update job status to running
            job.status = "running"
            job.started_at = datetime.now(UTC)
            self.db.commit()

            # Extract payload data
            payload = job.payload or {}
            project_id = payload.get("project_id")
            source_ids = payload.get("source_ids")
            profile_name = payload.get("profile", "general")

            if not project_id:
                raise ValueError("project_id is required in job payload")

            # Convert project_id to UUID if string
            if isinstance(project_id, str):
                project_id = UUID(project_id)

            logger.info(
                "extraction_job_processing",
                job_id=str(job.id),
                project_id=str(project_id),
                profile=profile_name,
                source_count=len(source_ids) if source_ids else "all_pending",
            )

            # Process sources
            if source_ids:
                # Process specific sources
                if isinstance(source_ids[0], str):
                    source_ids = [UUID(sid) for sid in source_ids]
                result = await self.pipeline_service.process_batch(
                    source_ids=source_ids,
                    project_id=project_id,
                    profile_name=profile_name,
                )
            else:
                # Process all pending sources for project
                result = await self.pipeline_service.process_project_pending(
                    project_id=project_id,
                    profile_name=profile_name,
                )

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
            job.error = str(e)
            job.completed_at = datetime.now(UTC)
            self.db.commit()
            logger.error(
                "extraction_job_error",
                job_id=str(job.id),
                error=str(e),
                exc_info=True,
            )
