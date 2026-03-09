"""Tests for v2 extraction data model (extraction_items.py)."""

import pytest

from services.extraction.extraction_items import (
    ChunkExtractionResult,
    EntityItem,
    FieldItem,
    ListValueItem,
    SourceLocation,
    locate_in_source,
    read_field_value,
    to_v2_data,
    v2_to_flat,
)


class TestSourceLocation:
    def test_creation(self):
        loc = SourceLocation(
            heading_path=["Products", "Gearboxes"],
            char_offset=100,
            char_end=150,
            chunk_index=2,
        )
        assert loc.heading_path == ["Products", "Gearboxes"]
        assert loc.char_offset == 100
        assert loc.char_end == 150
        assert loc.chunk_index == 2

    def test_frozen_immutability(self):
        loc = SourceLocation(
            heading_path=["A"], char_offset=0, char_end=10, chunk_index=0
        )
        with pytest.raises(AttributeError):
            loc.chunk_index = 5  # type: ignore[misc]

    def test_none_offsets(self):
        loc = SourceLocation(
            heading_path=[], char_offset=None, char_end=None, chunk_index=0
        )
        assert loc.char_offset is None
        assert loc.char_end is None


class TestFieldItem:
    def test_creation(self):
        item = FieldItem(
            value="Acme Corp",
            confidence=0.95,
            quote="Acme Corp is a leading...",
            grounding=0.9,
            location=None,
        )
        assert item.value == "Acme Corp"
        assert item.confidence == 0.95
        assert item.grounding == 0.9

    def test_with_location(self):
        loc = SourceLocation(["About"], 50, 75, 1)
        item = FieldItem("test", 0.8, "quote", 1.0, loc)
        assert item.location is not None
        assert item.location.chunk_index == 1


class TestListValueItem:
    def test_creation(self):
        item = ListValueItem(
            value="ISO 9001",
            confidence=0.8,
            quote="certified to ISO 9001",
            grounding=1.0,
            location=None,
        )
        assert item.value == "ISO 9001"
        assert item.confidence == 0.8
        assert item.grounding == 1.0


class TestEntityItem:
    def test_creation(self):
        entity = EntityItem(
            fields={"name": "Widget X", "type": "gearbox"},
            confidence=0.9,
            quote="Widget X is a gearbox",
            grounding=0.85,
            location=None,
        )
        assert entity.fields["name"] == "Widget X"
        assert entity.confidence == 0.9


class TestChunkExtractionResult:
    def test_defaults(self):
        result = ChunkExtractionResult(chunk_index=0)
        assert result.field_items == {}
        assert result.list_items == {}
        assert result.entity_items == {}

    def test_with_data(self):
        result = ChunkExtractionResult(
            chunk_index=1,
            field_items={
                "name": FieldItem("Acme", 0.9, "Acme Corp", 1.0, None),
            },
            list_items={
                "certs": [ListValueItem("ISO 9001", 0.8, "ISO 9001 cert", 1.0, None)],
            },
        )
        assert "name" in result.field_items
        assert len(result.list_items["certs"]) == 1


class TestLocateInSource:
    def test_finds_exact_quote(self):
        content = "Acme Corp is a leading manufacturer of gearboxes."
        chunk = type("Chunk", (), {"header_path": ["About"], "chunk_index": 0})()
        loc = locate_in_source("leading manufacturer", content, chunk)
        assert loc is not None
        assert loc.heading_path == ["About"]
        assert loc.char_offset is not None
        assert loc.chunk_index == 0
        # Verify position is correct in original content
        span = content[loc.char_offset:loc.char_end]
        assert "leading manufacturer" in span.lower()

    def test_case_insensitive(self):
        content = "ACME CORP is a Leading Manufacturer"
        chunk = type("Chunk", (), {"header_path": [], "chunk_index": 0})()
        loc = locate_in_source("leading manufacturer", content, chunk)
        assert loc is not None
        assert loc.char_offset is not None
        span = content[loc.char_offset:loc.char_end]
        assert "Leading Manufacturer" in span

    def test_whitespace_positions_correct(self):
        """Bug fix: positions must be in original content, not normalized."""
        content = "Acme   Corp   is   a   leading   manufacturer"
        chunk = type("Chunk", (), {"header_path": [], "chunk_index": 0})()
        loc = locate_in_source("leading manufacturer", content, chunk)
        assert loc is not None
        assert loc.char_offset is not None
        span = content[loc.char_offset:loc.char_end]
        assert "leading" in span
        assert "manufacturer" in span

    def test_empty_quote_returns_none(self):
        assert locate_in_source("", "content", None) is None
        assert locate_in_source(None, "content", None) is None

    def test_no_match_still_returns_location(self):
        chunk = type("Chunk", (), {"header_path": ["X"], "chunk_index": 3})()
        loc = locate_in_source("nonexistent quote xyz", "some content", chunk)
        assert loc is not None
        assert loc.char_offset is None  # Could not find position
        assert loc.chunk_index == 3

    def test_missing_chunk_attributes(self):
        chunk = object()  # No header_path or chunk_index
        loc = locate_in_source("test", "test content", chunk)
        assert loc is not None
        assert loc.heading_path == []
        assert loc.chunk_index == 0

    def test_match_tier_populated(self):
        content = "Acme Corp is a leading manufacturer"
        chunk = type("Chunk", (), {"header_path": [], "chunk_index": 0})()
        loc = locate_in_source("leading manufacturer", content, chunk)
        assert loc is not None
        assert loc.match_tier >= 1
        assert loc.match_quality > 0


