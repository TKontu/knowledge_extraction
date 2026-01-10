"""Fact validator for filtering and validating extracted facts."""

from models import ExtractedFact, ExtractionProfile


class FactValidator:
    """Validates and filters extracted facts based on quality criteria."""

    def __init__(self, min_confidence: float = 0.5, min_fact_length: int = 1):
        """Initialize validator with filtering criteria.

        Args:
            min_confidence: Minimum confidence threshold (0.0 to 1.0).
            min_fact_length: Minimum length for fact text.
        """
        self.min_confidence = min_confidence
        self.min_fact_length = min_fact_length

    def validate(
        self, facts: list[ExtractedFact], profile: ExtractionProfile
    ) -> list[ExtractedFact]:
        """Validate and filter facts based on quality criteria.

        Args:
            facts: List of extracted facts to validate.
            profile: Extraction profile with allowed categories.

        Returns:
            List of valid facts that pass all criteria.
        """
        valid_facts = []

        for fact in facts:
            if self._is_valid_fact(fact, profile):
                valid_facts.append(fact)

        return valid_facts

    def _is_valid_fact(self, fact: ExtractedFact, profile: ExtractionProfile) -> bool:
        """Check if a single fact meets all validation criteria.

        Args:
            fact: Fact to validate.
            profile: Extraction profile with allowed categories.

        Returns:
            True if fact is valid, False otherwise.
        """
        # Check if fact text is not empty
        if not fact.fact or not fact.fact.strip():
            return False

        # Check minimum length
        if len(fact.fact) < self.min_fact_length:
            return False

        # Check confidence threshold
        if fact.confidence < self.min_confidence:
            return False

        # Check if category is in profile's allowed categories
        if fact.category not in profile.categories:
            return False

        return True
