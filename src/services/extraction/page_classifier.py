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

    Uses URL patterns and title keywords to identify page type and select
    only the field groups likely to contain relevant information.
    """

    # URL pattern -> relevant field groups mapping
    URL_PATTERNS: dict[str, list[str]] = {
        # Product pages
        r"/products?($|/)": ["products_gearbox", "products_motor", "products_accessory"],
        r"/gearbox|/gear-?box|/reducer|/gear-?reducer": [
            "products_gearbox",
            "manufacturing",
        ],
        r"/motor|/electric-?motor|/servo|/drive": ["products_motor", "manufacturing"],
        r"/coupling|/shaft|/bearing|/brake|/clutch": ["products_accessory"],
        # Service pages
        r"/service|/repair|/maintenance|/refurbish": ["services"],
        r"/field-?service|/on-?site": ["services"],
        # Company pages
        r"/about|/company|/who-?we-?are|/history": ["company_info", "company_meta"],
        r"/contact|/location|/office|/address": ["company_info"],
        r"/certific|/quality|/iso|/standard": ["company_meta"],
        r"/facilit|/plant|/factory|/manufactur": ["company_meta", "manufacturing"],
    }

    # Patterns that indicate pages to skip entirely
    SKIP_PATTERNS: list[str] = [
        r"/career|/job|/employ|/vacanc",
        r"/news|/blog|/press|/media|/event",
        r"/privacy|/terms|/legal|/cookie|/gdpr",
        r"/login|/account|/cart|/checkout",
        r"/sitemap|/search|/404|/error",
    ]

    # Title keywords -> field groups mapping
    TITLE_KEYWORDS: dict[str, list[str]] = {
        "gearbox": ["products_gearbox"],
        "gear box": ["products_gearbox"],
        "reducer": ["products_gearbox"],
        "planetary": ["products_gearbox"],
        "helical": ["products_gearbox"],
        "motor": ["products_motor"],
        "servo": ["products_motor"],
        "coupling": ["products_accessory"],
        "service": ["services"],
        "repair": ["services"],
        "maintenance": ["services"],
        "about": ["company_info"],
        "contact": ["company_info"],
        "certification": ["company_meta"],
        "iso": ["company_meta"],
    }

    def __init__(
        self,
        method: ClassificationMethod = ClassificationMethod.RULE_BASED,
        available_groups: list[str] | None = None,
    ):
        """Initialize classifier.

        Args:
            method: Classification method to use.
            available_groups: List of valid field group names. If provided,
                classification results are filtered to only include these.
        """
        self._method = method
        self._available_groups = set(available_groups) if available_groups else None

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

        # Check skip patterns first
        for pattern in self.SKIP_PATTERNS:
            if re.search(pattern, url_lower):
                return ClassificationResult(
                    page_type="skip",
                    relevant_groups=[],
                    skip_extraction=True,
                    confidence=0.9,
                    method=ClassificationMethod.RULE_BASED,
                    reasoning=f"URL matches skip pattern: {pattern}",
                )

        matched_groups: set[str] = set()
        confidence = 0.0
        page_type = "general"
        reasoning_parts: list[str] = []

        # URL pattern matching
        for pattern, groups in self.URL_PATTERNS.items():
            if re.search(pattern, url_lower):
                matched_groups.update(groups)
                confidence = max(confidence, 0.8)
                page_type = self._infer_page_type(groups)
                reasoning_parts.append(f"URL matches: {pattern}")

        # Title keyword matching
        if title:
            title_lower = title.lower()
            for keyword, groups in self.TITLE_KEYWORDS.items():
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
