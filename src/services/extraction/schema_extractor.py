"""Schema-based LLM extraction with field groups."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import structlog
from openai import AsyncOpenAI

from config import LLMConfig
from constants import LLM_RETRY_HINT
from exceptions import LLMExtractionError
from services.extraction.content_cleaner import strip_structural_junk
from services.extraction.field_groups import FieldGroup
from services.llm.json_repair import try_repair_json

if TYPE_CHECKING:
    from services.extraction.schema_adapter import ExtractionContext
    from services.llm.queue import LLMRequestQueue

logger = structlog.get_logger(__name__)


def _singularize(word: str) -> str:
    """Naive singularization for English plural nouns.

    Handles common patterns: -ies→-y, -ses/-xes/-zes→drop 2, -s→drop 1.
    Preserves words ending in -ss, -us, -is (already singular-looking).
    """
    if not word or len(word) < 3:
        return word
    lower = word.lower()
    if lower.endswith(("ss", "us", "is")):
        return word
    if lower.endswith("ies"):
        return word[:-3] + "y"
    if lower.endswith(("ses", "xes", "zes")):
        return word[:-2]
    if lower.endswith("s"):
        return word[:-1]
    return word


class SchemaExtractor:
    """Extracts structured data based on field group schemas.

    Supports two modes:
    - Direct mode: Calls LLM directly (when llm_queue is None)
    - Queue mode: Submits to Redis queue for processing (when llm_queue is provided)
    """

    def __init__(
        self,
        llm: LLMConfig,
        *,
        llm_queue: "LLMRequestQueue | None" = None,
        content_limit: int = 20000,
        source_quoting: bool = True,
        request_timeout: int = 300,
        context: "ExtractionContext | None" = None,
        data_version: int = 1,
    ):
        """Initialize SchemaExtractor.

        Args:
            llm: LLM configuration with model, API URLs, and retry settings.
            llm_queue: Optional LLM request queue. If provided, uses queue mode.
            content_limit: Max characters of source content to send to LLM.
            source_quoting: Whether to include source quotes in extraction.
            request_timeout: Timeout in seconds for queued LLM requests.
            context: Optional extraction context for prompt customization.
            data_version: Extraction data format version (1=flat, 2=per-field structured).
        """
        from services.extraction.schema_adapter import ExtractionContext

        self._llm = llm
        self.llm_queue = llm_queue
        self.model = llm.model
        self.context = context or ExtractionContext()

        # Cache settings used at runtime
        self._content_limit = content_limit
        self._source_quoting_enabled = source_quoting
        self._request_timeout = request_timeout
        self._data_version = data_version

        # Only create direct client if not using queue
        if llm_queue is None:
            self.client = AsyncOpenAI(
                base_url=llm.base_url,
                api_key=llm.api_key,
                timeout=llm.http_timeout,
            )
        else:
            self.client = None

    async def extract_field_group(
        self,
        content: str,
        field_group: FieldGroup,
        source_context: str | None = None,
        strict_quoting: bool = False,
    ) -> dict[str, Any]:
        """Extract fields for a specific field group.

        Args:
            content: Markdown content to extract from.
            field_group: Field group definition.
            source_context: Optional source context (e.g., company name, website name).
            strict_quoting: If True, use stricter quoting instructions (retry mode).

        Returns:
            Dictionary of extracted field values.

        Raises:
            LLMExtractionError: If extraction fails.
        """
        context_value = source_context

        if self.llm_queue is not None:
            return await self._extract_via_queue(
                content, field_group, context_value, strict_quoting=strict_quoting
            )
        else:
            return await self._extract_direct(
                content, field_group, context_value, strict_quoting=strict_quoting
            )

    async def _extract_via_queue(
        self,
        content: str,
        field_group: FieldGroup,
        source_context: str | None,
        strict_quoting: bool = False,
    ) -> dict[str, Any]:
        """Extract via LLM request queue.

        Args:
            content: Markdown content.
            field_group: Field group definition.
            source_context: Optional source context.
            strict_quoting: If True, use stricter quoting instructions.

        Returns:
            Extracted field values.

        Raises:
            LLMExtractionError: If queue returns error or timeout.
        """
        from services.llm.models import LLMRequest

        # Build prompts first (for consistency with direct extraction)
        system_prompt = self._build_system_prompt(field_group, strict_quoting=strict_quoting)
        user_prompt = self._build_user_prompt(content, field_group, source_context)

        # Build request
        request_timeout = self._request_timeout
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
        strict_quoting: bool = False,
    ) -> dict[str, Any]:
        """Extract via direct LLM call with retry and variation.

        Uses exponential backoff with temperature variation on retries to avoid
        getting stuck in the same failure mode (e.g., hallucination loops).

        Args:
            content: Markdown content.
            field_group: Field group definition.
            source_context: Optional source context.
            strict_quoting: If True, use stricter quoting instructions.

        Returns:
            Extracted field values.

        Raises:
            LLMExtractionError: If all retry attempts fail.
        """
        max_retries = self._llm.max_retries
        base_temp = self._llm.base_temperature
        temp_increment = self._llm.retry_temperature_increment
        backoff_min = self._llm.retry_backoff_min
        backoff_max = self._llm.retry_backoff_max
        max_tokens = self._llm.max_tokens

        last_error: Exception | None = None

        for attempt in range(1, max_retries + 1):
            # Vary temperature on retries to get different outputs
            temperature = base_temp + (attempt - 1) * temp_increment

            # Build prompts (add conciseness hint on retries)
            system_prompt = self._build_system_prompt(field_group, strict_quoting=strict_quoting)
            if attempt > 1:
                system_prompt += LLM_RETRY_HINT

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
                finish_reason = response.choices[0].finish_reason

                # Check for truncation due to max_tokens limit
                if finish_reason == "length":
                    logger.warning(
                        "schema_extraction_truncated",
                        field_group=field_group.name,
                        is_entity_list=field_group.is_entity_list,
                        response_length=len(result_text) if result_text else 0,
                        max_tokens=max_tokens,
                        attempt=attempt,
                    )
                    # For entity lists, truncation means incomplete JSON array
                    # Try to repair, but if it fails, return empty result
                    if field_group.is_entity_list:
                        try:
                            result_data = try_repair_json(
                                result_text, context="schema_extract_truncated"
                            )
                        except json.JSONDecodeError:
                            logger.warning(
                                "schema_extraction_truncated_unrecoverable",
                                field_group=field_group.name,
                                response_preview=result_text[:500]
                                if result_text
                                else None,
                            )
                            # Return empty list with truncation flag so the
                            # orchestrator can record the data loss on the extraction.
                            return {
                                field_group.name: [],
                                "confidence": 0.0,
                                "_truncated": True,
                            }
                    else:
                        # Non-entity fields - try normal repair
                        result_data = try_repair_json(
                            result_text, context="schema_extract_truncated"
                        )
                else:
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
                    truncated=finish_reason == "length",
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

    def _build_system_prompt(
        self, field_group: FieldGroup, strict_quoting: bool = False
    ) -> str:
        """Build system prompt for field group extraction (version dispatcher)."""
        if self._data_version >= 2:
            if field_group.is_entity_list:
                return self._build_entity_list_system_prompt_v2(
                    field_group, strict_quoting=strict_quoting
                )
            return self._build_system_prompt_v2(
                field_group, strict_quoting=strict_quoting
            )
        if field_group.is_entity_list:
            return self._build_entity_list_system_prompt_v1(
                field_group, strict_quoting=strict_quoting
            )
        return self._build_system_prompt_v1(
            field_group, strict_quoting=strict_quoting
        )

    def _build_system_prompt_v1(
        self, field_group: FieldGroup, strict_quoting: bool = False
    ) -> str:
        """Build v1 system prompt for field group extraction (flat format)."""
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

        quoting_instruction = ""
        if self._source_quoting_enabled:
            if strict_quoting:
                quoting_instruction = """
