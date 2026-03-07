"""Tests for cardinality-based chunk merge (v2)."""

from services.extraction.chunk_merge import (
    field_cardinality,
    merge_boolean,
    merge_chunk_results,
    merge_entities,
    merge_list_values,
    merge_single_answer,
    merge_summary,
)
from services.extraction.extraction_items import (
    ChunkExtractionResult,
    EntityItem,
    FieldItem,
    ListValueItem,
)
from services.extraction.field_groups import FieldDefinition, FieldGroup


def _field(name, ftype="text"):
    return FieldDefinition(name, ftype, f"desc for {name}")


def _chunk(idx, field_items=None, list_items=None, entity_items=None):
    return ChunkExtractionResult(
        chunk_index=idx,
        field_items=field_items or {},
        list_items=list_items or {},
        entity_items=entity_items or {},
    )


def _item(value, confidence=0.8, quote=None, grounding=0.9, location=None):
    return FieldItem(value, confidence, quote, grounding, location)


class TestFieldCardinality:
    def test_text_is_single(self):
        assert field_cardinality(_field("x", "text")) == "single"

    def test_integer_is_single(self):
        assert field_cardinality(_field("x", "integer")) == "single"

    def test_float_is_single(self):
        assert field_cardinality(_field("x", "float")) == "single"

    def test_enum_is_single(self):
        assert field_cardinality(_field("x", "enum")) == "single"

    def test_boolean(self):
        assert field_cardinality(_field("x", "boolean")) == "boolean"

    def test_list_is_multi_value(self):
        assert field_cardinality(_field("x", "list")) == "multi_value"

    def test_summary(self):
        assert field_cardinality(_field("x", "summary")) == "summary"


class TestMergeSingleAnswer:
    def test_picks_best_by_score(self):
        field = _field("name")
        chunks = [
            _chunk(0, {"name": _item("Acme", confidence=0.7, grounding=0.8)}),
            _chunk(1, {"name": _item("Acme Corp", confidence=0.9, grounding=0.95)}),
        ]
        result = merge_single_answer(field, chunks)
        assert result.value == "Acme Corp"
        assert result.confidence == 0.9
        assert hasattr(result, "alternatives")
        assert len(result.alternatives) == 1

    def test_single_chunk(self):
        field = _field("name")
        chunks = [_chunk(0, {"name": _item("Acme", confidence=0.9, grounding=1.0)})]
        result = merge_single_answer(field, chunks)
        assert result.value == "Acme"

    def test_no_values(self):
        field = _field("name")
        chunks = [_chunk(0)]  # No field_items for "name"
        result = merge_single_answer(field, chunks)
        assert result.value is None
        assert result.confidence == 0.0

    def test_null_values_skipped(self):
        field = _field("name")
        chunks = [
            _chunk(0, {"name": _item(None, confidence=0.9, grounding=1.0)}),
            _chunk(1, {"name": _item("Real", confidence=0.5, grounding=0.8)}),
        ]
        result = merge_single_answer(field, chunks)
        assert result.value == "Real"


class TestMergeBoolean:
    def test_credible_true_wins(self):
        field = _field("is_mfg", "boolean")
        chunks = [
            _chunk(0, {"is_mfg": _item(False, confidence=0.6, grounding=0.9)}),
            _chunk(1, {"is_mfg": _item(True, confidence=0.8, grounding=0.9)}),
        ]
        result = merge_boolean(field, chunks)
        assert result.value is True

    def test_low_confidence_true_loses(self):
        field = _field("is_mfg", "boolean")
        chunks = [
            _chunk(0, {"is_mfg": _item(True, confidence=0.3, grounding=0.5)}),
            _chunk(1, {"is_mfg": _item(False, confidence=0.9, grounding=1.0)}),
        ]
        result = merge_boolean(field, chunks)
        assert result.value is False

    def test_no_values(self):
        field = _field("x", "boolean")
        result = merge_boolean(field, [_chunk(0)])
        assert result.value is False

    def test_all_false(self):
        field = _field("x", "boolean")
        chunks = [
            _chunk(0, {"x": _item(False, confidence=0.8, grounding=1.0)}),
            _chunk(1, {"x": _item(False, confidence=0.6, grounding=1.0)}),
        ]
        result = merge_boolean(field, chunks)
        assert result.value is False


class TestMergeListValues:
    def test_union_dedup(self):
        field = _field("certs", "list")
        chunks = [
            _chunk(
                0,
                list_items={
                    "certs": [
                        ListValueItem("ISO 9001", 0.8, "ISO 9001 cert", 1.0, None),
                        ListValueItem("ISO 14001", 0.8, "ISO 14001 cert", 0.9, None),
                    ]
                },
            ),
            _chunk(
                1,
                list_items={
                    "certs": [
                        ListValueItem(
                            "ISO 9001", 0.8, "another ISO ref", 0.8, None
                        ),  # duplicate
                        ListValueItem("ATEX", 0.8, "ATEX certified", 1.0, None),
                    ]
                },
            ),
        ]
        result = merge_list_values(field, chunks)
        values = [item.value for item in result]
        assert len(values) == 3
        assert "ISO 9001" in values
        assert "ISO 14001" in values
        assert "ATEX" in values

    def test_empty(self):
        field = _field("certs", "list")
        result = merge_list_values(field, [_chunk(0)])
        assert result == []

    def test_null_values_skipped(self):
        field = _field("certs", "list")
        chunks = [
            _chunk(
                0,
                list_items={
                    "certs": [
                        ListValueItem(None, 0.0, None, 0.0, None),
                        ListValueItem("ISO", 0.8, "ISO", 1.0, None),
                    ]
                },
            ),
        ]
        result = merge_list_values(field, chunks)
        assert len(result) == 1


