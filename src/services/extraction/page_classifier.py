"""Page classification for targeted field group extraction."""

import re
from dataclasses import dataclass
from enum import Enum

import structlog

logger = structlog.get_logger(__name__)


class ClassificationMethod(str, Enum):
    """Method used for page classification."""

    RULE_BASED = "rule"
    LLM_ASSISTED = "llm"  # Future
    HYBRID = "hybrid"  # Future


@dataclass
class ClassificationResult:
    """Result of page classification."""

    page_type: str  # product, service, about, contact, skip, general
    relevant_groups: list[str]  # Field group names to extract
    skip_extraction: bool  # True if page should be skipped entirely
    confidence: float  # 0.0 - 1.0
    method: ClassificationMethod
    reasoning: str | None = None


class PageClassifier:
    """Classifies pages to determine relevant extraction field groups.

    Template-agnostic classifier that:
    1. Skips irrelevant pages (careers, news, legal, etc.) - universal patterns
    2. Optionally filters field groups based on configurable URL/title patterns

    By default, only skip detection is enabled. Field group filtering requires
    explicit patterns to be provided during initialization.
    """

    # Universal patterns that indicate pages to skip entirely (template-agnostic)
    DEFAULT_SKIP_PATTERNS: list[str] = [
        r"/career|/job|/employ|/vacanc",
        r"/news|/blog|/press|/media|/event",
        r"/privacy|/terms|/legal|/cookie|/gdpr",
        r"/login|/account|/cart|/checkout",
        r"/sitemap|/search|/404|/error",
    ]

    def __init__(
        self,
        method: ClassificationMethod = ClassificationMethod.RULE_BASED,
        available_groups: list[str] | None = None,
        url_patterns: dict[str, list[str]] | None = None,
        title_keywords: dict[str, list[str]] | None = None,
        skip_patterns: list[str] | None = None,
    ):
        """Initialize classifier.

        Args:
            method: Classification method to use.
            available_groups: List of valid field group names. If provided,
                classification results are filtered to only include these.
            url_patterns: Optional URL pattern -> field groups mapping.
                If not provided, no field group filtering is done (all groups used).
            title_keywords: Optional title keyword -> field groups mapping.
                If not provided, no title-based filtering is done.
            skip_patterns: Optional custom skip patterns. If not provided,
                DEFAULT_SKIP_PATTERNS are used.
        """
        self._method = method
        self._available_groups = set(available_groups) if available_groups else None
        self._url_patterns = url_patterns or {}
        self._title_keywords = title_keywords or {}
        self._skip_patterns = skip_patterns if skip_patterns is not None else self.DEFAULT_SKIP_PATTERNS

    def classify(
        self,
        url: str,
        title: str | None = None,
    ) -> ClassificationResult:
        """Classify a page and determine relevant field groups.

        Args:
            url: Page URL.
            title: Page title (optional but improves accuracy).

        Returns:
            ClassificationResult with page type and relevant groups.
        """
        if self._method == ClassificationMethod.RULE_BASED:
            result = self._classify_rule_based(url, title)
        else:
            # Future: LLM-assisted classification
            result = self._classify_rule_based(url, title)

        # Filter to available groups if specified
        if self._available_groups and result.relevant_groups:
            result.relevant_groups = [
                g for g in result.relevant_groups if g in self._available_groups
            ]

        return result

    def _classify_rule_based(
        self,
        url: str,
        title: str | None,
    ) -> ClassificationResult:
        """Rule-based classification using URL and title patterns."""
        url_lower = url.lower()

        # Check skip patterns first (template-agnostic)
        for pattern in self._skip_patterns:
            if re.search(pattern, url_lower):
                return ClassificationResult(
                    page_type="skip",
                    relevant_groups=[],
                    skip_extraction=True,
                    confidence=0.9,
                    method=ClassificationMethod.RULE_BASED,
                    reasoning=f"URL matches skip pattern: {pattern}",
                )

        # If no URL/title patterns configured, return all groups (template-agnostic default)
        if not self._url_patterns and not self._title_keywords:
            return ClassificationResult(
                page_type="general",
                relevant_groups=[],  # Empty means "use all groups"
                skip_extraction=False,
                confidence=0.5,
                method=ClassificationMethod.RULE_BASED,
                reasoning="No field group patterns configured, using all groups",
            )

        matched_groups: set[str] = set()
        confidence = 0.0
        page_type = "general"
        reasoning_parts: list[str] = []

        # URL pattern matching (only if patterns provided)
        for pattern, groups in self._url_patterns.items():
            if re.search(pattern, url_lower):
                matched_groups.update(groups)
                confidence = max(confidence, 0.8)
                page_type = self._infer_page_type(groups)
                reasoning_parts.append(f"URL matches: {pattern}")

        # Title keyword matching (only if keywords provided)
        if title and self._title_keywords:
            title_lower = title.lower()
            for keyword, groups in self._title_keywords.items():
                if keyword in title_lower:
                    matched_groups.update(groups)
                    confidence = max(confidence, 0.7)
                    reasoning_parts.append(f"Title contains: {keyword}")

        # Default: use ALL groups with low confidence (conservative)
        # This ensures we don't miss important content on unclassified pages
        if not matched_groups:
            return ClassificationResult(
                page_type="general",
                relevant_groups=[],  # Empty means "use all groups"
                skip_extraction=False,
                confidence=0.3,
                method=ClassificationMethod.RULE_BASED,
                reasoning="No patterns matched, using all groups",
            )

        return ClassificationResult(
            page_type=page_type,
            relevant_groups=list(matched_groups),
            skip_extraction=False,
            confidence=confidence,
            method=ClassificationMethod.RULE_BASED,
            reasoning="; ".join(reasoning_parts) if reasoning_parts else None,
        )

    def _infer_page_type(self, groups: list[str]) -> str:
        """Infer page type from matched groups."""
        if any("product" in g for g in groups):
            return "product"
        if "services" in groups:
            return "service"
        if "company_info" in groups:
            return "about"
        return "general"
