"""Entity extraction from extractions using LLM."""

from __future__ import annotations

import json
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

    def _build_prompt(
        self,
        extraction_data: dict,
        entity_types: list[dict],
        source_group: str,
    ) -> dict[str, str]:
        """Build prompts for entity extraction.

        Args:
            extraction_data: Extraction data dictionary
            entity_types: List of entity type definitions from project
            source_group: Source grouping identifier (e.g., company name)

        Returns:
            Dictionary with 'system' and 'user' prompts
        """
        # Build entity type documentation
        entity_docs = []
        for et in entity_types:
            name = et["name"]
            desc = et.get("description", "")
            entity_docs.append(f"- {name}: {desc}")
        entity_types_doc = "\n".join(entity_docs)

        # Get primary text field from extraction data
        text_content = (
            extraction_data.get("fact_text")
            or extraction_data.get("text")
            or str(extraction_data)
        )

        system_prompt = f"""
Extract entities from this extracted data. Return JSON with entities found.

Source Group: "{source_group}"

Entity types to extract:
{entity_types_doc}

Output format:
{{
  "entities": [
    {{
      "type": "entity_type_name",
      "value": "original text",
      "normalized": "normalized_value",
      "attributes": {{}}
    }}
  ]
}}

Guidelines:
- Only extract entities explicitly mentioned in the data
- Do not infer or guess entities not present
- Normalize values for deduplication (lowercase, canonical form)
- For limits: extract numeric values and units in attributes
- For pricing: extract amounts and periods in attributes"""

        user_prompt = f"""Extract entities from this data:

{text_content}"""

        return {"system": system_prompt, "user": user_prompt}

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
            # Extract amount in cents and period
            # Remove currency symbols and commas
            normalized = normalized.replace("$", "").replace(",", "")

            # Look for number + period pattern
            match = re.search(r"(\d+(?:\.\d+)?)\s*(?:/|per)\s*(\w+)", normalized)
            if match:
                amount = match.group(1)
                period = match.group(2)
                # Convert to cents (remove decimal point)
                cents = str(int(float(amount) * 100)) if "." in amount else amount
                return f"{cents}_per_{period}"

        # For plan, feature, and unknown types: use default lowercase + strip
        return normalized

    async def _call_llm(self, prompt: dict[str, str]) -> list[dict]:
        """Call LLM and parse entity response.

        Args:
            prompt: Dictionary with 'system' and 'user' prompts

        Returns:
            List of entity dictionaries
        """
        try:
            response = await self._llm_client.client.chat.completions.create(
                model=self._llm_client.model,
                messages=[
                    {"role": "system", "content": prompt["system"]},
                    {"role": "user", "content": prompt["user"]},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
            )

            content = response.choices[0].message.content
            parsed = json.loads(content)
            return parsed.get("entities", [])

        except json.JSONDecodeError as e:
            logger.warning("failed_to_parse_llm_response", error=str(e))
            return []
        except Exception as e:
            logger.error("llm_call_failed", error=str(e))
            return []

    async def _store_entities(
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
            entity_obj, created = await self._entity_repo.get_or_create(
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
        # Step 1: Build prompt
        prompt = self._build_prompt(
            extraction_data=extraction_data,
            entity_types=entity_types,
            source_group=source_group,
        )

        # Step 2: Call LLM
        entity_dicts = await self._call_llm(prompt)

        # Step 3: Store entities with deduplication
        stored_entities = await self._store_entities(
            entities=entity_dicts,
            project_id=project_id,
            source_group=source_group,
        )

        # Step 4: Link entities to extraction
        entities = []
        for entity, _created in stored_entities:
            await self._entity_repo.link_to_extraction(
                entity_id=entity.id,
                extraction_id=extraction_id,
            )
            entities.append(entity)

        return entities
