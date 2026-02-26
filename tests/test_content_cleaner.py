"""Tests for content_cleaner module."""

from services.extraction.content_cleaner import (
    clean_markdown_for_embedding,
    compute_line_link_density,
    find_content_by_line_density,
    strip_structural_junk,
)


class TestStripStructuralJunk:
    """Test Layer 1: universal structural pattern removal."""

    def test_strips_empty_alt_images(self):
        """Should remove ![](url) tracking pixels and logos."""
        content = "Hello ![](https://example.com/pixel.png) world"
        result = strip_structural_junk(content)
        assert "![](https://example.com/pixel.png)" not in result
        assert "Hello" in result
        assert "world" in result

    def test_strips_skip_to_content_links(self):
        """Should remove [Skip to content](#main) accessibility links."""
        content = "[Skip to content](#main)\n\n# Welcome"
        result = strip_structural_junk(content)
        assert "Skip to content" not in result
        assert "# Welcome" in result

    def test_strips_skip_to_main_content(self):
        """Should remove various skip-to patterns."""
        content = "[Skip to main content](#main)\nReal content here"
        result = strip_structural_junk(content)
        assert "Skip to" not in result
        assert "Real content here" in result

    def test_strips_bare_link_list_items(self):
        """Should remove '* [Link](url)' with nothing after."""
        content = "* [Products](/products)\n* [About](/about)\nReal paragraph text."
        result = strip_structural_junk(content)
        assert "* [Products](/products)" not in result
        assert "* [About](/about)" not in result
        assert "Real paragraph text." in result

    def test_preserves_described_link_list_items(self):
        """Should keep '* [Link](url) — Description' items."""
        content = "* [Products](/products) — Our full catalog\n* [About](/about)"
        result = strip_structural_junk(content)
        assert "Our full catalog" in result

    def test_strips_bare_image_lines(self):
        """Should remove ![alt](url) alone on a line."""
        content = "Text above\n![Logo](https://example.com/logo.png)\nText below"
        result = strip_structural_junk(content)
        assert "![Logo]" not in result
        assert "Text above" in result
        assert "Text below" in result

    def test_preserves_inline_images(self):
        """Should preserve images within paragraph text."""
        content = "See our ![chart](chart.png) for details about products."
        result = strip_structural_junk(content)
        # Inline images are NOT on their own line, so they're preserved
        assert "chart" in result

    def test_collapses_excessive_newlines(self):
        """Should collapse 3+ consecutive newlines to 2."""
        content = "Paragraph one\n\n\n\n\nParagraph two"
        result = strip_structural_junk(content)
        assert "\n\n\n" not in result
        assert "Paragraph one" in result
        assert "Paragraph two" in result

    def test_handles_empty_input(self):
        """Should handle empty string."""
        assert strip_structural_junk("") == ""

    def test_handles_none_input(self):
        """Should handle None input."""
        assert strip_structural_junk(None) is None

    def test_handles_whitespace_only(self):
        """Should handle whitespace-only input."""
        assert strip_structural_junk("   \n  \n  ") == ""

    def test_strips_dash_list_items(self):
        """Should handle dash-style bare link list items."""
        content = "- [Home](/)\n- [Contact](/contact)\nParagraph."
        result = strip_structural_junk(content)
        assert "- [Home](/)" not in result
        assert "- [Contact](/contact)" not in result
        assert "Paragraph." in result


class TestComputeLineLinkDensity:
    """Test link density calculation per line."""

    def test_pure_text_returns_zero(self):
        """Plain text should have 0.0 density."""
        density = compute_line_link_density("This is plain text with no links.")
        assert density == 0.0

    def test_pure_link_returns_high_density(self):
        """A line that is just a markdown link should have high density."""
        density = compute_line_link_density("[Click here](https://example.com)")
        assert density > 0.7

    def test_mixed_content_returns_moderate(self):
        """Text with some links should return moderate density."""
        density = compute_line_link_density(
            "Read more about [products](https://example.com/products) and their features"
        )
        assert 0.1 < density < 0.7

    def test_bare_url_counted(self):
        """Bare URLs should be counted in density."""
        density = compute_line_link_density("Visit https://example.com/page for info")
        assert density > 0.0

    def test_empty_line_returns_zero(self):
        """Empty line should return 0.0."""
        assert compute_line_link_density("") == 0.0

    def test_multiple_links_high_density(self):
        """Multiple links should accumulate density."""
        line = "[A](url1) [B](url2) [C](url3)"
        density = compute_line_link_density(line)
        assert density > 0.5


