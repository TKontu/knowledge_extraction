"""Schema-based LLM extraction with field groups."""

import asyncio
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import structlog
from openai import AsyncOpenAI

from config import Settings
from services.extraction.field_groups import FieldGroup
from services.llm.json_repair import try_repair_json

if TYPE_CHECKING:
    from services.extraction.schema_adapter import ExtractionContext
    from src.services.llm.queue import LLMRequestQueue

logger = structlog.get_logger(__name__)


class LLMExtractionError(Exception):
    """Raised when LLM extraction fails."""

    pass


class SchemaExtractor:
    """Extracts structured data based on field group schemas.

    Supports two modes:
    - Direct mode: Calls LLM directly (when llm_queue is None)
    - Queue mode: Submits to Redis queue for processing (when llm_queue is provided)
    """

    def __init__(
        self,
        settings: Settings,
        llm_queue: "LLMRequestQueue | None" = None,
        context: "ExtractionContext | None" = None,
    ):
        """Initialize SchemaExtractor.

        Args:
            settings: Application settings.
            llm_queue: Optional LLM request queue. If provided, uses queue mode.
            context: Optional extraction context for prompt customization.
        """
        from services.extraction.schema_adapter import ExtractionContext

        self.settings = settings
        self.llm_queue = llm_queue
        self.model = settings.llm_model
        self.context = context or ExtractionContext()

        # Only create direct client if not using queue
        if llm_queue is None:
            self.client = AsyncOpenAI(
                base_url=settings.openai_base_url,
                api_key=settings.openai_api_key,
                timeout=settings.llm_http_timeout,
            )
        else:
            self.client = None

    async def extract_field_group(
        self,
        content: str,
        field_group: FieldGroup,
        source_context: str | None = None,
        company_name: str | None = None,  # Deprecated, backward compat
    ) -> dict[str, Any]:
        """Extract fields for a specific field group.

        Args:
            content: Markdown content to extract from.
            field_group: Field group definition.
            source_context: Optional source context (e.g., company name, website name).
            company_name: DEPRECATED. Use source_context instead.

        Returns:
            Dictionary of extracted field values.

        Raises:
            LLMExtractionError: If extraction fails.
        """
        # Backward compatibility: use company_name if source_context not provided
        context_value = source_context or company_name

        if self.llm_queue is not None:
            return await self._extract_via_queue(content, field_group, context_value)
        else:
            return await self._extract_direct(content, field_group, context_value)

    async def _extract_via_queue(
        self,
        content: str,
        field_group: FieldGroup,
        source_context: str | None,
    ) -> dict[str, Any]:
        """Extract via LLM request queue.

        Args:
            content: Markdown content.
            field_group: Field group definition.
            source_context: Optional source context.

        Returns:
            Extracted field values.

        Raises:
            LLMExtractionError: If queue returns error or timeout.
        """
        from services.llm.models import LLMRequest

        # Build prompts first (for consistency with direct extraction)
        system_prompt = self._build_system_prompt(field_group)
        user_prompt = self._build_user_prompt(content, field_group, source_context)

        # Build request
        request_timeout = getattr(self.settings, "llm_request_timeout", 300)
        request = LLMRequest(
            request_id=str(uuid4()),
            request_type="extract_field_group",
            payload={
                "content": content,
                "field_group": {
                    "name": field_group.name,
                    "description": field_group.description,
                    "fields": [
                        {
                            "name": f.name,
                            "field_type": f.field_type,
                            "description": f.description,
                            "required": f.required,
                            "default": f.default,
                            "enum_values": f.enum_values,
                        }
                        for f in field_group.fields
                    ],
                    "prompt_hint": field_group.prompt_hint,
                    "is_entity_list": field_group.is_entity_list,
                },
                "source_context": source_context,
                "model": self.model,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
            },
            priority=5,
            created_at=datetime.now(UTC),
            timeout_at=datetime.now(UTC) + timedelta(seconds=request_timeout),
        )

        logger.info(
            "schema_extraction_queued",
            request_id=request.request_id,
            field_group=field_group.name,
            content_length=len(content),
        )

        # Submit and wait
        await self.llm_queue.submit(request)
        response = await self.llm_queue.wait_for_result(
            request.request_id,
            timeout=request_timeout,
        )

        # Handle response status
        if response.status == "error":
            raise LLMExtractionError(f"LLM extraction failed: {response.error}")
        elif response.status == "timeout":
            raise LLMExtractionError(f"LLM extraction timeout: {response.error}")

        # Apply defaults and return
        result = self._apply_defaults(response.result or {}, field_group)

        logger.info(
            "schema_extraction_completed",
            request_id=request.request_id,
            field_group=field_group.name,
            processing_time_ms=response.processing_time_ms,
        )

        return result

    async def _extract_direct(
        self,
        content: str,
        field_group: FieldGroup,
        source_context: str | None,
    ) -> dict[str, Any]:
        """Extract via direct LLM call with retry and variation.

        Uses exponential backoff with temperature variation on retries to avoid
        getting stuck in the same failure mode (e.g., hallucination loops).

        Args:
            content: Markdown content.
            field_group: Field group definition.
            source_context: Optional source context.

        Returns:
            Extracted field values.

        Raises:
            LLMExtractionError: If all retry attempts fail.
        """
        max_retries = self.settings.llm_max_retries
        base_temp = self.settings.llm_base_temperature
        temp_increment = self.settings.llm_retry_temperature_increment
        backoff_min = self.settings.llm_retry_backoff_min
        backoff_max = self.settings.llm_retry_backoff_max
        max_tokens = self.settings.llm_max_tokens

        last_error: Exception | None = None

        for attempt in range(1, max_retries + 1):
            # Vary temperature on retries to get different outputs
            temperature = base_temp + (attempt - 1) * temp_increment

            # Build prompts (add conciseness hint on retries)
            system_prompt = self._build_system_prompt(field_group)
            if attempt > 1:
                system_prompt += "\n\nIMPORTANT: Be concise. Output valid JSON only."

            user_prompt = self._build_user_prompt(content, field_group, source_context)

            logger.info(
                "schema_extraction_started",
                field_group=field_group.name,
                content_length=len(content),
                is_entity_list=field_group.is_entity_list,
                attempt=attempt,
                temperature=temperature,
            )

            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    response_format={"type": "json_object"},
                    temperature=temperature,
                    max_tokens=max_tokens,
                )

                result_text = response.choices[0].message.content
                result_data = try_repair_json(result_text, context="schema_extract")

                # Apply defaults for missing fields
                result = self._apply_defaults(result_data, field_group)

                logger.info(
                    "schema_extraction_completed",
                    field_group=field_group.name,
                    fields_extracted=len(
                        [k for k, v in result.items() if v is not None]
                    ),
                    attempt=attempt,
                )

                return result

            except Exception as e:
                last_error = e
                response_preview = None
                if "result_text" in locals() and result_text:
                    response_preview = result_text[:500]
                logger.warning(
                    "schema_extraction_attempt_failed",
                    field_group=field_group.name,
                    error=str(e),
                    error_type=type(e).__name__,
                    attempt=attempt,
                    max_retries=max_retries,
                    content_preview=content[:300] if content else None,
                    response_preview=response_preview,
                )

                if attempt < max_retries:
                    wait_time = min(backoff_min * (2 ** (attempt - 1)), backoff_max)
                    logger.info("llm_retry_backoff", wait_seconds=wait_time)
                    await asyncio.sleep(wait_time)

        # All retries exhausted
        logger.error(
            "schema_extraction_failed_all_retries",
            field_group=field_group.name,
            error=str(last_error),
            error_type=type(last_error).__name__ if last_error else None,
            attempts=max_retries,
            content_preview=content[:300] if content else None,
            exc_info=True,
        )
        raise LLMExtractionError(
            f"Schema extraction failed after {max_retries} attempts: {last_error}"
        ) from last_error

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

        return f"""You are extracting {field_group.description} from {self.context.source_type}.

Fields to extract:
{fields_str}

{field_group.prompt_hint}

Output JSON with exactly these fields. Use null for unknown values.
For boolean fields, only return true if there is clear evidence.
"""

    def _build_entity_list_system_prompt(self, field_group: FieldGroup) -> str:
        """Build system prompt for entity list extraction.

        Uses the field group name as the output key (e.g., "employees", "locations")
        instead of hardcoding "products".
        """
        field_specs = []
        id_field = None
        for f in field_group.fields:
            spec = f'- "{f.name}" ({f.field_type}): {f.description or ""}'
            field_specs.append(spec)
            # Find the ID field for the example
            if (
                f.name in ("product_name", "entity_id", "name", "id")
                and id_field is None
            ):
                id_field = f.name

        fields_str = "\n".join(field_specs)

        # Use group name as output key
        output_key = field_group.name

        # Build example using actual ID field or first field
        if id_field:
            example_fields = f'"{id_field}": "...", ...'
        else:
            first_field = field_group.fields[0].name if field_group.fields else "field"
            example_fields = f'"{first_field}": "...", ...'

        # Singular form for "each X" phrasing
        entity_singular = field_group.name.rstrip("s")

        return f"""You are extracting {field_group.description} from {self.context.source_type}.

For each {entity_singular} found, extract:
{fields_str}

{field_group.prompt_hint}

Output JSON with structure:
{{
  "{output_key}": [
    {{{example_fields}}},
    ...
  ],
  "confidence": 0.0-1.0
}}

Only include items you find clear evidence for. Return empty list if none found.
"""

    def _build_user_prompt(
        self,
        content: str,
        field_group: FieldGroup,
        source_context: str | None,
    ) -> str:
        """Build user prompt with content."""
        context_line = (
            f"{self.context.source_label}: {source_context}\n\n"
            if source_context
            else ""
        )

        return f"""{context_line}Extract {field_group.name} information from this content:

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
