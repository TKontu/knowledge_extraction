"""Content cleaning for embedding and extraction.

Two-layer approach: universal safe patterns + line-density windowing.
- Full cleaning (Layer 1 + 2): classification path (before embedding/reranking)
- Layer 1 only: extraction LLM input (Phase 2C) — safe structural removal

Design: language-agnostic, template-agnostic, conservative (<1% false positives).
"""

import re

# Layer 1: Universal safe patterns (structural, never real content)
UNIVERSAL_PATTERNS: list[re.Pattern] = [
    # Empty-alt images: ![](url) — logos, tracking pixels, spacers
    re.compile(r"!\[\]\(https?://[^)]+\)\s*", re.IGNORECASE),
    # Skip-to-content accessibility links
    re.compile(r"^\[Skip to [^\]]*\]\([^)]*\)\s*\n?", re.MULTILINE | re.IGNORECASE),
    # Bare link list items: "* [Link](url)" with nothing after
    # Preserves: "* [Link](url) — Description" (has text after)
    re.compile(
        r"^(?:[\*\-]\s+)\[([^\]]{1,80})\]\([^)]*(?:\([^)]*\)[^)]*)*\)\s*$",
        re.MULTILINE,
    ),
    # Bare image lines: "![alt](url)" alone on a line
    re.compile(r"^!\[[^\]]*\]\([^)]+\)\s*$", re.MULTILINE),
]


# Layer 2: Line-density content windowing


def compute_line_link_density(line: str) -> float:
    """Ratio of markdown link syntax chars to total chars. 0.0-1.0."""
    if not line:
        return 0.0
    total_len = len(line)
    link_chars = 0
    for match in re.finditer(r"\[([^\]]*)\]\([^)]*\)", line):
        link_chars += len(match.group(0))
    for match in re.finditer(r"(?<!\()https?://\S+", line):
        link_chars += len(match.group(0))
    return link_chars / total_len


def find_content_by_line_density(
    content: str,
    min_content_lines: int = 3,
    density_threshold: float = 0.4,
    min_line_length: int = 20,
    max_scan_lines: int = 200,
) -> int:
    """Find char offset where real content begins using link density.

    Scans from top. Content = low density (<0.4) + meaningful length (>20 chars).
    Returns offset of first run of min_content_lines consecutive content lines.
    Returns 0 if content starts immediately or no clear region found (conservative).
    """
    if not content:
        return 0

    lines = content.split("\n")
    consecutive_content = 0
    content_start_line = 0

    for i, line in enumerate(lines[:max_scan_lines]):
        stripped = line.strip()
        if not stripped or len(stripped) < min_line_length:
            continue

        density = compute_line_link_density(stripped)
        if density < density_threshold:
            if consecutive_content == 0:
                content_start_line = i
            consecutive_content += 1
            if consecutive_content >= min_content_lines:
                return sum(len(lines[j]) + 1 for j in range(content_start_line))
        else:
            consecutive_content = 0

    return 0


def strip_structural_junk(content: str) -> str:
    """Layer 1 only: strip universal structural patterns.

    Safe for extraction input — removes only content that is never extractable
    (tracking pixels, bare nav links, skip-to-content, bare images).
    Does NOT apply line-density windowing (Layer 2) to preserve all real content.
    """
    if not content:
        return content
    cleaned = content
    for pattern in UNIVERSAL_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def clean_markdown_for_embedding(content: str) -> str:
    """Full clean: Layer 1 (patterns) + Layer 2 (density). For classification only."""
    if not content:
        return content

    cleaned = strip_structural_junk(content)

    content_offset = find_content_by_line_density(cleaned)
    if content_offset > 0:
        cleaned = cleaned[content_offset:]

    return cleaned.strip()
