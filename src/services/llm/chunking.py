"""Document chunking for LLM processing."""

import re

from models import DocumentChunk


def count_tokens(text: str) -> int:
    """Approximate token count, CJK-aware.

    English/Latin: ~4 chars per token.
    CJK characters: ~1.5 chars per token (conservative).

    Args:
        text: Input text to count tokens for.

    Returns:
        Approximate number of tokens.
    """
    cjk_count = 0
    for ch in text:
        cp = ord(ch)
        if (
            0x4E00 <= cp <= 0x9FFF      # CJK Unified Ideographs
            or 0x3400 <= cp <= 0x4DBF   # CJK Extension A
            or 0xF900 <= cp <= 0xFAFF   # CJK Compatibility
            or 0x3040 <= cp <= 0x309F   # Hiragana
            or 0x30A0 <= cp <= 0x30FF   # Katakana
            or 0xAC00 <= cp <= 0xD7AF   # Hangul
        ):
            cjk_count += 1

    non_cjk_count = len(text) - cjk_count
    return (non_cjk_count // 4) + int(cjk_count / 1.5)


def _get_tail_text(text: str, target_tokens: int) -> str:
    """Get paragraph-aligned tail text of approximately target_tokens.

    Takes whole paragraphs from the end of text until the target token
    budget is reached. Hard-caps output to target_tokens even if a single
    paragraph is larger.

    Args:
        text: Source text to extract tail from.
        target_tokens: Approximate token budget for the tail.

    Returns:
        Tail text within the token budget.
    """
    if not text.strip() or target_tokens <= 0:
        return ""

    paragraphs = [p for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return ""

    max_chars = target_tokens * 4  # 4 chars ≈ 1 token

    # Collect paragraphs from the end
    collected: list[str] = []
    total = 0
    for para in reversed(paragraphs):
        para_tokens = count_tokens(para)
        if total + para_tokens > target_tokens and collected:
            break
        collected.append(para)
        total += para_tokens

    collected.reverse()
    result = "\n\n".join(collected)

    # Hard-cap: if a single large paragraph exceeded the budget,
    # take only the last max_chars characters
    if len(result) > max_chars:
        result = result[-max_chars:]

    return result


def split_by_headers(markdown: str) -> list[str]:
    """Split markdown on H2+ headers, keeping header with content.

    Splits on any header level >= 2 (##, ###, ####, etc.).
    H1 (#) is excluded as it's typically the page title.

    Args:
        markdown: Markdown text to split.

    Returns:
        List of sections, each starting with a header or content before first header.
    """
    # Split before any header level >= 2
    pattern = r"(?=^#{2,} )"
    sections = re.split(pattern, markdown, flags=re.MULTILINE)

    # Filter empty sections and strip whitespace
    sections = [s.strip() for s in sections if s.strip()]

    return sections


def extract_header_path(markdown: str) -> list[str]:
    """Extract header breadcrumb path from markdown.

    Args:
        markdown: Markdown text to extract headers from.

    Returns:
        List of headers forming a breadcrumb path.
    """
    lines = markdown.split("\n")
    headers: list[str] = []

    for line in lines:
        line = line.strip()
        if line.startswith("# ") and not line.startswith("## "):
            # H1 - reset path
            headers = [line[2:].strip()]
        elif line.startswith("## ") and not line.startswith("### "):
            # H2 - keep H1, replace rest
            if headers:
                headers = headers[:1] + [line[3:].strip()]
            else:
                headers = [line[3:].strip()]
        elif line.startswith("### "):
            # H3 - keep H1 and H2, replace rest
            if len(headers) >= 2:
                headers = headers[:2] + [line[4:].strip()]
            elif len(headers) == 1:
                headers = headers[:1] + [line[4:].strip()]
            else:
                headers = [line[4:].strip()]

    return headers


def split_large_section(section: str, max_tokens: int) -> list[str]:
    """Split a large section into smaller chunks by paragraphs.

    Args:
        section: Text section to split.
        max_tokens: Maximum tokens per chunk.

    Returns:
        List of smaller text chunks.
    """
    # Extract header if present
    header = ""
    content = section
    lines = section.split("\n", 1)
    if lines[0].startswith("#"):
        header = lines[0] + "\n"
        content = lines[1] if len(lines) > 1 else ""
        # Reduce max_tokens by header size to account for it
        header_tokens = count_tokens(header)
        adjusted_max = max(max_tokens - header_tokens, max_tokens // 2)
    else:
        adjusted_max = max_tokens

    # Split by double newlines (paragraphs)
    paragraphs = content.split("\n\n") if content else []
    chunks: list[str] = []
    current_chunk = ""
    current_tokens = 0

    for para in paragraphs:
        if not para.strip():
            continue

        para_tokens = count_tokens(para)

        # If single paragraph exceeds max, split by words
        if para_tokens > adjusted_max:
            # Save current chunk if exists
            if current_chunk:
                chunk_text = header + current_chunk if header else current_chunk
                chunks.append(chunk_text.strip())
                current_chunk = ""
                current_tokens = 0

            # Split by words
            words = para.split()
            word_chunk = ""
            for word in words:
                test_chunk = word_chunk + word + " "
                test_chunk_with_header = header + test_chunk if header else test_chunk
                if count_tokens(test_chunk_with_header) <= max_tokens:
                    word_chunk = test_chunk
                else:
                    if word_chunk:
                        chunk_text = header + word_chunk if header else word_chunk
                        chunks.append(chunk_text.strip())
                    word_chunk = word + " "
            if word_chunk:
                chunk_text = header + word_chunk if header else word_chunk
                chunks.append(chunk_text.strip())

        # Paragraph fits in current chunk
        elif current_tokens + para_tokens <= adjusted_max:
            current_chunk += "\n\n" + para if current_chunk else para
            current_tokens += para_tokens

        # Start new chunk
        else:
            if current_chunk:
                chunk_text = header + current_chunk if header else current_chunk
                chunks.append(chunk_text.strip())
            current_chunk = para
            current_tokens = para_tokens

    # Don't forget last chunk
    if current_chunk:
        chunk_text = header + current_chunk if header else current_chunk
        chunks.append(chunk_text.strip())

    return chunks if chunks else [section]


def chunk_document(
    markdown: str, max_tokens: int = 5000, overlap_tokens: int = 0
) -> list[DocumentChunk]:
    """Chunk document semantically, respecting markdown structure.

    Args:
        markdown: Markdown text to chunk.
        max_tokens: Maximum tokens per chunk (default 5000 = ~20K chars,
            aligned with EXTRACTION_CONTENT_LIMIT).
        overlap_tokens: Token overlap between consecutive chunks.
            When >0, each chunk after the first gets the tail paragraphs
            of the previous chunk prepended. Set to 0 to disable.

    Returns:
        List of DocumentChunk objects.
    """
    if not markdown.strip():
        return []

    sections = split_by_headers(markdown)

    # For very short documents with single section, return as single chunk
    if len(sections) == 1 and count_tokens(sections[0]) <= max_tokens:
        return [
            DocumentChunk(
                content=sections[0].strip(),
                chunk_index=0,
                total_chunks=1,
                header_path=extract_header_path(sections[0]),
            )
        ]
    chunks: list[str] = []
    current_chunk = ""
    current_tokens = 0

    for section in sections:
        section_tokens = count_tokens(section)

        # Section fits in current chunk
        if current_tokens + section_tokens <= max_tokens:
            current_chunk += "\n\n" + section if current_chunk else section
            current_tokens += section_tokens

        # Section too large - need to split it
        elif section_tokens > max_tokens:
            # Save current chunk first
            if current_chunk:
                chunks.append(current_chunk.strip())
                current_chunk = ""
                current_tokens = 0

            # Split large section
            for sub_chunk in split_large_section(section, max_tokens):
                chunks.append(sub_chunk)

        # Start new chunk
        else:
            if current_chunk:
                chunks.append(current_chunk.strip())
            current_chunk = section
            current_tokens = section_tokens

    # Don't forget last chunk
    if current_chunk:
        chunks.append(current_chunk.strip())

    # Apply overlap: prepend tail of previous chunk to each subsequent chunk
    if overlap_tokens > 0 and len(chunks) > 1:
        overlapped: list[str] = [chunks[0]]
        for i in range(1, len(chunks)):
            tail = _get_tail_text(chunks[i - 1], overlap_tokens)
            if tail:
                overlapped.append(tail + "\n\n" + chunks[i])
            else:
                overlapped.append(chunks[i])
        chunks = overlapped

    # Convert to DocumentChunk objects
    total_chunks = len(chunks)
    return [
        DocumentChunk(
            content=chunk,
            chunk_index=i,
            total_chunks=total_chunks,
            header_path=extract_header_path(chunk),
        )
        for i, chunk in enumerate(chunks)
    ]
