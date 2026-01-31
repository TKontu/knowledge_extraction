"""Tests for SmartClassifier service."""

import json
import math
from unittest.mock import AsyncMock, MagicMock

import pytest

from config import Settings
from services.extraction.field_groups import FieldDefinition, FieldGroup
from services.extraction.page_classifier import ClassificationMethod
from services.extraction.schema_adapter import ClassificationConfig
from services.extraction.smart_classifier import (
    SmartClassificationResult,
    SmartClassifier,
)


def create_embedding_with_similarity(target_similarity: float, dim: int = 1024) -> list[float]:
    """Create a unit vector with specified cosine similarity to [1, 0, 0, ...].

    Given a reference vector A = [1, 0, 0, ...], creates vector B such that
    cosine_similarity(A, B) ≈ target_similarity.

    Args:
        target_similarity: Desired cosine similarity (-1.0 to 1.0).
        dim: Vector dimension.

    Returns:
        Unit vector with the specified similarity to the reference vector.
    """
    # Clamp to valid range
    s = max(-1.0, min(1.0, target_similarity))

    # B = [s, sqrt(1-s²), 0, 0, ...]
    # This gives cos(θ) = A·B = s (since |A| = |B| = 1)
    vec = [0.0] * dim
    vec[0] = s
    if abs(s) < 1.0:
        vec[1] = math.sqrt(1 - s * s)
    return vec


def reference_embedding(dim: int = 1024) -> list[float]:
    """Create the reference unit vector [1, 0, 0, ...].

    Args:
        dim: Vector dimension.

    Returns:
        Unit vector pointing along first axis.
    """
    vec = [0.0] * dim
    vec[0] = 1.0
    return vec


@pytest.fixture
def settings():
    """Create test settings."""
    settings = MagicMock(spec=Settings)
    settings.smart_classification_enabled = True
    settings.reranker_model = "bge-reranker-v2-m3"
    settings.classification_embedding_high_threshold = 0.75
    settings.classification_embedding_low_threshold = 0.4
    settings.classification_reranker_threshold = 0.5
    settings.classification_cache_ttl = 86400
    # Use default skip patterns for backward compatibility in tests
    settings.classification_use_default_skip_patterns = True
    return settings


@pytest.fixture
def embedding_service():
    """Create mock embedding service."""
    service = MagicMock()
    service.embed = AsyncMock()
    service.embed_batch = AsyncMock()
    service.rerank = AsyncMock()
    return service


@pytest.fixture
def redis_client():
    """Create mock Redis client."""
    client = MagicMock()
    client.get = AsyncMock(return_value=None)  # No cache by default
    client.setex = AsyncMock()
    return client


@pytest.fixture
def field_groups():
    """Create sample field groups for testing."""
    return [
        FieldGroup(
            name="products_gearbox",
            description="Information about gearbox products",
            fields=[
                FieldDefinition(
                    name="gearbox_type",
                    field_type="text",
                    description="Type of gearbox (planetary, helical, etc.)",
                ),
            ],
            prompt_hint="Extract gearbox product information",
        ),
        FieldGroup(
            name="company_info",
            description="Company overview and general information",
            fields=[
                FieldDefinition(
                    name="company_name",
                    field_type="text",
                    description="Name of the company",
                ),
            ],
            prompt_hint="Extract company information",
        ),
        FieldGroup(
            name="services",
            description="Services offered by the company",
            fields=[
                FieldDefinition(
                    name="service_type",
                    field_type="text",
                    description="Type of service offered",
                ),
            ],
            prompt_hint="Extract service information",
        ),
    ]


@pytest.fixture
def smart_classifier(embedding_service, redis_client, settings):
    """Create SmartClassifier instance."""
    return SmartClassifier(
        embedding_service=embedding_service,
        redis_client=redis_client,
        settings=settings,
    )


