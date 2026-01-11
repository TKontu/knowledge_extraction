"""Entity extraction from extractions using LLM."""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.llm.client import LLMClient
    from services.storage.repositories.entity import EntityRepository


class EntityExtractor:
    """Extracts entities from extraction data using LLM."""

    def __init__(
        self,
        llm_client: LLMClient,
        entity_repo: EntityRepository,
    ):
        """Initialize entity extractor.

        Args:
            llm_client: LLM client for entity extraction
            entity_repo: Entity repository for storage and deduplication
        """
        self._llm_client = llm_client
        self._entity_repo = entity_repo
