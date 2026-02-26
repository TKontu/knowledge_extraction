"""LLM-based smart merge service for domain-level aggregation."""

import json
from dataclasses import dataclass, field
from typing import Any

import structlog

from services.llm.client import LLMClient
from services.reports.schema_table_generator import ColumnMetadata

logger = structlog.get_logger(__name__)


@dataclass
class MergeCandidate:
    """A candidate value for merging from a specific source URL."""

    value: Any
    source_url: str
    source_title: str | None
    confidence: float | None


@dataclass
class MergeResult:
    """Result of merging multiple candidates into a single value."""

    value: Any
    confidence: float
    sources_used: list[str] = field(default_factory=list)
    reasoning: str | None = None


class SmartMergeService:
    """Service for intelligently merging column values across URLs using LLM.

    Handles per-column merging for domain-level aggregation. Uses short-circuits
    for trivial cases (all null, single value, all identical) to minimize LLM calls.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        max_candidates: int = 100,
        min_confidence: float = 0.3,
    ):
        """Initialize SmartMergeService.

        Args:
            llm_client: LLM client for merge synthesis.
            max_candidates: Maximum candidates to include in merge prompt.
            min_confidence: Minimum confidence to include candidate.
        """
        self._llm_client = llm_client
        self._max_candidates = max_candidates
        self._min_confidence = min_confidence

    async def merge_column(
        self,
        column_name: str,
        column_meta: ColumnMetadata,
        candidates: list[MergeCandidate],
    ) -> MergeResult:
        """Merge multiple candidate values into a single result.

        Uses short-circuits for trivial cases to minimize LLM calls:
        - All null → return null
        - Single non-null → return it
        - All identical → return without LLM

        Args:
            column_name: Name of the column being merged.
            column_meta: Metadata about the column (type, description).
            candidates: List of candidate values from different URLs.

        Returns:
            MergeResult with synthesized value and provenance.
        """
        # Filter out low-confidence and unknown-confidence candidates
        filtered = [
            c for c in candidates
            if c.confidence is not None and c.confidence >= self._min_confidence
        ]

        # Get non-null values
        non_null = [c for c in filtered if c.value is not None]

        # Short-circuit: all null
        if not non_null:
            return MergeResult(
                value=None,
                confidence=0.0,
                sources_used=[],
                reasoning="All values were null",
            )

        # Short-circuit: single non-null value
        if len(non_null) == 1:
            c = non_null[0]
            return MergeResult(
                value=c.value,
                confidence=c.confidence or 0.8,
                sources_used=[c.source_url],
                reasoning="Single value available",
            )

        # Short-circuit: all identical values
        first_value = non_null[0].value
        all_identical = all(self._values_equal(c.value, first_value) for c in non_null)
        if all_identical:
            # Average confidence, use all sources
            avg_conf = sum(c.confidence or 0.8 for c in non_null) / len(non_null)
            return MergeResult(
                value=first_value,
                confidence=min(1.0, avg_conf + 0.1),  # Boost for agreement
                sources_used=[c.source_url for c in non_null],
                reasoning="All sources agree",
            )

        # Need LLM synthesis - limit candidates
        merge_candidates = non_null[: self._max_candidates]

        try:
            return await self._llm_merge(column_name, column_meta, merge_candidates)
        except Exception as e:
            logger.error(
                "llm_merge_failed",
                column=column_name,
                error=str(e),
                candidate_count=len(merge_candidates),
            )
            # Fallback: return highest confidence value
            best = max(merge_candidates, key=lambda c: c.confidence or 0.0)
            return MergeResult(
                value=best.value,
                confidence=best.confidence or 0.5,
                sources_used=[best.source_url],
                reasoning=f"LLM merge failed, using highest confidence value: {e}",
            )

    async def _llm_merge(
        self,
        column_name: str,
        column_meta: ColumnMetadata,
        candidates: list[MergeCandidate],
    ) -> MergeResult:
        """Use LLM to synthesize the best value from multiple candidates.

        Args:
            column_name: Column name for context.
            column_meta: Column metadata for context.
            candidates: Non-null candidates to merge.

        Returns:
            MergeResult from LLM synthesis.
        """
        # Build prompt
        prompt = self._build_merge_prompt(column_name, column_meta, candidates)

        # Call LLM - complete() returns parsed dict when response_format is json_object
        response = await self._llm_client.complete(
            system_prompt=self._get_system_prompt(),
            user_prompt=prompt,
            response_format={"type": "json_object"},
            temperature=0.1,  # Low temperature for consistency
        )

        # Parse response (already a dict from LLM client)
        return self._parse_merge_response(response, candidates)

    def _build_merge_prompt(
        self,
        column_name: str,
        column_meta: ColumnMetadata,
        candidates: list[MergeCandidate],
    ) -> str:
        """Build the merge prompt for LLM.

        Args:
            column_name: Column name.
            column_meta: Column metadata.
            candidates: Candidates to merge.

        Returns:
            Formatted prompt string.
        """
        # Format candidates
        candidate_lines = []
        for c in candidates:
            title_part = f" ({c.source_title})" if c.source_title else ""
            conf_part = f" [confidence: {c.confidence:.2f}]" if c.confidence is not None else ""
            value_str = json.dumps(c.value) if not isinstance(c.value, str) else c.value
            candidate_lines.append(f"- {c.source_url}{title_part}: {value_str}{conf_part}")

        candidates_text = "\n".join(candidate_lines)

        # Build type hint
        type_hint = f"Expected type: {column_meta.field_type}"
        if column_meta.enum_values:
            type_hint += f" (options: {', '.join(column_meta.enum_values)})"

        return f"""Field: {column_name}
