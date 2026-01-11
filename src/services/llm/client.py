"""LLM client for fact extraction."""

import json

import structlog
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

from config import Settings
from models import ExtractedFact

logger = structlog.get_logger(__name__)


class LLMClient:
    """Client for interacting with LLM for fact extraction."""

    def __init__(self, settings: Settings):
        """Initialize LLM client.

        Args:
            settings: Application settings.
        """
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
            Exception: If LLM call fails or returns invalid JSON.
        """
        system_prompt = self._build_system_prompt(categories)
        user_prompt = self._build_user_prompt(content)

        logger.info(
            "llm_extraction_started",
            model=self.model,
            content_length=len(content),
            categories=categories,
            profile=profile_name,
        )
        try:
            response = await self.client.chat.completions.create(
                model=self.model,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,  # Low temperature for consistent extraction
            )

            result_text = response.choices[0].message.content
            result_data = json.loads(result_text)

            # Parse facts and filter out incomplete ones
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

            logger.info(
                "llm_extraction_completed", model=self.model, facts_extracted=len(facts)
            )
            return facts
        except Exception as e:
            logger.error(
                "llm_extraction_failed", model=self.model, error=str(e), exc_info=True
            )
            raise

    def _build_system_prompt(self, categories: list[str]) -> str:
        """Build system prompt for extraction.

        Args:
            categories: List of allowed categories.

        Returns:
            System prompt string.
        """
        categories_str = ", ".join(categories)
        return f"""You are a technical fact extractor. Extract concrete, verifiable facts from documentation.

Categories: {categories_str}

Output a JSON object with this exact structure:
{{
  "facts": [
    {{
      "fact": "Specific technical statement",
      "category": "one of: {categories_str}",
      "confidence": 0.0-1.0,
      "source_quote": "brief supporting quote from source"
    }}
  ]
}}

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
