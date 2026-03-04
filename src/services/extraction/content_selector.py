"""Content selection for domain-dedup-aware extraction."""


def get_extraction_content(source, *, domain_dedup_enabled: bool = True) -> str:
    """Return cleaned_content if domain_dedup_enabled, else raw content.

    Args:
        source: Source ORM object with content and cleaned_content fields.
        domain_dedup_enabled: Whether domain dedup is enabled. Defaults to True.

    Returns:
        The appropriate content string for extraction.
    """
    if domain_dedup_enabled:
        return (
            source.cleaned_content
            if source.cleaned_content is not None
            else source.content
        )
    return source.content
