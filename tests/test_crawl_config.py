"""Tests for CrawlConfig dataclass."""

from services.extraction.schema_adapter import CrawlConfig


class TestCrawlConfigFromDict:
    """Tests for CrawlConfig.from_dict() class method."""

    def test_from_dict_none_returns_none(self):
        """Test that None input returns None."""
        result = CrawlConfig.from_dict(None)
        assert result is None

    def test_from_dict_empty_returns_none(self):
        """Test that empty dict returns None (backward compatible)."""
        result = CrawlConfig.from_dict({})
        assert result is None

    def test_from_dict_with_focus_terms(self):
        """Test parsing focus_terms."""
        data = {
            "focus_terms": ["product specifications", "pricing plans"],
        }

        config = CrawlConfig.from_dict(data)

        assert config is not None
        assert config.focus_terms == ["product specifications", "pricing plans"]
        assert config.include_patterns is None
        assert config.exclude_patterns is None
        assert config.relevance_threshold is None

    def test_from_dict_with_patterns(self):
        """Test parsing include and exclude patterns."""
        data = {
            "include_patterns": [".*/products/.*", ".*/pricing/.*"],
            "exclude_patterns": [".*/careers/.*", ".*/blog/.*"],
        }

        config = CrawlConfig.from_dict(data)

        assert config is not None
        assert config.include_patterns == [".*/products/.*", ".*/pricing/.*"]
        assert config.exclude_patterns == [".*/careers/.*", ".*/blog/.*"]

    def test_from_dict_with_threshold(self):
        """Test parsing relevance_threshold."""
        data = {
            "relevance_threshold": 0.5,
        }

        config = CrawlConfig.from_dict(data)

        assert config is not None
        assert config.relevance_threshold == 0.5

    def test_from_dict_full_config(self):
        """Test parsing complete configuration."""
        data = {
            "include_patterns": [".*/docs/.*"],
            "exclude_patterns": [".*/archive/.*"],
            "focus_terms": ["API documentation", "SDK reference"],
            "relevance_threshold": 0.6,
        }

        config = CrawlConfig.from_dict(data)

        assert config is not None
        assert config.include_patterns == [".*/docs/.*"]
        assert config.exclude_patterns == [".*/archive/.*"]
        assert config.focus_terms == ["API documentation", "SDK reference"]
        assert config.relevance_threshold == 0.6


class TestCrawlConfigValidation:
    """Tests for CrawlConfig.validate() method."""

    def test_validate_empty_config_is_valid(self):
        """Test that empty config is valid."""
        config = CrawlConfig()
        is_valid, errors = config.validate()

        assert is_valid is True
        assert errors == []

    def test_validate_valid_include_patterns(self):
        """Test valid regex patterns pass validation."""
        config = CrawlConfig(
            include_patterns=[".*/products/.*", "^https://example\\.com/docs/.*"]
        )
        is_valid, errors = config.validate()

        assert is_valid is True
        assert errors == []

    def test_validate_invalid_include_pattern_regex(self):
        """Test invalid regex in include_patterns fails validation."""
        config = CrawlConfig(
            include_patterns=["[invalid(regex"]  # Invalid regex
        )
        is_valid, errors = config.validate()

        assert is_valid is False
        assert len(errors) == 1
        assert "include_patterns[0]" in errors[0]
        assert "invalid regex" in errors[0].lower()

    def test_validate_include_patterns_not_list(self):
        """Test non-list include_patterns fails validation."""
        config = CrawlConfig()
        config.include_patterns = "not a list"  # type: ignore
        is_valid, errors = config.validate()

        assert is_valid is False
        assert "must be a list" in errors[0]

    def test_validate_include_patterns_non_string_item(self):
        """Test non-string items in include_patterns fail validation."""
        config = CrawlConfig(
            include_patterns=[".*/valid/.*", 123, None]  # type: ignore
        )
        is_valid, errors = config.validate()

        assert is_valid is False
        assert any("include_patterns[1] must be a string" in e for e in errors)
        assert any("include_patterns[2] must be a string" in e for e in errors)

    def test_validate_valid_exclude_patterns(self):
        """Test valid regex patterns in exclude_patterns pass."""
        config = CrawlConfig(
            exclude_patterns=[".*/careers/.*", ".*/news/\\d{4}/.*"]
        )
        is_valid, errors = config.validate()

        assert is_valid is True
        assert errors == []

    def test_validate_invalid_exclude_pattern_regex(self):
        """Test invalid regex in exclude_patterns fails validation."""
        config = CrawlConfig(
            exclude_patterns=["valid", "(unclosed"]  # Second is invalid
        )
        is_valid, errors = config.validate()

        assert is_valid is False
        assert len(errors) == 1
        assert "exclude_patterns[1]" in errors[0]

    def test_validate_valid_focus_terms(self):
        """Test valid focus_terms pass validation."""
        config = CrawlConfig(
            focus_terms=["product specifications", "technical details", "pricing"]
        )
        is_valid, errors = config.validate()

        assert is_valid is True
        assert errors == []

    def test_validate_focus_terms_not_list(self):
        """Test non-list focus_terms fails validation."""
        config = CrawlConfig()
        config.focus_terms = "not a list"  # type: ignore
        is_valid, errors = config.validate()

        assert is_valid is False
        assert "must be a list" in errors[0]

    def test_validate_focus_terms_non_string_item(self):
        """Test non-string items in focus_terms fail validation."""
        config = CrawlConfig(
            focus_terms=["valid term", 123]  # type: ignore
        )
        is_valid, errors = config.validate()

        assert is_valid is False
        assert "focus_terms[1] must be a string" in errors[0]

    def test_validate_valid_threshold(self):
        """Test valid relevance_threshold passes validation."""
        config = CrawlConfig(relevance_threshold=0.5)
        is_valid, errors = config.validate()

        assert is_valid is True
        assert errors == []

    def test_validate_threshold_zero(self):
        """Test threshold of 0.0 is valid."""
        config = CrawlConfig(relevance_threshold=0.0)
        is_valid, errors = config.validate()

        assert is_valid is True

    def test_validate_threshold_one(self):
        """Test threshold of 1.0 is valid."""
        config = CrawlConfig(relevance_threshold=1.0)
        is_valid, errors = config.validate()

        assert is_valid is True

    def test_validate_threshold_below_range(self):
        """Test threshold below 0.0 fails validation."""
        config = CrawlConfig(relevance_threshold=-0.1)
        is_valid, errors = config.validate()

        assert is_valid is False
        assert "between 0.0 and 1.0" in errors[0]

    def test_validate_threshold_above_range(self):
        """Test threshold above 1.0 fails validation."""
        config = CrawlConfig(relevance_threshold=1.5)
        is_valid, errors = config.validate()

        assert is_valid is False
        assert "between 0.0 and 1.0" in errors[0]

    def test_validate_threshold_not_number(self):
        """Test non-numeric threshold fails validation."""
        config = CrawlConfig()
        config.relevance_threshold = "high"  # type: ignore
        is_valid, errors = config.validate()

        assert is_valid is False
        assert "must be a number" in errors[0]

    def test_validate_multiple_errors(self):
        """Test multiple validation errors are collected."""
        config = CrawlConfig(
            include_patterns=["[invalid"],
            exclude_patterns=["(also invalid"],
            focus_terms=[123],  # type: ignore
            relevance_threshold=2.0,
        )
        is_valid, errors = config.validate()

        assert is_valid is False
        assert len(errors) >= 4  # At least one error per invalid field


