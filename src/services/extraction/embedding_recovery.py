"""Embedding Recovery Service for orphaned extractions."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

import structlog
from sqlalchemy.orm import Session

from orm_models import Extraction
from services.storage.embedding import EmbeddingService
from services.storage.qdrant.repository import QdrantRepository
from services.storage.repositories.extraction import ExtractionRepository

logger = structlog.get_logger(__name__)


@dataclass
class RecoveryResult:
    """Result of a single batch recovery operation."""

    succeeded: int
    failed: int
    extraction_ids: list[UUID]


@dataclass
class RecoverySummary:
    """Summary of full recovery run."""

    total_processed: int
    total_succeeded: int
    total_failed: int
    batches_processed: int


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
        """Initialize embedding recovery service.

        Args:
            db: Database session
            embedding_service: Service for generating embeddings
            qdrant_repo: Repository for Qdrant operations
            extraction_repo: Repository for extraction operations
            batch_size: Number of extractions to process per batch
        """
        self._db = db
        self._embedding_service = embedding_service
        self._qdrant_repo = qdrant_repo
        self._extraction_repo = extraction_repo
        self._batch_size = batch_size

    async def find_orphaned_extractions(
        self,
        project_id: UUID | None = None,
        limit: int = 100,
    ) -> list[Extraction]:
        """Find extractions with embedding_id IS NULL.

        Args:
            project_id: Optional project UUID to filter by
            limit: Maximum number of results to return

        Returns:
            List of orphaned Extraction instances
        """
        return await self._extraction_repo.find_orphaned(
            project_id=project_id, limit=limit
        )

    async def recover_batch(
        self,
        extractions: list[Extraction],
    ) -> RecoveryResult:
        """Retry embedding for a batch of extractions.

        Args:
            extractions: List of extraction instances to recover

        Returns:
            RecoveryResult with success/failure counts
        """
        if not extractions:
            return RecoveryResult(succeeded=0, failed=0, extraction_ids=[])

        succeeded = 0
        failed = 0
        successful_ids = []
        points = []

        # Extract fact texts and build points data
        texts_to_embed = []
        valid_extractions = []

        for extraction in extractions:
            try:
                fact_text = extraction.data.get("fact_text")
                if not fact_text:
                    logger.warning(
                        "extraction_missing_fact_text",
                        extraction_id=str(extraction.id),
                    )
                    failed += 1
                    continue

                texts_to_embed.append(fact_text)
                valid_extractions.append(extraction)
            except Exception as e:
                logger.error(
                    "extraction_validation_error",
                    extraction_id=str(extraction.id),
                    error=str(e),
                )
                failed += 1

        if not texts_to_embed:
            logger.warning("no_valid_extractions_to_recover")
            return RecoveryResult(succeeded=0, failed=failed, extraction_ids=[])

        try:
            # Generate embeddings in batch
            embeddings = await self._embedding_service.embed_batch(texts_to_embed)

            # Build Qdrant points
            for extraction, embedding in zip(valid_extractions, embeddings, strict=True):
                point_id = str(extraction.id)
                points.append(
                    {
                        "id": point_id,
                        "vector": embedding,
                        "payload": {
                            "extraction_id": str(extraction.id),
                            "project_id": str(extraction.project_id),
                            "source_group": extraction.source_group,
                            "extraction_type": extraction.extraction_type,
                            "fact_text": extraction.data.get("fact_text", ""),
                        },
                    }
                )

            # Upsert to Qdrant in batch
            await self._qdrant_repo.upsert_batch(points)

            # Update embedding_ids in database
            extraction_ids = [e.id for e in valid_extractions]
            updated_count = await self._extraction_repo.update_embedding_ids_batch(
                extraction_ids
            )

            succeeded = updated_count
            successful_ids = extraction_ids

            logger.info(
                "batch_recovery_completed",
                succeeded=succeeded,
                failed=failed,
            )

        except Exception as e:
            logger.error(
                "batch_recovery_failed",
                error=str(e),
                extraction_count=len(valid_extractions),
            )
            failed += len(valid_extractions)

        return RecoveryResult(
            succeeded=succeeded,
            failed=failed,
            extraction_ids=successful_ids,
        )

    async def run_recovery(
        self,
        project_id: UUID | None = None,
        max_batches: int = 10,
    ) -> RecoverySummary:
        """Run full recovery process.

        Args:
            project_id: Optional project UUID to filter by
            max_batches: Maximum number of batches to process

        Returns:
            RecoverySummary with overall statistics
        """
        total_succeeded = 0
        total_failed = 0
        batches_processed = 0

        logger.info(
            "recovery_started",
            project_id=str(project_id) if project_id else None,
            max_batches=max_batches,
        )

        for batch_num in range(max_batches):
            # Find orphaned extractions
            orphans = await self.find_orphaned_extractions(
                project_id=project_id, limit=self._batch_size
            )

            if not orphans:
                logger.info("no_more_orphans_found", batch_num=batch_num)
                break

            # Recover batch
            result = await self.recover_batch(orphans)

            total_succeeded += result.succeeded
            total_failed += result.failed
            batches_processed += 1

            logger.info(
                "batch_recovered",
                batch_num=batch_num,
                succeeded=result.succeeded,
                failed=result.failed,
            )

        summary = RecoverySummary(
            total_processed=total_succeeded + total_failed,
            total_succeeded=total_succeeded,
            total_failed=total_failed,
            batches_processed=batches_processed,
        )

        logger.info(
            "recovery_completed",
            total_processed=summary.total_processed,
            total_succeeded=summary.total_succeeded,
            total_failed=summary.total_failed,
            batches_processed=summary.batches_processed,
        )

        return summary