class TestSmartClassifierSkipPatterns:
    """Test rule-based skip pattern detection."""

    async def test_skips_career_pages(self, smart_classifier, field_groups):
        """Career pages should be skipped via rule-based check."""
        result = await smart_classifier.classify(
            url="https://example.com/careers/engineer",
            title="Job Openings",
            content="Join our team!",
            field_groups=field_groups,
        )

        assert result.skip_extraction is True
        assert result.page_type == "skip"
        assert "skip pattern" in result.reasoning.lower()

    async def test_skips_privacy_pages(self, smart_classifier, field_groups):
        """Privacy pages should be skipped via rule-based check."""
        result = await smart_classifier.classify(
            url="https://example.com/privacy-policy",
            title="Privacy Policy",
            content="We collect data...",
            field_groups=field_groups,
        )

        assert result.skip_extraction is True

    async def test_skips_login_pages(self, smart_classifier, field_groups):
        """Login pages should be skipped via rule-based check."""
        result = await smart_classifier.classify(
            url="https://example.com/login",
            title="Sign In",
            content="Enter your credentials",
            field_groups=field_groups,
        )

        assert result.skip_extraction is True


class TestSmartClassifierHighConfidence:
    """Test high confidence classification path (similarity > 0.75)."""

    async def test_high_similarity_uses_matched_groups(
        self, smart_classifier, embedding_service, redis_client, field_groups
    ):
        """High similarity scores should return matched groups directly."""
        # Mock embeddings with controlled cosine similarities
        # Page embedding is the reference vector [1, 0, 0, ...]
        page_embedding = reference_embedding()
        gearbox_embedding = create_embedding_with_similarity(0.95)  # High similarity
        company_embedding = create_embedding_with_similarity(0.3)   # Low similarity
        services_embedding = create_embedding_with_similarity(0.2)  # Low similarity

        embedding_service.embed.return_value = page_embedding
        embedding_service.embed_batch.return_value = [
            gearbox_embedding,
            company_embedding,
            services_embedding,
        ]

        result = await smart_classifier.classify(
            url="https://example.com/products/gearboxes",
            title="Gearbox Products",
            content="Our planetary gearboxes are industry leading...",
            field_groups=field_groups,
        )

        assert not result.skip_extraction
        assert "products_gearbox" in result.relevant_groups
        assert result.confidence >= 0.75
        assert result.method == ClassificationMethod.HYBRID
        assert "High embedding similarity" in result.reasoning

    async def test_high_confidence_includes_all_matching_groups(
        self, smart_classifier, embedding_service, redis_client, field_groups, settings
    ):
        """All groups above high threshold should be included."""
        # Mock embeddings with controlled cosine similarities
        # Both gearbox and company have high similarity (>= 0.75)
        page_embedding = reference_embedding()
        gearbox_embedding = create_embedding_with_similarity(0.85)  # Above 0.75 threshold
        company_embedding = create_embedding_with_similarity(0.80)  # Above 0.75 threshold
        services_embedding = create_embedding_with_similarity(0.2)  # Below threshold

        embedding_service.embed.return_value = page_embedding
        embedding_service.embed_batch.return_value = [
            gearbox_embedding,
            company_embedding,
            services_embedding,
        ]

        result = await smart_classifier.classify(
            url="https://example.com/about/products",
            title="Our Products and Company",
            content="Company makes great gearboxes...",
            field_groups=field_groups,
        )

        # Both gearbox and company should be matched (high similarity)
        assert "products_gearbox" in result.relevant_groups
        assert "company_info" in result.relevant_groups
        assert "services" not in result.relevant_groups


class TestSmartClassifierLowConfidence:
    """Test low confidence classification path (similarity < 0.4)."""

    async def test_low_similarity_uses_all_groups(
        self, smart_classifier, embedding_service, redis_client, field_groups
    ):
        """Low similarity scores should return empty groups (use all)."""
        # Mock embeddings - page doesn't match any group well (all below 0.4 threshold)
        page_embedding = reference_embedding()
        gearbox_embedding = create_embedding_with_similarity(0.2)   # Below 0.4
        company_embedding = create_embedding_with_similarity(0.15)  # Below 0.4
        services_embedding = create_embedding_with_similarity(0.1)  # Below 0.4

        embedding_service.embed.return_value = page_embedding
        embedding_service.embed_batch.return_value = [
            gearbox_embedding,
            company_embedding,
            services_embedding,
        ]

        result = await smart_classifier.classify(
            url="https://example.com/random-page",
            title="Random Content",
            content="This page doesn't match any field groups well...",
            field_groups=field_groups,
        )

        assert not result.skip_extraction
        assert result.relevant_groups == []  # Empty = use all groups
        assert "using all groups" in result.reasoning.lower()


