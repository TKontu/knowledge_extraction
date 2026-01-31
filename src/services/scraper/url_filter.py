"""URL relevance filtering using embeddings.

Filters URLs by semantic relevance to extraction context (field_groups).
Uses the same embedding approach as SmartClassifier but for URL metadata.
"""

import math
from dataclasses import dataclass

import structlog

from config import Settings
from services.extraction.field_groups import FieldGroup
from services.storage.embedding import EmbeddingService

logger = structlog.get_logger(__name__)


@dataclass
class FilteredUrl:
    """A URL with relevance scoring.

    Attributes:
        url: The URL string.
        title: Page title from map metadata (may be None).
        description: Page description from map metadata (may be None).
        relevance_score: Cosine similarity score to target context (0.0-1.0).
        is_relevant: Whether the URL passed the relevance threshold.
    """

    url: str
    title: str | None
    description: str | None
    relevance_score: float
    is_relevant: bool


@dataclass
class UrlFilterResult:
    """Result of URL filtering operation.

    Attributes:
        total_urls: Total number of URLs processed.
        relevant_urls: List of URLs that passed the relevance filter.
        filtered_out: Number of URLs filtered out.
        threshold_used: The relevance threshold that was applied.
    """

    total_urls: int
    relevant_urls: list[FilteredUrl]
    filtered_out: int
    threshold_used: float


