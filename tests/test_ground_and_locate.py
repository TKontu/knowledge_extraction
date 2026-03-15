"""Tests for the 4-tier offset-mapped ground_and_locate algorithm."""

from services.extraction.extraction_items import SourceLocation, locate_in_source
from services.extraction.grounding import (
    ContentMaps,
    _compose_maps,
    _normalize_with_map,
    _preprocess_quote,
    _punct_strip_with_map,
    _strip_markdown_with_map,
    _tier1_locate,
    _tier2_locate,
    _tier3_locate,
    _tier4_locate,
    ground_and_locate,
    ground_and_locate_precomputed,
    precompute_content_maps,
)

# ── Preprocessing tests ──


class TestPreprocessQuote:
    def test_strips_trailing_dots(self):
        assert _preprocess_quote("products...") == "products"

    def test_strips_trailing_ellipsis_unicode(self):
        assert _preprocess_quote("products…") == "products"

    def test_normalizes_unicode_dashes(self):
        assert _preprocess_quote("en\u2013dash") == "en-dash"
        assert _preprocess_quote("em\u2014dash") == "em-dash"

    def test_strips_whitespace(self):
        assert _preprocess_quote("  hello  ") == "hello"

    def test_empty(self):
        assert _preprocess_quote("") == ""
        assert _preprocess_quote("...") == ""


# ── Offset map tests ──


class TestNormalizeWithMap:
    def test_basic(self):
        text = "Hello World"
        norm, omap = _normalize_with_map(text)
        assert norm == "hello world"
        assert len(omap) == len(norm)

    def test_multi_whitespace_collapse(self):
        text = "hello   world"
        norm, omap = _normalize_with_map(text)
        assert norm == "hello world"
        # 'w' in original is at index 8
        w_idx = norm.index("w")
        assert omap[w_idx] == 8

    def test_newlines_collapse(self):
        text = "line one\n\nline two"
        norm, omap = _normalize_with_map(text)
        assert norm == "line one line two"

    def test_leading_trailing_whitespace(self):
        text = "  hello  "
        norm, omap = _normalize_with_map(text)
        assert norm == "hello"

    def test_position_roundtrip(self):
        text = "The  quick   brown fox"
        norm, omap = _normalize_with_map(text)
        # Every char in normalized maps back to a valid original index
        for i, c in enumerate(norm):
            orig_idx = omap[i]
            assert 0 <= orig_idx < len(text)
            if c != " ":
                assert text[orig_idx].lower() == c

    def test_unicode_dashes_normalized(self):
        text = "value\u2013range"
        norm, omap = _normalize_with_map(text)
        assert "-" in norm

    def test_empty(self):
        norm, omap = _normalize_with_map("")
        assert norm == ""
        assert omap == []


class TestPunctStripWithMap:
    def test_removes_punctuation(self):
        text = "hello, world!"
        stripped, omap = _punct_strip_with_map(text)
        assert stripped == "hello world"

    def test_positions_correct(self):
        text = "a: b"
        stripped, omap = _punct_strip_with_map(text)
        assert stripped == "a b"
        # 'b' should map back to index 3
        b_idx = stripped.index("b")
        assert omap[b_idx] == 3

    def test_double_spaces_after_removal(self):
        text = "a - b"
        stripped, omap = _punct_strip_with_map(text)
        assert stripped == "a b"

    def test_empty(self):
        stripped, omap = _punct_strip_with_map("")
        assert stripped == ""
        assert omap == []


