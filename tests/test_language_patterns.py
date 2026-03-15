"""Tests for language URL pattern generation."""

from services.filtering.language import LanguageCode
from services.filtering.patterns import generate_language_exclusion_patterns


class TestLanguagePatterns:
    """Tests for language pattern generation."""

    def test_generates_path_patterns(self) -> None:
        """Test generating path-based patterns."""
        patterns = generate_language_exclusion_patterns([LanguageCode.DE])

        assert ".*/de/.*" in patterns
        assert ".*/de-.*" in patterns
        assert ".*-de/.*" in patterns

    def test_generates_query_patterns(self) -> None:
        """Test generating query parameter patterns."""
        patterns = generate_language_exclusion_patterns([LanguageCode.FI])

        assert ".*\\?.*lang=fi.*" in patterns
        assert ".*\\?.*language=fi.*" in patterns
        assert ".*\\?.*locale=fi.*" in patterns

    def test_generates_patterns_for_multiple_languages(self) -> None:
        """Test generating patterns for multiple languages."""
        patterns = generate_language_exclusion_patterns(
            [LanguageCode.DE, LanguageCode.FI]
        )

        # Should have patterns for both languages
        assert ".*/de/.*" in patterns
        assert ".*/fi/.*" in patterns
        assert len(patterns) == 12  # 6 patterns per language

    def test_empty_language_list(self) -> None:
        """Test generating patterns with empty language list."""
        patterns = generate_language_exclusion_patterns([])
        assert patterns == []

    def test_pattern_count_matches_expected(self) -> None:
        """Test that pattern count is correct."""
        # Each language should generate 6 patterns (3 path + 3 query)
        patterns_one_lang = generate_language_exclusion_patterns([LanguageCode.DE])
        assert len(patterns_one_lang) == 6

        patterns_two_langs = generate_language_exclusion_patterns(
            [LanguageCode.DE, LanguageCode.FI]
        )
        assert len(patterns_two_langs) == 12

        patterns_three_langs = generate_language_exclusion_patterns(
            [LanguageCode.DE, LanguageCode.FI, LanguageCode.FR]
        )
        assert len(patterns_three_langs) == 18
