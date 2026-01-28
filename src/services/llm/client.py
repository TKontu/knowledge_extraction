"""LLM client for fact and entity extraction."""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING
from uuid import uuid4

import structlog
from openai import AsyncOpenAI

from config import Settings
from models import ExtractedFact
from services.llm.json_repair import try_repair_json

if TYPE_CHECKING:
    from src.services.llm.queue import LLMRequestQueue

logger = structlog.get_logger(__name__)


class LLMExtractionError(Exception):
    """Raised when LLM extraction fails."""

    pass


class LLMClient:
    """Client for interacting with LLM for fact and entity extraction.

    Supports two modes:
    - Direct mode: Calls LLM directly (when llm_queue is None)
    - Queue mode: Submits to Redis queue for processing (when llm_queue is provided)
    """

    def __init__(
        self,
        settings: Settings,
        llm_queue: "LLMRequestQueue | None" = None,
    ):
        """Initialize LLM client.

        Args:
            settings: Application settings.
            llm_queue: Optional LLM request queue. If provided, uses queue mode.
        """
        self.settings = settings
        self.llm_queue = llm_queue
        self.model = settings.llm_model
        self._closed = False

        # Only create direct client if not using queue
        if llm_queue is None:
            self.client = AsyncOpenAI(
                base_url=settings.openai_base_url,
                api_key=settings.openai_api_key,
                timeout=settings.llm_http_timeout,
            )
        else:
            self.client = None

    async def close(self) -> None:
        """Close the LLM client and release resources.

        Safe to call multiple times (idempotent).
        In queue mode, this is a no-op since no direct client exists.
        """
        if self._closed:
            return

        self._closed = True

        if self.client is not None:
            await self.client.close()

    async def __aenter__(self) -> "LLMClient":
        """Enter async context manager."""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb) -> None:
        """Exit async context manager and close client."""
        await self.close()

    async def extract_facts(
        self, content: str, categories: list[str], profile_name: str = "general"
    ) -> list[ExtractedFact]:
        """Extract facts from content using LLM.

        Args:
            content: Markdown content to extract facts from.
            categories: List of allowed categories.
            profile_name: Name of extraction profile (for prompt context).

        Returns:
            List of extracted facts.

        Raises:
            LLMExtractionError: If LLM call fails or returns invalid JSON.
        """
        if self.llm_queue is not None:
            return await self._extract_facts_via_queue(
                content, categories, profile_name
            )
        else:
            return await self._extract_facts_direct(content, categories, profile_name)

    async def _extract_facts_via_queue(
        self,
        content: str,
        categories: list[str],
        profile_name: str,
    ) -> list[ExtractedFact]:
        """Extract facts via LLM request queue.

        Args:
            content: Markdown content.
            categories: List of allowed categories.
            profile_name: Name of extraction profile.

        Returns:
            List of extracted facts.

        Raises:
            LLMExtractionError: If queue returns error or timeout.
        """
        from services.llm.models import LLMRequest

        # Build prompts (same as direct mode)
        system_prompt = self._build_system_prompt(categories)
        user_prompt = self._build_user_prompt(content)

        # Build request with prompts in payload
        request_timeout = getattr(self.settings, "llm_request_timeout", 300)
        request = LLMRequest(
            request_id=str(uuid4()),
            request_type="extract_facts",
            payload={
                "content": content,
                "categories": categories,
                "profile_name": profile_name,
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "model": self.model,
            },
            priority=5,
            created_at=datetime.now(UTC),
            timeout_at=datetime.now(UTC) + timedelta(seconds=request_timeout),
        )

        logger.info(
            "fact_extraction_queued",
            request_id=request.request_id,
            content_length=len(content),
            categories=categories,
            profile=profile_name,
        )

        # Submit and wait (handle queue errors)
        from services.llm.queue import QueueFullError, RequestTimeoutError

        try:
            await self.llm_queue.submit(request)
            response = await self.llm_queue.wait_for_result(
                request.request_id,
                timeout=request_timeout,
            )
        except QueueFullError as e:
            logger.error("llm_queue_full", request_id=request.request_id, error=str(e))
            raise LLMExtractionError(f"LLM queue full: {e}") from e
        except RequestTimeoutError as e:
            logger.error(
                "llm_request_timeout", request_id=request.request_id, error=str(e)
            )
            raise LLMExtractionError(f"LLM request timeout: {e}") from e

        # Handle response status
        if response.status == "error":
            raise LLMExtractionError(f"LLM extraction failed: {response.error}")
        elif response.status == "timeout":
            raise LLMExtractionError(f"LLM extraction timeout: {response.error}")

        # Parse facts from result
        result_data = response.result or {}
        facts = self._parse_facts_from_result(result_data)

        logger.info(
            "fact_extraction_completed",
            request_id=request.request_id,
            facts_extracted=len(facts),
            processing_time_ms=response.processing_time_ms,
        )

        return facts

    async def _extract_facts_direct(
        self,
        content: str,
        categories: list[str],
        profile_name: str,
    ) -> list[ExtractedFact]:
        """Extract facts via direct LLM call with retry and variation.

        Uses exponential backoff with temperature variation on retries to avoid
        getting stuck in the same failure mode (e.g., hallucination loops).

        Args:
            content: Markdown content.
            categories: List of allowed categories.
            profile_name: Name of extraction profile.

        Returns:
            List of extracted facts.

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
            system_prompt = self._build_system_prompt(categories)
            if attempt > 1:
                system_prompt += "\n\nIMPORTANT: Be concise. Output valid JSON only."

            user_prompt = self._build_user_prompt(content)

            logger.info(
                "llm_extraction_started",
                model=self.model,
                content_length=len(content),
                categories=categories,
                profile=profile_name,
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
                result_data = try_repair_json(
                    result_text, context="extract_facts_direct"
                )

                facts = self._parse_facts_from_result(result_data)

                logger.info(
                    "llm_extraction_completed",
                    model=self.model,
                    facts_extracted=len(facts),
                    attempt=attempt,
                )
                return facts

            except Exception as e:
                last_error = e
                logger.warning(
                    "llm_extraction_attempt_failed",
                    model=self.model,
                    error=str(e),
                    attempt=attempt,
                    max_retries=max_retries,
                )

                if attempt < max_retries:
                    # Exponential backoff: 2^attempt * backoff_min, capped at backoff_max
                    wait_time = min(backoff_min * (2 ** (attempt - 1)), backoff_max)
                    logger.info("llm_retry_backoff", wait_seconds=wait_time)
                    await asyncio.sleep(wait_time)

        # All retries exhausted
        logger.error(
            "llm_extraction_failed_all_retries",
            model=self.model,
            error=str(last_error),
            attempts=max_retries,
        )
        raise LLMExtractionError(
            f"LLM extraction failed after {max_retries} attempts: {last_error}"
        ) from last_error

    def _parse_facts_from_result(self, result_data: dict) -> list[ExtractedFact]:
        """Parse facts from LLM result data.

        Args:
            result_data: Dictionary with 'facts' key.

        Returns:
            List of ExtractedFact objects.
        """
        facts: list[ExtractedFact] = []
        for fact_data in result_data.get("facts", []):
            try:
                fact = ExtractedFact(
                    fact=fact_data["fact"],
                    category=fact_data["category"],
                    confidence=fact_data.get("confidence", 0.8),
                    source_quote=fact_data.get("source_quote"),
                )
                facts.append(fact)
            except (KeyError, TypeError):
                # Skip facts with missing required fields
                continue
        return facts

    def _build_system_prompt(self, categories: list[str]) -> str:
        """Build system prompt for extraction.

        Args:
            categories: List of allowed categories.

        Returns:
            System prompt string.
        """
        categories_str = ", ".join(categories)
        return f"""You are a technical fact extractor. Extract the TOP 10 most important, concrete, verifiable facts from documentation.

