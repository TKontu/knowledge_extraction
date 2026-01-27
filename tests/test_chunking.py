"""Tests for document chunking."""

from services.llm.chunking import (
    chunk_document,
    count_tokens,
    split_by_headers,
)


class TestCountTokens:
    """Test token counting approximation."""

    def test_empty_string(self) -> None:
        assert count_tokens("") == 0

    def test_single_word(self) -> None:
        # "test" = 4 chars ≈ 1 token
        assert count_tokens("test") == 1

    def test_sentence(self) -> None:
        # 23 chars ≈ 5-6 tokens
        text = "This is a test sentence"
        assert 5 <= count_tokens(text) <= 6

    def test_long_text(self) -> None:
        # 400 chars ≈ 100 tokens
        text = "a" * 400
        assert count_tokens(text) == 100


class TestSplitByHeaders:
    """Test markdown header splitting."""

    def test_no_headers(self) -> None:
        markdown = "Just plain text without headers."
        result = split_by_headers(markdown)
        assert len(result) == 1
        assert result[0] == "Just plain text without headers."

    def test_single_h2_header(self) -> None:
        markdown = "## Introduction\nSome content here."
        result = split_by_headers(markdown)
        assert len(result) == 1
        assert result[0] == "## Introduction\nSome content here."

    def test_multiple_h2_headers(self) -> None:
        markdown = """## Section 1
Content for section 1.

## Section 2
Content for section 2."""
        result = split_by_headers(markdown)
        assert len(result) == 2
        assert result[0].startswith("## Section 1")
        assert result[1].startswith("## Section 2")

    def test_mixed_header_levels(self) -> None:
        markdown = """# Title

## Section 1
Content 1.

### Subsection 1.1
Nested content.

## Section 2
Content 2."""
        result = split_by_headers(markdown)
        # Should split on ## headers, combining content before first ## with first section
        assert len(result) == 2
        # First section includes content before first ##
        assert "# Title" in result[0]
        assert "## Section 1" in result[0]
        assert "### Subsection 1.1" in result[0]
        assert result[1].startswith("## Section 2")

    def test_empty_sections(self) -> None:
        markdown = """## Section 1

## Section 2

## Section 3"""
        result = split_by_headers(markdown)
        # Should have 3 sections (empty content is fine)
        assert len(result) == 3

    def test_preserves_header_with_content(self) -> None:
        markdown = """## API Reference
The API provides several endpoints.

### Authentication
Use Bearer tokens."""
        result = split_by_headers(markdown)
        assert len(result) == 1
        # Should include the header in the content
        assert "## API Reference" in result[0]
        assert "### Authentication" in result[0]


class TestChunkDocument:
    """Test document chunking with token limits."""

    def test_short_document_single_chunk(self) -> None:
        markdown = "This is a short document."
        chunks = chunk_document(markdown, max_tokens=1000)
        assert len(chunks) == 1
        assert chunks[0].content == "This is a short document."
        assert chunks[0].chunk_index == 0
        assert chunks[0].total_chunks == 1

    def test_multiple_sections_fit_in_one_chunk(self) -> None:
        markdown = """## Section 1
Content 1.

## Section 2
Content 2."""
        chunks = chunk_document(markdown, max_tokens=1000)
        assert len(chunks) == 1
        assert "## Section 1" in chunks[0].content
        assert "## Section 2" in chunks[0].content

    def test_sections_split_across_chunks(self) -> None:
        # Each section is ~20 tokens (with header), max 30 tokens per chunk
        markdown = (
            """## Section 1
"""
            + ("word " * 15)
            + """

## Section 2
"""
            + ("word " * 15)
        )

        chunks = chunk_document(markdown, max_tokens=30)
        # Should create 2 chunks (one section per chunk)
        assert len(chunks) == 2
        assert chunks[0].chunk_index == 0
        assert chunks[0].total_chunks == 2
        assert chunks[1].chunk_index == 1
        assert chunks[1].total_chunks == 2

    def test_single_large_section_splits(self) -> None:
        # Create a section larger than max_tokens
        large_section = "## Large Section\n" + ("word " * 500)
        chunks = chunk_document(large_section, max_tokens=100)

        # Should split into multiple chunks
        assert len(chunks) > 1
        # All chunks should be under max_tokens
        for chunk in chunks:
            assert count_tokens(chunk.content) <= 100

    def test_header_path_extraction(self) -> None:
        markdown = """# Main Title

## Section 1
Content here.

### Subsection 1.1
Nested content."""

        chunks = chunk_document(markdown, max_tokens=1000)
        # Should extract header path
        assert chunks[0].header_path is not None
        # Should contain at least the main section
        assert len(chunks[0].header_path) >= 1

    def test_empty_document(self) -> None:
        chunks = chunk_document("", max_tokens=1000)
        assert len(chunks) == 0

    def test_chunk_indices_sequential(self) -> None:
        markdown = ("## Section\n" + ("word " * 50) + "\n") * 5
        chunks = chunk_document(markdown, max_tokens=50)

        # Verify indices are sequential
        for i, chunk in enumerate(chunks):
            assert chunk.chunk_index == i
            assert chunk.total_chunks == len(chunks)