CRITICAL QUOTING REQUIREMENT:
Include a "_quotes" object mapping each non-null field to an EXACT verbatim excerpt (15-50 chars) copied directly from the source text.
The quote MUST appear word-for-word in the source content. Do NOT paraphrase, translate, or fabricate quotes.
If you cannot find an exact quote in the source for a field, set that field to null rather than inventing a quote.
Example: "_quotes": {"field_name": "exact text copied from source"}
"""
            else:
                quoting_instruction = """
Include a "_quotes" object mapping each non-null field to a brief verbatim excerpt (15-50 chars) from the source that supports the value.
Example: "_quotes": {"field_name": "exact text from source"}
"""

        return f"""You are extracting {field_group.description} from {self.context.source_type}.

Fields to extract:
{fields_str}

{field_group.prompt_hint}

RULES:
- Extract ONLY from the content provided below. Do NOT use outside knowledge.
- If the content does not contain information for a field, return null.
- If the content is not relevant to {field_group.description}, return null for ALL fields.
- For boolean fields, return true ONLY if there is explicit evidence in the content. Default to false.
- For list fields, return empty list [] if no items found.

Output JSON with exactly these fields and a "confidence" field (0.0-1.0):
- 0.0 if the content has no relevant information
- 0.5-0.7 if only partial information found
- 0.8-1.0 if the content is clearly relevant with good data
{quoting_instruction}"""

    def _build_system_prompt_v2(
        self, field_group: FieldGroup, strict_quoting: bool = False
    ) -> str:
        """Build v2 system prompt: per-field {value, confidence, quote}."""
        field_specs = []
        for f in field_group.fields:
            spec = f'- "{f.name}" ({f.field_type}): {f.description}'
            if f.enum_values:
                spec += f" [options: {', '.join(f.enum_values)}]"
            if f.required:
                spec += " [REQUIRED]"
            field_specs.append(spec)

        fields_str = "\n".join(field_specs)

        # Build example showing per-field structure
        example_field = field_group.fields[0].name if field_group.fields else "field"
        quoting_note = ""
        if self._source_quoting_enabled:
            if strict_quoting:
                quoting_note = (
                    '\nCRITICAL: The "quote" for each field MUST be an EXACT verbatim excerpt '
                    "(15-50 chars) copied directly from the source text. "
                    "Do NOT paraphrase, translate, or fabricate quotes. "
                    "If you cannot find an exact quote, set the field to null."
                )
            else:
                quoting_note = (
                    '\nInclude a "quote" with each field: a brief verbatim excerpt '
                    "(15-50 chars) from the source that supports the value."
                )

        return f"""You are extracting {field_group.description} from {self.context.source_type}.

