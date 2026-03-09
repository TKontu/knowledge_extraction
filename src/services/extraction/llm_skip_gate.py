"""LLM-based skip-gate for filtering irrelevant pages before extraction.

Binary classifier: given a page and extraction schema, decides "extract" or "skip".
Uses a small/fast LLM (e.g., gemma3-4B) to avoid wasting expensive extraction calls
on pages with no relevant data.

Safety: defaults to "extract" on any error, ambiguity, or missing input.
"""

import json
import time
from dataclasses import dataclass

import structlog

logger = structlog.get_logger(__name__)

SYSTEM_PROMPT = """You classify web pages for a structured data extraction pipeline.

You receive an extraction schema describing target data types, plus a web page.

Decision rules:
- "extract" = page contains data matching ANY field group in the schema
- "skip" = page has NO matching data (wrong industry, empty, navigation-only,
  login, job listings, holiday notices, legal/privacy, forum index)

When genuinely uncertain, prefer "extract" — missing data costs more than
a wasted extraction call.

Output JSON only: {"decision": "extract" or "skip"}"""

USER_TEMPLATE = """EXTRACTION SCHEMA:
{schema_summary}

PAGE:
URL: {url}
Title: {title}

Content:
{content}

Should this page be extracted or skipped? JSON only:"""


@dataclass(frozen=True)
class SkipGateResult:
    """Result of LLM skip-gate classification."""

    decision: str  # "extract" or "skip"
    confidence: float  # from LLM metadata (not used for gating)
    latency: float
    method: str = "llm_skip_gate"


class LLMSkipGate:
    """Binary LLM classifier: should this page be extracted or skipped?"""

    def __init__(self, llm_client, content_limit: int = 2000):
        self._llm = llm_client
        self._content_limit = content_limit

    async def should_extract(
        self,
        url: str,
        title: str | None,
        content: str,
        schema: dict,
    ) -> SkipGateResult:
        """Classify whether a page should be extracted.

        Args:
            url: Page URL.
            title: Page title.
            content: Page content (markdown).
            schema: Extraction schema dict with field_groups.

        Returns:
            SkipGateResult with decision ("extract" or "skip").
        """
        start = time.monotonic()

        # Safety: no schema → extract everything
        if not schema:
            return SkipGateResult(
                decision="extract", confidence=0.0,
                latency=time.monotonic() - start,
            )

        # Safety: very short content → extract (may be truncated)
        if len(content.strip()) < 100:
            return SkipGateResult(
                decision="extract", confidence=0.0,
                latency=time.monotonic() - start,
            )

        schema_summary = build_schema_summary(schema)
        sampled = _sample_content(content, self._content_limit)
        user_prompt = USER_TEMPLATE.format(
            schema_summary=schema_summary,
            url=url,
            title=title or "(no title)",
            content=sampled,
        )

        try:
            response = await self._llm.complete(
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                response_format={"type": "json_object"},
                temperature=0.0,
            )
            latency = time.monotonic() - start

            # response is a dict from json_object mode
            if isinstance(response, dict):
                decision = _parse_decision_from_dict(response)
            else:
                decision = _parse_decision(str(response))

            logger.debug(
                "skip_gate_result",
                url=url,
                decision=decision,
                latency=f"{latency:.2f}s",
            )
            return SkipGateResult(
                decision=decision, confidence=0.0, latency=latency,
            )

        except Exception:
            latency = time.monotonic() - start
            logger.warning(
                "skip_gate_error_defaulting_extract",
                url=url,
                latency=f"{latency:.2f}s",
                exc_info=True,
            )
            return SkipGateResult(
                decision="extract", confidence=0.0, latency=latency,
            )


def _sample_content(content: str, limit: int) -> str:
    """Sample content from beginning, middle, and end.

    Instead of just the first N chars (which may be boilerplate/navigation),
    take roughly equal portions from three positions so the LLM sees a
    representative slice of the page.
    """
    if len(content) <= limit:
        return content

    # Split budget: 40% start, 30% middle, 30% end
    start_size = int(limit * 0.4)
    mid_size = int(limit * 0.3)
    end_size = limit - start_size - mid_size

    start_part = content[:start_size]

    mid_point = len(content) // 2
    mid_start = mid_point - mid_size // 2
    mid_part = content[mid_start : mid_start + mid_size]

    end_part = content[-end_size:]

    return f"{start_part}\n[...]\n{mid_part}\n[...]\n{end_part}"


def _parse_decision_from_dict(d: dict) -> str:
    """Parse decision from a parsed JSON dict."""
    val = d.get("decision", "")
    if isinstance(val, str) and val.strip().lower() == "skip":
        return "skip"
    return "extract"


def _parse_decision(text: str) -> str:
    """Parse LLM response text into 'extract' or 'skip'. Default: 'extract'."""
    text = text.strip()
    # Handle thinking tags
    if "<think>" in text:
        idx = text.rfind("</think>")
        if idx != -1:
            text = text[idx + 8 :].strip()
    # Handle markdown fences
    if text.startswith("```"):
        text = text.split("\n", 1)[1] if "\n" in text else text[3:]
        text = text.rsplit("```", 1)[0].strip()
    # Try JSON
    try:
        d = json.loads(text)
        if isinstance(d, dict):
            return _parse_decision_from_dict(d)
        return "extract"
    except (json.JSONDecodeError, AttributeError):
        pass
    # Keyword fallback
    if '"skip"' in text.lower() or "'skip'" in text.lower():
        return "skip"
    return "extract"


def build_schema_summary(schema: dict) -> str:
    """Build a human-readable schema summary from extraction_schema.

    Auto-generates from field_groups so it works for any template
    (drivetrain, recipes, jobs, etc.).
    """
    field_groups = schema.get("field_groups", [])
    if not field_groups:
        return ""

    # Extract entity type from schema metadata if available
    entity_type = schema.get("entity_type", "")
    domain = schema.get("domain", "")

    lines = []
    if domain:
        lines.append(f"Data domain: {domain}")
    if entity_type:
        lines.append(f"Entity type: {entity_type}")
    if lines:
        lines.append("")

    lines.append("Target field groups:")
    for fg in field_groups:
        name = fg.get("name", "unknown")
        desc = fg.get("description", "")
        lines.append(f"  {name}: {desc}" if desc else f"  {name}")

        # Show key fields (first few)
        fields = fg.get("fields", [])
        if fields:
            field_names = [f.get("name", "") for f in fields[:4] if f.get("name")]
            if field_names:
                lines.append(f"    Key fields: {', '.join(field_names)}")

    return "\n".join(lines)
