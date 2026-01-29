"""Entity extraction from extractions using LLM."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING
from uuid import UUID

import structlog

if TYPE_CHECKING:
    from services.llm.client import LLMClient
    from services.storage.repositories.entity import EntityRepository

from orm_models import Entity

logger = structlog.get_logger(__name__)


class EntityExtractor:
    """Extracts entities from extraction data using LLM.

    Delegates LLM calls to LLMClient.extract_entities(), which supports
    both direct mode and queue mode for Redis-based batching.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        entity_repo: EntityRepository,
    ):
        """Initialize entity extractor.

        Args:
            llm_client: LLM client for entity extraction (supports queue mode).
            entity_repo: Entity repository for storage and deduplication.
        """
        self._llm_client = llm_client
        self._entity_repo = entity_repo

    def _normalize(self, entity_type: str, value: str) -> str:
        """Normalize entity value for deduplication.

        Args:
            entity_type: Type of entity (plan, feature, limit, pricing, etc.)
            value: Original entity value

        Returns:
            Normalized value suitable for deduplication
        """
        # Default normalization: lowercase and strip whitespace
        normalized = value.lower().strip()

        if entity_type == "limit":
            # Extract numeric value and unit
            # Match patterns like "10,000/min", "1000 per month", "10,000 requests/min"
            # Remove commas from numbers
            normalized = normalized.replace(",", "")

            # Extract number (at start)
            number_match = re.search(r"^(\d+(?:\.\d+)?)", normalized)
            if not number_match:
                return normalized

            number = number_match.group(1)

            # Extract unit (after / or per)
            unit_match = re.search(r"(?:/|per)\s*(\w+)", normalized)
            if unit_match:
                unit = unit_match.group(1)
                # Convert to standard format: number_per_unit
                # Remove decimal if .0
                if "." in number:
                    number = str(int(float(number)))
                # Expand abbreviations
                unit_map = {
                    "min": "minute",
                    "hr": "hour",
                    "sec": "second",
                    "mo": "month",
                }
                unit = unit_map.get(unit, unit)
                return f"{number}_per_{unit}"

        elif entity_type == "pricing":
            # Extract amount in microcents (millionths of a dollar) and period
            # This preserves sub-cent prices like $0.001/request
            # Remove currency symbols and commas
            normalized = normalized.replace("$", "").replace(",", "")

            # Look for number + period pattern
            match = re.search(r"(\d+(?:\.\d+)?)\s*(?:/|per)\s*(\w+)", normalized)
            if match:
                amount_str = match.group(1)
                period = match.group(2)
                # Convert to microcents (millionths of dollar) for full precision
                # $1.00 = 1,000,000 microcents
                # $0.001 = 1,000 microcents
                microcents = int(float(amount_str) * 1_000_000)
                return f"{microcents}_microcents_per_{period}"

        # For plan, feature, and unknown types: use default lowercase + strip
        return normalized

    def _store_entities(
        self,
        entities: list[dict],
        project_id: UUID,
        source_group: str,
    ) -> list[tuple[Entity, bool]]:
        """Store entities with deduplication.

        Args:
            entities: List of entity dictionaries from LLM
            project_id: Project UUID
            source_group: Source grouping identifier

        Returns:
            List of (Entity, created) tuples
        """
        results = []
        for entity in entities:
            entity_obj, created = self._entity_repo.get_or_create(
                project_id=project_id,
                source_group=source_group,
                entity_type=entity["type"],
                value=entity["value"],
                normalized_value=self._normalize(entity["type"], entity["value"]),
                attributes=entity.get("attributes", {}),
            )
            results.append((entity_obj, created))
        return results

    async def extract(
        self,
        extraction_id: UUID,
        extraction_data: dict,
        project_id: UUID,
        entity_types: list[dict],
        source_group: str,
    ) -> list[Entity]:
        """Extract entities from extraction data and link to extraction.

        Args:
            extraction_id: Extraction UUID to link entities to
            extraction_data: Extraction data dictionary
            project_id: Project UUID
            entity_types: List of entity type definitions from project
            source_group: Source grouping identifier

        Returns:
            List of Entity objects
        """
        # Step 1: Call LLM via LLMClient.extract_entities()
        # This handles both direct and queue modes automatically
        entity_dicts = await self._llm_client.extract_entities(
            extraction_data=extraction_data,
            entity_types=entity_types,
            source_group=source_group,
        )

        # Step 2: Store entities with deduplication and normalization
        stored_entities = self._store_entities(
            entities=entity_dicts,
            project_id=project_id,
            source_group=source_group,
        )

        # Step 3: Link entities to extraction
        entities = []
        for entity, _created in stored_entities:
            link, link_created = self._entity_repo.link_to_extraction(
                entity_id=entity.id,
                extraction_id=extraction_id,
            )
            if link_created:
                logger.debug(
                    "entity_linked",
                    entity_id=str(entity.id),
                    extraction_id=str(extraction_id),
                )
            entities.append(entity)

        return entities