class TestSmartClassifierMediumConfidence:
    """Test medium confidence classification path (reranker fallback)."""

    async def test_medium_confidence_uses_reranker(
        self, smart_classifier, embedding_service, redis_client, field_groups, settings
    ):
        """Medium similarity should trigger reranker for confirmation."""
        # Mock embeddings - moderate similarity (between 0.4 and 0.75)
        page_embedding = reference_embedding()
        gearbox_embedding = create_embedding_with_similarity(0.6)  # Medium - triggers reranker
        company_embedding = create_embedding_with_similarity(0.5)  # Medium
        services_embedding = create_embedding_with_similarity(0.45)  # Medium

        embedding_service.embed.return_value = page_embedding
        embedding_service.embed_batch.return_value = [
            gearbox_embedding,
            company_embedding,
            services_embedding,
        ]

        # Mock reranker to confirm gearbox is relevant
        embedding_service.rerank.return_value = [
            (0, 0.8),  # gearbox - high relevance
            (1, 0.3),  # company - low relevance
            (2, 0.2),  # services - low relevance
        ]

        result = await smart_classifier.classify(
            url="https://example.com/products",
            title="Products",
            content="Our products include various mechanical components...",
            field_groups=field_groups,
        )

        # Reranker should be called
        embedding_service.rerank.assert_called_once()

        # Only gearbox should be confirmed (above 0.5 threshold)
        assert "products_gearbox" in result.relevant_groups
        assert "company_info" not in result.relevant_groups

    async def test_reranker_confirms_multiple_groups(
        self, smart_classifier, embedding_service, redis_client, field_groups, settings
    ):
        """Reranker should confirm all groups above threshold."""
        # All embeddings in medium range (0.4 to 0.75) to trigger reranker
        page_embedding = reference_embedding()
        gearbox_embedding = create_embedding_with_similarity(0.55)
        company_embedding = create_embedding_with_similarity(0.50)
        services_embedding = create_embedding_with_similarity(0.52)

        embedding_service.embed.return_value = page_embedding
        embedding_service.embed_batch.return_value = [
            gearbox_embedding,
            company_embedding,
            services_embedding,
        ]

        # Reranker confirms gearbox and services are relevant
        embedding_service.rerank.return_value = [
            (0, 0.7),  # gearbox - above threshold
            (2, 0.6),  # services - above threshold
            (1, 0.3),  # company - below threshold
        ]

        result = await smart_classifier.classify(
            url="https://example.com/products-services",
            title="Products and Services",
            content="We offer products and services...",
            field_groups=field_groups,
        )

        assert "products_gearbox" in result.relevant_groups
        assert "services" in result.relevant_groups
        assert "company_info" not in result.relevant_groups

    async def test_reranker_failure_falls_back_to_embedding_scores(
        self, smart_classifier, embedding_service, redis_client, field_groups
    ):
        """Reranker failure should fall back to embedding-based groups."""
        # Medium similarity (0.4-0.75) triggers reranker path
        page_embedding = reference_embedding()
        gearbox_embedding = create_embedding_with_similarity(0.6)
        company_embedding = create_embedding_with_similarity(0.5)
        services_embedding = create_embedding_with_similarity(0.45)

        embedding_service.embed.return_value = page_embedding
        embedding_service.embed_batch.return_value = [
            gearbox_embedding,
            company_embedding,
            services_embedding,
        ]

        # Reranker fails
        embedding_service.rerank.side_effect = Exception("Reranker unavailable")

        result = await smart_classifier.classify(
            url="https://example.com/products",
            title="Products",
            content="Product content...",
            field_groups=field_groups,
        )

        # Should still return a result (fallback)
        assert not result.skip_extraction
        assert "Reranker failed" in result.reasoning


