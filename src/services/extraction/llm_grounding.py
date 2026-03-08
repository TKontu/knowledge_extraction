"""LLM-based grounding verification for extraction fields.

For fields where string-match grounding score is 0.0 but a quote exists,
this module uses an LLM to verify whether the quote actually supports the
claimed value. Catches multilingual quotes, paraphrases, and semantic
mismatches that string-match misses.

Trial-validated: Qwen3-30B achieves 80% detection / 100% recall for
employee counts, 100% detection for product specs.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

import structlog

from services.extraction.grounding import GROUNDING_DEFAULTS, _coerce_quote

if TYPE_CHECKING:
    from services.llm.client import LLMClient

logger = structlog.get_logger(__name__)

_RESCUE_SYSTEM_PROMPT = """You are a source verification assistant. You will be given:
1. A field name and its claimed value (from a data extraction)
2. The source text that the value was supposedly extracted from

Your task: find the EXACT verbatim passage in the source text that supports the claimed value.

Rules:
- The passage must be copied EXACTLY from the source text (verbatim, word-for-word)
- Do NOT paraphrase, summarize, or modify the passage in any way
- The passage should directly contain or strongly imply the claimed value
- If no supporting passage exists in the source text, say found=false

Respond with JSON: {"found": true/false, "quote": "exact verbatim passage from source"}
If not found, respond: {"found": false, "quote": null}"""

_SYSTEM_PROMPT = """You are a fact verification assistant. You will be given:
1. A field name and its claimed value (from a data extraction)
2. A verbatim quote from the source document

Your task: determine whether the quote SUPPORTS the claimed value.

Rules:
- "Supported" means the quote contains information that directly confirms the value
- Numbers must match: "35 employees" does NOT support employee_count=5000
- Unit conversions are NOT supported: "40HP" does NOT support power_rating_kw=29.8
- Different categories are NOT supported: "€1 Billion revenue" does NOT support employee_count=1000
- Multilingual is OK: "30 mil colaboradores" DOES support employee_count=30000
- Approximate matches are OK: "over 140,000" DOES support employee_count=140000

