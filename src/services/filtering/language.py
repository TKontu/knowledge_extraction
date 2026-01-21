"""Language detection service for filtering non-English content."""

import asyncio
import re
from dataclasses import dataclass
from enum import Enum
from functools import lru_cache
from typing import Any
from urllib.parse import urlparse, parse_qs

import structlog

logger = structlog.get_logger(__name__)


class LanguageCode(str, Enum):
    """ISO 639-1 language codes."""

    EN = "en"  # English
    DE = "de"  # German
    FI = "fi"  # Finnish
    FR = "fr"  # French
    ES = "es"  # Spanish
    IT = "it"  # Italian
    NL = "nl"  # Dutch
    PT = "pt"  # Portuguese
    PL = "pl"  # Polish
    RU = "ru"  # Russian
    SV = "sv"  # Swedish
    NO = "no"  # Norwegian
    DA = "da"  # Danish
    CS = "cs"  # Czech
    HU = "hu"  # Hungarian
    RO = "ro"  # Romanian
    TR = "tr"  # Turkish
    JA = "ja"  # Japanese
    ZH = "zh"  # Chinese
    KO = "ko"  # Korean
    AR = "ar"  # Arabic


@dataclass
class LanguageResult:
    """Result of language detection."""

    language: str
    confidence: float
    is_english: bool
    detected_from: str  # "url_path", "url_query", "url_subdomain", "content", "fallback"

    def __post_init__(self) -> None:
        """Validate fields."""
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError(f"Confidence must be between 0 and 1, got {self.confidence}")


