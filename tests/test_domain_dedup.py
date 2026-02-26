"""Tests for domain-level boilerplate deduplication."""

from services.extraction.domain_dedup import (
    compute_domain_fingerprint,
    compute_section_fingerprints,
    extract_path_prefix,
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

    def test_threshold_floor_defaults_to_min_pages(self):
        """Without threshold_floor, min_pages acts as the floor (existing behavior)."""
        # 5 pages, block on 4/5 (80% > 70%) — but min_pages=5 forces threshold=5
        block = "Shared navigation block that appears across most pages in section."
        pages = []
        for i in range(5):
            if i < 4:
                pages.append(f"{block}\n\nUnique content for page {i} with enough text here.")
            else:
                pages.append(f"Different page {i} with no shared block but enough text here.")

        result = compute_domain_fingerprint(pages, threshold_pct=0.7, min_pages=5)
        assert hash_block(block) not in set(result.boilerplate_hashes)  # MISSED: need 5/5

    def test_threshold_floor_overrides_min_pages_floor(self):
        """With threshold_floor=3, block on 4/5 pages (80%) is detected."""
        block = "Shared navigation block that appears across most pages in section."
        pages = []
        for i in range(5):
            if i < 4:
                pages.append(f"{block}\n\nUnique content for page {i} with enough text here.")
            else:
                pages.append(f"Different page {i} with no shared block but enough text here.")

        result = compute_domain_fingerprint(
            pages, threshold_pct=0.7, min_pages=5, threshold_floor=3
        )
        assert hash_block(block) in set(result.boilerplate_hashes)  # DETECTED: need 3/5

    def test_threshold_floor_still_respects_pct(self):
        """threshold_floor doesn't lower below threshold_pct for larger sections."""
        # 10 pages, block on 5/10 (50% < 70%) — threshold_floor=3 doesn't help
        block = "Shared navigation block that appears across most pages in section."
        pages = []
        for i in range(10):
            if i < 5:
                pages.append(f"{block}\n\nUnique content for page {i} with enough text here.")
            else:
                pages.append(f"Different page {i} with no shared block but enough text here.")

        result = compute_domain_fingerprint(
            pages, threshold_pct=0.7, min_pages=5, threshold_floor=3
        )
        # threshold = max(3, int(10*0.7)) = max(3, 7) = 7. Need 7/10, only have 5.
        assert hash_block(block) not in set(result.boilerplate_hashes)

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


# --- TestExtractPathPrefix ---


MOTORS_NAV = (
    "Browse our motors: AC Motors | DC Motors | Servo Motors | Stepper Motors | "
    "Brushless Motors | Gear Motors | Linear Motors | Motor Accessories"
)

PRODUCTS_SIDEBAR = (
    "Product Categories: Gearboxes | Drives | Controllers | Sensors | "
    "Couplings | Bearings | Enclosures | Power Supplies | Cables"
)


class TestExtractPathPrefix:
    def test_standard_url(self):
        assert extract_path_prefix("https://example.com/motors-for/ac") == "/motors-for"

    def test_root_url(self):
        assert extract_path_prefix("https://example.com/") == "/"

    def test_no_path(self):
        assert extract_path_prefix("https://example.com") == "/"

    def test_deeper_depth(self):
        assert (
            extract_path_prefix("https://example.com/a/b/c", depth=2) == "/a/b"
        )

    def test_depth_exceeds_segments(self):
        assert extract_path_prefix("https://example.com/a", depth=3) == "/a"

    def test_query_params_ignored(self):
        assert (
            extract_path_prefix("https://example.com/motors?page=2") == "/motors"
        )

    def test_trailing_slash(self):
        assert extract_path_prefix("https://example.com/motors/") == "/motors"

    def test_encoded_path(self):
        assert (
            extract_path_prefix("https://example.com/my%20motors/list") == "/my%20motors"
        )


# --- TestComputeSectionFingerprints ---


class TestComputeSectionFingerprints:
    def test_detects_section_specific_boilerplate(self):
        """Two sections each with their own nav should be detected."""
        pages_with_uris: list[tuple[str, str]] = []
        # /motors-for section: 6 pages, all with MOTORS_NAV
        for i in range(6):
            content = f"{MOTORS_NAV}\n\nMotor product {i} details with enough text to count as block."
            pages_with_uris.append((content, f"https://example.com/motors-for/page{i}"))
        # /products section: 6 pages, all with PRODUCTS_SIDEBAR
        for i in range(6):
            content = f"{PRODUCTS_SIDEBAR}\n\nProduct item {i} details with enough text to count as block."
            pages_with_uris.append((content, f"https://example.com/products/item{i}"))

        result = compute_section_fingerprints(
            pages_with_uris, threshold_pct=0.7, min_pages=5
        )

        assert result.sections_analyzed == 2
        assert result.sections_with_boilerplate == 2
        # Each section should have its own nav hash
        motors_hashes = set(result.section_results["/motors-for"].boilerplate_hashes)
        products_hashes = set(result.section_results["/products"].boilerplate_hashes)
        assert hash_block(MOTORS_NAV) in motors_hashes
        assert hash_block(PRODUCTS_SIDEBAR) in products_hashes
        # No cross-contamination in the fingerprint results
        assert hash_block(MOTORS_NAV) not in products_hashes
        assert hash_block(PRODUCTS_SIDEBAR) not in motors_hashes

    def test_skips_sections_below_min_pages(self):
        """Sections with fewer than min_pages should be skipped."""
        pages_with_uris: list[tuple[str, str]] = []
        # Only 3 pages in /small section
        for i in range(3):
            content = f"{MOTORS_NAV}\n\nSmall section page {i} with enough text to be a block."
            pages_with_uris.append((content, f"https://example.com/small/page{i}"))
        # 6 pages in /large section
        for i in range(6):
            content = f"{PRODUCTS_SIDEBAR}\n\nLarge section page {i} with enough text to be a block."
            pages_with_uris.append((content, f"https://example.com/large/page{i}"))

        result = compute_section_fingerprints(
            pages_with_uris, threshold_pct=0.7, min_pages=5
        )

        assert result.sections_analyzed == 1
        assert "/small" not in result.section_results
        assert "/large" in result.section_results

    def test_exclude_hashes_filters_domain_level(self):
        """Domain-level hashes should be excluded from section results."""
        pages_with_uris: list[tuple[str, str]] = []
        for i in range(6):
            content = f"{COOKIE_BANNER}\n\n{MOTORS_NAV}\n\nMotor page {i} unique content is here."
            pages_with_uris.append((content, f"https://example.com/motors/page{i}"))

        cookie_hash = hash_block(COOKIE_BANNER)
        result = compute_section_fingerprints(
            pages_with_uris,
            threshold_pct=0.7,
            min_pages=5,
            exclude_hashes={cookie_hash},
        )

        assert result.sections_analyzed == 1
        motors_hashes = set(result.section_results["/motors"].boilerplate_hashes)
        assert hash_block(MOTORS_NAV) in motors_hashes
        assert cookie_hash not in motors_hashes

    def test_empty_input(self):
        result = compute_section_fingerprints([])
        assert result.sections_analyzed == 0
        assert result.sections_with_boilerplate == 0
        assert result.total_section_hashes == 0

    def test_all_pages_in_same_section(self):
        """All pages under same prefix should form one section."""
        pages_with_uris: list[tuple[str, str]] = []
        for i in range(8):
            content = f"{MOTORS_NAV}\n\nPage {i} with unique motor content and description."
            pages_with_uris.append((content, f"https://example.com/motors/page{i}"))

        result = compute_section_fingerprints(
            pages_with_uris, threshold_pct=0.7, min_pages=5
        )

        assert result.sections_analyzed == 1
        assert "/motors" in result.section_results
        assert hash_block(MOTORS_NAV) in set(
            result.section_results["/motors"].boilerplate_hashes
        )

    def test_threshold_floor_detects_boilerplate_in_small_section(self):
        """Section with 5 pages, block on 4/5 (80%) — caught with threshold_floor=3."""
        pages_with_uris: list[tuple[str, str]] = []
        for i in range(5):
            if i < 4:
                content = f"{MOTORS_NAV}\n\nMotor page {i} with unique content here enough for block."
            else:
                content = f"Page {i} without nav but with enough unique content for block."
            pages_with_uris.append((content, f"https://example.com/motors/page{i}"))

        # Default threshold_floor=3: threshold = max(3, int(5*0.7)) = max(3,3) = 3
        result = compute_section_fingerprints(
            pages_with_uris, threshold_pct=0.7, min_pages=5, threshold_floor=3
        )
        assert result.sections_with_boilerplate == 1
        motors_hashes = set(result.section_results["/motors"].boilerplate_hashes)
        assert hash_block(MOTORS_NAV) in motors_hashes

    def test_threshold_floor_high_value_misses_same_case(self):
        """Same 4/5 case with threshold_floor=5 (like old behavior) — missed."""
        pages_with_uris: list[tuple[str, str]] = []
        for i in range(5):
            if i < 4:
                content = f"{MOTORS_NAV}\n\nMotor page {i} with unique content here enough for block."
            else:
                content = f"Page {i} without nav but with enough unique content for block."
            pages_with_uris.append((content, f"https://example.com/motors/page{i}"))

        result = compute_section_fingerprints(
            pages_with_uris, threshold_pct=0.7, min_pages=5, threshold_floor=5
        )
        # threshold = max(5, int(5*0.7)) = 5. Need 5/5, only 4 have it.
        motors_hashes = set(result.section_results["/motors"].boilerplate_hashes)
        assert hash_block(MOTORS_NAV) not in motors_hashes


# --- Two-Pass End-to-End ---


class TestTwoPassEndToEnd:
    def test_section_nav_missed_at_domain_caught_at_section(self):
        """Section nav at 60% of domain pages is missed globally but caught per-section."""
        pages: list[str] = []
        uris: list[str] = []

        # 6 /motors-for pages with MOTORS_NAV (60% of 10 total)
        for i in range(6):
            content = (
                f"{COOKIE_BANNER}\n\n{MOTORS_NAV}\n\n"
                f"Motor product {i} specification with detailed technical information."
            )
            pages.append(content)
            uris.append(f"https://hansen-motor.com/motors-for/motor{i}")

        # 4 /about pages without MOTORS_NAV
        for i in range(4):
            content = (
                f"{COOKIE_BANNER}\n\n"
                f"About page {i} with company history and team information details."
            )
            pages.append(content)
            uris.append(f"https://hansen-motor.com/about/page{i}")

        # Domain-level: COOKIE_BANNER on all 10 → caught. MOTORS_NAV on 6/10 (60%) → missed.
        domain_fp = compute_domain_fingerprint(
            pages, threshold_pct=0.7, min_pages=5
        )
        domain_hashes = set(domain_fp.boilerplate_hashes)
        assert hash_block(COOKIE_BANNER) in domain_hashes
        assert hash_block(MOTORS_NAV) not in domain_hashes  # 60% < 70% threshold

        # Section-level: MOTORS_NAV on 6/6 /motors-for pages → caught
        pages_with_uris = list(zip(pages, uris, strict=True))
        section_fp = compute_section_fingerprints(
            pages_with_uris,
            threshold_pct=0.7,
            min_pages=5,
            exclude_hashes=domain_hashes,
        )

        # /motors-for has 6 pages ≥ min_pages=5, MOTORS_NAV on 100%
        assert "/motors-for" in section_fp.section_results
        motors_hashes = set(section_fp.section_results["/motors-for"].boilerplate_hashes)
        assert hash_block(MOTORS_NAV) in motors_hashes

        # Now strip /motors-for pages with merged hashes
        for i in range(6):
            effective = domain_hashes | motors_hashes
            cleaned, removed = strip_boilerplate(pages[i], effective)
            assert COOKIE_BANNER not in cleaned
            assert MOTORS_NAV not in cleaned
            assert removed > 0

        # /about pages should NOT have MOTORS_NAV stripped (no cross-contamination)
        for i in range(6, 10):
            effective_about = set(domain_hashes)
            # /about section only had 4 pages, below min_pages, so no section hashes
            cleaned, removed = strip_boilerplate(pages[i], effective_about)
            assert COOKIE_BANNER not in cleaned
            # Unique about content preserved
            assert "About page" in cleaned

    def test_section_hashes_not_applied_to_other_sections(self):
        """Verify section hashes from /motors are NOT used to strip /products pages."""
        pages_with_uris: list[tuple[str, str]] = []

        # /motors section with MOTORS_NAV
        for i in range(6):
            content = (
                f"{MOTORS_NAV}\n\n"
                f"Motor item {i} technical description with enough detail for block."
            )
            pages_with_uris.append(
                (content, f"https://example.com/motors/item{i}")
            )

        # /products section with PRODUCTS_SIDEBAR
        for i in range(6):
            content = (
                f"{PRODUCTS_SIDEBAR}\n\n"
                f"Product listing {i} with detailed specifications and pricing info."
            )
            pages_with_uris.append(
                (content, f"https://example.com/products/item{i}")
            )

        section_fp = compute_section_fingerprints(
            pages_with_uris, threshold_pct=0.7, min_pages=5
        )

        products_hashes = set(section_fp.section_results["/products"].boilerplate_hashes)

        # Strip a /products page with only /products section hashes (not /motors)
        product_page = (
            f"{PRODUCTS_SIDEBAR}\n\n"
            "Product listing 99 with detailed specifications and pricing info."
        )
        cleaned, _ = strip_boilerplate(product_page, products_hashes)
        assert PRODUCTS_SIDEBAR not in cleaned

        # MOTORS_NAV should NOT be stripped from a page that happens to contain it
        mixed_page = (
            f"{MOTORS_NAV}\n\n{PRODUCTS_SIDEBAR}\n\n"
            "Mixed page with both navigations and some unique content here."
        )
        cleaned_mixed, _ = strip_boilerplate(mixed_page, products_hashes)
        # PRODUCTS_SIDEBAR stripped, but MOTORS_NAV preserved (wrong section)
        assert PRODUCTS_SIDEBAR not in cleaned_mixed
        assert MOTORS_NAV in cleaned_mixed
