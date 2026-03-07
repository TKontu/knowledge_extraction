"""Tests for v2 inline grounding functions (ground_field_item, ground_entity_item)."""

from services.extraction.grounding import ground_entity_item, ground_field_item


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
        """Boolean with no quote → neutral 0.5."""
        score = ground_field_item("is_manufacturer", True, None, self.CHUNK, "boolean")
        assert score == 0.5

    def test_none_grounding_mode(self):
        """Text/summary fields return 1.0 regardless."""
        score = ground_field_item(
            "overview", "Some long summary", None, self.CHUNK, "text"
        )
        assert score == 1.0

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