class TestSmartClassifierCaching:
    """Test Redis caching of field group embeddings."""

    async def test_caches_field_group_embeddings(
        self, smart_classifier, embedding_service, redis_client, field_groups
    ):
        """Field group embeddings should be cached in Redis."""
        # Use proper vectors with controlled similarity
        page_embedding = reference_embedding()
        gearbox_embedding = create_embedding_with_similarity(0.9)  # High similarity
        company_embedding = create_embedding_with_similarity(0.3)  # Low
        services_embedding = create_embedding_with_similarity(0.2)  # Low

        embedding_service.embed.return_value = page_embedding
        embedding_service.embed_batch.return_value = [
            gearbox_embedding,
            company_embedding,
            services_embedding,
        ]

        await smart_classifier.classify(
            url="https://example.com/products",
            title="Products",
            content="Content...",
            field_groups=field_groups,
        )

        # Should have tried to cache each group embedding
        assert redis_client.setex.call_count == 3

    async def test_uses_cached_embeddings(
        self, smart_classifier, embedding_service, redis_client, field_groups
    ):
        """Should use cached embeddings instead of regenerating."""
        # Cached embedding with high similarity to reference
        cached_embedding = create_embedding_with_similarity(0.85)

        # Return cached embeddings for all groups via mget (batch retrieval)
        redis_client.mget = AsyncMock(
            return_value=[json.dumps(cached_embedding) for _ in field_groups]
        )

        page_embedding = reference_embedding()
        embedding_service.embed.return_value = page_embedding
        # embed_batch won't be called if all are cached
        embedding_service.embed_batch.return_value = []

        await smart_classifier.classify(
            url="https://example.com/products",
            title="Products",
            content="Content...",
            field_groups=field_groups,
        )

        # Should have called mget for batch cache retrieval
        redis_client.mget.assert_called()
        # embed_batch should not be called since all were cached
        embedding_service.embed_batch.assert_not_called()

    async def test_partial_cache_hit(
        self, smart_classifier, embedding_service, redis_client, field_groups
    ):
        """Should use cached embeddings for some groups and embed others."""
        # First group cached, second and third not cached
        cached_embedding = create_embedding_with_similarity(0.85)
        uncached_embedding_1 = create_embedding_with_similarity(0.3)
        uncached_embedding_2 = create_embedding_with_similarity(0.2)

        # mget returns: [cached, None, None] - first group cached, others not
        redis_client.mget = AsyncMock(
            return_value=[json.dumps(cached_embedding), None, None]
        )

        page_embedding = reference_embedding()
        embedding_service.embed.return_value = page_embedding
        # embed_batch called only for uncached groups (2 groups)
        embedding_service.embed_batch.return_value = [
            uncached_embedding_1,
            uncached_embedding_2,
        ]

        await smart_classifier.classify(
            url="https://example.com/products",
            title="Products",
            content="Content...",
            field_groups=field_groups,
        )

        # Should have called mget for batch cache retrieval
        redis_client.mget.assert_called_once()
        # embed_batch should be called for the 2 uncached groups
        embedding_service.embed_batch.assert_called_once()
        # Verify only 2 texts were passed to embed_batch (the uncached groups)
        call_args = embedding_service.embed_batch.call_args[0][0]
        assert len(call_args) == 2
        # Should have cached the 2 newly embedded groups
        assert redis_client.setex.call_count == 2


class TestSmartClassifierDisabled:
    """Test behavior when smart classification is disabled."""

    async def test_disabled_uses_rule_based(
        self, embedding_service, redis_client, field_groups
    ):
        """When disabled, should fall back to rule-based classification."""
        settings = MagicMock(spec=Settings)
        settings.smart_classification_enabled = False

        classifier = SmartClassifier(
            embedding_service=embedding_service,
            redis_client=redis_client,
            settings=settings,
        )

        result = await classifier.classify(
            url="https://example.com/products",
            title="Products",
            content="Content...",
            field_groups=field_groups,
        )

        # Should not call embedding service
        embedding_service.embed.assert_not_called()
        embedding_service.embed_batch.assert_not_called()

        # Result should be rule-based
        assert result.method == ClassificationMethod.RULE_BASED


