"""Tests for content_selector module."""

from unittest.mock import Mock, patch

from services.extraction.content_selector import get_extraction_content


class TestGetExtractionContent:
    """Tests for get_extraction_content function."""

    def test_returns_raw_content_when_dedup_disabled(self):
        """Returns source.content when domain_dedup_enabled is False."""
        source = Mock()
        source.content = "raw content"
        source.cleaned_content = "cleaned content"

        with patch(
            "services.extraction.content_selector.app_settings"
        ) as mock_settings:
            mock_settings.extraction.domain_dedup_enabled = False
            result = get_extraction_content(source)

        assert result == "raw content"

    def test_returns_cleaned_content_when_dedup_enabled(self):
        """Returns cleaned_content when domain_dedup_enabled and cleaned exists."""
        source = Mock()
        source.content = "raw content"
        source.cleaned_content = "cleaned content"

        with patch(
            "services.extraction.content_selector.app_settings"
        ) as mock_settings:
            mock_settings.extraction.domain_dedup_enabled = True
            result = get_extraction_content(source)

        assert result == "cleaned content"

    def test_falls_back_to_raw_when_cleaned_is_none(self):
        """Returns raw content when dedup enabled but cleaned_content is None."""
        source = Mock()
        source.content = "raw content"
        source.cleaned_content = None

        with patch(
            "services.extraction.content_selector.app_settings"
        ) as mock_settings:
            mock_settings.extraction.domain_dedup_enabled = True
            result = get_extraction_content(source)

        assert result == "raw content"