class TestFindContentByLineDensity:
    """Test Layer 2: line-density content windowing."""

    def test_content_starts_immediately(self):
        """Should return 0 when content starts at the top."""
        content = "This is a long paragraph about gearbox manufacturing.\nAnother line about products.\nMore real content here with details."
        offset = find_content_by_line_density(content)
        assert offset == 0

    def test_skips_nav_preamble(self):
        """Should skip navigation links at the top."""
        nav = "\n".join([
            "[Home](/) [Products](/products) [About](/about)",
            "[Contact](/contact) [Careers](/careers) [News](/news)",
            "[Support](/support) [Investors](/investors) [Blog](/blog)",
            "[Partners](/partners) [Resources](/resources)",
            "",
            "# About Our Company",
            "We manufacture high-quality industrial gearboxes.",
            "Our products serve customers worldwide.",
            "Founded in 1950, we have decades of experience.",
        ])
        offset = find_content_by_line_density(nav)
        assert offset > 0
        remaining = nav[offset:]
        assert "About Our Company" in remaining or "manufacture" in remaining

    def test_returns_zero_for_empty(self):
        """Should return 0 for empty content."""
        assert find_content_by_line_density("") == 0

    def test_returns_zero_when_no_clear_content(self):
        """Should return 0 (conservative) when no content region found."""
        all_links = "\n".join([
            "[Link1](url1) [Link2](url2)",
        ] * 5)
        offset = find_content_by_line_density(all_links)
        assert offset == 0

    def test_works_with_non_english_content(self):
        """Should work on non-English content (language agnostic)."""
        content = "\n".join([
            "[Startseite](/) [Produkte](/produkte) [Kontakt](/kontakt)",
            "[Über uns](/ueber-uns) [Karriere](/karriere)",
            "",
            "# Über unser Unternehmen",
            "Wir sind ein führender Hersteller von Industriegetrieben.",
            "Unsere Produkte werden weltweit eingesetzt.",
            "Gegründet im Jahr 1950, verfügen wir über jahrzehntelange Erfahrung.",
        ])
        offset = find_content_by_line_density(content)
        assert offset > 0
        remaining = content[offset:]
        assert "Unternehmen" in remaining or "Hersteller" in remaining


class TestCleanMarkdownForEmbedding:
    """Test full cleaning pipeline (Layer 1 + Layer 2)."""

    def test_combines_both_layers(self):
        """Should apply structural removal then density windowing."""
        content = "\n".join([
            "[Skip to content](#main)",
            "![](https://example.com/tracking.png)",
            "* [Home](/)",
            "* [Products](/products)",
            "* [About](/about)",
            "[Nav1](url1) [Nav2](url2) [Nav3](url3) [Nav4](url4)",
            "[Nav5](url5) [Nav6](url6) [Nav7](url7) [Nav8](url8)",
            "",
            "# Welcome to Our Company",
            "We are a leading manufacturer of industrial equipment.",
            "Our products serve customers in over 50 countries worldwide.",
            "Founded in 1990, we have three decades of innovation.",
        ])
        result = clean_markdown_for_embedding(content)
        assert "Skip to content" not in result
        assert "tracking.png" not in result
        assert "manufacturer" in result or "Welcome" in result

    def test_handles_clean_content(self):
        """Should return clean content mostly unchanged."""
        content = "# About Us\nWe make great products.\nOur team is experienced."
        result = clean_markdown_for_embedding(content)
        assert "About Us" in result
        assert "great products" in result

    def test_handles_empty(self):
        """Should handle empty input."""
        assert clean_markdown_for_embedding("") == ""

    def test_handles_none(self):
        """Should handle None input."""
        assert clean_markdown_for_embedding(None) is None
