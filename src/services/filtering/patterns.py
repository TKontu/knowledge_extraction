"""URL pattern generation for language-based filtering."""

import re
from typing import Sequence

import structlog

from .language import LanguageCode

logger = structlog.get_logger(__name__)


# Default languages to exclude (common European languages)
DEFAULT_EXCLUDED_LANGUAGES = [
    LanguageCode.DE,  # German
    LanguageCode.FI,  # Finnish
    LanguageCode.FR,  # French
    LanguageCode.ES,  # Spanish
    LanguageCode.IT,  # Italian
    LanguageCode.NL,  # Dutch
    LanguageCode.PT,  # Portuguese
    LanguageCode.PL,  # Polish
    LanguageCode.RU,  # Russian
    LanguageCode.SV,  # Swedish
    LanguageCode.NO,  # Norwegian
    LanguageCode.DA,  # Danish
]


def generate_language_exclusion_patterns(
    excluded_languages: Sequence[LanguageCode],
) -> list[str]:
    """Generate regex patterns to exclude language-specific URLs.

    Creates patterns for common language URL structures:
    - Path-based: /de/, /de-DE/, /page-de/
    - Query-based: ?lang=de, ?language=de

    Args:
        excluded_languages: List of language codes to exclude.

    Returns:
        List of regex patterns for Firecrawl exclude_paths.

    Example:
        >>> patterns = generate_language_exclusion_patterns([LanguageCode.DE, LanguageCode.FI])
        >>> patterns
        ['.*/de/.*', '.*/de-.*', '.*-de/.*', '.*/fi/.*', '.*/fi-.*', '.*-fi/.*', ...]
    """
    patterns = []

    for lang in excluded_languages:
        lang_code = lang.value.lower()

        # Path-based patterns (proper regex syntax for Firecrawl)
        patterns.extend(
            [
                f".*/{lang_code}/.*",  # /de/page
                f".*/{lang_code}-.*",  # /de-DE/page
                f".*-{lang_code}/.*",  # /page-de/content
            ]
        )

        # Query parameter patterns (proper regex syntax)
        patterns.extend(
            [
                f".*\\?.*lang={lang_code}.*",  # ?lang=de
                f".*\\?.*language={lang_code}.*",  # ?language=de
                f".*\\?.*locale={lang_code}.*",  # ?locale=de
            ]
        )

    logger.info(
        "generated_language_patterns",
        excluded_languages=[lang.value for lang in excluded_languages],
        pattern_count=len(patterns),
    )

    return patterns


def should_exclude_url(url: str, excluded_patterns: list[str]) -> bool:
    """Check if URL matches any exclusion pattern (client-side validation).

    Args:
        url: URL to check.
        excluded_patterns: List of regex patterns.

    Returns:
        True if URL should be excluded, False otherwise.

    Note:
        Patterns are already in regex format, ready for Firecrawl.
    """
    url_lower = url.lower()

    for pattern in excluded_patterns:
        # Patterns are already regex, use directly
        if re.search(pattern, url_lower):
            logger.debug("url_matches_exclusion_pattern", url=url, pattern=pattern)
            return True

    return False