Description: {column_meta.description}
{type_hint}

Values from different pages of the same company website:
{candidates_text}

Synthesize the most reliable value for this field. Consider:
1. Page relevance (product pages are authoritative for specs, about pages for company info)
2. Confidence scores from extraction
3. Agreement across sources (consistency increases reliability)
4. Specificity (prefer concrete values over vague ones)

Return a JSON object with:
- "value": the synthesized value (use appropriate type: boolean, number, string, or array)
- "confidence": your confidence 0.0-1.0
- "sources_used": list of source URLs that contributed to this value
- "reasoning": brief explanation of your synthesis decision"""

    def _get_system_prompt(self) -> str:
        """Get system prompt for merge LLM calls."""
        return """You are a data synthesis expert. You merge extracted data from multiple web pages of the same company into a single reliable value.

Rules:
- Return ONLY valid JSON, no markdown or explanation outside the JSON
- For boolean fields, return true/false (not strings)
- For numeric fields, return numbers (not strings)
- For text fields, synthesize the most complete/accurate text
- For list fields, merge and deduplicate items
- Prefer values from authoritative pages (product specs, about us) over incidental mentions
- Higher confidence scores indicate more reliable extractions"""

    def _parse_merge_response(
        self,
        response: dict,
        candidates: list[MergeCandidate],
    ) -> MergeResult:
        """Parse LLM response into MergeResult.

        Args:
            response: Parsed LLM response dict from LLMClient.complete().
            candidates: Original candidates for fallback.

        Returns:
            Parsed MergeResult.
        """
        try:
            # Validate sources_used is a list
            sources = response.get("sources_used", [])
            if not isinstance(sources, list):
                sources = [sources] if sources else []

            return MergeResult(
                value=response.get("value"),
                confidence=float(response.get("confidence", 0.8)),
                sources_used=sources,
                reasoning=response.get("reasoning"),
            )
        except (KeyError, TypeError, ValueError) as e:
            logger.warning(
                "merge_response_parse_failed",
                error=str(e),
                response=response,
            )
            # Fallback to highest confidence
            best = max(candidates, key=lambda c: c.confidence or 0.0)
            return MergeResult(
                value=best.value,
                confidence=best.confidence or 0.5,
                sources_used=[best.source_url],
                reasoning=f"Failed to parse LLM response: {e}",
            )

    def _values_equal(self, a: Any, b: Any) -> bool:
        """Check if two values are equal, handling various types.

        Args:
            a: First value.
            b: Second value.

        Returns:
            True if values are considered equal.
        """
        if type(a) != type(b):
            return False
        if isinstance(a, list):
            if len(a) != len(b):
                return False
            return all(self._values_equal(x, y) for x, y in zip(a, b))
        if isinstance(a, dict):
            if set(a.keys()) != set(b.keys()):
                return False
            return all(self._values_equal(a[k], b[k]) for k in a)
        return a == b
