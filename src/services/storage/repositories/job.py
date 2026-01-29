"""Repository for Job CRUD operations."""

from __future__ import annotations

from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from orm_models import Job


class JobRepository:
    """Repository for managing Job entities."""

    def __init__(self, session: Session):
        """Initialize repository with database session.

        Args:
            session: SQLAlchemy session for database operations
        """
        self._session = session

    async def get(self, job_id: UUID) -> Job | None:
        """Get job by ID.

        Args:
            job_id: Job UUID

        Returns:
            Job instance or None if not found
        """
        result = self._session.execute(select(Job).where(Job.id == job_id))
        return result.scalar_one_or_none()

    async def request_cancellation(self, job_id: UUID) -> Job | None:
        """Request cancellation of a job.

        Sets the job status to 'cancelling' if the job is in a cancellable state
        (queued or running). Workers should check for this status at checkpoints.

        Args:
            job_id: Job UUID

        Returns:
            Updated Job instance or None if not found or not cancellable
        """
        job = await self.get(job_id)
        if not job:
            return None

        # Only queued or running jobs can be cancelled
        if job.status not in ("queued", "running"):
            return None

        job.status = "cancelling"
        job.cancellation_requested_at = datetime.now(UTC)
        self._session.flush()
        return job

    async def mark_cancelled(self, job_id: UUID) -> Job | None:
        """Mark a job as fully cancelled.

        Called by workers when they detect cancellation and have stopped processing.

        Args:
            job_id: Job UUID

        Returns:
            Updated Job instance or None if not found
        """
        job = await self.get(job_id)
        if not job:
            return None

        job.status = "cancelled"
        job.completed_at = datetime.now(UTC)
        self._session.flush()
        return job

    async def delete(self, job_id: UUID) -> bool:
        """Delete a job record.

        Args:
            job_id: Job UUID

        Returns:
            True if deleted, False if not found
        """
        job = await self.get(job_id)
        if not job:
            return False

        self._session.delete(job)
        self._session.flush()
        return True

    async def is_cancellation_requested(self, job_id: UUID) -> bool:
        """Check if cancellation has been requested for a job.

        Workers should call this at key checkpoints to determine if they should
        stop processing.

        Args:
            job_id: Job UUID

        Returns:
            True if job status is 'cancelling', False otherwise
        """
        job = await self.get(job_id)
        return job is not None and job.status == "cancelling"