Respond with JSON: {"supported": true/false, "reason": "brief explanation"}"""


@dataclass(frozen=True)
class RescueResult:
    """Result of an LLM rescue attempt for a borderline-grounded field."""

    quote: str | None  # Rescued verbatim quote, or None if not found
    grounding: float  # Re-verified grounding score (0.0 if not found)
    latency: float


@dataclass(frozen=True)
class LLMGroundingResult:
    """Result of LLM grounding verification for a single field."""

    supported: bool | None  # True/False/None (error)
    reason: str
    latency: float


class LLMGroundingVerifier:
    """Verifies extraction quotes against claimed values using LLM.

    Only called for fields where:
    - grounding_mode == "required"
    - string-match score == 0.0
    - quote exists and is non-empty
    - field_type is NOT boolean (35% false rejection rate in trials)
    """

    def __init__(
        self,
        llm_client: LLMClient,
    ):
        self._llm = llm_client

    async def verify_quote(
        self,
        field_name: str,
        value: Any,
        quote: str,
    ) -> LLMGroundingResult:
        """Ask LLM: does this quote support this claimed value?"""
        user_prompt = (
            f"Field: {field_name}\n"
            f"Claimed value: {value}\n"
            f'Quote from source: "{quote}"\n\n'
            f"Does this quote support the claimed value?"
        )

        start = time.monotonic()
        try:
            response = await self._llm.complete(
                system_prompt=_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_format={"type": "json_object"},
                temperature=0.0,
            )
            latency = time.monotonic() - start

            supported = response.get("supported")
            reason = response.get("reason", "")

            if not isinstance(supported, bool):
                return LLMGroundingResult(
                    supported=None,
                    reason=f"Malformed LLM response: {response}",
                    latency=latency,
                )

            return LLMGroundingResult(
                supported=bool(supported),
                reason=str(reason),
                latency=latency,
            )

        except Exception as e:
            latency = time.monotonic() - start
            logger.warning(
                "llm_grounding_error",
                field=field_name,
                value=value,
                error=str(e),
            )
            return LLMGroundingResult(
                supported=None,
                reason=f"LLM error: {e}",
                latency=latency,
            )

    async def rescue_quote(
        self,
        field_name: str,
        value: Any,
        source_content: str,
    ) -> "RescueResult":
        """Ask LLM to find the exact verbatim passage supporting a value.

        Used for borderline grounding scores (0.3-0.8) where string-match
        may have false-positived. The LLM searches the source text for
        the actual supporting passage, then we re-verify it.

        Args:
            field_name: Name of the field being rescued.
            value: The extracted value to find support for.
            source_content: Source text to search in (truncated to ~16K chars).

        Returns:
            RescueResult with rescued quote and re-verified grounding score.
        """
        from services.extraction.grounding import verify_quote_in_source

        # Truncate source to keep prompt within LLM context budget.
        # Chunks are ~20K chars (~5K tokens). 16K chars (~4K tokens) is safe
        # for any model while covering most of the chunk content.
        truncated_source = source_content[:16000] if source_content else ""
        if not truncated_source:
            return RescueResult(quote=None, grounding=0.0, latency=0.0)

        user_prompt = (
            f"Field: {field_name}\n"
            f"Claimed value: {value}\n\n"
            f"Source text:\n{truncated_source}\n\n"
            f"Find the exact verbatim passage in the source text that supports "
            f"this value. If no supporting passage exists, say so."
        )

        start = time.monotonic()
        try:
            response = await self._llm.complete(
                system_prompt=_RESCUE_SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_format={"type": "json_object"},
                temperature=0.0,
            )
            latency = time.monotonic() - start

            found = response.get("found", False)
            rescued_quote = response.get("quote")

            if not found or not rescued_quote or not isinstance(rescued_quote, str):
                return RescueResult(quote=None, grounding=0.0, latency=latency)

            # Re-verify the rescued quote actually exists in source
            re_score = verify_quote_in_source(rescued_quote, source_content)
            if re_score < 0.8:
                logger.info(
                    "rescue_quote_reverify_failed",
                    field=field_name,
                    value=value,
                    re_score=re_score,
                    latency=latency,
                )
                return RescueResult(quote=None, grounding=0.0, latency=latency)

            logger.info(
                "rescue_quote_success",
                field=field_name,
                value=value,
                re_score=re_score,
                latency=latency,
            )
            return RescueResult(
                quote=rescued_quote,
                grounding=re_score,
                latency=latency,
            )

        except Exception as e:
            latency = time.monotonic() - start
            logger.warning(
                "rescue_quote_error",
                field=field_name,
                value=value,
                error=str(e),
            )
            return RescueResult(quote=None, grounding=0.0, latency=latency)

    async def verify_extraction(
        self,
        data: dict,
        grounding_scores: dict[str, float],
        field_types: dict[str, str],
    ) -> dict[str, float]:
        """Verify all unresolved fields in an extraction via LLM.

        Only verifies fields where:
        - grounding_scores[field] == 0.0
        - a non-empty quote exists in data["_quotes"]
        - field type is not boolean or text

        Args:
            data: Extraction data dict (includes _quotes).
            grounding_scores: Current string-match scores.
            field_types: Map of field_name -> type.

        Returns:
            Updated copy of grounding_scores with LLM results applied.
        """
        updated = dict(grounding_scores)
        quotes = data.get("_quotes", {}) or {}

        for field_name, score in grounding_scores.items():
            # Only verify fields that string-match couldn't resolve
            if score >= 0.5:
                continue

            # Must have a quote to verify against
            quote = _coerce_quote(quotes.get(field_name))
            if not quote:
                continue

            # Skip boolean fields (35% false rejection in trials)
            field_type = field_types.get(field_name, "string")
            grounding_mode = GROUNDING_DEFAULTS.get(field_type, "required")
            if grounding_mode != "required":
                continue

            value = data.get(field_name)
            if value is None:
                continue

            result = await self.verify_quote(field_name, value, quote)

            if result.supported is True:
                updated[field_name] = 1.0
                logger.info(
                    "llm_grounding_verified",
                    field=field_name,
                    value=value,
                    reason=result.reason,
                    latency=result.latency,
                )
            elif result.supported is False:
                # Keep at 0.0 — LLM confirmed it's not grounded
                logger.info(
                    "llm_grounding_rejected",
                    field=field_name,
                    value=value,
                    reason=result.reason,
                    latency=result.latency,
                )
            # supported=None (error) → leave score unchanged

        return updated