class TestReadFieldValue:
    def test_v1_flat(self):
        data = {"company_name": "Acme", "employee_count": 500, "confidence": 0.8}
        assert read_field_value(data, "company_name", data_version=1) == "Acme"
        assert read_field_value(data, "employee_count", data_version=1) == 500

    def test_v1_missing_field(self):
        assert read_field_value({"a": 1}, "b", data_version=1) is None

    def test_v2_single_field(self):
        data = {
            "company_name": {
                "value": "Acme",
                "confidence": 0.95,
                "grounding": 1.0,
            }
        }
        assert read_field_value(data, "company_name", data_version=2) == "Acme"

    def test_v2_list_field(self):
        data = {
            "certifications": {
                "items": [
                    {"value": "ISO 9001", "grounding": 1.0},
                    {"value": "ISO 14001", "grounding": 0.9},
                ]
            }
        }
        result = read_field_value(data, "certifications", data_version=2)
        assert result == ["ISO 9001", "ISO 14001"]

    def test_v2_missing_field(self):
        assert read_field_value({"a": {"value": 1}}, "b", data_version=2) is None

    def test_empty_data(self):
        assert read_field_value({}, "x", data_version=1) is None
        assert read_field_value({}, "x", data_version=2) is None
        assert read_field_value(None, "x", data_version=1) is None


class TestToV2Data:
    def test_single_fields(self):
        items = {
            "name": FieldItem("Acme", 0.9, "Acme Corp", 1.0, None),
            "count": FieldItem(500, 0.7, "~500 employees", 0.8, None),
        }
        result = to_v2_data(items, {}, {}, "company_info")
        assert result["name"]["value"] == "Acme"
        assert result["name"]["confidence"] == 0.9
        assert result["name"]["quote"] == "Acme Corp"
        assert result["count"]["value"] == 500
        assert result["_meta"]["data_version"] == 2
        assert result["_meta"]["group"] == "company_info"

    def test_list_fields(self):
        lists = {
            "certs": [
                ListValueItem("ISO 9001", 0.8, "ISO 9001 cert", 1.0, None),
                ListValueItem("ISO 14001", 0.7, None, 0.5, None),
            ]
        }
        result = to_v2_data({}, lists, {}, "info")
        assert len(result["certs"]["items"]) == 2
        assert result["certs"]["items"][0]["value"] == "ISO 9001"
        assert "quote" not in result["certs"]["items"][1]  # None quote omitted

    def test_entity_fields(self):
        entities = {
            "products": [
                EntityItem(
                    {"name": "Widget", "type": "gear"},
                    0.9,
                    "Widget is a gear",
                    0.85,
                    None,
                ),
            ]
        }
        result = to_v2_data({}, {}, entities, "products")
        items = result["products"]["items"]
        assert len(items) == 1
        assert items[0]["fields"]["name"] == "Widget"

    def test_with_location(self):
        loc = SourceLocation(["Products"], 10, 30, 0)
        items = {"name": FieldItem("X", 0.9, "X quote", 1.0, loc)}
        result = to_v2_data(items, {}, {}, "g")
        assert result["name"]["location"]["heading_path"] == ["Products"]
        assert result["name"]["location"]["chunk_index"] == 0


class TestV2ToFlat:
    def test_single_fields(self):
        v2 = {
            "company_name": {
                "value": "Acme",
                "confidence": 0.95,
                "grounding": 1.0,
                "quote": "Acme Corp",
            },
            "employee_count": {
                "value": 500,
                "confidence": 0.7,
                "grounding": 0.8,
                "quote": "~500",
            },
            "_meta": {"data_version": 2},
        }
        flat = v2_to_flat(v2)
        assert flat["company_name"] == "Acme"
        assert flat["employee_count"] == 500
        assert flat["_quotes"]["company_name"] == "Acme Corp"
        assert flat["_quotes"]["employee_count"] == "~500"
        assert flat["confidence"] == pytest.approx(0.825)  # avg(0.95, 0.7)

    def test_list_field(self):
        v2 = {
            "certs": {
                "items": [
                    {"value": "ISO 9001", "grounding": 1.0, "quote": "ISO 9001 cert"},
                    {"value": "ISO 14001", "grounding": 0.9},
                ]
            },
            "_meta": {"data_version": 2},
        }
        flat = v2_to_flat(v2)
        assert flat["certs"] == ["ISO 9001", "ISO 14001"]

    def test_entity_field(self):
        v2 = {
            "products": {
                "items": [
                    {
                        "fields": {"name": "Widget", "type": "gear"},
                        "confidence": 0.9,
                        "grounding": 0.85,
                        "quote": "Widget gear",
                    },
                ]
            },
            "_meta": {"data_version": 2},
        }
        flat = v2_to_flat(v2)
        assert flat["products"] == [{"name": "Widget", "type": "gear"}]

    def test_empty_data(self):
        assert v2_to_flat({}) == {}
        assert v2_to_flat(None) == {}

    def test_roundtrip_preserves_values(self):
        """v2 -> flat -> read should preserve field values."""
        field_items = {
            "name": FieldItem("Acme", 0.9, "Acme Corp", 1.0, None),
        }
        list_items = {
            "certs": [ListValueItem("ISO", 0.8, "ISO cert", 1.0, None)],
        }
        v2 = to_v2_data(field_items, list_items, {}, "test")
        flat = v2_to_flat(v2)
        assert flat["name"] == "Acme"
        assert flat["certs"] == ["ISO"]