class TestSmartClassifierErrorHandling:
    """Test error handling and fallback behavior."""

    async def test_embedding_error_falls_back_to_rule_based(
        self, smart_classifier, embedding_service, redis_client, field_groups
    ):
        """Embedding service errors should fall back to rule-based."""
        embedding_service.embed.side_effect = Exception("Embedding service unavailable")

        result = await smart_classifier.classify(
            url="https://example.com/products",
            title="Products",
            content="Content...",
            field_groups=field_groups,
        )

        # Should not skip extraction
        assert not result.skip_extraction
        # Should use all groups (conservative fallback)
        assert result.relevant_groups == []
        assert "failed" in result.reasoning.lower()

    async def test_empty_field_groups_returns_all(
        self, smart_classifier, embedding_service, redis_client
    ):
        """Empty field groups should return result indicating use all."""
        result = await smart_classifier.classify(
            url="https://example.com/products",
            title="Products",
            content="Content...",
            field_groups=[],
        )

        assert result.relevant_groups == []
        assert not result.skip_extraction


class TestCosineSimilarity:
    """Test cosine similarity calculation."""

    def test_identical_vectors_similarity_one(self, smart_classifier):
        """Identical vectors should have similarity of 1.0."""
        vec = [1.0, 2.0, 3.0]
        similarity = smart_classifier._cosine_similarity(vec, vec)
        assert math.isclose(similarity, 1.0, rel_tol=1e-9)

    def test_orthogonal_vectors_similarity_zero(self, smart_classifier):
        """Orthogonal vectors should have similarity of 0.0."""
        vec_a = [1.0, 0.0, 0.0]
        vec_b = [0.0, 1.0, 0.0]
        similarity = smart_classifier._cosine_similarity(vec_a, vec_b)
        assert math.isclose(similarity, 0.0, rel_tol=1e-9)

    def test_opposite_vectors_similarity_negative(self, smart_classifier):
        """Opposite vectors should have similarity of -1.0."""
        vec_a = [1.0, 0.0]
        vec_b = [-1.0, 0.0]
        similarity = smart_classifier._cosine_similarity(vec_a, vec_b)
        assert math.isclose(similarity, -1.0, rel_tol=1e-9)

    def test_different_length_vectors_returns_zero(self, smart_classifier):
        """Vectors of different lengths should return 0.0."""
        vec_a = [1.0, 2.0, 3.0]
        vec_b = [1.0, 2.0]
        similarity = smart_classifier._cosine_similarity(vec_a, vec_b)
        assert similarity == 0.0

    def test_zero_vector_returns_zero(self, smart_classifier):
        """Zero vector should return 0.0 similarity."""
        vec_a = [1.0, 2.0, 3.0]
        vec_b = [0.0, 0.0, 0.0]
        similarity = smart_classifier._cosine_similarity(vec_a, vec_b)
        assert similarity == 0.0


class TestSmartClassificationResult:
    """Test SmartClassificationResult dataclass."""

    def test_extends_classification_result(self):
        """SmartClassificationResult should extend ClassificationResult."""
        result = SmartClassificationResult(
            page_type="product",
            relevant_groups=["products_gearbox"],
            skip_extraction=False,
            confidence=0.8,
            method=ClassificationMethod.HYBRID,
            reasoning="Test",
            embedding_scores={"products_gearbox": 0.85},
            reranker_scores={"products_gearbox": 0.9},
        )

        assert result.page_type == "product"
        assert result.relevant_groups == ["products_gearbox"]
        assert result.embedding_scores == {"products_gearbox": 0.85}
        assert result.reranker_scores == {"products_gearbox": 0.9}

    def test_optional_score_fields(self):
        """Embedding and reranker scores should be optional."""
        result = SmartClassificationResult(
            page_type="general",
            relevant_groups=[],
            skip_extraction=False,
            confidence=0.5,
            method=ClassificationMethod.HYBRID,
        )

        assert result.embedding_scores is None
        assert result.reranker_scores is None


class TestPageSummaryCreation:
    """Test page summary creation for embedding."""

    def test_creates_summary_with_title_and_url(self, smart_classifier):
        """Summary should include title and URL."""
        summary = smart_classifier._create_page_summary(
            url="https://example.com/products",
            title="Product Page",
            content="Product content here",
        )

        assert "Title: Product Page" in summary
        assert "URL: https://example.com/products" in summary
        assert "Product content here" in summary

    def test_truncates_long_content(self, smart_classifier):
        """Long content should be truncated to ~2000 chars."""
        long_content = "x" * 5000
        summary = smart_classifier._create_page_summary(
            url="https://example.com",
            title="Title",
            content=long_content,
        )

        # Should be truncated
        assert len(summary) < len(long_content) + 200  # Account for title/url

    def test_handles_missing_title(self, smart_classifier):
        """Should handle missing title gracefully."""
        summary = smart_classifier._create_page_summary(
            url="https://example.com",
            title=None,
            content="Content",
        )

        assert "Title:" not in summary
        assert "URL: https://example.com" in summary
        assert "Content" in summary


