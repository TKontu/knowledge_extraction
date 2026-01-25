"""LLM-based synthesis for report generation."""

from dataclasses import dataclass

import structlog

from services.llm.client import LLMClient, LLMExtractionError

logger = structlog.get_logger(__name__)


@dataclass
class SynthesisResult:
    """Result from LLM synthesis."""

    synthesized_text: str
    sources_used: list[str]  # URIs
    confidence: float
    conflicts_noted: list[str]


@dataclass
class MergeResult:
    """Result from field value merging."""

    value: str | int | float | bool | list
    sources: list[str]  # URIs
    confidence: float


class ReportSynthesizer:
    """LLM-based synthesis for report generation."""

    # Max extractions per synthesis call to avoid token limits
    MAX_FACTS_PER_SYNTHESIS = 15

    def __init__(self, llm_client: LLMClient):
        self._llm = llm_client

    async def synthesize_facts(
        self,
        facts: list[dict],  # Each has: data, confidence, source_uri, source_title
        synthesis_type: str = "summarize",  # "summarize", "compare", "aggregate"
    ) -> SynthesisResult:
        """
        Synthesize multiple facts into coherent output with attribution.

        Args:
            facts: List of extraction dicts with source info
            synthesis_type: How to combine - summarize, compare, or aggregate

        Returns:
            SynthesisResult with combined text and source attribution
        """
        if not facts:
            return SynthesisResult(
                synthesized_text="No facts available.",
                sources_used=[],
                confidence=0.0,
                conflicts_noted=[],
            )

        # Chunk if too many facts
        if len(facts) > self.MAX_FACTS_PER_SYNTHESIS:
            return await self._synthesize_chunked(facts, synthesis_type)

        # Build facts text for prompt
        facts_text = self._format_facts_for_prompt(facts)

        # System prompt is static (cacheable by LLM providers)
        system_prompt = """You are synthesizing extracted facts from multiple source documents.

Instructions:
1. Combine related facts into coherent statements
2. When facts conflict, note the discrepancy and prefer higher confidence sources
3. Preserve key details and specifics from each source
4. Include source attribution in brackets [Source: page_title]

Output as JSON:
{
  "synthesized_text": "Combined fact with [Source: title] attribution...",
  "sources_used": ["uri1", "uri2"],
  "confidence": 0.85,
  "conflicts_noted": ["description of any conflicts found"]
}"""

        # User prompt contains the variable content (facts)
        user_prompt = f"""Synthesize these facts using '{synthesis_type}' approach.

Facts to synthesize:
{facts_text}"""

        try:
            result = await self._llm.complete(
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                response_format={"type": "json_object"},
            )
            return SynthesisResult(
                synthesized_text=result.get("synthesized_text", ""),
                sources_used=result.get("sources_used", []),
                confidence=result.get("confidence", 0.8),
                conflicts_noted=result.get("conflicts_noted", []),
            )
        except LLMExtractionError as e:
            logger.warning("llm_synthesis_failed", error=str(e))
            return self._fallback_synthesis(facts)

    async def _synthesize_chunked(
        self,
        facts: list[dict],
        synthesis_type: str,
    ) -> SynthesisResult:
        """Synthesize large fact sets by chunking."""
        chunks = [
            facts[i : i + self.MAX_FACTS_PER_SYNTHESIS]
            for i in range(0, len(facts), self.MAX_FACTS_PER_SYNTHESIS)
        ]

        # Synthesize each chunk
        chunk_results = []
        for chunk in chunks:
            result = await self.synthesize_facts(chunk, synthesis_type)
            chunk_results.append(result)

        # Combine chunk results
        all_text = "\n\n".join(r.synthesized_text for r in chunk_results)
        all_sources = list(set(s for r in chunk_results for s in r.sources_used))
        all_conflicts = [c for r in chunk_results for c in r.conflicts_noted]
        avg_confidence = (
            sum(r.confidence for r in chunk_results) / len(chunk_results)
            if chunk_results
            else 0.0
        )

        return SynthesisResult(
            synthesized_text=all_text,
            sources_used=all_sources,
            confidence=avg_confidence,
            conflicts_noted=all_conflicts,
        )

    def _fallback_synthesis(self, facts: list[dict]) -> SynthesisResult:
        """Rule-based fallback when LLM fails."""
        # Join facts with bullet points
        lines = []
        sources = set()
        for fact in facts:
            data = fact.get("data", {})
            text = data.get("fact", str(data))
            source_title = fact.get("source_title", "Unknown")
            lines.append(f"- {text} [Source: {source_title}]")
            if fact.get("source_uri"):
                sources.add(fact["source_uri"])

        return SynthesisResult(
            synthesized_text="\n".join(lines),
            sources_used=list(sources),
            confidence=0.7,
            conflicts_noted=["Fallback: LLM synthesis unavailable"],
        )

    def _format_facts_for_prompt(self, facts: list[dict]) -> str:
        """Format facts for LLM prompt."""
        lines = []
        for i, fact in enumerate(facts, 1):
            data = fact.get("data", {})
            text = data.get("fact", str(data))
            confidence = fact.get("confidence", 0.8)
            source = fact.get("source_title", "Unknown")
            uri = fact.get("source_uri", "")
            lines.append(
                f'{i}. "{text}" (confidence: {confidence:.2f}, source: {source}, uri: {uri})'
            )
        return "\n".join(lines)

    async def merge_field_values(
        self,
        field_name: str,
        values: list[dict],  # Each has: value, source_uri, confidence
        field_type: str,  # "boolean", "text", "number", "list"
    ) -> MergeResult:
        """
        Merge values for a single field with LLM intelligence.

        For simple types (boolean, number), use rule-based logic.
        For text fields, use LLM to synthesize intelligently.
        For lists, deduplicate then optionally summarize if too long.
        """
        if not values:
            return MergeResult(value=None, sources=[], confidence=0.0)

        sources = [v.get("source_uri") for v in values if v.get("source_uri")]

        if field_type == "boolean":
            # Use any() - True if ANY source says True
            merged_val = any(v.get("value") for v in values)
            return MergeResult(
                value=merged_val,
                sources=sources,
                confidence=max(v.get("confidence", 0.8) for v in values),
            )

        elif field_type in ("number", "integer", "float"):
            # Use max
            numeric_vals = [
                v.get("value") for v in values if v.get("value") is not None
            ]
            merged_val = max(numeric_vals) if numeric_vals else None
            return MergeResult(
                value=merged_val,
                sources=sources,
                confidence=max(v.get("confidence", 0.8) for v in values),
            )

        elif field_type == "list":
            # Deduplicate
            flat = []
            for v in values:
                val = v.get("value", [])
                if isinstance(val, list):
                    flat.extend(val)
                else:
                    flat.append(val)
            unique = list(dict.fromkeys(str(x) for x in flat))
            return MergeResult(
                value=unique,
                sources=sources,
                confidence=0.9,
            )

        else:
            # Text - use LLM synthesis
            return await self._synthesize_text(field_name, values)

    async def _synthesize_text(
        self,
        field_name: str,
        texts: list[dict],
    ) -> MergeResult:
        """Use LLM to intelligently combine text values."""
        unique_texts = list(
            dict.fromkeys(str(t.get("value", "")) for t in texts if t.get("value"))
        )

        # If only one unique value, just return it
        if len(unique_texts) <= 1:
            return MergeResult(
                value=unique_texts[0] if unique_texts else None,
                sources=[t.get("source_uri") for t in texts if t.get("source_uri")],
                confidence=0.95,
            )

        # Multiple unique values - use LLM to merge
        texts_formatted = "\n".join(
            f'- "{t.get("value")}" (confidence: {t.get("confidence", 0.8):.2f}, source: {t.get("source_uri", "unknown")})'
            for t in texts
        )

        system_prompt = f"""Merge these text values for the field "{field_name}":

{texts_formatted}

Instructions:
- Combine into a single coherent text
- Preserve important details from each source
- If values conflict, prefer higher confidence sources
- Keep the result concise

Output as JSON:
{{
  "merged_text": "Combined text here",
  "sources_used": ["uri1", "uri2"],
  "confidence": 0.9
}}"""

        try:
            result = await self._llm.complete(
                system_prompt=system_prompt,
                user_prompt="Merge these text values.",
                response_format={"type": "json_object"},
            )
            return MergeResult(
                value=result.get("merged_text", unique_texts[0]),
                sources=result.get("sources_used", []),
                confidence=result.get("confidence", 0.85),
            )
        except LLMExtractionError as e:
            logger.warning("llm_text_merge_failed", field=field_name, error=str(e))
            # Fallback: take longest
            longest = max(unique_texts, key=len) if unique_texts else None
            return MergeResult(
                value=longest,
                sources=[t.get("source_uri") for t in texts if t.get("source_uri")],
                confidence=0.7,
            )
