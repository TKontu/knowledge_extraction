"""URL pattern generation for language-based filtering."""

from collections.abc import Sequence

import structlog

from .language import LanguageCode

logger = structlog.get_logger(__name__)


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
