"""Service for cleaning up job artifacts."""

from uuid import UUID

import structlog
from sqlalchemy import select
from sqlalchemy.orm import Session

from models import JobCleanupStats
from orm_models import Extraction, Source
from services.dlq.service import DLQService
from services.storage.qdrant.repository import QdrantRepository

logger = structlog.get_logger(__name__)


class JobCleanupService:
    """Service for cleaning up artifacts created by a job.

    Handles deletion of:
    - Qdrant embeddings (by extraction_id)
    - Extractions (cascaded from source deletion)
    - Entities (cascaded from source deletion)
    - Sources (where created_by_job_id = job_id)
    - DLQ items (by job_id)
    """

    def __init__(
        self,
        db: Session,
        qdrant_repo: QdrantRepository,
        dlq_service: DLQService,
    ) -> None:
        """Initialize JobCleanupService.

        Args:
            db: Database session for persistence operations.
            qdrant_repo: Qdrant repository for embedding deletions.
            dlq_service: DLQ service for Redis cleanup.
        """
        self._db = db
        self._qdrant = qdrant_repo
        self._dlq = dlq_service

    async def cleanup_job_artifacts(self, job_id: UUID) -> JobCleanupStats:
        """Delete all artifacts created by a job.

        Cleanup order (respects FK constraints):
        1. Delete Qdrant embeddings (by extraction_id)
        2. Delete sources (cascades to extractions, entities via FK)
        3. Delete DLQ items (Redis, by job_id)

        Args:
            job_id: UUID of the job whose artifacts should be deleted.

        Returns:
            JobCleanupStats with counts of deleted items.
        """
        logger.info("job_cleanup_started", job_id=str(job_id))

        # Step 1: Get sources created by this job
        sources = self._db.execute(
            select(Source).where(Source.created_by_job_id == job_id)
        ).scalars().all()

        source_ids = [s.id for s in sources]
        sources_count = len(source_ids)

        logger.debug(
            "job_cleanup_sources_found",
            job_id=str(job_id),
            sources_count=sources_count,
        )

        # Step 2: Get extractions for these sources (need IDs for embedding cleanup)
        extractions = []
        if source_ids:
            extractions = self._db.execute(
                select(Extraction).where(Extraction.source_id.in_(source_ids))
            ).scalars().all()

        extraction_ids = [e.id for e in extractions]
        extractions_count = len(extraction_ids)

        # Count extractions with embedding_id set (for logging)
        extraction_ids_with_embedding_id = [
            e.id for e in extractions if e.embedding_id
        ]

        logger.debug(
            "job_cleanup_extractions_found",
            job_id=str(job_id),
            extractions_count=extractions_count,
            with_embedding_id=len(extraction_ids_with_embedding_id),
        )

        # Step 3: Delete Qdrant embeddings
        # Use ALL extraction IDs since Qdrant uses extraction.id as point ID.
        # This handles both:
        # - New extractions (embedding_id is set)
        # - Historical extractions (embedding_id may be NULL but Qdrant point exists)
        # Qdrant delete is idempotent - no error if point doesn't exist.
        embeddings_deleted = 0
        if extraction_ids:
            embeddings_deleted = await self._qdrant.delete_batch(extraction_ids)
            logger.debug(
                "job_cleanup_embeddings_deleted",
                job_id=str(job_id),
                embeddings_deleted=embeddings_deleted,
            )

        # Step 4: Delete sources (cascades to extractions, entities)
        sources_deleted = 0
        if source_ids:
            from sqlalchemy import delete

            result = self._db.execute(
                delete(Source).where(Source.created_by_job_id == job_id)
            )
            sources_deleted = result.rowcount
            self._db.flush()

            logger.debug(
                "job_cleanup_sources_deleted",
                job_id=str(job_id),
                sources_deleted=sources_deleted,
            )

        # Step 5: Delete DLQ items
        dlq_deleted = await self._dlq.remove_by_job_id(str(job_id))
        if dlq_deleted > 0:
            logger.debug(
                "job_cleanup_dlq_deleted",
                job_id=str(job_id),
                dlq_deleted=dlq_deleted,
            )

        stats = JobCleanupStats(
            sources_deleted=sources_deleted,
            extractions_deleted=extractions_count,  # Cascaded from sources
            embeddings_deleted=embeddings_deleted,
            dlq_items_deleted=dlq_deleted,
        )

        logger.info(
            "job_cleanup_completed",
            job_id=str(job_id),
            sources_deleted=stats.sources_deleted,
            extractions_deleted=stats.extractions_deleted,
            embeddings_deleted=stats.embeddings_deleted,
            dlq_items_deleted=stats.dlq_items_deleted,
        )

        return stats
