"""Background worker for processing consolidation jobs."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING
from uuid import UUID

import structlog
from sqlalchemy.orm import Session

from constants import JobStatus
from orm_models import Job
from services.extraction.consolidation_service import ConsolidationService
from services.projects.repository import ProjectRepository
from services.storage.repositories.job import JobRepository

if TYPE_CHECKING:
    from config import LLMConfig

logger = structlog.get_logger(__name__)


class ConsolidationWorker:
    """Background worker for processing consolidation jobs.

    Handles queued consolidation jobs by:
    1. Updating job status to "running"
    2. Running ConsolidationService with optional LLM synthesis
    3. Updating job with results and completion status

    Args:
        db: Database session for persistence.
        llm_config: LLM configuration (required for llm_summarize fields).
    """

    def __init__(
        self,
        db: Session,
        *,
        llm_config: LLMConfig | None = None,
    ) -> None:
        self.db = db
        self._llm_config = llm_config
        self.job_repo = JobRepository(db)

    async def process_job(self, job: Job) -> None:
        """Process a single consolidation job.

        Args:
            job: Job instance with type="consolidate" and payload containing
                project_id, optional source_group, and use_llm flag.
        """
        job.status = JobStatus.RUNNING
        job.started_at = datetime.now(UTC)
        self.db.commit()

        try:
            payload = job.payload or {}
            project_id = UUID(payload["project_id"])
            source_group = payload.get("source_group")
            use_llm = payload.get("use_llm", False)

            repo = ProjectRepository(self.db)
            service = ConsolidationService(self.db, repo)

            llm_client = None
            if use_llm and self._llm_config:
                from services.llm.client import LLMClient

                llm_client = LLMClient(self._llm_config)
                await llm_client.__aenter__()

            try:
                if source_group:
                    records = await service.consolidate_source_group(
                        project_id, source_group, llm_client=llm_client,
                    )
                    self.db.commit()
                    result = {
                        "source_groups": 1,
                        "records_created": len(records),
                        "errors": 0,
                    }
                else:
                    result = await service.consolidate_project(
                        project_id, llm_client=llm_client,
                    )
                    self.db.commit()
            finally:
                if llm_client is not None:
                    await llm_client.__aexit__(None, None, None)

            job.status = JobStatus.COMPLETED
            job.result = result
            job.completed_at = datetime.now(UTC)
            self.db.commit()

            logger.info(
                "consolidation_job_completed",
                job_id=str(job.id),
                project_id=str(project_id),
                result=result,
            )

        except Exception as e:
            self.db.rollback()
            job.status = JobStatus.FAILED
            job.error = str(e)
            job.completed_at = datetime.now(UTC)
            self.db.commit()

            logger.error(
                "consolidation_job_failed",
                job_id=str(job.id),
                error=str(e),
                exc_info=True,
            )
