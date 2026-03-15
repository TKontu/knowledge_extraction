"""Tests for v2 inline grounding functions (ground_field_item, ground_entity_item)."""

from services.extraction.grounding import (
    ground_entity_fields,
    ground_entity_item,
    ground_field_item,
    score_entity_confidence,
)


class TestGroundFieldItem:
    """Test ground_field_item with each grounding mode."""

    CHUNK = "Acme Corp has approximately 500 employees and manufactures industrial gearboxes since 1995."

    def test_required_string_grounded(self):
        """String field with exact quote → high score."""
        score = ground_field_item(
            "company_name",
            "Acme Corp",
            "Acme Corp has approximately",
            self.CHUNK,
            "string",
        )
        assert score >= 0.8

    def test_required_string_fabricated_quote(self):
        """String field with fabricated quote → low score."""
        score = ground_field_item(
            "company_name",
            "Acme Corp",
            "Acme is the best company",
            self.CHUNK,
            "string",
        )
        assert score < 0.8

    def test_required_integer_grounded(self):
        """Integer field with value in quote and quote in source."""
        score = ground_field_item(
            "employee_count", 500, "approximately 500 employees", self.CHUNK, "integer"
        )
        assert score >= 0.8

    def test_required_integer_wrong_value(self):
        """Integer field with wrong value in quote → low Layer B."""
        score = ground_field_item(
            "employee_count", 1000, "approximately 500 employees", self.CHUNK, "integer"
        )
        assert score == 0.0  # 1000 not in quote

    def test_required_no_quote(self):
        """Required field with no quote → 0.0."""
        score = ground_field_item(
            "company_name", "Acme Corp", None, self.CHUNK, "string"
        )
        assert score == 0.0

    def test_required_empty_quote(self):
        score = ground_field_item("company_name", "Acme Corp", "", self.CHUNK, "string")
        assert score == 0.0

    def test_semantic_boolean_grounded(self):
        """Boolean field (semantic mode) checks quote-in-source only."""
        score = ground_field_item(
            "is_manufacturer",
            True,
            "manufactures industrial gearboxes",
            self.CHUNK,
            "boolean",
        )
        assert score >= 0.8

    def test_semantic_boolean_no_quote(self):
        """Boolean with no quote → no grounding evidence (0.0)."""
        score = ground_field_item("is_manufacturer", True, None, self.CHUNK, "boolean")
        assert score == 0.0

    def test_text_required_grounding_mode(self):
        """Text fields use required mode: value must appear in quote."""
        # Value "Acme Corp" appears in quote → grounded
        score = ground_field_item(
            "company_name",
            "Acme Corp",
            "Acme Corp has approximately",
            self.CHUNK,
            "text",
        )
        assert score >= 0.8
        # Without a quote → no grounding evidence
        score_no_quote = ground_field_item(
            "company_name", "Acme Corp", None, self.CHUNK, "text"
        )
        assert score_no_quote == 0.0

    def test_text_required_multi_word_value(self):
        """Text field with multi-word value — exact substring vs partial word match."""
        source = "The company is headquartered in Zurich, Switzerland since 1995. It has offices in Geneva and Zurich."

        # Value appears as exact substring in quote → high score
        score = ground_field_item(
            "location",
            "Zurich, Switzerland",
            "headquartered in Zurich, Switzerland since 1995",
            source,
            "text",
        )
        assert score >= 0.8

        # Value words present but reordered in quote → partial score
        score_partial = ground_field_item(
            "location",
            "Zurich and Geneva",
            "offices in Geneva and Zurich",
            source,
            "text",
        )
        assert 0.3 <= score_partial < 0.8

    def test_summary_type_none_mode(self):
        score = ground_field_item(
            "description", "A description", "any quote", self.CHUNK, "summary"
        )
        assert score == 1.0

    def test_enum_grounded(self):
        score = ground_field_item(
            "sector", "industrial", "industrial gearboxes", self.CHUNK, "enum"
        )
        assert score >= 0.8

    def test_list_grounded(self):
        score = ground_field_item(
            "products", ["gearboxes"], "industrial gearboxes", self.CHUNK, "list"
        )
        assert score >= 0.8

    def test_no_chunk_content(self):
        """No chunk content → Layer A fails."""
        score = ground_field_item("company_name", "Acme", "Acme Corp", "", "string")
        assert score == 0.0