class UrlRelevanceFilter:
    """Filter URLs by semantic relevance to field groups.

    Uses embeddings to compare URL metadata (title + description) against
    the extraction context derived from field_groups. This enables intelligent
    URL filtering before batch scraping, reducing unnecessary scrape requests.

    Example:
        filter = UrlRelevanceFilter(embedding_service, settings)
        result = await filter.filter_urls(
            urls=[{"url": "...", "title": "...", "description": "..."}],
            field_groups=field_groups,
            focus_terms=["product specifications", "pricing"],
            threshold=0.4,
        )
        for url_info in result.relevant_urls:
            print(f"Relevant: {url_info.url} (score: {url_info.relevance_score})")
    """

    def __init__(
        self,
        embedding_service: EmbeddingService,
        settings: Settings,
    ) -> None:
        """Initialize UrlRelevanceFilter.

        Args:
            embedding_service: Service for generating embeddings.
            settings: Application settings for default threshold.
        """
        self._embedding_service = embedding_service
        self._settings = settings

    async def filter_urls(
        self,
        urls: list[dict],
        field_groups: list[FieldGroup],
        focus_terms: list[str] | None = None,
        threshold: float | None = None,
    ) -> UrlFilterResult:
        """Filter URLs by relevance to field groups.

        Creates a target context embedding from field_groups and focus_terms,
        then compares each URL's metadata (title + description) against it
        using cosine similarity.

        Args:
            urls: List of URL dicts from Map endpoint.
                Each dict should have: url (required), title (optional), description (optional).
            field_groups: Target field groups that define what we're extracting.
                Their descriptions form the "target context" for relevance.
            focus_terms: Additional semantic focus terms to enhance context.
                Useful for domain-specific filtering (e.g., "pricing", "specs").
            threshold: Minimum similarity score for relevance (0.0-1.0).
                Defaults to settings.smart_crawl_default_relevance_threshold.

        Returns:
            UrlFilterResult with relevant URLs and statistics.
        """
        if not urls:
            return UrlFilterResult(
                total_urls=0,
                relevant_urls=[],
                filtered_out=0,
                threshold_used=threshold or self._settings.smart_crawl_default_relevance_threshold,
            )

        if threshold is None:
            threshold = self._settings.smart_crawl_default_relevance_threshold

        logger.info(
            "url_filter_started",
            url_count=len(urls),
            field_group_count=len(field_groups),
            focus_terms=focus_terms,
            threshold=threshold,
        )

        # Step 1: Create target context embedding from field_groups + focus_terms
        context_text = self._create_context_text(field_groups, focus_terms)
        context_embedding = await self._embedding_service.embed(context_text)

        # Step 2: Create URL metadata texts for batch embedding
        url_texts = []
        valid_urls: list[dict] = []

        for url_info in urls:
            url_text = self._create_url_text(url_info)
            if url_text:  # Only embed URLs with some metadata
                url_texts.append(url_text)
                valid_urls.append(url_info)
            else:
                # URLs with no metadata get a pass-through (can't filter without info)
                logger.debug(
                    "url_no_metadata",
                    url=url_info.get("url", "unknown"),
                )

        # Step 3: Batch embed all URL metadata
        url_embeddings: list[list[float]] = []
        if url_texts:
            try:
                url_embeddings = await self._embedding_service.embed_batch(url_texts)
            except Exception as e:
                logger.error(
                    "url_embedding_failed",
                    error=str(e),
                    url_count=len(url_texts),
                )
                # On embedding failure, pass all URLs through
                return UrlFilterResult(
                    total_urls=len(urls),
                    relevant_urls=[
                        FilteredUrl(
                            url=u.get("url", ""),
                            title=u.get("title"),
                            description=u.get("description"),
                            relevance_score=0.5,  # Unknown
                            is_relevant=True,  # Conservative
                        )
                        for u in urls
                    ],
                    filtered_out=0,
                    threshold_used=threshold,
                )

        # Step 4: Calculate similarity scores and filter
        relevant_urls: list[FilteredUrl] = []
        filtered_out = 0

        # Process URLs that had embeddings
        for url_info, url_embedding in zip(valid_urls, url_embeddings, strict=True):
            similarity = self._cosine_similarity(context_embedding, url_embedding)
            is_relevant = similarity >= threshold

            filtered_url = FilteredUrl(
                url=url_info.get("url", ""),
                title=url_info.get("title"),
                description=url_info.get("description"),
                relevance_score=similarity,
                is_relevant=is_relevant,
            )

            if is_relevant:
                relevant_urls.append(filtered_url)
            else:
                filtered_out += 1
                logger.debug(
                    "url_filtered_out",
                    url=filtered_url.url,
                    score=round(similarity, 3),
                    threshold=threshold,
                )

        # Include URLs without metadata (pass-through)
        urls_without_metadata = set(u.get("url") for u in urls) - set(
            u.get("url") for u in valid_urls
        )
        for url_str in urls_without_metadata:
            # Find the original dict
            original = next((u for u in urls if u.get("url") == url_str), None)
            if original:
                relevant_urls.append(
                    FilteredUrl(
                        url=url_str,
                        title=original.get("title"),
                        description=original.get("description"),
                        relevance_score=0.5,  # Unknown - no metadata
                        is_relevant=True,  # Conservative pass-through
                    )
                )

        # Sort by relevance score (highest first)
        relevant_urls.sort(key=lambda x: x.relevance_score, reverse=True)

        logger.info(
            "url_filter_completed",
            total_urls=len(urls),
            relevant_count=len(relevant_urls),
            filtered_out=filtered_out,
            threshold=threshold,
        )

        return UrlFilterResult(
            total_urls=len(urls),
            relevant_urls=relevant_urls,
            filtered_out=filtered_out,
            threshold_used=threshold,
        )

    def _create_context_text(
        self,
        field_groups: list[FieldGroup],
        focus_terms: list[str] | None = None,
    ) -> str:
        """Create target context text from field groups and focus terms.

        Combines field group descriptions and focus terms into a single
        text representation for embedding.

        Args:
            field_groups: Field groups defining extraction targets.
            focus_terms: Additional focus terms.

        Returns:
            Combined context text for embedding.
        """
        parts = []

        # Add focus terms first (high priority context)
        if focus_terms:
            parts.append("Focus: " + ", ".join(focus_terms))
            parts.append("")

        # Add field group descriptions
        if field_groups:
            parts.append("Extraction targets:")
            for group in field_groups:
                # Include group name and description
                parts.append(f"- {group.name}: {group.description}")

                # Include field descriptions for richer context
                field_descs = [f.description for f in group.fields[:5]]  # Limit fields
                if field_descs:
                    parts.append(f"  Fields: {', '.join(field_descs)}")

        return "\n".join(parts)

    def _create_url_text(self, url_info: dict) -> str | None:
        """Create text representation of URL metadata for embedding.

        Args:
            url_info: URL dict with url, title, description keys.

        Returns:
            Text for embedding, or None if no useful metadata.
        """
        parts = []

        title = url_info.get("title")
        description = url_info.get("description")
        url = url_info.get("url", "")

        # Extract path hints from URL
        if url:
            from urllib.parse import urlparse
            parsed = urlparse(url)
            path_parts = [p for p in parsed.path.split("/") if p]
            if path_parts:
                # Use last 2-3 path segments as context
                path_hint = " ".join(path_parts[-3:]).replace("-", " ").replace("_", " ")
                parts.append(f"Path: {path_hint}")

        if title:
            parts.append(f"Title: {title}")

        if description:
            parts.append(f"Description: {description}")

        # Return None if we only have path (not enough context)
        if not title and not description:
            return None

        return "\n".join(parts)

    def _cosine_similarity(
        self,
        vec_a: list[float],
        vec_b: list[float],
    ) -> float:
        """Calculate cosine similarity between two vectors.

        Args:
            vec_a: First vector.
            vec_b: Second vector.

        Returns:
            Cosine similarity score (0.0 to 1.0 for normalized embeddings).
        """
        if len(vec_a) != len(vec_b):
            return 0.0

        dot_product = sum(a * b for a, b in zip(vec_a, vec_b, strict=True))
        magnitude_a = math.sqrt(sum(a * a for a in vec_a))
        magnitude_b = math.sqrt(sum(b * b for b in vec_b))

        if magnitude_a == 0 or magnitude_b == 0:
            return 0.0

        return dot_product / (magnitude_a * magnitude_b)
