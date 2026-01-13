"""Schema-based LLM extraction with field groups."""

import json
from typing import Any

import structlog
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from config import Settings
from services.extraction.field_groups import FieldGroup

logger = structlog.get_logger(__name__)


class SchemaExtractor:
    """Extracts structured data based on field group schemas."""

    def __init__(self, settings: Settings):
        self.client = AsyncOpenAI(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
            timeout=settings.llm_http_timeout,
        )
        self.model = settings.llm_model

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=60),
    )
    async def extract_field_group(
        self,
        content: str,
        field_group: FieldGroup,
        company_name: str | None = None,
    ) -> dict[str, Any]:
        """Extract fields for a specific field group.

        Args:
            content: Markdown content to extract from.
            field_group: Field group definition.
            company_name: Optional company name for context.

        Returns:
            Dictionary of extracted field values.
        """
        system_prompt = self._build_system_prompt(field_group)
        user_prompt = self._build_user_prompt(content, field_group, company_name)

        logger.info(
            "schema_extraction_started",
            field_group=field_group.name,
            content_length=len(content),
            is_entity_list=field_group.is_entity_list,
        )

        response = await self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
        )

        result_text = response.choices[0].message.content
        result_data = json.loads(result_text)

        # Apply defaults for missing fields
        result = self._apply_defaults(result_data, field_group)

        logger.info(
            "schema_extraction_completed",
            field_group=field_group.name,
            fields_extracted=len([k for k, v in result.items() if v is not None]),
        )

        return result

    def _build_system_prompt(self, field_group: FieldGroup) -> str:
        """Build system prompt for field group extraction."""
        if field_group.is_entity_list:
            return self._build_entity_list_system_prompt(field_group)

        # Build field descriptions
        field_specs = []
        for f in field_group.fields:
            spec = f'- "{f.name}" ({f.field_type}): {f.description}'
            if f.enum_values:
                spec += f" [options: {', '.join(f.enum_values)}]"
            if f.required:
                spec += " [REQUIRED]"
            field_specs.append(spec)

        fields_str = "\n".join(field_specs)

        return f"""You are extracting {field_group.description} from company documentation.

Fields to extract:
{fields_str}

{field_group.prompt_hint}

Output JSON with exactly these fields. Use null for unknown values.
For boolean fields, only return true if there is clear evidence.
"""

    def _build_entity_list_system_prompt(self, field_group: FieldGroup) -> str:
        """Build system prompt for entity list extraction (products)."""
        field_specs = []
        for f in field_group.fields:
            spec = f'- "{f.name}" ({f.field_type}): {f.description or ""}'
            field_specs.append(spec)

        fields_str = "\n".join(field_specs)

        return f"""You are extracting {field_group.description} from company documentation.

For each product found, extract:
{fields_str}

{field_group.prompt_hint}

Output JSON with structure:
{{
  "products": [
    {{"product_name": "...", "series_name": "...", ...}},
    ...
  ],
  "confidence": 0.0-1.0
}}

Only include products you find clear evidence for. Return empty list if none found.
"""

    def _build_user_prompt(
        self,
        content: str,
        field_group: FieldGroup,
        company_name: str | None,
    ) -> str:
        """Build user prompt with content."""
        company_context = f"Company: {company_name}\n\n" if company_name else ""

        return f"""{company_context}Extract {field_group.name} information from this content:

---
{content[:8000]}
---"""

    def _apply_defaults(
        self, result: dict[str, Any], field_group: FieldGroup
    ) -> dict[str, Any]:
        """Apply default values for missing fields."""
        for f in field_group.fields:
            if f.name not in result or result[f.name] is None:
                if f.default is not None:
                    result[f.name] = f.default
                elif f.field_type == "boolean":
                    result[f.name] = False
                elif f.field_type == "list":
                    result[f.name] = []

        return result