class TestStripMarkdownWithMap:
    def test_link(self):
        text = "See [click here](http://example.com) for info"
        stripped, omap = _strip_markdown_with_map(text)
        assert "click here" in stripped
        assert "http://example.com" not in stripped
        assert "[" not in stripped

    def test_bold(self):
        text = "This is **bold** text"
        stripped, omap = _strip_markdown_with_map(text)
        assert "bold" in stripped
        assert "**" not in stripped

    def test_italic(self):
        text = "This is *italic* text"
        stripped, omap = _strip_markdown_with_map(text)
        assert "italic" in stripped
        assert stripped.count("*") == 0

    def test_table_pipe_to_space(self):
        text = "| col1 | col2 |"
        stripped, omap = _strip_markdown_with_map(text)
        assert "|" not in stripped
        assert "col1" in stripped
        assert "col2" in stripped

    def test_table_separator_removed(self):
        text = "| col1 | col2 |\n|------|------|\n| a | b |"
        stripped, omap = _strip_markdown_with_map(text)
        assert "------" not in stripped
        assert "a" in stripped

    def test_inline_code(self):
        text = "Use `function()` here"
        stripped, omap = _strip_markdown_with_map(text)
        assert "function()" in stripped
        assert "`" not in stripped

    def test_image(self):
        text = "See ![alt text](image.png) below"
        stripped, omap = _strip_markdown_with_map(text)
        assert "alt text" in stripped
        assert "image.png" not in stripped

    def test_position_roundtrip(self):
        text = "See [link](url) here"
        stripped, omap = _strip_markdown_with_map(text)
        for i, c in enumerate(stripped):
            orig_idx = omap[i]
            # Pipes are converted to spaces, so skip those
            if c != " " or text[orig_idx] == " ":
                assert text[orig_idx] == c or (c == " " and text[orig_idx] == "|")

    def test_empty(self):
        stripped, omap = _strip_markdown_with_map("")
        assert stripped == ""
        assert omap == []


class TestComposeMaps:
    def test_two_maps(self):
        # map_a: [0, 2, 4] (3 chars map to original positions)
        # map_b: [0, 2]    (2 chars map to positions in map_a's text)
        # result: [map_a[0], map_a[2]] = [0, 4]
        assert _compose_maps([0, 2, 4], [0, 2]) == [0, 4]

    def test_identity(self):
        assert _compose_maps([0, 1, 2], [0, 1, 2]) == [0, 1, 2]

    def test_out_of_bounds_uses_last(self):
        # map_b references index 5, map_a only has 3 elements
        result = _compose_maps([0, 2, 4], [0, 5])
        assert result == [0, 4]  # clamps to last element

    def test_three_map_composition(self):
        map_a = [0, 3, 6, 9]
        map_b = [0, 2, 3]
        map_c = [0, 1]
        # First compose a and b: [map_a[0], map_a[2], map_a[3]] = [0, 6, 9]
        ab = _compose_maps(map_a, map_b)
        assert ab == [0, 6, 9]
        # Then compose ab and c: [ab[0], ab[1]] = [0, 6]
        abc = _compose_maps(ab, map_c)
        assert abc == [0, 6]


# ── Per-tier tests ──


class TestTier1:
    def test_exact_match(self):
        content = "Acme Corp is a leading manufacturer"
        norm, omap = _normalize_with_map(content)
        result = _tier1_locate("leading manufacturer", norm, omap)
        assert result is not None
        assert result.score == 1.0
        assert result.match_tier == 1
        span = content[result.source_offset : result.source_end]
        assert span == "leading manufacturer"

    def test_case_insensitive(self):
        content = "ACME CORP Leading Manufacturer"
        norm, omap = _normalize_with_map(content)
        result = _tier1_locate("leading manufacturer", norm, omap)
        assert result is not None
        span = content[result.source_offset : result.source_end]
        assert span.lower() == "leading manufacturer"

    def test_whitespace_collapse(self):
        content = "leading   manufacturer   of   gears"
        norm, omap = _normalize_with_map(content)
        result = _tier1_locate("leading manufacturer of gears", norm, omap)
        assert result is not None
        span = content[result.source_offset : result.source_end]
        assert "leading" in span
        assert "gears" in span

    def test_no_match(self):
        norm, omap = _normalize_with_map("unrelated content")
        assert _tier1_locate("not here", norm, omap) is None


