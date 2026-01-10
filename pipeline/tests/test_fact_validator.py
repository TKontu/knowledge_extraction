"""Tests for fact validator."""

import pytest

from models import ExtractedFact, ExtractionProfile


@pytest.fixture
def sample_profile():
    """Create a sample extraction profile."""
    return ExtractionProfile(
        name="technical_specs",
        categories=["specs", "hardware", "requirements"],
        prompt_focus="Hardware specifications",
        depth="detailed",
        is_builtin=True,
    )


@pytest.fixture
def valid_facts():
    """Create a list of valid extracted facts."""
    return [
        ExtractedFact(
            fact="Minimum 8GB RAM required",
            category="hardware",
            confidence=0.95,
            source_quote="8GB RAM",
        ),
        ExtractedFact(
            fact="Supports Docker 20.10 or higher",
            category="requirements",
            confidence=0.9,
            source_quote="Docker 20.10",
        ),
        ExtractedFact(
            fact="Maximum throughput is 10,000 req/s",
            category="specs",
            confidence=0.85,
            source_quote="10,000 req/s",
        ),
    ]


class TestFactValidator:
    """Tests for FactValidator class."""

    def test_validate_all_valid_facts(self, sample_profile, valid_facts):
        """Test validating all valid facts."""
        from services.extraction.validator import FactValidator

        validator = FactValidator()
        result = validator.validate(valid_facts, sample_profile)

        assert len(result) == 3
        assert all(isinstance(f, ExtractedFact) for f in result)

    def test_filter_by_confidence_threshold(self, sample_profile, valid_facts):
        """Test filtering facts below confidence threshold."""
        from services.extraction.validator import FactValidator

        # Add a low-confidence fact
        low_confidence_fact = ExtractedFact(
            fact="Maybe supports ARM architecture",
            category="specs",
            confidence=0.3,
            source_quote="ARM",
        )
        all_facts = valid_facts + [low_confidence_fact]

        validator = FactValidator(min_confidence=0.5)
        result = validator.validate(all_facts, sample_profile)

        # Should filter out the low-confidence fact
        assert len(result) == 3
        assert all(f.confidence >= 0.5 for f in result)

    def test_filter_invalid_categories(self, sample_profile, valid_facts):
        """Test filtering facts with invalid categories."""
        from services.extraction.validator import FactValidator

        # Add a fact with invalid category
        invalid_category_fact = ExtractedFact(
            fact="Some pricing information",
            category="pricing",  # Not in profile categories
            confidence=0.9,
            source_quote="pricing",
        )
        all_facts = valid_facts + [invalid_category_fact]

        validator = FactValidator()
        result = validator.validate(all_facts, sample_profile)

        # Should filter out the invalid category fact
        assert len(result) == 3
        categories = [f.category for f in result]
        assert "pricing" not in categories

    def test_filter_empty_facts(self, sample_profile, valid_facts):
        """Test filtering facts with empty text."""
        from services.extraction.validator import FactValidator

        # Add facts with empty or whitespace-only text
        empty_facts = [
            ExtractedFact(fact="", category="specs", confidence=0.9),
            ExtractedFact(fact="   ", category="specs", confidence=0.9),
            ExtractedFact(fact="\n\t", category="specs", confidence=0.9),
        ]
        all_facts = valid_facts + empty_facts

        validator = FactValidator()
        result = validator.validate(all_facts, sample_profile)

        # Should filter out empty facts
        assert len(result) == 3
        assert all(f.fact.strip() for f in result)

    def test_filter_short_facts(self, sample_profile, valid_facts):
        """Test filtering facts that are too short."""
        from services.extraction.validator import FactValidator

        # Add very short facts
        short_facts = [
            ExtractedFact(fact="Yes", category="specs", confidence=0.9),
            ExtractedFact(fact="No", category="specs", confidence=0.9),
            ExtractedFact(fact="OK", category="specs", confidence=0.9),
        ]
        all_facts = valid_facts + short_facts

        validator = FactValidator(min_fact_length=10)
        result = validator.validate(all_facts, sample_profile)

        # Should filter out facts shorter than 10 characters
        assert len(result) == 3
        assert all(len(f.fact) >= 10 for f in result)

    def test_validate_empty_list(self, sample_profile):
        """Test validating an empty list of facts."""
        from services.extraction.validator import FactValidator

        validator = FactValidator()
        result = validator.validate([], sample_profile)

        assert result == []

    def test_custom_confidence_threshold(self, sample_profile):
        """Test using a custom confidence threshold."""
        from services.extraction.validator import FactValidator

        facts = [
            ExtractedFact(fact="High confidence fact", category="specs", confidence=0.95),
            ExtractedFact(fact="Medium confidence fact", category="specs", confidence=0.75),
            ExtractedFact(fact="Low confidence fact", category="specs", confidence=0.6),
        ]

        # Use higher threshold
        validator = FactValidator(min_confidence=0.8)
        result = validator.validate(facts, sample_profile)

        assert len(result) == 1
        assert result[0].fact == "High confidence fact"

    def test_validate_preserves_fact_order(self, sample_profile, valid_facts):
        """Test that validation preserves the order of facts."""
        from services.extraction.validator import FactValidator

        validator = FactValidator()
        result = validator.validate(valid_facts, sample_profile)

        # Order should be preserved
        assert result[0].fact == "Minimum 8GB RAM required"
        assert result[1].fact == "Supports Docker 20.10 or higher"
        assert result[2].fact == "Maximum throughput is 10,000 req/s"

    def test_validate_with_missing_source_quote(self, sample_profile):
        """Test validating facts without source quotes."""
        from services.extraction.validator import FactValidator

        facts = [
            ExtractedFact(
                fact="Some technical fact",
                category="specs",
                confidence=0.9,
                source_quote=None,  # No source quote
            )
        ]

        validator = FactValidator()
        result = validator.validate(facts, sample_profile)

        # Should still be valid (source_quote is optional)
        assert len(result) == 1
        assert result[0].source_quote is None

    def test_multiple_validation_criteria(self, sample_profile):
        """Test applying multiple validation criteria at once."""
        from services.extraction.validator import FactValidator

        facts = [
            ExtractedFact(fact="Valid fact", category="specs", confidence=0.9),
            ExtractedFact(fact="Too short", category="specs", confidence=0.9),  # Too short
            ExtractedFact(fact="Invalid category fact", category="pricing", confidence=0.9),  # Wrong category
            ExtractedFact(fact="Low confidence fact here", category="specs", confidence=0.4),  # Low confidence
            ExtractedFact(fact="", category="specs", confidence=0.9),  # Empty
            ExtractedFact(fact="Another valid fact here", category="hardware", confidence=0.95),  # Valid
        ]

        validator = FactValidator(min_confidence=0.5, min_fact_length=10)
        result = validator.validate(facts, sample_profile)

        # Should only have 2 valid facts
        assert len(result) == 2
        assert result[0].fact == "Valid fact"
        assert result[1].fact == "Another valid fact here"

    def test_validator_with_zero_confidence_threshold(self, sample_profile):
        """Test validator with 0.0 confidence threshold."""
        from services.extraction.validator import FactValidator

        facts = [
            ExtractedFact(fact="Very low confidence", category="specs", confidence=0.01),
        ]

        validator = FactValidator(min_confidence=0.0)
        result = validator.validate(facts, sample_profile)

        assert len(result) == 1  # Should accept even very low confidence
