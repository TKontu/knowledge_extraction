"""Tests for UrlRelevanceFilter service."""

import math
from unittest.mock import AsyncMock, MagicMock

import pytest

from config import Settings
from services.extraction.field_groups import FieldDefinition, FieldGroup
from services.scraper.url_filter import (
    FilteredUrl,
    UrlFilterResult,
    UrlRelevanceFilter,
)


def create_embedding_with_similarity(target_similarity: float, dim: int = 1024) -> list[float]:
    """Create a unit vector with specified cosine similarity to [1, 0, 0, ...].

    Args:
        target_similarity: Desired cosine similarity (-1.0 to 1.0).
        dim: Vector dimension.

    Returns:
        Unit vector with the specified similarity to the reference vector.
    """
    s = max(-1.0, min(1.0, target_similarity))
    vec = [0.0] * dim
    vec[0] = s
    if abs(s) < 1.0:
        vec[1] = math.sqrt(1 - s * s)
    return vec


def reference_embedding(dim: int = 1024) -> list[float]:
    """Create the reference unit vector [1, 0, 0, ...]."""
    vec = [0.0] * dim
    vec[0] = 1.0
    return vec


@pytest.fixture
def settings():
    """Create test settings."""
    settings = MagicMock(spec=Settings)
    settings.smart_crawl_default_relevance_threshold = 0.4
    return settings


@pytest.fixture
def embedding_service():
    """Create mock embedding service."""
    service = MagicMock()
    service.embed = AsyncMock()
    service.embed_batch = AsyncMock()
    return service


@pytest.fixture
def field_groups():
    """Create sample field groups for testing."""
    return [
        FieldGroup(
            name="products",
            description="Information about products and specifications",
            fields=[
                FieldDefinition(
                    name="product_name",
                    field_type="text",
                    description="Name of the product",
                ),
                FieldDefinition(
                    name="specifications",
                    field_type="text",
                    description="Technical specifications",
                ),
            ],
            prompt_hint="Extract product information",
        ),
        FieldGroup(
            name="pricing",
            description="Pricing and cost information",
            fields=[
                FieldDefinition(
                    name="price",
                    field_type="float",
                    description="Product price",
                ),
            ],
            prompt_hint="Extract pricing data",
        ),
    ]


@pytest.fixture
def url_filter(embedding_service, settings):
    """Create UrlRelevanceFilter instance."""
    return UrlRelevanceFilter(embedding_service, settings)