class TestTier2:
    def test_punct_difference(self):
        content = "Products: gearboxes, motors, and drives"
        norm, omap = _normalize_with_map(content)
        # Quote without punctuation
        result = _tier2_locate("products gearboxes motors and drives", norm, omap)
        assert result is not None
        assert result.score == 0.95
        assert result.match_tier == 2
        span = content[result.source_offset : result.source_end]
        assert "Products" in span
        assert "drives" in span

    def test_em_dash_difference(self):
        content = "Power range: 50-100 kW"
        norm, omap = _normalize_with_map(content)
        result = _tier2_locate("power range 50100 kw", norm, omap)
        assert result is not None
        assert result.match_tier == 2

    def test_no_match(self):
        norm, omap = _normalize_with_map("some content here")
        assert _tier2_locate("completely different", norm, omap) is None


class TestTier3:
    def test_markdown_link(self):
        content = "See our [gearbox products](http://example.com/products) page"
        result = _tier3_locate("gearbox products", content)
        assert result is not None
        assert result.score == 0.9
        assert result.match_tier == 3
        # Span should be in the original content including markdown
        assert result.source_offset < result.source_end

    def test_bold_text(self):
        content = "Our **premium gearboxes** are industry leading"
        result = _tier3_locate("premium gearboxes", content)
        assert result is not None
        assert result.match_tier == 3

    def test_table_content(self):
        content = "| Product | Power |\n|---------|-------|\n| Gearbox | 50kW |"
        result = _tier3_locate("gearbox 50kw", content)
        assert result is not None
        assert result.match_tier == 3

    def test_no_match(self):
        assert _tier3_locate("not present", "some **bold** text") is None


class TestTier4:
    def test_block_fuzzy(self):
        content = "About the company\n\nAcme manufactures gearboxes motors and drives for industrial applications\n\nContact us"
        from services.extraction.grounding import _pos_normalize

        norm_quote = _pos_normalize("acme gearboxes motors drives industrial")
        result = _tier4_locate(norm_quote, content)
        assert result is not None
        assert result.match_tier == 4
        assert result.score >= 0.6

    def test_line_level_match(self):
        content = "Header\n\nLine one about gears\nLine two about motors and drives\nLine three about controls\n\nFooter"
        from services.extraction.grounding import _pos_normalize

        norm_quote = _pos_normalize("motors and drives")
        result = _tier4_locate(norm_quote, content)
        assert result is not None
        assert result.match_tier == 4

    def test_below_threshold(self):
        from services.extraction.grounding import _pos_normalize

        norm_quote = _pos_normalize("completely unrelated words here")
        assert _tier4_locate(norm_quote, "some other content entirely") is None

    def test_empty_quote(self):
        assert _tier4_locate("", "content") is None


# ── Position correctness ──


class TestPositionCorrectness:
    """Verify content[source_offset:source_end] contains expected text."""

    def test_tier1_position(self):
        content = "The  quick  brown  fox  jumps"
        result = ground_and_locate("quick brown fox", content)
        assert result.match_tier == 1
        span = content[result.source_offset : result.source_end]
        assert "quick" in span
        assert "fox" in span

    def test_tier1_with_newlines(self):
        content = "Header\n\nThe quick\nbrown fox jumps\n\nFooter"
        result = ground_and_locate("quick brown fox", content)
        assert result.match_tier == 1
        span = content[result.source_offset : result.source_end]
        assert "quick" in span
        assert "fox" in span

    def test_tier2_position(self):
        content = "Rating: A+ (excellent)"
        result = ground_and_locate("Rating A excellent", content)
        assert result.match_tier in (2, 3)
        span = content[result.source_offset : result.source_end]
        assert "Rating" in span or "rating" in span.lower()

    def test_tier3_position_markdown(self):
        content = "Buy our **premium gearboxes** today"
        result = ground_and_locate("premium gearboxes", content)
        assert result.source_offset is not None
        span = content[result.source_offset : result.source_end]
        # The span in original content may include markdown markers
        assert "premium" in span.lower() or "gearbox" in span.lower()

    def test_trailing_ellipsis_stripped(self):
        content = "We make premium gearboxes for all industries"
        result = ground_and_locate("premium gearboxes...", content)
        assert result.match_tier >= 1
        assert result.source_offset is not None

    def test_unicode_dash_normalized(self):
        content = "Range: 50-100 kW"
        result = ground_and_locate("Range: 50\u2013100 kW", content)
        assert result.match_tier >= 1
        assert result.source_offset is not None

    def test_empty_inputs(self):
        result = ground_and_locate("", "content")
        assert result.match_tier == 0
        assert result.source_offset is None

        result = ground_and_locate("quote", "")
        assert result.match_tier == 0
        assert result.source_offset is None

    def test_no_match(self):
        result = ground_and_locate(
            "completely unrelated xyz abc", "some other content here"
        )
        assert result.match_tier in (0, 4)


