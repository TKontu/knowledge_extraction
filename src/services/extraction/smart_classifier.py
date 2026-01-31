"""Smart page classifier using embeddings and reranker."""

import hashlib
import json
import math
from dataclasses import dataclass

import redis.asyncio as aioredis
import structlog

from config import Settings
from services.extraction.field_groups import FieldGroup
from services.extraction.page_classifier import (
    ClassificationMethod,
    ClassificationResult,
    PageClassifier,
)
from services.storage.embedding import EmbeddingService

logger = structlog.get_logger(__name__)


@dataclass
class SmartClassificationResult(ClassificationResult):
    """Extended classification result with smart classifier details."""

    embedding_scores: dict[str, float] | None = None  # group_name -> similarity score
    reranker_scores: dict[str, float] | None = None  # group_name -> reranker score


class SmartClassifier:
    """Embedding + reranker based page classification.

    Hybrid classification flow:
    1. Check rule-based skip patterns first (fast path for irrelevant pages)
    2. Embed page content and compare to cached field group embeddings
    3. High confidence (>0.75): Use matched groups directly
    4. Medium confidence (0.4-0.75): Use reranker to confirm relevance
    5. Low confidence (<0.4): Use all groups (conservative)
    """

    # Cache key prefix for field group embeddings
    CACHE_KEY_PREFIX = "classification:fg_embed:"

    def __init__(
        self,
        embedding_service: EmbeddingService,
        redis_client: aioredis.Redis,
        settings: Settings,
    ):
        """Initialize SmartClassifier.

        Args:
            embedding_service: Service for generating embeddings.
            redis_client: Async Redis client for caching.
            settings: Application settings.
        """
        self._embedding_service = embedding_service
        self._redis = redis_client
        self._settings = settings
        # Rule-based classifier for skip pattern detection
        self._rule_classifier = PageClassifier(
            method=ClassificationMethod.RULE_BASED,
        )

    async def classify(
        self,
        url: str,
        title: str | None,
        content: str,
        field_groups: list[FieldGroup],
    ) -> ClassificationResult:
        """Classify page using embeddings and reranker.

        Args:
            url: Page URL.
            title: Page title (optional).
            content: Page markdown content.
            field_groups: Available field groups to classify against.

        Returns:
            ClassificationResult with relevant groups and confidence.
        """
        # Step 1: Check rule-based skip patterns first (fast path)
        rule_result = self._rule_classifier.classify(url, title)
        if rule_result.skip_extraction:
            logger.debug(
                "smart_classifier_skip",
                url=url,
                reason=rule_result.reasoning,
            )
            return rule_result

        # If smart classification is disabled, fall back to rule-based
        if not self._settings.smart_classification_enabled:
            return rule_result

        # Step 2: Try embedding-based classification
        try:
            return await self._classify_with_embeddings(
                url=url,
                title=title,
                content=content,
                field_groups=field_groups,
            )
        except Exception as e:
            logger.warning(
                "smart_classifier_fallback",
                error=str(e),
                url=url,
            )
            # Fall back to rule-based on any error
            return ClassificationResult(
                page_type="general",
                relevant_groups=[],
                skip_extraction=False,
                confidence=0.5,
                method=ClassificationMethod.RULE_BASED,
                reasoning=f"Smart classification failed: {e}. Using all groups.",
            )

    async def _classify_with_embeddings(
        self,
        url: str,
        title: str | None,
        content: str,
        field_groups: list[FieldGroup],
    ) -> ClassificationResult:
        """Perform embedding-based classification.

        Args:
            url: Page URL.
            title: Page title.
            content: Page markdown content.
            field_groups: Field groups to classify against.

        Returns:
            ClassificationResult based on embedding similarity.
        """
        if not field_groups:
            return ClassificationResult(
                page_type="general",
                relevant_groups=[],
                skip_extraction=False,
                confidence=0.5,
                method=ClassificationMethod.HYBRID,
                reasoning="No field groups provided",
            )

        # Get embeddings for all field groups (cached)
        group_embeddings = await self._get_field_group_embeddings(field_groups)

        # Create page summary for embedding
        page_summary = self._create_page_summary(url, title, content)

        # Embed page content
        page_embedding = await self._embedding_service.embed(page_summary)

        # Calculate similarity scores
        scores: dict[str, float] = {}
        for group in field_groups:
            if group.name in group_embeddings:
                similarity = self._cosine_similarity(
                    page_embedding, group_embeddings[group.name]
                )
                scores[group.name] = similarity

        if not scores:
            return ClassificationResult(
                page_type="general",
                relevant_groups=[],
                skip_extraction=False,
                confidence=0.5,
                method=ClassificationMethod.HYBRID,
                reasoning="Could not compute similarity scores",
            )

        # Determine classification based on scores
        high_threshold = self._settings.classification_embedding_high_threshold
        low_threshold = self._settings.classification_embedding_low_threshold

        max_score = max(scores.values())
        high_confidence_groups = [
            name for name, score in scores.items() if score >= high_threshold
        ]

        # High confidence: use matched groups directly
        if high_confidence_groups:
            logger.info(
                "smart_classifier_high_confidence",
                url=url,
                groups=high_confidence_groups,
                max_score=max_score,
            )
            return SmartClassificationResult(
                page_type=self._infer_page_type(high_confidence_groups),
                relevant_groups=high_confidence_groups,
                skip_extraction=False,
                confidence=max_score,
                method=ClassificationMethod.HYBRID,
                reasoning=f"High embedding similarity (>={high_threshold})",
                embedding_scores=scores,
                reranker_scores=None,
            )

        # Low confidence: use all groups (conservative)
        if max_score < low_threshold:
            logger.info(
                "smart_classifier_low_confidence",
                url=url,
                max_score=max_score,
            )
            return SmartClassificationResult(
                page_type="general",
                relevant_groups=[],  # Empty = use all groups
                skip_extraction=False,
                confidence=max_score,
                method=ClassificationMethod.HYBRID,
                reasoning=f"Low embedding similarity (<{low_threshold}), using all groups",
                embedding_scores=scores,
                reranker_scores=None,
            )

        # Medium confidence: use reranker to confirm
        return await self._rerank_groups(
            url=url,
            content=content,
            field_groups=field_groups,
            embedding_scores=scores,
        )

    async def _rerank_groups(
        self,
        url: str,
        content: str,
        field_groups: list[FieldGroup],
        embedding_scores: dict[str, float],
    ) -> ClassificationResult:
        """Use reranker to confirm field group relevance.

        Args:
            url: Page URL.
            content: Page content for reranking.
            field_groups: Field groups to rerank.
            embedding_scores: Pre-computed embedding similarity scores.

        Returns:
            ClassificationResult with reranker-confirmed groups.
        """
        # Create documents for reranking (field group descriptions)
        group_texts = [self._create_group_text(g) for g in field_groups]
        group_names = [g.name for g in field_groups]

        # Truncate content for reranking at word boundary
        query = self._truncate_at_word_boundary(content, 2000)

        try:
            rerank_results = await self._embedding_service.rerank(
                query=query,
                documents=group_texts,
                model=self._settings.reranker_model,
            )
        except Exception as e:
            logger.warning(
                "reranker_failed",
                url=url,
                error=str(e),
            )
            # Fall back to medium-confidence groups based on embedding scores
            threshold = self._settings.classification_embedding_low_threshold
            medium_groups = [
                name for name, score in embedding_scores.items() if score >= threshold
            ]
            return SmartClassificationResult(
                page_type=self._infer_page_type(medium_groups),
                relevant_groups=medium_groups if medium_groups else [],
                skip_extraction=False,
                confidence=max(embedding_scores.values()) if embedding_scores else 0.5,
                method=ClassificationMethod.HYBRID,
                reasoning=f"Reranker failed, using embedding scores: {e}",
                embedding_scores=embedding_scores,
                reranker_scores=None,
            )

        # Build reranker scores map
        reranker_scores: dict[str, float] = {}
        for idx, score in rerank_results:
            if idx < len(group_names):
                reranker_scores[group_names[idx]] = score

        # Select groups above reranker threshold
        reranker_threshold = self._settings.classification_reranker_threshold
        confirmed_groups = [
            name for name, score in reranker_scores.items() if score >= reranker_threshold
        ]

        logger.info(
            "smart_classifier_reranked",
            url=url,
            confirmed_groups=confirmed_groups,
            reranker_scores=reranker_scores,
        )

        if not confirmed_groups:
            # No groups above threshold - use all groups (conservative)
            return SmartClassificationResult(
                page_type="general",
                relevant_groups=[],
                skip_extraction=False,
                confidence=max(reranker_scores.values()) if reranker_scores else 0.5,
                method=ClassificationMethod.HYBRID,
                reasoning=f"Reranker scores below threshold ({reranker_threshold}), using all groups",
                embedding_scores=embedding_scores,
                reranker_scores=reranker_scores,
            )

        max_rerank_score = max(
            reranker_scores[g] for g in confirmed_groups
        )
        return SmartClassificationResult(
            page_type=self._infer_page_type(confirmed_groups),
            relevant_groups=confirmed_groups,
            skip_extraction=False,
            confidence=max_rerank_score,
            method=ClassificationMethod.HYBRID,
            reasoning=f"Reranker confirmed groups (>={reranker_threshold})",
            embedding_scores=embedding_scores,
            reranker_scores=reranker_scores,
        )

    async def _get_field_group_embeddings(
        self,
        field_groups: list[FieldGroup],
    ) -> dict[str, list[float]]:
        """Get embeddings for field groups (cached in Redis).

        Uses batch mget for efficient cache retrieval.

        Args:
            field_groups: Field groups to embed.

        Returns:
            Dictionary mapping group name to embedding vector.
        """
        embeddings: dict[str, list[float]] = {}
        groups_to_embed: list[FieldGroup] = []

        # Build cache keys for all groups
        cache_keys = [self._get_cache_key(g) for g in field_groups]

        # Batch fetch from cache using mget
        cached_values: list[str | None] = []
        try:
            cached_values = await self._redis.mget(cache_keys)
        except Exception as e:
            logger.debug(
                "cache_batch_read_failed",
                error=str(e),
            )
            # Fall back to embedding all groups
            cached_values = [None] * len(field_groups)

        # Process cache results
        for group, cached in zip(field_groups, cached_values):
            if cached:
                try:
                    embeddings[group.name] = json.loads(cached)
                    continue
                except json.JSONDecodeError:
                    pass  # Fall through to embed
            groups_to_embed.append(group)

        # Embed uncached groups
        if groups_to_embed:
            texts = [self._create_group_text(g) for g in groups_to_embed]
            new_embeddings = await self._embedding_service.embed_batch(texts)

            for group, embedding in zip(groups_to_embed, new_embeddings):
                embeddings[group.name] = embedding

                # Cache the embedding
                cache_key = self._get_cache_key(group)
                try:
                    await self._redis.setex(
                        cache_key,
                        self._settings.classification_cache_ttl,
                        json.dumps(embedding),
                    )
                except Exception as e:
                    logger.debug(
                        "cache_write_failed",
                        group=group.name,
                        error=str(e),
                    )

        return embeddings

    def _get_cache_key(self, group: FieldGroup) -> str:
        """Generate cache key for field group embedding.

        Uses SHA256 hash of group text for stable keys.

        Args:
            group: Field group to generate key for.

        Returns:
            Cache key string.
        """
        group_text = self._create_group_text(group)
        text_hash = hashlib.sha256(group_text.encode()).hexdigest()[:16]
        return f"{self.CACHE_KEY_PREFIX}{text_hash}"

    def _create_group_text(self, group: FieldGroup) -> str:
        """Create text representation of field group for embedding.

        Args:
            group: Field group to represent.

        Returns:
            Text representation combining name, description, and fields.
        """
        lines = [f"{group.name}: {group.description}", "", "Fields:"]
        for field in group.fields:
            lines.append(f"- {field.name}: {field.description}")
        return "\n".join(lines)

    def _truncate_at_word_boundary(self, text: str, max_len: int) -> str:
        """Truncate text at word boundary.

        Args:
            text: Text to truncate.
            max_len: Maximum length.

        Returns:
            Truncated text at word boundary, or original if shorter than max_len.
        """
        if len(text) <= max_len:
            return text

        # Find last space before max_len
        truncated = text[:max_len]
        last_space = truncated.rfind(" ")

        if last_space > max_len * 0.8:  # Only use space if reasonably close to limit
            return truncated[:last_space].rstrip()

        return truncated

    def _create_page_summary(
        self,
        url: str,
        title: str | None,
        content: str,
    ) -> str:
        """Create page summary for embedding.

        Args:
            url: Page URL.
            title: Page title.
            content: Page markdown content.

        Returns:
            Summary text for embedding (title + truncated content).
        """
        # Truncate content to ~2000 characters at word boundary
        truncated_content = self._truncate_at_word_boundary(content, 2000)

        parts = []
        if title:
            parts.append(f"Title: {title}")
        parts.append(f"URL: {url}")
        parts.append("")
        parts.append(truncated_content)

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
            Cosine similarity score (-1.0 to 1.0). For normalized embeddings
            from models like BGE, values typically range from 0.0 to 1.0.
        """
        if len(vec_a) != len(vec_b):
            return 0.0

        dot_product = sum(a * b for a, b in zip(vec_a, vec_b))
        magnitude_a = math.sqrt(sum(a * a for a in vec_a))
        magnitude_b = math.sqrt(sum(b * b for b in vec_b))

        if magnitude_a == 0 or magnitude_b == 0:
            return 0.0

        return dot_product / (magnitude_a * magnitude_b)

    def _infer_page_type(self, groups: list[str]) -> str:
        """Infer page type from matched groups.

        Args:
            groups: List of matched group names.

        Returns:
            Inferred page type string.
        """
        if not groups:
            return "general"

        # Check for common patterns in group names
        for group in groups:
            group_lower = group.lower()
            if "product" in group_lower:
                return "product"
            if "service" in group_lower:
                return "service"
            if "company" in group_lower or "about" in group_lower:
                return "about"
            if "contact" in group_lower:
                return "contact"

        return "general"
