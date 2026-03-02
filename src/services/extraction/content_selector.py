"""Content selection for domain-dedup-aware extraction."""

from config import settings as app_settings


def get_extraction_content(source) -> str:
    """Return cleaned_content if domain_dedup_enabled, else raw content.

    Args:
        source: Source ORM object with content and cleaned_content fields.

    Returns:
        The appropriate content string for extraction.
    """
    if app_settings.domain_dedup_enabled:
        return (
            source.cleaned_content
            if source.cleaned_content is not None
            else source.content
        )
    return source.content