# ── Pre-computed variant ──


class TestPrecomputed:
    def test_same_result_as_non_precomputed(self):
        content = "The  quick  brown  fox  jumps  over"
        quote = "quick brown fox"
        r1 = ground_and_locate(quote, content)
        maps = precompute_content_maps(content)
        r2 = ground_and_locate_precomputed(quote, content, maps)
        assert r1.source_offset == r2.source_offset
        assert r1.source_end == r2.source_end
        assert r1.match_tier == r2.match_tier
        assert r1.score == r2.score

    def test_reuse_across_quotes(self):
        content = "Acme Corp makes gearboxes and motors for industrial use"
        maps = precompute_content_maps(content)
        r1 = ground_and_locate_precomputed("gearboxes and motors", content, maps)
        r2 = ground_and_locate_precomputed("industrial use", content, maps)
        assert r1.match_tier >= 1
        assert r2.match_tier >= 1
        assert r1.source_offset != r2.source_offset

    def test_content_maps_type(self):
        maps = precompute_content_maps("hello world")
        assert isinstance(maps, ContentMaps)
        assert isinstance(maps.norm_content, str)
        assert isinstance(maps.norm_map, list)


# ── locate_in_source integration ──


class TestLocateInSourceIntegration:
    def _make_chunk(self, header_path=None, chunk_index=0):
        return type(
            "Chunk",
            (),
            {
                "header_path": header_path or [],
                "chunk_index": chunk_index,
            },
        )()

    def test_with_content_maps(self):
        content = "The  quick  brown  fox"
        maps = precompute_content_maps(content)
        chunk = self._make_chunk(["About"], 1)
        loc = locate_in_source("quick brown fox", content, chunk, content_maps=maps)
        assert loc is not None
        assert loc.match_tier >= 1
        assert loc.match_quality > 0
        assert loc.char_offset is not None
        span = content[loc.char_offset : loc.char_end]
        assert "quick" in span

    def test_without_content_maps(self):
        content = "Acme Corp manufactures gearboxes"
        chunk = self._make_chunk()
        loc = locate_in_source("manufactures gearboxes", content, chunk)
        assert loc is not None
        assert loc.match_tier >= 1
        assert loc.char_offset is not None

    def test_match_tier_and_quality_populated(self):
        content = "Products: gearboxes, motors"
        chunk = self._make_chunk()
        loc = locate_in_source("gearboxes motors", content, chunk)
        assert loc is not None
        assert loc.match_tier > 0
        assert 0 < loc.match_quality <= 1.0

    def test_no_match_returns_location_with_none_offsets(self):
        chunk = self._make_chunk(["X"], 3)
        loc = locate_in_source("xyz completely unrelated", "some content", chunk)
        assert loc is not None
        assert loc.char_offset is None
        assert loc.chunk_index == 3
        assert loc.match_tier == 0

    def test_empty_quote_returns_none(self):
        assert locate_in_source("", "content", None) is None
        assert locate_in_source(None, "content", None) is None

    def test_source_location_new_fields_default(self):
        """New fields have sensible defaults for backward compat."""
        loc = SourceLocation(heading_path=[], char_offset=0, char_end=10, chunk_index=0)
        assert loc.match_tier == 0
        assert loc.match_quality == 1.0