class TestCrawlConfigIntegration:
    """Integration tests for CrawlConfig with template parsing."""

    def test_template_with_crawl_config(self):
        """Test parsing template with crawl_config section."""
        from services.extraction.schema_adapter import SchemaAdapter

        template = {
            "name": "test_template",
            "extraction_schema": {
                "name": "test",
                "field_groups": [
                    {
                        "name": "facts",
                        "description": "Test facts",
                        "fields": [
                            {
                                "name": "fact",
                                "field_type": "text",
                                "description": "A fact",
                            }
                        ],
                    }
                ],
            },
            "crawl_config": {
                "focus_terms": ["product specs", "pricing"],
                "relevance_threshold": 0.5,
                "exclude_patterns": [".*/careers/.*"],
            },
        }

        adapter = SchemaAdapter()
        field_groups, context, classification_config, crawl_config = adapter.parse_template(
            template
        )

        assert crawl_config is not None
        assert crawl_config.focus_terms == ["product specs", "pricing"]
        assert crawl_config.relevance_threshold == 0.5
        assert crawl_config.exclude_patterns == [".*/careers/.*"]

    def test_template_without_crawl_config(self):
        """Test parsing template without crawl_config returns None."""
        from services.extraction.schema_adapter import SchemaAdapter

        template = {
            "name": "test_template",
            "extraction_schema": {
                "name": "test",
                "field_groups": [
                    {
                        "name": "facts",
                        "description": "Test facts",
                        "fields": [
                            {
                                "name": "fact",
                                "field_type": "text",
                                "description": "A fact",
                            }
                        ],
                    }
                ],
            },
        }

        adapter = SchemaAdapter()
        field_groups, context, classification_config, crawl_config = adapter.parse_template(
            template
        )

        assert crawl_config is None

    def test_crawl_config_default_values(self):
        """Test CrawlConfig default values."""
        config = CrawlConfig()

        assert config.include_patterns is None
        assert config.exclude_patterns is None
        assert config.focus_terms is None
        assert config.relevance_threshold is None


class TestCrawlConfigEdgeCases:
    """Edge case tests for CrawlConfig."""

    def test_empty_pattern_list_is_valid(self):
        """Test empty pattern lists are valid."""
        config = CrawlConfig(
            include_patterns=[],
            exclude_patterns=[],
        )
        is_valid, errors = config.validate()

        assert is_valid is True
        assert errors == []

    def test_empty_focus_terms_list_is_valid(self):
        """Test empty focus_terms list is valid."""
        config = CrawlConfig(focus_terms=[])
        is_valid, errors = config.validate()

        assert is_valid is True
        assert errors == []

    def test_complex_regex_patterns(self):
        """Test complex but valid regex patterns pass validation."""
        config = CrawlConfig(
            include_patterns=[
                r"^https?://(?:www\.)?example\.com/products/.*",
                r".*/api/v\d+/.*",
            ],
            exclude_patterns=[
                r".*/\d{4}/\d{2}/\d{2}/.*",  # Date patterns
                r".*\.(pdf|doc|docx)$",  # File extensions
            ],
        )
        is_valid, errors = config.validate()

        assert is_valid is True
        assert errors == []

    def test_threshold_integer_value(self):
        """Test integer threshold value is valid."""
        config = CrawlConfig(relevance_threshold=1)  # Integer 1
        is_valid, errors = config.validate()

        assert is_valid is True