Fields to extract:
{fields_str}

{field_group.prompt_hint}

RULES:
- Extract ONLY from the content provided below. Do NOT use outside knowledge.
- If the content does not contain information for a field, set it to null.
- If the content is not relevant to {field_group.description}, set ALL fields to null.
- For boolean fields, return true ONLY if there is explicit evidence. Default to false.
- For list fields, return empty list [] if no items found.

Output JSON with per-field structure. Each field has its own value, confidence, and quote:
{{
  "fields": {{
    "{example_field}": {{"value": <extracted_value>, "confidence": 0.0-1.0, "quote": "exact text from source"}},
    ...
  }}
}}

Confidence per field:
- 0.0 if no information found for this field
- 0.5-0.7 if partial/uncertain information
- 0.8-1.0 if clear, well-supported data
{quoting_note}"""

    def _build_entity_list_system_prompt(
        self, field_group: FieldGroup, strict_quoting: bool = False
    ) -> str:
        """Build entity list system prompt (backward-compatible alias for v1)."""
        return self._build_entity_list_system_prompt_v1(
            field_group, strict_quoting=strict_quoting
        )

    def _build_entity_list_system_prompt_v1(
        self, field_group: FieldGroup, strict_quoting: bool = False
    ) -> str:
        """Build v1 system prompt for entity list extraction."""
        field_specs = []
        id_field = None
        id_field_names = (
            self.context.entity_id_fields
            if self.context
            else ("entity_id", "name", "id")
        )
        for f in field_group.fields:
            spec = f'- "{f.name}" ({f.field_type}): {f.description or ""}'
            field_specs.append(spec)
            if f.name in id_field_names and id_field is None:
                id_field = f.name

        fields_str = "\n".join(field_specs)
        output_key = field_group.name

        if id_field:
            example_fields = f'"{id_field}": "...", ...'
        else:
            first_field = field_group.fields[0].name if field_group.fields else "field"
            example_fields = f'"{first_field}": "...", ...'

        entity_singular = _singularize(field_group.name)

        quoting_instruction = ""
        if self._source_quoting_enabled:
            if strict_quoting:
                quoting_instruction = (
                    '\nCRITICAL QUOTING REQUIREMENT:\n'
                    'For each entity, include a "_quote" field with an EXACT verbatim '
                    "excerpt (15-50 chars) copied directly from the source text that "
                    "identifies this entity.\n"
                    "The quote MUST appear word-for-word in the source content. "
                    "Do NOT paraphrase, translate, or fabricate quotes.\n"
                    "If you cannot find an exact quote for an entity, omit that entity "
                    "entirely rather than inventing a quote.\n"
                )
            else:
                quoting_instruction = (
                    '\nFor each entity, include a "_quote" field with a brief verbatim '
                    "excerpt (15-50 chars) from the source that identifies this entity.\n"
                )

        max_items = field_group.max_items or 20

        return f"""You are extracting {field_group.description} from {self.context.source_type}.

