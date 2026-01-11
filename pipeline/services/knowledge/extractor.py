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
