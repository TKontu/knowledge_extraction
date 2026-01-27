"""Tests for language detection service."""

from unittest.mock import patch

import pytest

from services.filtering.language import (
    LanguageCode,
    LanguageDetectionService,
    LanguageResult,
    get_language_service,
)


class TestLanguageDetectionService:
    """Tests for LanguageDetectionService."""

    @pytest.fixture
    def service(self) -> LanguageDetectionService:
        """Create service instance."""
        return LanguageDetectionService(confidence_threshold=0.7)

    @pytest.mark.asyncio
    async def test_detects_english_from_content(
        self, service: LanguageDetectionService
    ) -> None:
        """Test detecting English from content."""
        text = "This is an English text. It should be detected as English language content."
        result = await service.detect(text)

        assert result.language == "en"
        assert result.is_english is True
        assert result.confidence >= 0.7
        assert result.detected_from in ("content", "fallback")

    @pytest.mark.asyncio
    async def test_detects_german_from_content(
        self, service: LanguageDetectionService
    ) -> None:
        """Test detecting German from content."""
        text = (
            "Dies ist ein deutscher Text. Er sollte als deutsche Sprache erkannt werden. "
            "Die deutsche Sprache ist eine wichtige europäische Sprache."
        )
        result = await service.detect(text)

        assert result.language == "de"
        assert result.is_english is False
        assert result.confidence >= 0.7
        assert result.detected_from in ("content", "fallback")

    @pytest.mark.asyncio
    async def test_detects_language_from_url_path(
        self, service: LanguageDetectionService
    ) -> None:
        """Test detecting language from URL path."""
        text = "Some text content"
        url = "https://example.com/de/page"
        result = await service.detect(text, url=url)

        assert result.language == "de"
        assert result.is_english is False
        assert result.confidence == 0.95
        assert result.detected_from == "url_path"

    @pytest.mark.asyncio
    async def test_detects_language_from_query_param(
        self, service: LanguageDetectionService
    ) -> None:
        """Test detecting language from query parameter."""
        text = "Some text content"
        url = "https://example.com/page?lang=fi"
        result = await service.detect(text, url=url)

        assert result.language == "fi"
        assert result.is_english is False
        assert result.confidence == 0.90
        assert result.detected_from == "url_query"

    @pytest.mark.asyncio
    async def test_detects_language_from_subdomain(
        self, service: LanguageDetectionService
    ) -> None:
        """Test detecting language from subdomain."""
        text = "Some text content"
        url = "https://de.example.com/page"
        result = await service.detect(text, url=url)

        assert result.language == "de"
        assert result.is_english is False
        assert result.confidence == 0.85
        assert result.detected_from == "url_subdomain"

    @pytest.mark.asyncio
    async def test_fallback_to_english_on_empty_text(
        self, service: LanguageDetectionService
    ) -> None:
        """Test that empty text raises ValueError."""
        with pytest.raises(ValueError, match="Text cannot be empty"):
            await service.detect("")

    @pytest.mark.asyncio
    async def test_fallback_to_english_on_detection_error(
        self, service: LanguageDetectionService
    ) -> None:
        """Test fallback to English when content detection fails."""
        # Mock langdetect to raise exception
        with patch.object(service, "_langdetect_available", True):
            with patch.object(
                service, "_langdetect_sync", side_effect=Exception("Detection failed")
            ):
                text = "Some text content"
                result = await service.detect(text)

                assert result.language == "en"
                assert result.is_english is True
                assert result.confidence == 0.5
                assert result.detected_from == "fallback"

    @pytest.mark.asyncio
    async def test_timeout_handling(self, service: LanguageDetectionService) -> None:
        """Test that detection handles timeout properly."""
        # This is more of an integration test - we'll test the timeout in the worker
        text = "English text for testing"
        result = await service.detect(text)
        assert result is not None

    @pytest.mark.asyncio
    async def test_url_heuristics_take_precedence(
        self, service: LanguageDetectionService
    ) -> None:
        """Test that URL heuristics are checked before content detection."""
        # Even with English content, German URL should be detected as German
        text = "This is English text"
        url = "https://example.com/de/page"
        result = await service.detect(text, url=url)

        assert result.language == "de"
        assert result.detected_from == "url_path"

    @pytest.mark.asyncio
    async def test_handles_url_with_language_in_path_segment(
        self, service: LanguageDetectionService
    ) -> None:
        """Test handling URLs with language codes in path segments."""
        test_cases = [
            ("https://example.com/en/about", "en", "url_path"),
            ("https://example.com/de-DE/products", "de", "url_path"),
            ("https://example.com/page-fr/content", "fr", "url_path"),
        ]

        for url, expected_lang, expected_source in test_cases:
            result = await service.detect("Some text", url=url)
            assert result.language == expected_lang
            assert result.detected_from == expected_source

    @pytest.mark.asyncio
    async def test_does_not_false_positive_on_english_words(
        self, service: LanguageDetectionService
    ) -> None:
        """Test that English words containing language codes don't trigger false positives."""
        # URLs like /order/, /define/, /design/ should not be detected as non-English
        text = "This is English content about ordering products."
        url = "https://example.com/order/123"

        result = await service.detect(text, url=url)

        # Should detect from content, not URL
        assert result.detected_from in ("content", "fallback")
        # Should be English (either from content detection or fallback)
        assert result.is_english is True

    def test_singleton_service(self) -> None:
        """Test that get_language_service returns singleton."""
        service1 = get_language_service(confidence_threshold=0.7)
        service2 = get_language_service(confidence_threshold=0.7)

        # Should be the same instance
        assert service1 is service2

    @pytest.mark.asyncio
    async def test_low_confidence_falls_back_to_english(
        self, service: LanguageDetectionService
    ) -> None:
        """Test that low confidence detection falls back to English."""
        with patch.object(service, "_langdetect_available", True):
            with patch.object(service, "_langdetect_sync", return_value=("de", 0.3)):
                text = "ambiguous short text"
                result = await service.detect(text)

                # Should fallback because confidence < threshold (0.7)
                assert result.language == "en"
                assert result.detected_from == "fallback"


class TestLanguageResult:
    """Tests for LanguageResult dataclass."""

    def test_creates_valid_result(self) -> None:
        """Test creating valid LanguageResult."""
        result = LanguageResult(
            language="en", confidence=0.95, is_english=True, detected_from="content"
        )

        assert result.language == "en"
        assert result.confidence == 0.95
        assert result.is_english is True
        assert result.detected_from == "content"

    def test_validates_confidence_range(self) -> None:
        """Test that confidence must be between 0 and 1."""
        with pytest.raises(ValueError, match="Confidence must be between 0 and 1"):
            LanguageResult(
                language="en", confidence=1.5, is_english=True, detected_from="content"
            )

        with pytest.raises(ValueError, match="Confidence must be between 0 and 1"):
            LanguageResult(
                language="en", confidence=-0.1, is_english=True, detected_from="content"
            )


class TestLanguageCode:
    """Tests for LanguageCode enum."""

    def test_contains_common_languages(self) -> None:
        """Test that enum contains common language codes."""
        assert LanguageCode.EN.value == "en"
        assert LanguageCode.DE.value == "de"
        assert LanguageCode.FI.value == "fi"
        assert LanguageCode.FR.value == "fr"
        assert LanguageCode.ES.value == "es"

    def test_can_be_used_as_string(self) -> None:
        """Test that LanguageCode can be used as string."""
        lang = LanguageCode.DE
        assert isinstance(lang, str)
        assert lang == "de"