class TestUrlRelevanceFilter:
    """Tests for UrlRelevanceFilter class."""

    @pytest.mark.asyncio
    async def test_filter_empty_urls_returns_empty(self, url_filter):
        """Test that empty URL list returns empty result."""
        result = await url_filter.filter_urls(
            urls=[],
            field_groups=[],
        )

        assert result.total_urls == 0
        assert result.relevant_urls == []
        assert result.filtered_out == 0
        assert result.threshold_used == 0.4  # Default threshold

    @pytest.mark.asyncio
    async def test_filter_urls_high_relevance(self, url_filter, embedding_service, field_groups):
        """Test URLs with high relevance scores pass the filter."""
        # Mock embeddings
        context_embedding = reference_embedding()
        url_embedding = create_embedding_with_similarity(0.8)  # High similarity

        embedding_service.embed.return_value = context_embedding
        embedding_service.embed_batch.return_value = [url_embedding]

        urls = [
            {
                "url": "https://example.com/products/widget",
                "title": "Widget Product Page",
                "description": "Technical specifications for our widget",
            }
        ]

        result = await url_filter.filter_urls(
            urls=urls,
            field_groups=field_groups,
        )

        assert result.total_urls == 1
        assert len(result.relevant_urls) == 1
        assert result.filtered_out == 0
        assert result.relevant_urls[0].url == "https://example.com/products/widget"
        assert result.relevant_urls[0].relevance_score >= 0.7  # High score

    @pytest.mark.asyncio
    async def test_filter_urls_low_relevance(self, url_filter, embedding_service, field_groups):
        """Test URLs with low relevance scores are filtered out."""
        context_embedding = reference_embedding()
        url_embedding = create_embedding_with_similarity(0.2)  # Low similarity

        embedding_service.embed.return_value = context_embedding
        embedding_service.embed_batch.return_value = [url_embedding]

        urls = [
            {
                "url": "https://example.com/careers/jobs",
                "title": "Join Our Team",
                "description": "Career opportunities at Example Corp",
            }
        ]

        result = await url_filter.filter_urls(
            urls=urls,
            field_groups=field_groups,
            threshold=0.4,
        )

        assert result.total_urls == 1
        assert len(result.relevant_urls) == 0
        assert result.filtered_out == 1

    @pytest.mark.asyncio
    async def test_filter_urls_mixed_relevance(self, url_filter, embedding_service, field_groups):
        """Test filtering with mix of relevant and irrelevant URLs."""
        context_embedding = reference_embedding()
        high_similarity = create_embedding_with_similarity(0.8)
        medium_similarity = create_embedding_with_similarity(0.5)
        low_similarity = create_embedding_with_similarity(0.2)

        embedding_service.embed.return_value = context_embedding
        embedding_service.embed_batch.return_value = [
            high_similarity,
            medium_similarity,
            low_similarity,
        ]

        urls = [
            {
                "url": "https://example.com/products",
                "title": "Products",
                "description": "Our product catalog",
            },
            {
                "url": "https://example.com/pricing",
                "title": "Pricing",
                "description": "Pricing information",
            },
            {
                "url": "https://example.com/careers",
                "title": "Careers",
                "description": "Job openings",
            },
        ]

        result = await url_filter.filter_urls(
            urls=urls,
            field_groups=field_groups,
            threshold=0.4,
        )

        assert result.total_urls == 3
        assert len(result.relevant_urls) == 2  # High and medium pass
        assert result.filtered_out == 1  # Low is filtered

    @pytest.mark.asyncio
    async def test_filter_urls_custom_threshold(self, url_filter, embedding_service, field_groups):
        """Test filtering with custom threshold."""
        context_embedding = reference_embedding()
        url_embedding = create_embedding_with_similarity(0.5)

        embedding_service.embed.return_value = context_embedding
        embedding_service.embed_batch.return_value = [url_embedding]

        urls = [
            {
                "url": "https://example.com/about",
                "title": "About Us",
                "description": "Company information",
            }
        ]

        # With threshold 0.4, URL passes
        result = await url_filter.filter_urls(
            urls=urls,
            field_groups=field_groups,
            threshold=0.4,
        )
        assert len(result.relevant_urls) == 1
        assert result.threshold_used == 0.4

        # With threshold 0.6, same URL is filtered
        result = await url_filter.filter_urls(
            urls=urls,
            field_groups=field_groups,
            threshold=0.6,
        )
        assert len(result.relevant_urls) == 0
        assert result.threshold_used == 0.6

    @pytest.mark.asyncio
    async def test_filter_urls_with_focus_terms(self, url_filter, embedding_service, field_groups):
        """Test that focus_terms enhance the context."""
        context_embedding = reference_embedding()
        url_embedding = create_embedding_with_similarity(0.7)

        embedding_service.embed.return_value = context_embedding
        embedding_service.embed_batch.return_value = [url_embedding]

        urls = [
            {
                "url": "https://example.com/specs",
                "title": "Specifications",
                "description": "Technical details",
            }
        ]

        await url_filter.filter_urls(
            urls=urls,
            field_groups=field_groups,
            focus_terms=["technical specifications", "product details"],
        )

        # Verify embed was called (context includes focus terms)
        embedding_service.embed.assert_called_once()
        call_args = embedding_service.embed.call_args[0][0]
        assert "technical specifications" in call_args.lower() or "Focus:" in call_args

    @pytest.mark.asyncio
    async def test_filter_urls_no_metadata_passthrough(self, url_filter, embedding_service, field_groups):
        """Test URLs without metadata pass through (conservative)."""
        context_embedding = reference_embedding()

        embedding_service.embed.return_value = context_embedding
        embedding_service.embed_batch.return_value = []  # No embeddings needed

        urls = [
            {
                "url": "https://example.com/page",
                "title": None,
                "description": None,
            }
        ]

        result = await url_filter.filter_urls(
            urls=urls,
            field_groups=field_groups,
        )

        # URLs without metadata should pass through
        assert result.total_urls == 1
        assert len(result.relevant_urls) == 1
        assert result.relevant_urls[0].relevance_score == 0.5  # Unknown score
        assert result.relevant_urls[0].is_relevant is True

    @pytest.mark.asyncio
    async def test_filter_urls_embedding_error_passthrough(self, url_filter, embedding_service, field_groups):
        """Test all URLs pass through on embedding error (conservative)."""
        embedding_service.embed.return_value = reference_embedding()
        embedding_service.embed_batch.side_effect = Exception("Embedding API error")

        urls = [
            {
                "url": "https://example.com/products",
                "title": "Products",
                "description": "Product catalog",
            },
            {
                "url": "https://example.com/services",
                "title": "Services",
                "description": "Our services",
            },
        ]

        result = await url_filter.filter_urls(
            urls=urls,
            field_groups=field_groups,
        )

        # All URLs should pass through on error
        assert result.total_urls == 2
        assert len(result.relevant_urls) == 2
        assert result.filtered_out == 0

    @pytest.mark.asyncio
    async def test_filter_urls_sorted_by_score(self, url_filter, embedding_service, field_groups):
        """Test results are sorted by relevance score (highest first)."""
        context_embedding = reference_embedding()

        # Return embeddings with different similarities
        embedding_service.embed.return_value = context_embedding
        embedding_service.embed_batch.return_value = [
            create_embedding_with_similarity(0.5),  # Medium
            create_embedding_with_similarity(0.9),  # High
            create_embedding_with_similarity(0.7),  # Medium-high
        ]

        urls = [
            {"url": "https://example.com/a", "title": "Page A", "description": "Desc A"},
            {"url": "https://example.com/b", "title": "Page B", "description": "Desc B"},
            {"url": "https://example.com/c", "title": "Page C", "description": "Desc C"},
        ]

        result = await url_filter.filter_urls(
            urls=urls,
            field_groups=field_groups,
            threshold=0.4,
        )

        # All should pass (all >= 0.4)
        assert len(result.relevant_urls) == 3

        # Should be sorted by score descending
        scores = [u.relevance_score for u in result.relevant_urls]
        assert scores == sorted(scores, reverse=True)

        # Highest score URL should be first
        assert result.relevant_urls[0].url == "https://example.com/b"


