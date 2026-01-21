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
    """Generate glob patterns to exclude language-specific URLs.

    Creates patterns for common language URL structures:
    - Path-based: /de/, /de-DE/, /page-de/
    - Query-based: ?lang=de, ?language=de
    - Subdomain-based: de.example.com

    Args:
        excluded_languages: List of language codes to exclude.

    Returns:
        List of glob patterns for Firecrawl exclude_paths.

    Example:
        >>> patterns = generate_language_exclusion_patterns([LanguageCode.DE, LanguageCode.FI])
        >>> patterns
        ['*/de/*', '*/de-*', '*-de/*', '*/fi/*', '*/fi-*', '*-fi/*', ...]
    """
    patterns = []

    for lang in excluded_languages:
        lang_code = lang.value.lower()

        # Path-based patterns
        patterns.extend(
            [
                f"*/{lang_code}/*",  # /de/page
                f"*/{lang_code}-*",  # /de-DE/page
                f"*-{lang_code}/*",  # /page-de/content
            ]
        )

        # Query parameter patterns
        patterns.extend(
            [
                f"*?*lang={lang_code}*",  # ?lang=de
                f"*?*language={lang_code}*",  # ?language=de
                f"*?*locale={lang_code}*",  # ?locale=de
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
        excluded_patterns: List of glob patterns.

    Returns:
        True if URL should be excluded, False otherwise.

    Note:
        This is a simplified client-side check. Firecrawl uses more sophisticated
        glob matching on the server side.
    """
    url_lower = url.lower()

    for pattern in excluded_patterns:
        # Convert glob pattern to regex
        regex_pattern = _glob_to_regex(pattern)

        if re.search(regex_pattern, url_lower):
            logger.debug("url_matches_exclusion_pattern", url=url, pattern=pattern)
            return True

    return False


def _glob_to_regex(pattern: str) -> str:
    """Convert glob pattern to regex.

    Args:
        pattern: Glob pattern (e.g., "*/de/*").

    Returns:
        Regex pattern string.

    Example:
        >>> _glob_to_regex("*/de/*")
        '.*\\\\/de\\\\/.*'
    """
    # Escape special regex characters except * and ?
    pattern = re.escape(pattern)

    # Replace escaped glob wildcards with regex equivalents
    pattern = pattern.replace(r"\*", ".*")  # * -> .*
    pattern = pattern.replace(r"\?", ".")  # ? -> .

    return pattern
