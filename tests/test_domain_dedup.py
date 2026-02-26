"""Tests for domain-level boilerplate deduplication."""

from services.extraction.domain_dedup import (
    compute_domain_fingerprint,
    hash_block,
    split_into_blocks,
    strip_boilerplate,
)

# --- Helpers ---

COOKIE_BANNER = (
    "We use cookies to ensure you get the best experience on our website. "
    "By continuing to browse, you agree to our use of cookies. "
    "Learn more about our cookie policy and privacy settings."
)

FOOTER = (
    "© 2024 Acme Corp. All rights reserved. "
    "Terms of Service | Privacy Policy | Contact Us | Sitemap"
)

UNIQUE_CONTENT_A = (
    "The XR-2000 industrial gearbox delivers 500 kW of power "
    "with a 98.5% efficiency rating, suitable for heavy-duty mining applications."
)

UNIQUE_CONTENT_B = (
    "Our SR-series planetary drives are designed for cement mill applications "
    "with torque ratings up to 1,200 kNm and service factors of 2.0."
)


def _make_page(
    unique: str, include_cookie: bool = True, include_footer: bool = True
) -> str:
    """Build a page with optional boilerplate blocks."""
    parts = []
    if include_cookie:
        parts.append(COOKIE_BANNER)
    parts.append(unique)
    if include_footer:
        parts.append(FOOTER)
    return "\n\n".join(parts)


# --- TestSplitIntoBlocks ---


class TestSplitIntoBlocks:
    def test_splits_on_double_newlines(self):
        content = "Block one content here.\n\nBlock two content here."
        blocks = split_into_blocks(content, min_block_chars=10)
        assert len(blocks) == 2
        assert "Block one" in blocks[0]
        assert "Block two" in blocks[1]

    def test_filters_short_blocks(self):
        content = "A\n\n" + "B" * 60 + "\n\nC"
        blocks = split_into_blocks(content, min_block_chars=50)
        assert len(blocks) == 1
        assert "B" * 60 in blocks[0]

    def test_handles_empty_content(self):
        assert split_into_blocks("") == []
        assert split_into_blocks("", min_block_chars=10) == []

    def test_handles_single_block(self):
        content = "A" * 100
        blocks = split_into_blocks(content, min_block_chars=50)
        assert len(blocks) == 1
        assert blocks[0] == content

    def test_preserves_block_content(self):
        block = "  This has leading spaces and trailing spaces  "
        content = block + "\n\n" + "X" * 60
        blocks = split_into_blocks(content, min_block_chars=10)
        assert blocks[0] == block

    def test_splits_on_blank_lines_with_whitespace(self):
        content = "Block A content is here" + "\n  \n" + "Block B content is here"
        blocks = split_into_blocks(content, min_block_chars=10)
        assert len(blocks) == 2


# --- TestHashBlock ---


class TestHashBlock:
    def test_deterministic_output(self):
        assert hash_block("hello world") == hash_block("hello world")

    def test_whitespace_normalized(self):
        assert hash_block("hello   world") == hash_block("hello world")
        assert hash_block("hello\tworld") == hash_block("hello world")
        assert hash_block("  hello world  ") == hash_block("hello world")
        assert hash_block("hello\n  world") == hash_block("hello world")

    def test_case_insensitive(self):
        assert hash_block("Hello World") == hash_block("hello world")
        assert hash_block("HELLO WORLD") == hash_block("hello world")

    def test_returns_16_hex_chars(self):
        result = hash_block("test content")
        assert len(result) == 16
        assert all(c in "0123456789abcdef" for c in result)

    def test_different_content_different_hash(self):
        assert hash_block("content alpha") != hash_block("content beta")


# --- TestComputeDomainFingerprint ---