class TestUrlRelevanceFilterHelpers:
    """Tests for helper methods."""

    def test_create_context_text_with_field_groups(self, url_filter, field_groups):
        """Test context text generation from field groups."""
        context_text = url_filter._create_context_text(field_groups, None)

        assert "products" in context_text.lower()
        assert "pricing" in context_text.lower()
        assert "specifications" in context_text.lower()

    def test_create_context_text_with_focus_terms(self, url_filter, field_groups):
        """Test context text includes focus terms."""
        context_text = url_filter._create_context_text(
            field_groups,
            ["gearbox", "motor specifications"]
        )

        assert "Focus:" in context_text
        assert "gearbox" in context_text.lower()
        assert "motor specifications" in context_text.lower()

    def test_create_url_text_with_all_metadata(self, url_filter):
        """Test URL text generation with full metadata."""
        url_info = {
            "url": "https://example.com/products/widget",
            "title": "Widget Product",
            "description": "A great widget",
        }

        url_text = url_filter._create_url_text(url_info)

        assert url_text is not None
        assert "Widget Product" in url_text
        assert "great widget" in url_text

    def test_create_url_text_no_metadata_returns_none(self, url_filter):
        """Test URL text returns None for URLs without metadata."""
        url_info = {
            "url": "https://example.com/page",
            "title": None,
            "description": None,
        }

        url_text = url_filter._create_url_text(url_info)
        assert url_text is None

    def test_cosine_similarity_identical_vectors(self, url_filter):
        """Test cosine similarity of identical vectors is 1.0."""
        vec = reference_embedding()
        similarity = url_filter._cosine_similarity(vec, vec)
        assert abs(similarity - 1.0) < 0.0001

    def test_cosine_similarity_orthogonal_vectors(self, url_filter):
        """Test cosine similarity of orthogonal vectors is 0.0."""
        vec_a = [1.0, 0.0, 0.0, 0.0]
        vec_b = [0.0, 1.0, 0.0, 0.0]
        similarity = url_filter._cosine_similarity(vec_a, vec_b)
        assert abs(similarity) < 0.0001

    def test_cosine_similarity_different_lengths(self, url_filter):
        """Test cosine similarity returns 0.0 for different length vectors."""
        vec_a = [1.0, 0.0, 0.0]
        vec_b = [1.0, 0.0]
        similarity = url_filter._cosine_similarity(vec_a, vec_b)
        assert similarity == 0.0

    def test_cosine_similarity_zero_vector(self, url_filter):
        """Test cosine similarity with zero vector returns 0.0."""
        vec_a = [1.0, 0.0, 0.0]
        vec_b = [0.0, 0.0, 0.0]
        similarity = url_filter._cosine_similarity(vec_a, vec_b)
        assert similarity == 0.0


class TestFilteredUrlDataclass:
    """Tests for FilteredUrl dataclass."""

    def test_filtered_url_creation(self):
        """Test FilteredUrl creation with all fields."""
        filtered = FilteredUrl(
            url="https://example.com",
            title="Example",
            description="A test page",
            relevance_score=0.85,
            is_relevant=True,
        )

        assert filtered.url == "https://example.com"
        assert filtered.title == "Example"
        assert filtered.description == "A test page"
        assert filtered.relevance_score == 0.85
        assert filtered.is_relevant is True

    def test_filtered_url_optional_fields(self):
        """Test FilteredUrl with optional fields as None."""
        filtered = FilteredUrl(
            url="https://example.com/page",
            title=None,
            description=None,
            relevance_score=0.5,
            is_relevant=True,
        )

        assert filtered.url == "https://example.com/page"
        assert filtered.title is None
        assert filtered.description is None


class TestUrlFilterResultDataclass:
    """Tests for UrlFilterResult dataclass."""

    def test_url_filter_result_creation(self):
        """Test UrlFilterResult creation."""
        filtered_urls = [
            FilteredUrl(
                url="https://example.com/a",
                title="A",
                description="Page A",
                relevance_score=0.9,
                is_relevant=True,
            )
        ]

        result = UrlFilterResult(
            total_urls=5,
            relevant_urls=filtered_urls,
            filtered_out=4,
            threshold_used=0.5,
        )

        assert result.total_urls == 5
        assert len(result.relevant_urls) == 1
        assert result.filtered_out == 4
        assert result.threshold_used == 0.5
