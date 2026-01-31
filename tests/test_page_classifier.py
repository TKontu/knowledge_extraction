"""Tests for PageClassifier service."""

import pytest

from services.extraction.page_classifier import (
    ClassificationMethod,
    ClassificationResult,
    PageClassifier,
)


# Sample patterns for testing (similar to drivetrain template)
SAMPLE_URL_PATTERNS = {
    r"/products?($|/)": ["products_gearbox", "products_motor", "products_accessory"],
    r"/gearbox|/gear-?box|/reducer|/gear-?reducer": ["products_gearbox", "manufacturing"],
    r"/motor|/electric-?motor|/servo|/drive": ["products_motor", "manufacturing"],
    r"/coupling|/shaft|/bearing|/brake|/clutch": ["products_accessory"],
    r"/service|/repair|/maintenance|/refurbish": ["services"],
    r"/field-?service|/on-?site": ["services"],
    r"/about|/company|/who-?we-?are|/history": ["company_info", "company_meta"],
    r"/contact|/location|/office|/address": ["company_info"],
    r"/certific|/quality|/iso|/standard": ["company_meta"],
    r"/facilit|/plant|/factory|/manufactur": ["company_meta", "manufacturing"],
}

SAMPLE_TITLE_KEYWORDS = {
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


class TestPageClassifier:
    """Tests for page classification system."""

    @pytest.fixture
    def classifier(self):
        """Create a classifier with sample patterns for testing field group filtering."""
        return PageClassifier(
            method=ClassificationMethod.RULE_BASED,
            url_patterns=SAMPLE_URL_PATTERNS,
            title_keywords=SAMPLE_TITLE_KEYWORDS,
        )

    @pytest.fixture
    def skip_only_classifier(self):
        """Create a classifier with only skip patterns (template-agnostic default)."""
        return PageClassifier(method=ClassificationMethod.RULE_BASED)

    def test_product_page_classification(self, classifier):
        """Product URLs should map to product field groups."""
        result = classifier.classify(
            url="https://example.com/products/gearboxes/planetary",
            title="Planetary Gearboxes - Example Corp",
        )
        assert result.page_type == "product"
        assert "products_gearbox" in result.relevant_groups
        assert not result.skip_extraction
        assert result.confidence >= 0.7

    def test_gearbox_url_classification(self, classifier):
        """Gearbox-specific URLs should map to gearbox groups."""
        result = classifier.classify(
            url="https://example.com/gearbox/helical-series",
            title="Helical Gearboxes",
        )
        assert "products_gearbox" in result.relevant_groups
        assert "manufacturing" in result.relevant_groups
        assert result.confidence >= 0.7

    def test_motor_url_classification(self, classifier):
        """Motor URLs should map to motor field groups."""
        result = classifier.classify(
            url="https://example.com/electric-motor/servo-drives",
            title="Servo Motors",
        )
        assert "products_motor" in result.relevant_groups
        assert not result.skip_extraction

    def test_service_page_classification(self, classifier):
        """Service URLs should map to services field group."""
        result = classifier.classify(
            url="https://example.com/services/repair",
            title="Repair Services",
        )
        assert "services" in result.relevant_groups
        assert not result.skip_extraction

    def test_about_page_classification(self, classifier):
        """About URLs should map to company info groups."""
        result = classifier.classify(
            url="https://example.com/about-us",
            title="About Our Company",
        )
        assert "company_info" in result.relevant_groups
        assert result.page_type == "about"

    def test_contact_page_classification(self, classifier):
        """Contact URLs should map to company info."""
        result = classifier.classify(
            url="https://example.com/contact",
            title="Contact Us",
        )
        assert "company_info" in result.relevant_groups

    def test_skip_career_page(self, classifier):
        """Career pages should be skipped."""
        result = classifier.classify(
            url="https://example.com/careers/engineer",
            title="Join Our Team",
        )
        assert result.skip_extraction
        assert result.relevant_groups == []
        assert result.page_type == "skip"
        assert result.confidence >= 0.8

    def test_skip_jobs_page(self, classifier):
        """Jobs pages should be skipped."""
        result = classifier.classify(
            url="https://example.com/jobs/apply",
            title="Job Openings",
        )
        assert result.skip_extraction

    def test_skip_news_page(self, classifier):
        """News/blog pages should be skipped."""
        result = classifier.classify(
            url="https://example.com/news/2024/announcement",
            title="Company News",
        )
        assert result.skip_extraction

    def test_skip_blog_page(self, classifier):
        """Blog pages should be skipped."""
        result = classifier.classify(
            url="https://example.com/blog/industry-trends",
            title="Industry Blog",
        )
        assert result.skip_extraction

    def test_skip_privacy_page(self, classifier):
        """Privacy/legal pages should be skipped."""
        result = classifier.classify(
            url="https://example.com/privacy-policy",
            title="Privacy Policy",
        )
        assert result.skip_extraction

    def test_skip_terms_page(self, classifier):
        """Terms pages should be skipped."""
        result = classifier.classify(
            url="https://example.com/terms-of-service",
            title="Terms of Service",
        )
        assert result.skip_extraction

    def test_skip_login_page(self, classifier):
        """Login pages should be skipped."""
        result = classifier.classify(
            url="https://example.com/login",
            title="Customer Login",
        )
        assert result.skip_extraction

    def test_fallback_uses_all_groups(self, classifier):
        """Unknown pages should return empty groups (meaning use all)."""
        result = classifier.classify(
            url="https://example.com/xyz123",
            title="Some Page",
        )
        assert result.relevant_groups == []  # Empty = use all
        assert not result.skip_extraction
        assert result.confidence < 0.5
        assert result.page_type == "general"

    def test_template_agnostic_no_patterns(self, skip_only_classifier):
        """Classifier without patterns should return all groups for non-skip pages."""
        result = skip_only_classifier.classify(
            url="https://example.com/products/gearboxes",
            title="Gearbox Products",
        )
        # No field group filtering without patterns
        assert result.relevant_groups == []  # Empty = use all groups
        assert not result.skip_extraction
        assert result.page_type == "general"

    def test_template_agnostic_still_skips(self, skip_only_classifier):
        """Classifier without patterns should still skip irrelevant pages."""
        result = skip_only_classifier.classify(
            url="https://example.com/careers/apply",
            title="Join Our Team",
        )
        assert result.skip_extraction
        assert result.page_type == "skip"

    def test_title_keyword_matching_gearbox(self, classifier):
        """Title keywords should influence classification."""
        result = classifier.classify(
            url="https://example.com/page",  # Generic URL
            title="Industrial Gearbox Solutions",
        )
        assert "products_gearbox" in result.relevant_groups

    def test_title_keyword_matching_motor(self, classifier):
        """Motor keyword in title should classify correctly."""
        result = classifier.classify(
            url="https://example.com/page",
            title="High-Performance Motor Catalog",
        )
        assert "products_motor" in result.relevant_groups

    def test_title_keyword_matching_service(self, classifier):
        """Service keyword in title should classify correctly."""
        result = classifier.classify(
            url="https://example.com/page",
            title="Maintenance Service Programs",
        )
        assert "services" in result.relevant_groups

    def test_available_groups_filtering(self):
        """Classification should filter to available groups."""
        classifier = PageClassifier(
            available_groups=["company_info", "services"],
            url_patterns=SAMPLE_URL_PATTERNS,
            title_keywords=SAMPLE_TITLE_KEYWORDS,
        )
        result = classifier.classify(
            url="https://example.com/products/gearboxes",
            title="Gearboxes",
        )
        # products_gearbox not in available_groups, so filtered out
        assert "products_gearbox" not in result.relevant_groups
        assert "products_motor" not in result.relevant_groups

    def test_available_groups_preserves_matching(self):
        """Available groups filter should preserve matching groups."""
        classifier = PageClassifier(
            available_groups=["company_info", "services", "products_gearbox"],
            url_patterns=SAMPLE_URL_PATTERNS,
            title_keywords=SAMPLE_TITLE_KEYWORDS,
        )
        result = classifier.classify(
            url="https://example.com/products/gearboxes",
            title="Planetary Gearboxes",
        )
        assert "products_gearbox" in result.relevant_groups
        # products_motor not in available, should be filtered out
        assert "products_motor" not in result.relevant_groups

    def test_multiple_patterns_match(self, classifier):
        """Multiple matching patterns should accumulate groups."""
        result = classifier.classify(
            url="https://example.com/about/manufacturing",
            title="About Our Manufacturing",
        )
        assert "company_info" in result.relevant_groups
        assert "manufacturing" in result.relevant_groups or "company_meta" in result.relevant_groups

    def test_case_insensitive_url_matching(self, classifier):
        """URL matching should be case insensitive."""
        result = classifier.classify(
            url="https://example.com/PRODUCTS/GEARBOX",
            title="Gearbox",
        )
        assert "products_gearbox" in result.relevant_groups

    def test_case_insensitive_title_matching(self, classifier):
        """Title matching should be case insensitive."""
        result = classifier.classify(
            url="https://example.com/page",
            title="INDUSTRIAL GEARBOX",
        )
        assert "products_gearbox" in result.relevant_groups

    def test_classification_result_has_method(self, classifier):
        """Classification result should include method used."""
        result = classifier.classify(
            url="https://example.com/products",
            title="Products",
        )
        assert result.method == ClassificationMethod.RULE_BASED

    def test_classification_result_has_reasoning(self, classifier):
        """Classification result should include reasoning."""
        result = classifier.classify(
            url="https://example.com/products/gearboxes",
            title="Gearboxes",
        )
        assert result.reasoning is not None
        assert "URL matches" in result.reasoning or "Title contains" in result.reasoning

    def test_skip_result_has_reasoning(self, classifier):
        """Skip classification should include reasoning."""
        result = classifier.classify(
            url="https://example.com/careers",
            title="Careers",
        )
        assert result.reasoning is not None
        assert "skip pattern" in result.reasoning

    def test_certification_page_classification(self, classifier):
        """Certification pages should map to company_meta."""
        result = classifier.classify(
            url="https://example.com/quality/certifications",
            title="ISO Certifications",
        )
        assert "company_meta" in result.relevant_groups

    def test_facility_page_classification(self, classifier):
        """Facility pages should map to company_meta and manufacturing."""
        result = classifier.classify(
            url="https://example.com/facilities",
            title="Our Manufacturing Facilities",
        )
        assert "company_meta" in result.relevant_groups or "manufacturing" in result.relevant_groups


class TestClassificationResult:
    """Tests for ClassificationResult dataclass."""

    def test_dataclass_fields(self):
        """ClassificationResult should have all required fields."""
        result = ClassificationResult(
            page_type="product",
            relevant_groups=["products_gearbox"],
            skip_extraction=False,
            confidence=0.8,
            method=ClassificationMethod.RULE_BASED,
            reasoning="Test",
        )
        assert result.page_type == "product"
        assert result.relevant_groups == ["products_gearbox"]
        assert result.skip_extraction is False
        assert result.confidence == 0.8
        assert result.method == ClassificationMethod.RULE_BASED
        assert result.reasoning == "Test"

    def test_optional_reasoning(self):
        """Reasoning should be optional."""
        result = ClassificationResult(
            page_type="general",
            relevant_groups=[],
            skip_extraction=False,
            confidence=0.3,
            method=ClassificationMethod.RULE_BASED,
        )
        assert result.reasoning is None


class TestClassificationMethod:
    """Tests for ClassificationMethod enum."""

    def test_enum_values(self):
        """ClassificationMethod should have expected values."""
        assert ClassificationMethod.RULE_BASED.value == "rule"
        assert ClassificationMethod.LLM_ASSISTED.value == "llm"
        assert ClassificationMethod.HYBRID.value == "hybrid"

    def test_string_conversion(self):
        """ClassificationMethod should convert to string."""
        assert str(ClassificationMethod.RULE_BASED) == "ClassificationMethod.RULE_BASED"
        assert ClassificationMethod.RULE_BASED.value == "rule"
