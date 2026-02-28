"""Tests for chunk overlap and limit alignment."""

import pytest

from services.llm.chunking import (
    _get_tail_text,
    chunk_document,
    count_tokens,
)


class TestGetTailText:
    """Test _get_tail_text helper for overlap extraction."""

    def test_empty_text(self) -> None:
        assert _get_tail_text("", 50) == ""

    def test_zero_target(self) -> None:
        assert _get_tail_text("some text here", 0) == ""

    def test_single_paragraph(self) -> None:
        text = "This is a single paragraph with some content."
        result = _get_tail_text(text, 100)
        assert result == text

    def test_multiple_paragraphs_within_budget(self) -> None:
        text = "Para one.\n\nPara two.\n\nPara three."
        result = _get_tail_text(text, 1000)
        assert "Para one" in result
        assert "Para two" in result
        assert "Para three" in result

    def test_takes_tail_paragraphs(self) -> None:
        """When budget is limited, takes paragraphs from the end."""
        # Each para ~10 tokens. Budget = 15 → should get last 1-2 paragraphs
        para = "word " * 10  # ~12 tokens
        text = f"{para}\n\n{para}\n\n{para}"
        result = _get_tail_text(text, 15)
        paragraphs = [p for p in result.split("\n\n") if p.strip()]
        # Should get at least 1 but not all 3
        assert 1 <= len(paragraphs) <= 2

    def test_always_returns_at_least_one_paragraph(self) -> None:
        """Even if first paragraph exceeds budget, still returns it."""
        big_para = "word " * 100  # ~125 tokens
        text = f"small.\n\n{big_para}"
        result = _get_tail_text(text, 5)  # Very small budget
        assert len(result) > 0

    def test_whitespace_only(self) -> None:
        assert _get_tail_text("   \n\n   ", 50) == ""


class TestChunkDocumentDefaultTokens:
    """Test that default max_tokens changed from 8000 to 5000."""

    def test_default_max_tokens_is_5000(self) -> None:
        """Default should be 5000 (aligned with EXTRACTION_CONTENT_LIMIT / 4)."""
        import inspect
        sig = inspect.signature(chunk_document)
        assert sig.parameters["max_tokens"].default == 5000

    def test_default_overlap_is_zero(self) -> None:
        """Default overlap should be 0 (disabled)."""
        import inspect
        sig = inspect.signature(chunk_document)
        assert sig.parameters["overlap_tokens"].default == 0


class TestChunkOverlap:
    """Test overlap implementation between chunks."""

    def test_overlap_produces_shared_content(self) -> None:
        """Chunks with overlap should share content at boundaries."""
        # Create content that splits into 2+ chunks at max_tokens=50
        section1 = "## Section 1\n" + ("alpha " * 30)  # ~40 tokens
        section2 = "## Section 2\n" + ("beta " * 30)   # ~40 tokens
        markdown = f"{section1}\n\n{section2}"

        chunks = chunk_document(markdown, max_tokens=50, overlap_tokens=20)

        assert len(chunks) >= 2
        # Second chunk should start with content from end of first chunk
        first_content = chunks[0].content
        second_content = chunks[1].content
        # The tail of chunk 0 should appear at the start of chunk 1
        # Check there's shared text
        first_words = set(first_content.split())
        second_words = set(second_content.split())
        overlap_words = first_words & second_words
        assert len(overlap_words) > 0

    def test_single_chunk_unaffected_by_overlap(self) -> None:
        """Documents that fit in one chunk should be unaffected by overlap setting."""
        markdown = "Short document."
        chunks_no_overlap = chunk_document(markdown, max_tokens=1000, overlap_tokens=0)
        chunks_with_overlap = chunk_document(markdown, max_tokens=1000, overlap_tokens=200)

        assert len(chunks_no_overlap) == 1
        assert len(chunks_with_overlap) == 1
        assert chunks_no_overlap[0].content == chunks_with_overlap[0].content

    def test_overlap_zero_no_shared_content(self) -> None:
        """With overlap=0, chunks should not have prepended content."""
        section1 = "## Section 1\n" + ("alpha " * 30)
        section2 = "## Section 2\n" + ("beta " * 30)
        markdown = f"{section1}\n\n{section2}"

        chunks = chunk_document(markdown, max_tokens=50, overlap_tokens=0)

        if len(chunks) >= 2:
            # Second chunk should NOT start with "alpha" content
            assert not chunks[1].content.startswith("alpha")

    def test_overlap_paragraph_aligned(self) -> None:
        """Overlap should be paragraph-aligned (whole paragraphs)."""
        text = "Para A content here.\n\nPara B content here.\n\nPara C content here."
        tail = _get_tail_text(text, 20)
        # Should be complete paragraphs, not cut mid-word
        for para in tail.split("\n\n"):
            if para.strip():
                assert para.strip().endswith(".")

    def test_chunk_indices_correct_with_overlap(self) -> None:
        """Chunk indices should still be sequential with overlap."""
        section1 = "## Section 1\n" + ("word " * 30)
        section2 = "## Section 2\n" + ("word " * 30)
        markdown = f"{section1}\n\n{section2}"

        chunks = chunk_document(markdown, max_tokens=50, overlap_tokens=20)
        for i, chunk in enumerate(chunks):
            assert chunk.chunk_index == i
            assert chunk.total_chunks == len(chunks)