class LanguageDetectionService:
    """Service for detecting language from URLs and content."""

    # URL patterns for language detection
    PATH_PATTERN = re.compile(r"/(en|de|fi|fr|es|it|nl|pt|pl|ru|sv|no|da|cs|hu|ro|tr|ja|zh|ko|ar)(?:/|$|-)|-(en|de|fi|fr|es|it|nl|pt|pl|ru|sv|no|da|cs|hu|ro|tr|ja|zh|ko|ar)/")
    QUERY_PARAM_PATTERN = re.compile(r"[?&](?:lang|language|locale)=([a-z]{2})")
    SUBDOMAIN_PATTERN = re.compile(r"^(en|de|fi|fr|es|it|nl|pt|pl|ru|sv|no|da|cs|hu|ro|tr|ja|zh|ko|ar)\.")

    def __init__(self, confidence_threshold: float = 0.7) -> None:
        """Initialize language detection service.

        Args:
            confidence_threshold: Minimum confidence for content-based detection (0.0-1.0).
        """
        self.confidence_threshold = confidence_threshold
        self._executor = None
        self._langdetect_available = self._check_langdetect()

    def _check_langdetect(self) -> bool:
        """Check if langdetect is available."""
        try:
            import langdetect  # noqa: F401

            return True
        except ImportError:
            logger.warning("langdetect_not_available", message="Install with: pip install langdetect")
            return False

    async def detect(self, text: str, url: str | None = None) -> LanguageResult:
        """Detect language from text and optional URL.

        Args:
            text: Text content to analyze.
            url: Optional URL for heuristic-based detection.

        Returns:
            LanguageResult with detected language and metadata.

        Raises:
            ValueError: If text is empty.
        """
        if not text or not text.strip():
            raise ValueError("Text cannot be empty")

        # Fast path: Try URL-based detection first
        if url:
            url_result = self._detect_from_url(url)
            if url_result:
                return url_result

        # Slow path: Content-based detection
        try:
            content_result = await self._detect_from_content(text)
            if content_result:
                return content_result
        except Exception as e:
            logger.error("content_detection_failed", error=str(e), exc_info=True)

        # Fallback: Assume English
        return LanguageResult(
            language=LanguageCode.EN.value,
            confidence=0.5,
            is_english=True,
            detected_from="fallback",
        )

    def _detect_from_url(self, url: str) -> LanguageResult | None:
        """Detect language from URL patterns.

        Args:
            url: URL to analyze.

        Returns:
            LanguageResult if language detected from URL, None otherwise.
        """
        try:
            # Check path (e.g., /de/, /de-DE/, /page-de/)
            path_match = self.PATH_PATTERN.search(url.lower())
            if path_match:
                # Match could be in group 1 or group 2 (for -lang/ pattern)
                lang_code = path_match.group(1) or path_match.group(2)
                return LanguageResult(
                    language=lang_code,
                    confidence=0.95,
                    is_english=(lang_code == LanguageCode.EN.value),
                    detected_from="url_path",
                )

            # Check query parameters (e.g., ?lang=de)
            query_match = self.QUERY_PARAM_PATTERN.search(url.lower())
            if query_match:
                lang_code = query_match.group(1)
                return LanguageResult(
                    language=lang_code,
                    confidence=0.90,
                    is_english=(lang_code == LanguageCode.EN.value),
                    detected_from="url_query",
                )

            # Check subdomain (e.g., de.example.com)
            parsed = urlparse(url)
            subdomain_match = self.SUBDOMAIN_PATTERN.match(parsed.hostname or "")
            if subdomain_match:
                lang_code = subdomain_match.group(1)
                return LanguageResult(
                    language=lang_code,
                    confidence=0.85,
                    is_english=(lang_code == LanguageCode.EN.value),
                    detected_from="url_subdomain",
                )

        except Exception as e:
            logger.warning("url_detection_failed", url=url, error=str(e))

        return None

    async def _detect_from_content(self, text: str) -> LanguageResult | None:
        """Detect language from text content using langdetect.

        Args:
            text: Text to analyze.

        Returns:
            LanguageResult if detection succeeds, None otherwise.
        """
        if not self._langdetect_available:
            return None

        try:
            # Run blocking langdetect in executor
            loop = asyncio.get_event_loop()
            if self._executor is None:
                from concurrent.futures import ThreadPoolExecutor

                self._executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="langdetect")

            lang_code, confidence = await loop.run_in_executor(
                self._executor, self._langdetect_sync, text
            )

            # Check confidence threshold
            if confidence < self.confidence_threshold:
                logger.debug(
                    "low_confidence_detection",
                    language=lang_code,
                    confidence=confidence,
                    threshold=self.confidence_threshold,
                )
                return None

            return LanguageResult(
                language=lang_code,
                confidence=confidence,
                is_english=(lang_code == LanguageCode.EN.value),
                detected_from="content",
            )

        except Exception as e:
            logger.error("content_detection_error", error=str(e), exc_info=True)
            return None

    def _langdetect_sync(self, text: str) -> tuple[str, float]:
        """Synchronous langdetect wrapper.

        Args:
            text: Text to analyze.

        Returns:
            Tuple of (language_code, confidence).
        """
        import langdetect
        from langdetect import DetectorFactory

        # Set seed for reproducibility
        DetectorFactory.seed = 0

        # Detect language with probabilities
        probabilities = langdetect.detect_langs(text)

        if not probabilities:
            raise ValueError("No language detected")

        # Get top result
        top = probabilities[0]
        return (top.lang, top.prob)

    def __del__(self) -> None:
        """Cleanup executor on deletion."""
        if self._executor:
            self._executor.shutdown(wait=False)


# Singleton instance
_service_instance: LanguageDetectionService | None = None


@lru_cache(maxsize=1)
def get_language_service(confidence_threshold: float = 0.7) -> LanguageDetectionService:
    """Get singleton language detection service.

    Args:
        confidence_threshold: Minimum confidence for content-based detection.

    Returns:
        LanguageDetectionService instance.
    """
    global _service_instance
    if _service_instance is None:
        _service_instance = LanguageDetectionService(confidence_threshold=confidence_threshold)
    return _service_instance