class TestFieldGroupTextCreation:
    """Test field group text creation for embedding."""

    def test_creates_group_text_with_fields(self, smart_classifier, field_groups):
        """Group text should include name, description, and fields."""
        group = field_groups[0]  # products_gearbox
        text = smart_classifier._create_group_text(group)

        assert "products_gearbox" in text
        assert "gearbox products" in text.lower()
        assert "Fields:" in text
        assert "gearbox_type" in text

    def test_cache_key_is_stable(self, smart_classifier, field_groups):
        """Same group should produce same cache key."""
        group = field_groups[0]
        key1 = smart_classifier._get_cache_key(group)
        key2 = smart_classifier._get_cache_key(group)

        assert key1 == key2
        assert key1.startswith("classification:fg_embed:")


class TestPageTypeInference:
    """Test page type inference from matched groups."""

    def test_infers_product_type(self, smart_classifier):
        """Groups with 'product' should infer product type."""
        page_type = smart_classifier._infer_page_type(["products_gearbox", "products_motor"])
        assert page_type == "product"

    def test_infers_service_type(self, smart_classifier):
        """Groups with 'service' should infer service type."""
        page_type = smart_classifier._infer_page_type(["services", "field_services"])
        assert page_type == "service"

    def test_infers_about_type(self, smart_classifier):
        """Groups with 'company' or 'about' should infer about type."""
        page_type = smart_classifier._infer_page_type(["company_info"])
        assert page_type == "about"

    def test_infers_general_for_unknown(self, smart_classifier):
        """Unknown groups should infer general type."""
        page_type = smart_classifier._infer_page_type(["random_group"])
        assert page_type == "general"

    def test_empty_groups_returns_general(self, smart_classifier):
        """Empty groups should return general type."""
        page_type = smart_classifier._infer_page_type([])
        assert page_type == "general"