For each {entity_singular} found, extract:
{fields_str}

{field_group.prompt_hint}

IMPORTANT RULES:
- Extract ONLY from the content provided below. Do NOT use outside knowledge.
- Extract ONLY the most relevant/significant items (max {max_items} items)
- If this content does not contain any {entity_singular} information, return an empty list.
- Skip generic lists that are just navigation or coverage info, not actual entities.

Output JSON with structure:
{{
  "{output_key}": [
    {{{example_fields}}},
    ...
  ],
  "confidence": 0.0-1.0
}}

Confidence guidance:
- 0.0 if the content has no relevant {entity_singular} information
- 0.5-0.7 if only a few items found or items have sparse detail
- 0.8-1.0 if the content is clearly relevant with well-populated items

Only include items you find clear evidence for. Return empty list if none found.
Keep output concise - quality over quantity.
{quoting_instruction}"""

    def _build_entity_list_system_prompt_v2(
        self,
        field_group: FieldGroup,
        strict_quoting: bool = False,
        already_found: list[str] | None = None,
    ) -> str:
        """Build v2 system prompt for entity list extraction with per-entity provenance."""
        field_specs = []
        id_field = None
        id_field_names = (
            self.context.entity_id_fields
            if self.context
            else ("entity_id", "name", "id")
        )
        for f in field_group.fields:
            spec = f'- "{f.name}" ({f.field_type}): {f.description or ""}'
            field_specs.append(spec)
            if f.name in id_field_names and id_field is None:
                id_field = f.name

        fields_str = "\n".join(field_specs)
        output_key = field_group.name
        entity_singular = _singularize(field_group.name)
        max_items = field_group.max_items or 20

        quoting_note = ""
        if self._source_quoting_enabled:
            if strict_quoting:
                quoting_note = (
                    '\nCRITICAL: "_quote" MUST be an EXACT verbatim excerpt (15-50 chars) '
                    "copied from the source. Do NOT paraphrase or fabricate. "
                    "Omit entity entirely if no exact quote found."
                )
            else:
                quoting_note = (
                    '\nFor each entity, include "_quote": a brief verbatim excerpt '
                    "(15-50 chars) from the source identifying this entity."
                )

        exclusion_block = ""
        if already_found:
            exclusion_list = ", ".join(already_found[:50])
            exclusion_block = f"""
Already extracted entities (DO NOT repeat these): [{exclusion_list}]
Extract ONLY entities NOT in this list.
"""

        return f"""You are extracting {field_group.description} from {self.context.source_type}.

For each {entity_singular} found, extract:
{fields_str}

{field_group.prompt_hint}
{exclusion_block}
IMPORTANT RULES:
- Extract ONLY from the content provided below. Do NOT use outside knowledge.
- Extract ONLY the most relevant/significant items (max {max_items} items)
- If no {entity_singular} information found, return an empty list.
- Skip generic navigation/coverage lists, not actual entities.

Output JSON with per-entity confidence and quote:
{{
  "{output_key}": [
    {{<fields>, "_confidence": 0.0-1.0, "_quote": "exact text from source"}},
    ...
  ],
  "has_more": true/false
}}

Set "has_more" to true if there are more entities in the content not yet extracted.