class TestComputeDomainFingerprint:
    def test_identifies_boilerplate_above_threshold(self):
        pages = [
            _make_page(f"Unique product description number {i} " * 5) for i in range(10)
        ]
        result = compute_domain_fingerprint(pages, threshold_pct=0.7, min_pages=5)

        assert result.pages_analyzed == 10
        assert result.blocks_boilerplate >= 2  # cookie + footer
        # The boilerplate hashes should match cookie and footer
        bp_set = set(result.boilerplate_hashes)
        assert hash_block(COOKIE_BANNER) in bp_set
        assert hash_block(FOOTER) in bp_set

    def test_skips_when_below_min_pages(self):
        pages = [_make_page(f"Unique {i} " * 10) for i in range(3)]
        result = compute_domain_fingerprint(pages, min_pages=5)

        assert result.pages_analyzed == 3
        assert result.boilerplate_hashes == []
        assert result.blocks_total == 0
        assert result.blocks_boilerplate == 0

    def test_respects_threshold_pct(self):
        # 10 pages, cookie on all 10, footer on only 5
        pages = []
        for i in range(10):
            pages.append(
                _make_page(
                    f"Unique product info number {i} " * 5,
                    include_footer=(i < 5),
                )
            )
        # threshold_pct=0.7 → need 7 pages
        result = compute_domain_fingerprint(pages, threshold_pct=0.7, min_pages=5)
        bp_set = set(result.boilerplate_hashes)
        assert hash_block(COOKIE_BANNER) in bp_set
        assert hash_block(FOOTER) not in bp_set  # only on 5/10 pages

    def test_ignores_unique_blocks(self):
        pages = [
            _make_page(f"Completely unique content for page {i} " * 5)
            for i in range(10)
        ]
        result = compute_domain_fingerprint(pages, threshold_pct=0.7, min_pages=5)

        bp_set = set(result.boilerplate_hashes)
        # Unique content should never be flagged
        for i in range(10):
            unique_block = f"Completely unique content for page {i} " * 5
            assert hash_block(unique_block) not in bp_set

    def test_handles_empty_pages(self):
        pages = ["", "", "", "", ""]
        result = compute_domain_fingerprint(pages, min_pages=5)
        assert result.boilerplate_hashes == []
        assert result.blocks_total == 0

    def test_real_world_cookie_banner(self):
        """Same cookie banner on all pages should be detected."""
        banner = (
            "This website uses cookies to enhance your browsing experience. "
            "We use necessary cookies to make our site work. We'd also like to "
            "set analytics cookies that help us make improvements by measuring "
            "how you use the site. Cookie Settings Accept All Cookies"
        )
        pages = [
            f"{banner}\n\n# Page {i}\n\nActual content about product {i} goes here with enough text."
            for i in range(10)
        ]
        result = compute_domain_fingerprint(pages, threshold_pct=0.7, min_pages=5)
        assert hash_block(banner) in set(result.boilerplate_hashes)

    def test_duplicate_block_on_same_page_counts_once(self):
        """A block appearing twice on one page should only count as one page occurrence."""
        dup_block = (
            "This block is duplicated within the same page for testing purposes."
        )
        pages = []
        for i in range(10):
            # Only pages 0-3 have the dup_block (4 pages < threshold of 7)
            if i < 4:
                pages.append(f"{dup_block}\n\n{dup_block}\n\nUnique page {i} " * 5)
            else:
                pages.append(
                    f"Different content for page {i} that is long enough to be a block."
                )
        result = compute_domain_fingerprint(pages, threshold_pct=0.7, min_pages=5)
        assert hash_block(dup_block) not in set(result.boilerplate_hashes)


# --- TestStripBoilerplate ---


class TestStripBoilerplate:
    def test_removes_boilerplate_blocks(self):
        bp_hashes = {hash_block(COOKIE_BANNER), hash_block(FOOTER)}
        page = _make_page(UNIQUE_CONTENT_A)
        cleaned, removed = strip_boilerplate(page, bp_hashes)

        assert COOKIE_BANNER not in cleaned
        assert FOOTER not in cleaned
        assert UNIQUE_CONTENT_A in cleaned
        assert removed > 0

    def test_preserves_unique_content(self):
        bp_hashes = {hash_block(COOKIE_BANNER)}
        page = _make_page(UNIQUE_CONTENT_A)
        cleaned, _ = strip_boilerplate(page, bp_hashes)

        assert UNIQUE_CONTENT_A in cleaned
        assert FOOTER in cleaned

    def test_handles_empty_boilerplate_set(self):
        page = _make_page(UNIQUE_CONTENT_A)
        cleaned, removed = strip_boilerplate(page, set())
        assert cleaned == page
        assert removed == 0

    def test_handles_empty_content(self):
        cleaned, removed = strip_boilerplate("", {hash_block("anything")})
        assert cleaned == ""
        assert removed == 0

    def test_collapses_blank_lines_after_removal(self):
        bp_hashes = {hash_block(COOKIE_BANNER)}
        page = COOKIE_BANNER + "\n\n\n\n" + UNIQUE_CONTENT_A
        cleaned, _ = strip_boilerplate(page, bp_hashes)

        # Should not have 3+ consecutive newlines
        assert "\n\n\n" not in cleaned
        assert UNIQUE_CONTENT_A in cleaned

    def test_returns_correct_bytes_removed(self):
        bp_hashes = {hash_block(COOKIE_BANNER), hash_block(FOOTER)}
        page = _make_page(UNIQUE_CONTENT_A)
        cleaned, removed = strip_boilerplate(page, bp_hashes)

        # bytes_removed = original_len - cleaned_len
        assert removed == len(page) - len(cleaned)
        assert removed > 0

    def test_no_removal_when_no_match(self):
        bp_hashes = {
            hash_block(
                "This hash does not match anything in the actual content at all."
            )
        }
        page = _make_page(UNIQUE_CONTENT_A)
        cleaned, removed = strip_boilerplate(page, bp_hashes)

        assert removed == 0
        assert COOKIE_BANNER in cleaned
        assert UNIQUE_CONTENT_A in cleaned
        assert FOOTER in cleaned


# --- Integration: fingerprint → strip ---


class TestEndToEnd:
    def test_fingerprint_then_strip(self):
        """Full pipeline: compute fingerprint, then strip boilerplate from each page."""
        pages = [
            _make_page(f"Product {i} specs with enough detail to be a real block." * 2)
            for i in range(10)
        ]

        fp = compute_domain_fingerprint(pages, threshold_pct=0.7, min_pages=5)
        assert fp.blocks_boilerplate >= 2

        bp_set = set(fp.boilerplate_hashes)
        total_removed = 0
        for page in pages:
            cleaned, removed = strip_boilerplate(page, bp_set)
            assert COOKIE_BANNER not in cleaned
            assert FOOTER not in cleaned
            total_removed += removed

        assert total_removed > 0