class TestClassificationConfigIntegration:
    """Tests for ClassificationConfig integration with SmartClassifier."""

    async def test_custom_skip_patterns_override_defaults(
        self, embedding_service, redis_client, field_groups
    ):
        """Custom skip patterns should override default patterns."""
        settings = MagicMock(spec=Settings)
        settings.smart_classification_enabled = True

        # Custom config that only skips /custom-skip/
        classification_config = ClassificationConfig(skip_patterns=[r"/custom-skip/"])

        classifier = SmartClassifier(
            embedding_service=embedding_service,
            redis_client=redis_client,
            settings=settings,
            classification_config=classification_config,
        )

        # Custom pattern should match
        result = await classifier.classify(
            url="https://example.com/custom-skip/page",
            title="Custom Page",
            content="Content",
            field_groups=field_groups,
        )
        assert result.skip_extraction is True

        # Default patterns (careers) should NOT match
        result = await classifier.classify(
            url="https://example.com/careers/engineer",
            title="Careers",
            content="Join our team",
            field_groups=field_groups,
        )
        assert result.skip_extraction is False

    async def test_empty_skip_patterns_disables_skipping(
        self, embedding_service, redis_client, field_groups
    ):
        """Empty skip patterns list disables all URL-based skipping."""
        settings = MagicMock(spec=Settings)
        settings.smart_classification_enabled = True
        settings.classification_embedding_high_threshold = 0.75
        settings.classification_embedding_low_threshold = 0.4
        settings.classification_cache_ttl = 86400

        # Explicitly empty patterns = no skipping
        classification_config = ClassificationConfig(skip_patterns=[])

        classifier = SmartClassifier(
            embedding_service=embedding_service,
            redis_client=redis_client,
            settings=settings,
            classification_config=classification_config,
        )

        # Mock embeddings for career page (low similarity to proceed)
        page_embedding = reference_embedding()
        group_embeddings = [
            create_embedding_with_similarity(0.2),
            create_embedding_with_similarity(0.15),
            create_embedding_with_similarity(0.1),
        ]

        embedding_service.embed.return_value = page_embedding
        embedding_service.embed_batch.return_value = group_embeddings

        # Career pages should NOT be skipped with empty patterns
        result = await classifier.classify(
            url="https://example.com/careers/engineer",
            title="Careers",
            content="Join our team",
            field_groups=field_groups,
        )
        assert result.skip_extraction is False

    async def test_null_patterns_with_smart_classification_is_context_agnostic(
        self, embedding_service, redis_client, field_groups
    ):
        """Null patterns with smart classification enabled uses no skip patterns."""
        settings = MagicMock(spec=Settings)
        settings.smart_classification_enabled = True
        settings.classification_use_default_skip_patterns = False
        settings.classification_embedding_high_threshold = 0.75
        settings.classification_embedding_low_threshold = 0.4
        settings.classification_cache_ttl = 86400

        # None patterns = context-agnostic (no skipping when smart enabled)
        classification_config = ClassificationConfig(skip_patterns=None)

        classifier = SmartClassifier(
            embedding_service=embedding_service,
            redis_client=redis_client,
            settings=settings,
            classification_config=classification_config,
        )

        # Mock embeddings
        page_embedding = reference_embedding()
        group_embeddings = [
            create_embedding_with_similarity(0.2),
            create_embedding_with_similarity(0.15),
            create_embedding_with_similarity(0.1),
        ]

        embedding_service.embed.return_value = page_embedding
        embedding_service.embed_batch.return_value = group_embeddings

        # Career pages should NOT be skipped (context-agnostic mode)
        result = await classifier.classify(
            url="https://example.com/careers/engineer",
            title="Careers",
            content="Join our team",
            field_groups=field_groups,
        )
        assert result.skip_extraction is False

    async def test_null_patterns_without_smart_uses_defaults(
        self, embedding_service, redis_client, field_groups
    ):
        """Null patterns without smart classification uses default patterns."""
        settings = MagicMock(spec=Settings)
        settings.smart_classification_enabled = False

        # None patterns = use defaults when smart classification disabled
        classification_config = ClassificationConfig(skip_patterns=None)

        classifier = SmartClassifier(
            embedding_service=embedding_service,
            redis_client=redis_client,
            settings=settings,
            classification_config=classification_config,
        )

        # Career pages should be skipped (default behavior)
        result = await classifier.classify(
            url="https://example.com/careers/engineer",
            title="Careers",
            content="Join our team",
            field_groups=field_groups,
        )
        assert result.skip_extraction is True

    async def test_global_override_forces_default_patterns(
        self, embedding_service, redis_client, field_groups
    ):
        """classification_use_default_skip_patterns setting forces defaults."""
        settings = MagicMock(spec=Settings)
        settings.smart_classification_enabled = True
        settings.classification_use_default_skip_patterns = True  # Force defaults

        # None patterns with override = use defaults
        classification_config = ClassificationConfig(skip_patterns=None)

        classifier = SmartClassifier(
            embedding_service=embedding_service,
            redis_client=redis_client,
            settings=settings,
            classification_config=classification_config,
        )

        # Career pages should be skipped (global override forces defaults)
        result = await classifier.classify(
            url="https://example.com/careers/engineer",
            title="Careers",
            content="Join our team",
            field_groups=field_groups,
        )
        assert result.skip_extraction is True

    async def test_explicit_patterns_override_global_setting(
        self, embedding_service, redis_client, field_groups
    ):
        """Explicit patterns in config override global setting."""
        settings = MagicMock(spec=Settings)
        settings.smart_classification_enabled = True
        settings.classification_use_default_skip_patterns = True  # Would force defaults
        settings.classification_embedding_high_threshold = 0.75
        settings.classification_embedding_low_threshold = 0.4
        settings.classification_cache_ttl = 86400

        # Explicit empty patterns should override global setting
        classification_config = ClassificationConfig(skip_patterns=[])

        classifier = SmartClassifier(
            embedding_service=embedding_service,
            redis_client=redis_client,
            settings=settings,
            classification_config=classification_config,
        )

        # Mock embeddings
        page_embedding = reference_embedding()
        group_embeddings = [
            create_embedding_with_similarity(0.2),
            create_embedding_with_similarity(0.15),
            create_embedding_with_similarity(0.1),
        ]

        embedding_service.embed.return_value = page_embedding
        embedding_service.embed_batch.return_value = group_embeddings

        # Career pages should NOT be skipped (explicit empty overrides global)
        result = await classifier.classify(
            url="https://example.com/careers/engineer",
            title="Careers",
            content="Join our team",
            field_groups=field_groups,
        )
        assert result.skip_extraction is False