Confidence per entity:
- 0.5-0.7 if sparse detail
- 0.8-1.0 if well-supported with clear evidence
{quoting_note}"""

    def _build_user_prompt(
        self,
        content: str,
        field_group: FieldGroup,
        source_context: str | None,
    ) -> str:
        """Build user prompt with content.

        Applies Layer 1 content cleaning (structural junk removal) before
        truncation so more real content fits within the extraction window.
        """
        context_line = (
            f"{self.context.source_label}: {source_context}\n\n"
            if source_context
            else ""
        )

        cleaned = strip_structural_junk(content)
        limit = self._content_limit

        return f"""{context_line}Extract {field_group.name} information from ONLY the content below:

---
{cleaned[:limit]}
---"""

    def _apply_defaults(
        self, result: dict[str, Any], field_group: FieldGroup
    ) -> dict[str, Any]:
        """Apply default values for missing fields (v1 format only)."""
        for f in field_group.fields:
            if f.name not in result or result[f.name] is None:
                if f.default is not None:
                    result[f.name] = f.default
                elif f.field_type == "boolean":
                    result[f.name] = False
                elif f.field_type == "list":
                    result[f.name] = []

        return result

    @staticmethod
    def detect_response_format(raw: dict) -> int:
        """Detect if LLM returned v1 (flat) or v2 (structured) format.

        Returns:
            1 for flat format, 2 for per-field structured format.
        """
        if "fields" in raw and isinstance(raw["fields"], dict):
            # Check that at least one nested value looks like {value: ..., confidence: ...}
            for _k, v in raw["fields"].items():
                if isinstance(v, dict) and "value" in v:
                    return 2
        return 1

    @staticmethod
    def parse_v2_response(raw: dict, field_group: FieldGroup) -> dict:
        """Parse v2 per-field structured response into normalized format.

        Handles both proper v2 ({"fields": {"name": {"value": ..., ...}}})
        and graceful fallback to v1 flat format if LLM ignores the format.

        Args:
            raw: Raw parsed JSON from LLM response.
            field_group: Field group definition.

        Returns:
            Normalized v2 dict with "fields" key containing per-field data.
        """
        detected = SchemaExtractor.detect_response_format(raw)

        if detected == 2:
            # Already v2 format — normalize field names
            fields = raw["fields"]
            normalized: dict[str, Any] = {}
            for f in field_group.fields:
                if f.name in fields:
                    entry = fields[f.name]
                    if isinstance(entry, dict) and "value" in entry:
                        normalized[f.name] = {
                            "value": entry.get("value"),
                            "confidence": float(entry.get("confidence", 0.5)),
                            "quote": entry.get("quote"),
                        }
                    else:
                        # Field present but not structured — wrap it
                        normalized[f.name] = {
                            "value": entry,
                            "confidence": 0.5,
                            "quote": None,
                        }
                else:
                    normalized[f.name] = {
                        "value": None,
                        "confidence": 0.0,
                        "quote": None,
                    }
            return {"fields": normalized}

        # Fallback: v1 flat format — convert to v2 structure
        quotes = raw.get("_quotes", {}) or {}
        group_confidence = float(raw.get("confidence", 0.5))
        normalized = {}
        for f in field_group.fields:
            value = raw.get(f.name)
            normalized[f.name] = {
                "value": value,
                "confidence": group_confidence if value is not None else 0.0,
                "quote": quotes.get(f.name),
            }
        return {"fields": normalized}

    @staticmethod
    def parse_v2_entity_response(raw: dict, field_group: FieldGroup) -> dict:
        """Parse v2 entity list response with per-entity confidence.

        Args:
            raw: Raw parsed JSON from LLM response.
            field_group: Field group definition.

        Returns:
            Dict with entity_key list and has_more flag.
        """
        entity_key = field_group.name
        entities = raw.get(entity_key, [])
        if not isinstance(entities, list):
            entities = []

        normalized = []
        for entity in entities:
            if not isinstance(entity, dict):
                continue
            fields = {}
            for f in field_group.fields:
                fields[f.name] = entity.get(f.name)
            normalized.append({
                "fields": fields,
                "_confidence": float(entity.get("_confidence", entity.get("confidence", 0.5))),
                "_quote": entity.get("_quote", entity.get("quote")),
            })

        return {
            entity_key: normalized,
            "has_more": bool(raw.get("has_more", False)),
        }