class TestGroundEntityItem:
    CHUNK = "Our product Widget-X is a high-precision gearbox for automotive use."

    def test_entity_grounded(self):
        score = ground_entity_item("Widget-X is a high-precision", self.CHUNK)
        assert score >= 0.8

    def test_entity_fabricated_quote(self):
        score = ground_entity_item("Widget-X is the industry leader", self.CHUNK)
        assert score < 0.8

    def test_entity_no_quote(self):
        score = ground_entity_item(None, self.CHUNK)
        assert score == 0.0

    def test_entity_empty_content(self):
        score = ground_entity_item("Widget-X", "")
        assert score == 0.0

    def test_entity_exact_match(self):
        score = ground_entity_item("high-precision gearbox", self.CHUNK)
        assert score >= 0.95


class TestEntityFieldGroundingIntegration:
    """Integration tests: entity field grounding in extraction flow."""

    CHUNK = "The FZG-500 planetary gearbox delivers 50 kW with 97% efficiency for mining applications."
    QUOTE = "FZG-500 planetary gearbox delivers 50 kW with 97% efficiency"

    FIELD_DEFS = [
        {"name": "product_name", "field_type": "string"},
        {"name": "subcategory", "field_type": "string"},
        {"name": "power_rating_kw", "field_type": "float"},
        {"name": "efficiency_percent", "field_type": "float"},
    ]

    def test_all_fields_grounded(self):
        """All field values present in quote should score high."""
        fields = {
            "product_name": "FZG-500",
            "subcategory": "planetary",
            "power_rating_kw": 50,
            "efficiency_percent": 97,
        }
        scores = ground_entity_fields(fields, self.QUOTE, self.CHUNK, self.FIELD_DEFS)
        assert scores["product_name"] >= 0.5
        assert scores["subcategory"] >= 0.5
        assert scores["power_rating_kw"] == 1.0
        assert scores["efficiency_percent"] == 1.0

    def test_hallucinated_numeric_detected(self):
        """A hallucinated numeric value (10x the real one) scores 0."""
        fields = {
            "product_name": "FZG-500",
            "power_rating_kw": 500,  # Hallucinated: 10x real value
        }
        scores = ground_entity_fields(fields, self.QUOTE, self.CHUNK, self.FIELD_DEFS)
        assert scores["power_rating_kw"] == 0.0
        assert scores["product_name"] >= 0.5

    def test_combined_with_entity_confidence(self):
        """Field grounding feeds into entity confidence scoring."""
        fields = {
            "product_name": "FZG-500",
            "subcategory": "planetary",
            "power_rating_kw": 50,
            "efficiency_percent": 97,
        }
        field_gnd = ground_entity_fields(
            fields, self.QUOTE, self.CHUNK, self.FIELD_DEFS
        )

        conf_grounded = score_entity_confidence(
            fields,
            self.FIELD_DEFS,
            0.5,
            field_grounding=field_gnd,
            quote=self.QUOTE,
        )

        # Same entity but with hallucinated value
        fields_bad = dict(fields)
        fields_bad["power_rating_kw"] = 500
        field_gnd_bad = ground_entity_fields(
            fields_bad, self.QUOTE, self.CHUNK, self.FIELD_DEFS
        )
        conf_bad = score_entity_confidence(
            fields_bad,
            self.FIELD_DEFS,
            0.5,
            field_grounding=field_gnd_bad,
            quote=self.QUOTE,
        )

        assert conf_grounded > conf_bad