class TestResolveSkipPatterns:
    """Tests for _resolve_skip_patterns method."""

    def test_explicit_empty_list(self, embedding_service, redis_client):
        """Explicit empty list returns empty list."""
        settings = MagicMock(spec=Settings)
        settings.smart_classification_enabled = True
        settings.classification_use_default_skip_patterns = False

        classification_config = ClassificationConfig(skip_patterns=[])
        classifier = SmartClassifier(
            embedding_service=embedding_service,
            redis_client=redis_client,
            settings=settings,
            classification_config=classification_config,
        )

        assert classifier._rule_classifier._skip_patterns == []

    def test_explicit_custom_patterns(self, embedding_service, redis_client):
        """Explicit custom patterns are used."""
        settings = MagicMock(spec=Settings)
        settings.smart_classification_enabled = True
        settings.classification_use_default_skip_patterns = False

        custom = [r"/my-pattern/"]
        classification_config = ClassificationConfig(skip_patterns=custom)
        classifier = SmartClassifier(
            embedding_service=embedding_service,
            redis_client=redis_client,
            settings=settings,
            classification_config=classification_config,
        )

        assert classifier._rule_classifier._skip_patterns == custom

    def test_none_with_smart_enabled_uses_empty(self, embedding_service, redis_client):
        """None patterns with smart classification uses empty list."""
        settings = MagicMock(spec=Settings)
        settings.smart_classification_enabled = True
        settings.classification_use_default_skip_patterns = False

        classification_config = ClassificationConfig(skip_patterns=None)
        classifier = SmartClassifier(
            embedding_service=embedding_service,
            redis_client=redis_client,
            settings=settings,
            classification_config=classification_config,
        )

        assert classifier._rule_classifier._skip_patterns == []

    def test_none_with_smart_disabled_uses_defaults(self, embedding_service, redis_client):
        """None patterns without smart classification uses defaults."""
        settings = MagicMock(spec=Settings)
        settings.smart_classification_enabled = False

        classification_config = ClassificationConfig(skip_patterns=None)
        classifier = SmartClassifier(
            embedding_service=embedding_service,
            redis_client=redis_client,
            settings=settings,
            classification_config=classification_config,
        )

        # Should use DEFAULT_SKIP_PATTERNS (passed as None to PageClassifier)
        from services.extraction.page_classifier import PageClassifier

        assert classifier._rule_classifier._skip_patterns == PageClassifier.DEFAULT_SKIP_PATTERNS

    def test_no_config_with_smart_enabled_uses_empty(self, embedding_service, redis_client):
        """No config with smart classification uses empty list (context-agnostic)."""
        settings = MagicMock(spec=Settings)
        settings.smart_classification_enabled = True
        settings.classification_use_default_skip_patterns = False

        # No classification_config provided
        classifier = SmartClassifier(
            embedding_service=embedding_service,
            redis_client=redis_client,
            settings=settings,
            classification_config=None,
        )

        assert classifier._rule_classifier._skip_patterns == []

    def test_global_override_forces_defaults(self, embedding_service, redis_client):
        """Global override setting forces default patterns."""
        settings = MagicMock(spec=Settings)
        settings.smart_classification_enabled = True
        settings.classification_use_default_skip_patterns = True  # Override

        classification_config = ClassificationConfig(skip_patterns=None)
        classifier = SmartClassifier(
            embedding_service=embedding_service,
            redis_client=redis_client,
            settings=settings,
            classification_config=classification_config,
        )

        from services.extraction.page_classifier import PageClassifier

        assert classifier._rule_classifier._skip_patterns == PageClassifier.DEFAULT_SKIP_PATTERNS
