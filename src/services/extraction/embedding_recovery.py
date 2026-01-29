"""Service for recovering orphaned extractions by retrying embeddings."""

from dataclasses import dataclass, field
from uuid import UUID

import structlog
from sqlalchemy.orm import Session

from orm_models import Extraction
from services.alerting import get_alert_service
from services.storage.embedding import EmbeddingService
from services.storage.qdrant.repository import EmbeddingItem, QdrantRepository
from services.storage.repositories.extraction import ExtractionRepository

logger = structlog.get_logger(__name__)


@dataclass
class RecoveryResult:
    """Result from recovering a batch of extractions."""

    succeeded: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)


@dataclass
class RecoverySummary:
    """Summary of full recovery process."""

    total_found: int = 0
    total_recovered: int = 0
    total_failed: int = 0
    batches_processed: int = 0
    errors: list[str] = field(default_factory=list)


class EmbeddingRecoveryService:
    """Recovers orphaned extractions by retrying embedding generation."""

    def __init__(
        self,
        db: Session,
        embedding_service: EmbeddingService,
        qdrant_repo: QdrantRepository,
        extraction_repo: ExtractionRepository,
        batch_size: int = 50,
    ):
        """Initialize recovery service.

        Args:
            db: Database session.
            embedding_service: Service for generating embeddings.
            qdrant_repo: Repository for Qdrant operations.
            extraction_repo: Repository for extraction operations.
            batch_size: Number of extractions to process per batch.
        """
        self._db = db
        self._embedding_service = embedding_service
        self._qdrant_repo = qdrant_repo
        self._extraction_repo = extraction_repo
        self._batch_size = batch_size

    def find_orphaned_extractions(
        self,
        project_id: UUID | None = None,
        limit: int = 100,
    ) -> list[Extraction]:
        """Find extractions with embedding_id IS NULL.

        Args:
            project_id: Optional project UUID to filter by.
            limit: Maximum number of results to return.

        Returns:
            List of orphaned Extraction instances.
        """
        return self._extraction_repo.find_orphaned(
            project_id=project_id,
            limit=limit,
        )

    async def recover_batch(
        self,
        extractions: list[Extraction],
    ) -> RecoveryResult:
        """Retry embedding for a batch of extractions.

        Args:
            extractions: List of orphaned extractions to recover.

        Returns:
            RecoveryResult with success/failure counts.
        """
        if not extractions:
            return RecoveryResult()

        result = RecoveryResult()

        try:
            # Extract fact texts for embedding
            fact_texts = [
                extraction.data.get("fact_text", "")
                for extraction in extractions
            ]

            # Generate embeddings
            embeddings = await self._embedding_service.embed_batch(fact_texts)

            # Prepare items for Qdrant
            items = [
                EmbeddingItem(
                    extraction_id=extraction.id,
                    embedding=embedding,
                    payload={
                        "project_id": str(extraction.project_id),
                        "source_group": extraction.source_group,
                        "extraction_type": extraction.extraction_type,
                    },
                )
                for extraction, embedding in zip(extractions, embeddings, strict=True)
            ]

            # Upsert to Qdrant
            await self._qdrant_repo.upsert_batch(items)

            # Update embedding_id in database
            extraction_ids = [extraction.id for extraction in extractions]
            self._extraction_repo.update_embedding_ids_batch(extraction_ids)

            result.succeeded = len(extractions)

            logger.info(
                "embedding_recovery_batch_succeeded",
                batch_size=len(extractions),
                extraction_ids=[str(e.id) for e in extractions],
            )

        except Exception as e:
            result.failed = len(extractions)
            error_msg = f"Batch recovery failed: {str(e)}"
            result.errors.append(error_msg)

            logger.error(
                "embedding_recovery_batch_failed",
                error=str(e),
                batch_size=len(extractions),
                extraction_ids=[str(e.id) for e in extractions],
            )

        return result

    async def run_recovery(
        self,
        project_id: UUID | None = None,
        max_batches: int = 10,
    ) -> RecoverySummary:
        """Run full recovery process.

        Args:
            project_id: Optional project UUID to filter by.
            max_batches: Maximum number of batches to process.

        Returns:
            RecoverySummary with overall statistics.
        """
        summary = RecoverySummary()

        for batch_num in range(max_batches):
            # Find next batch of orphaned extractions
            orphaned = self.find_orphaned_extractions(
                project_id=project_id,
                limit=self._batch_size,
            )

            if not orphaned:
                # No more orphans to process
                logger.info(
                    "embedding_recovery_complete",
                    batches_processed=summary.batches_processed,
                    total_recovered=summary.total_recovered,
                )
                break

            summary.total_found += len(orphaned)

            # Recover batch
            batch_result = await self.recover_batch(orphaned)
            summary.total_recovered += batch_result.succeeded
            summary.total_failed += batch_result.failed
            summary.errors.extend(batch_result.errors)
            summary.batches_processed += 1

            logger.info(
                "embedding_recovery_batch_complete",
                batch_num=batch_num + 1,
                found=len(orphaned),
                succeeded=batch_result.succeeded,
                failed=batch_result.failed,
            )

        # Send recovery completion alert
        if summary.total_recovered > 0 or summary.total_failed > 0:
            try:
                alert_service = get_alert_service()
                await alert_service.alert_recovery_completed(
                    recovered=summary.total_recovered,
                    failed=summary.total_failed,
                    project_id=project_id,  # None for global recovery is valid
                )
            except Exception as alert_err:
                logger.warning(
                    "recovery_alert_failed",
                    error=str(alert_err),
                )

        return summary