class TestMergeSummary:
    def test_longest_confident_wins(self):
        field = _field("overview", "summary")
        chunks = [
            _chunk(0, {"overview": _item("Short.", confidence=0.8, grounding=1.0)}),
            _chunk(
                1,
                {
                    "overview": _item(
                        "This is a much longer summary text with more detail.",
                        confidence=0.7,
                        grounding=1.0,
                    )
                },
            ),
        ]
        result = merge_summary(field, chunks)
        assert "longer" in result.value

    def test_filters_low_confidence(self):
        field = _field("overview", "summary")
        chunks = [
            _chunk(
                0,
                {
                    "overview": _item(
                        "Very long text " * 10, confidence=0.1, grounding=1.0
                    )
                },
            ),
            _chunk(
                1,
                {"overview": _item("Short confident.", confidence=0.8, grounding=1.0)},
            ),
        ]
        result = merge_summary(field, chunks)
        assert result.value == "Short confident."

    def test_empty(self):
        field = _field("overview", "summary")
        result = merge_summary(field, [_chunk(0)])
        assert result.value is None


class TestMergeEntities:
    def _entity(self, name, etype="gear", confidence=0.9, grounding=0.85):
        return EntityItem(
            fields={"name": name, "type": etype},
            confidence=confidence,
            quote=f"{name} is a {etype}",
            grounding=grounding,
            location=None,
        )

    def _group(self):
        return FieldGroup(
            name="products",
            description="products",
            fields=[_field("name"), _field("type")],
            prompt_hint="",
            is_entity_list=True,
        )

    def test_dedup_by_name(self):
        group = self._group()
        chunks = [
            _chunk(
                0,
                entity_items={
                    "products": [
                        self._entity("Widget A"),
                        self._entity("Widget B"),
                    ]
                },
            ),
            _chunk(
                1,
                entity_items={
                    "products": [
                        self._entity("Widget A"),  # duplicate
                        self._entity("Widget C"),
                    ]
                },
            ),
        ]
        result = merge_entities(chunks, group)
        names = [e.fields["name"] for e in result]
        assert len(names) == 3
        assert "Widget A" in names
        assert "Widget B" in names
        assert "Widget C" in names

    def test_empty(self):
        group = self._group()
        result = merge_entities([_chunk(0)], group)
        assert result == []

    def test_case_insensitive_dedup(self):
        group = self._group()
        chunks = [
            _chunk(0, entity_items={"products": [self._entity("Widget A")]}),
            _chunk(1, entity_items={"products": [self._entity("widget a")]}),
        ]
        result = merge_entities(chunks, group)
        assert len(result) == 1


class TestMergeChunkResults:
    def test_field_group_produces_v2_data(self):
        group = FieldGroup(
            name="company_info",
            description="company info",
            fields=[
                _field("name", "text"),
                _field("count", "integer"),
                _field("is_mfg", "boolean"),
                _field("certs", "list"),
            ],
            prompt_hint="",
        )
        chunks = [
            _chunk(
                0,
                field_items={
                    "name": _item("Acme", confidence=0.9, grounding=1.0),
                    "count": _item(500, confidence=0.7, grounding=0.8),
                    "is_mfg": _item(True, confidence=0.8, grounding=0.9),
                },
                list_items={
                    "certs": [ListValueItem("ISO", 0.8, "ISO cert", 1.0, None)],
                },
            ),
        ]
        result = merge_chunk_results(chunks, group)
        assert result["name"]["value"] == "Acme"
        assert result["count"]["value"] == 500
        assert result["is_mfg"]["value"] is True
        assert result["certs"]["items"][0]["value"] == "ISO"
        assert result["_meta"]["data_version"] == 2

    def test_entity_group_produces_v2_data(self):
        group = FieldGroup(
            name="products",
            description="products",
            fields=[_field("name"), _field("type")],
            prompt_hint="",
            is_entity_list=True,
        )
        chunks = [
            _chunk(
                0,
                entity_items={
                    "products": [
                        EntityItem(
                            {"name": "W", "type": "g"}, 0.9, "W is g", 0.85, None
                        ),
                    ]
                },
            ),
        ]
        result = merge_chunk_results(chunks, group)
        assert "products" in result
        assert result["products"]["items"][0]["fields"]["name"] == "W"

    def test_empty_chunks(self):
        group = FieldGroup(
            name="test",
            description="test",
            fields=[_field("x")],
            prompt_hint="",
        )
        assert merge_chunk_results([], group) == {}