Categories: {categories_str}

Output a JSON object with this exact structure:
{{
  "facts": [
    {{
      "fact": "Specific technical statement (concise, max 2 sentences)",
      "category": "one of: {categories_str}",
      "confidence": 0.0-1.0,
      "source_quote": "brief supporting quote (max 50 words)"
    }}
  ]
}}

CRITICAL CONSTRAINTS:
- Extract MAXIMUM 10 facts (prioritize most important/unique information)
- Keep facts concise (1-2 sentences each)
- Keep source_quote brief (max 50 words)
- Must output valid, complete JSON - no truncation allowed

Rules:
- Only extract factual, specific information
- Skip marketing language and vague claims
- Each fact should be self-contained
- Assign confidence based on how explicit the source is
- Include source_quote for attribution"""

    def _build_user_prompt(self, content: str) -> str:
        """Build user prompt with content.

        Args:
            content: Content to extract facts from.

        Returns:
            User prompt string.
        """
        return f"""Extract facts from this documentation:

---
{content}
---"""

    async def extract_entities(
        self,
        extraction_data: dict,
        entity_types: list[dict],
        source_group: str,
    ) -> list[dict]:
        """Extract entities from extraction data using LLM.

        Args:
            extraction_data: Extraction data dictionary.
            entity_types: List of entity type definitions.
            source_group: Source grouping identifier (e.g., company name).

        Returns:
            List of entity dictionaries.

        Raises:
            LLMExtractionError: If LLM call fails.
        """
        if self.llm_queue is not None:
            return await self._extract_entities_via_queue(
                extraction_data, entity_types, source_group
            )
        else:
            return await self._extract_entities_direct(
                extraction_data, entity_types, source_group
            )

    async def _extract_entities_via_queue(
        self,
        extraction_data: dict,
        entity_types: list[dict],
        source_group: str,
    ) -> list[dict]:
        """Extract entities via LLM request queue.

        Args:
            extraction_data: Extraction data dictionary.
            entity_types: List of entity type definitions.
            source_group: Source grouping identifier.

        Returns:
            List of entity dictionaries.

        Raises:
            LLMExtractionError: If queue returns error or timeout.
        """
        from services.llm.models import LLMRequest

        # Build prompts
        prompts = self._build_entity_prompts(
            extraction_data, entity_types, source_group
        )

        # Build request with prompts in payload
        request_timeout = getattr(self.settings, "llm_request_timeout", 300)
        request = LLMRequest(
            request_id=str(uuid4()),
            request_type="extract_entities",
            payload={
                "extraction_data": extraction_data,
                "entity_types": entity_types,
                "source_group": source_group,
                "system_prompt": prompts["system"],
                "user_prompt": prompts["user"],
                "model": self.model,
            },
            priority=5,
            created_at=datetime.now(UTC),
            timeout_at=datetime.now(UTC) + timedelta(seconds=request_timeout),
        )

        logger.info(
            "entity_extraction_queued",
            request_id=request.request_id,
            source_group=source_group,
            entity_types=[et["name"] for et in entity_types],
        )

        # Submit and wait (handle queue errors)
        from services.llm.queue import QueueFullError, RequestTimeoutError

        try:
            await self.llm_queue.submit(request)
            response = await self.llm_queue.wait_for_result(
                request.request_id,
                timeout=request_timeout,
            )
        except QueueFullError as e:
            logger.error("llm_queue_full", request_id=request.request_id, error=str(e))
            raise LLMExtractionError(f"LLM queue full: {e}") from e
        except RequestTimeoutError as e:
            logger.error(
                "llm_request_timeout", request_id=request.request_id, error=str(e)
            )
            raise LLMExtractionError(f"LLM request timeout: {e}") from e

        # Handle response status
        if response.status == "error":
            raise LLMExtractionError(f"Entity extraction failed: {response.error}")
        elif response.status == "timeout":
            raise LLMExtractionError(f"Entity extraction timeout: {response.error}")

        # Return entities
        result = response.result or {}
        entities = result.get("entities", [])

        logger.info(
            "entity_extraction_completed",
            request_id=request.request_id,
            entities_extracted=len(entities),
            processing_time_ms=response.processing_time_ms,
        )

        return entities

    async def _extract_entities_direct(
        self,
        extraction_data: dict,
        entity_types: list[dict],
        source_group: str,
    ) -> list[dict]:
        """Extract entities via direct LLM call with retry and variation.

        Uses exponential backoff with temperature variation on retries to avoid
        getting stuck in the same failure mode.

        Args:
            extraction_data: Extraction data dictionary.
            entity_types: List of entity type definitions.
            source_group: Source grouping identifier.

        Returns:
            List of entity dictionaries.

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
            # Vary temperature on retries
            temperature = base_temp + (attempt - 1) * temp_increment

            # Build prompts (add conciseness hint on retries)
            prompts = self._build_entity_prompts(
                extraction_data, entity_types, source_group
            )
            system_prompt = prompts["system"]
            if attempt > 1:
                system_prompt += "\n\nIMPORTANT: Be concise. Output valid JSON only."

            logger.info(
                "entity_extraction_started",
                model=self.model,
                source_group=source_group,
                entity_types=[et["name"] for et in entity_types],
                attempt=attempt,
                temperature=temperature,
            )

            try:
                response = await self.client.chat.completions.create(
                    model=self.model,
                    messages=[
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": prompts["user"]},
                    ],
                    response_format={"type": "json_object"},
                    temperature=temperature,
                    max_tokens=max_tokens,
                )

                content = response.choices[0].message.content
                parsed = try_repair_json(content, context="extract_entities_direct")
                entities = parsed.get("entities", [])

                logger.info(
                    "entity_extraction_completed",
                    model=self.model,
                    entities_extracted=len(entities),
                    attempt=attempt,
                )

                return entities

            except json.JSONDecodeError as e:
                # JSON parse errors are recoverable - retry with different temperature
                last_error = e
                logger.warning(
                    "entity_extraction_json_parse_failed",
                    error=str(e),
                    attempt=attempt,
                    max_retries=max_retries,
                )
            except Exception as e:
                last_error = e
                logger.warning(
                    "entity_extraction_attempt_failed",
                    model=self.model,
                    error=str(e),
                    attempt=attempt,
                    max_retries=max_retries,
                )

            if attempt < max_retries:
                wait_time = min(backoff_min * (2 ** (attempt - 1)), backoff_max)
                logger.info("llm_retry_backoff", wait_seconds=wait_time)
                await asyncio.sleep(wait_time)

        # All retries exhausted
        logger.error(
            "entity_extraction_failed_all_retries",
            model=self.model,
            error=str(last_error),
            attempts=max_retries,
        )
        raise LLMExtractionError(
            f"Entity extraction failed after {max_retries} attempts: {last_error}"
        ) from last_error

    def _build_entity_prompts(
        self,
        extraction_data: dict,
        entity_types: list[dict],
        source_group: str,
    ) -> dict[str, str]:
        """Build prompts for entity extraction.

        Args:
            extraction_data: Extraction data dictionary.
            entity_types: List of entity type definitions.
            source_group: Source grouping identifier.

        Returns:
            Dictionary with 'system' and 'user' prompts.
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

        system_prompt = f"""Extract entities from this extracted data. Return JSON with entities found.

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

    async def complete(
        self,
        system_prompt: str,
        user_prompt: str,
        response_format: dict | None = None,
        temperature: float | None = None,
    ) -> dict:
        """Generic LLM completion for arbitrary prompts.

        Args:
            system_prompt: System message for the LLM.
            user_prompt: User message/query.
            response_format: Optional response format (e.g., {"type": "json_object"}).
            temperature: Optional temperature override.

        Returns:
            Parsed JSON response as dict.

        Raises:
            LLMExtractionError: If LLM call fails or returns invalid JSON.
        """
        if self.llm_queue is not None:
            return await self._complete_via_queue(
                system_prompt, user_prompt, response_format, temperature
            )
        return await self._complete_direct(
            system_prompt, user_prompt, response_format, temperature
        )

    async def _complete_direct(
        self,
        system_prompt: str,
        user_prompt: str,
        response_format: dict | None = None,
        temperature: float | None = None,
    ) -> dict:
        """Direct LLM completion with retry logic."""
        import json

        max_retries = self.settings.llm_max_retries
        base_temp = temperature or self.settings.llm_base_temperature
        temp_increment = self.settings.llm_retry_temperature_increment

        for attempt in range(1, max_retries + 1):
            # Vary temperature on retries to get different outputs
            current_temp = base_temp + (attempt - 1) * temp_increment

            try:
                kwargs = {
                    "model": self.model,
                    "messages": [
                        {"role": "system", "content": system_prompt},
                        {"role": "user", "content": user_prompt},
                    ],
                    "temperature": current_temp,
                    "max_tokens": self.settings.llm_max_tokens,
                }
                if response_format:
                    kwargs["response_format"] = response_format

                response = await self.client.chat.completions.create(**kwargs)
                content = response.choices[0].message.content

                # Parse as JSON if json_object format requested
                if response_format and response_format.get("type") == "json_object":
                    return try_repair_json(content, context="complete_direct")
                return {"text": content}

            except Exception as e:
                if attempt == max_retries:
                    raise LLMExtractionError(f"LLM completion failed: {e}") from e
                await asyncio.sleep(
                    self.settings.llm_retry_backoff_min * (2 ** (attempt - 1))
                )

    async def _complete_via_queue(
        self,
        system_prompt: str,
        user_prompt: str,
        response_format: dict | None = None,
        temperature: float | None = None,
    ) -> dict:
        """Queue-based LLM completion."""
        from services.llm.models import LLMRequest
        from services.llm.queue import QueueFullError, RequestTimeoutError

        request = LLMRequest(
            request_id=str(uuid4()),
            request_type="complete",
            payload={
                "system_prompt": system_prompt,
                "user_prompt": user_prompt,
                "response_format": response_format,
                "temperature": temperature,
                "model": self.model,
            },
            priority=5,
            created_at=datetime.now(UTC),
            timeout_at=datetime.now(UTC) + timedelta(seconds=300),
        )

        try:
            await self.llm_queue.submit(request)
            response = await self.llm_queue.wait_for_result(
                request.request_id, timeout=300
            )
        except (QueueFullError, RequestTimeoutError) as e:
            raise LLMExtractionError(f"LLM queue error: {e}") from e

        if response.status in ("error", "timeout"):
            raise LLMExtractionError(f"LLM completion failed: {response.error}")

        return response.result or {}
