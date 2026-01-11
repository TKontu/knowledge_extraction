"""Extraction orchestrator for coordinating fact extraction."""

import time
from uuid import UUID

from models import ExtractedFact, ExtractionProfile, ExtractionResult
from services.llm.chunking import chunk_document


class ExtractionOrchestrator:
    """Orchestrates the extraction process: chunking → LLM → merging."""

    def __init__(self, llm_client):
        """Initialize orchestrator with LLM client.

        Args:
            llm_client: LLM client for fact extraction.
        """
        self._llm_client = llm_client

    async def extract(
        self, page_id: UUID, markdown: str, profile: ExtractionProfile
    ) -> ExtractionResult:
        """Extract facts from markdown content.

        Args:
            page_id: UUID of the page being processed.
            markdown: Markdown content to extract facts from.
            profile: Extraction profile with categories and settings.

        Returns:
            ExtractionResult with extracted facts and metadata.

        Raises:
            Exception: If LLM extraction fails.
        """
        start_time = time.perf_counter()

        # Handle empty content
        if not markdown.strip():
            return ExtractionResult(
                page_id=page_id,
                facts=[],
                chunks_processed=0,
                extraction_time_ms=max(
                    1, int((time.perf_counter() - start_time) * 1000)
                ),
            )

        # Chunk the document
        chunks = chunk_document(markdown)

        # Extract facts from each chunk
        all_facts: list[ExtractedFact] = []
        for chunk in chunks:
            chunk_facts = await self._llm_client.extract_facts(
                content=chunk.content,
                categories=profile.categories,
                profile_name=profile.name,
            )

            # Add header context to facts
            for fact in chunk_facts:
                if chunk.header_path and not fact.header_context:
                    fact.header_context = " > ".join(chunk.header_path)

            all_facts.extend(chunk_facts)

        # Deduplicate exact duplicates
        unique_facts = self._deduplicate_facts(all_facts)

        # Calculate extraction time (at least 1ms to avoid 0)
        extraction_time_ms = max(1, int((time.perf_counter() - start_time) * 1000))

        return ExtractionResult(
            page_id=page_id,
            facts=unique_facts,
            chunks_processed=len(chunks),
            extraction_time_ms=extraction_time_ms,
        )

    def _deduplicate_facts(self, facts: list[ExtractedFact]) -> list[ExtractedFact]:
        """Remove exact duplicate facts.

        Args:
            facts: List of facts potentially containing duplicates.

        Returns:
            List of unique facts.
        """
        seen_facts: set[tuple[str, str]] = set()  # (fact_text, category)
        unique_facts: list[ExtractedFact] = []

        for fact in facts:
            fact_key = (fact.fact, fact.category)
            if fact_key not in seen_facts:
                seen_facts.add(fact_key)
                unique_facts.append(fact)

        return unique_facts
