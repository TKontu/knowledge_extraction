"""Unified embedding service for extraction pipelines.

Handles the "embed extractions -> upsert to Qdrant" flow,
eliminating duplication between generic and schema pipelines.
"""

from dataclasses import dataclass

import structlog

from services.storage.embedding import EmbeddingService
from services.storage.qdrant.repository import EmbeddingItem, QdrantRepository

logger = structlog.get_logger(__name__)

# Max items per list value when building embeddable text
EMBEDDING_MAX_LIST_ITEMS = 50


@dataclass
class EmbeddingResult:
    """Result from an embed-and-upsert operation."""

    embedded_count: int
    errors: list[str]
    failed_count: int = 0


class ExtractionEmbeddingService:
    """Embeds extractions and upserts to Qdrant.

    Args:
        embedding_service: Service for generating embeddings.
        qdrant_repo: Repository for vector storage.
    """

    def __init__(
        self,
        embedding_service: EmbeddingService,
        qdrant_repo: QdrantRepository,
    ):
        self._embedding_service = embedding_service
        self._qdrant_repo = qdrant_repo

    @staticmethod
    def extraction_to_text(extraction) -> str:
        """Convert extraction ORM object to embeddable text.

        Args:
            extraction: Extraction ORM object with extraction_type and data.

        Returns:
            Text representation for embedding.
        """
        parts = []
        if extraction.extraction_type:
            parts.append(f"Type: {extraction.extraction_type}")
        if extraction.data:
            for key, value in extraction.data.items():
                if key.startswith("_") or key == "confidence":
                    continue
                if value is not None:
                    if isinstance(value, list):
                        for item in value[:EMBEDDING_MAX_LIST_ITEMS]:
                            if isinstance(item, dict):
                                item_parts = [
                                    f"{k}: {v}"
                                    for k, v in item.items()
                                    if not str(k).startswith("_") and v is not None
                                ]
                                parts.append("; ".join(item_parts))
                            else:
                                parts.append(str(item))
                    else:
                        parts.append(f"{key}: {value}")
        return "\n".join(parts)

    async def embed_and_upsert(self, extractions: list) -> EmbeddingResult:
        """Batch embed extractions and upsert to Qdrant.

        Extractions must have .id set (post-flush).

        Args:
            extractions: List of Extraction ORM objects.

        Returns:
            EmbeddingResult with count and any errors.
        """
        if not extractions:
            return EmbeddingResult(embedded_count=0, errors=[])

        texts = [self.extraction_to_text(e) for e in extractions]
        valid = [(e, t) for e, t in zip(extractions, texts, strict=True) if t.strip()]
        if not valid:
            return EmbeddingResult(
                embedded_count=0,
                errors=[f"All {len(extractions)} extractions produced empty text"],
            )

        try:
            embeddings = await self._embedding_service.embed_batch(
                [t for _, t in valid]
            )

            items = [
                EmbeddingItem(
                    extraction_id=extraction.id,
                    embedding=embedding,
                    payload={
                        "project_id": str(extraction.project_id),
                        "source_id": str(extraction.source_id),
                        "source_group": extraction.source_group or "",
                        "extraction_type": extraction.extraction_type or "",
                    },
                )
                for (extraction, _), embedding in zip(valid, embeddings, strict=True)
            ]

            await self._qdrant_repo.upsert_batch(items)
            return EmbeddingResult(embedded_count=len(items), errors=[])

        except Exception as e:
            logger.error(
                "extraction_embedding_failed",
                error=str(e),
                extraction_count=len(valid),
            )
            return EmbeddingResult(
                embedded_count=0, errors=[str(e)], failed_count=len(valid)
            )

