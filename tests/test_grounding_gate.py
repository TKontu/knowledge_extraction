"""Tests for apply_grounding_gate: post-parse async filtering/rescue."""

from unittest.mock import AsyncMock

import pytest

from services.extraction.extraction_items import (
    ChunkExtractionResult,
    EntityItem,
    FieldItem,
    ListValueItem,
    SourceLocation,
)
from services.extraction.llm_grounding import RescueResult
from services.extraction.schema_orchestrator import apply_grounding_gate


@pytest.fixture
def mock_verifier():
    v = AsyncMock()
    v.rescue_quote = AsyncMock(
        return_value=RescueResult(quote=None, grounding=0.0, latency=0.1)
    )
    return v


def _loc(idx: int = 0) -> SourceLocation:
    return SourceLocation(heading_path=[], char_offset=0, char_end=10, chunk_index=idx)


# Default field_types for tests — all required unless overridden
_STRING_TYPES = {"company_name": "string", "employee_count": "integer",
                 "fabricated": "string", "location": "string",
                 "keep": "string", "drop": "string", "rescue": "string"}


class TestFieldItemGating:
    @pytest.mark.asyncio
    async def test_keep_high_grounding(self, mock_verifier):
        """Fields with grounding >= 0.8 are kept."""
        result = ChunkExtractionResult(
            chunk_index=0,
            field_items={
                "company_name": FieldItem("ABB", 0.9, "ABB Corp", 1.0, _loc()),
                "employee_count": FieldItem(105000, 0.8, "105,000 employees", 0.95, _loc()),
            },
        )
        gated = await apply_grounding_gate(
            result, "source text", mock_verifier, field_types=_STRING_TYPES
        )
        assert "company_name" in gated.field_items
        assert "employee_count" in gated.field_items
        mock_verifier.rescue_quote.assert_not_called()

    @pytest.mark.asyncio
    async def test_drop_low_grounding(self, mock_verifier):
        """Fields with grounding < 0.3 are dropped."""
        result = ChunkExtractionResult(
            chunk_index=0,
            field_items={
                "company_name": FieldItem("ABB", 0.9, "ABB Corp", 1.0, _loc()),
                "fabricated": FieldItem("fake", 0.9, "no quote", 0.1, _loc()),
            },
        )
        gated = await apply_grounding_gate(
            result, "source text", mock_verifier, field_types=_STRING_TYPES
        )
        assert "company_name" in gated.field_items
        assert "fabricated" not in gated.field_items
        mock_verifier.rescue_quote.assert_not_called()

    @pytest.mark.asyncio
    async def test_rescue_borderline_success(self, mock_verifier):
        """Borderline fields (0.3-0.8) are rescued via LLM."""
        mock_verifier.rescue_quote.return_value = RescueResult(
            quote="actual verbatim quote",
            grounding=0.95,
            latency=0.2,
        )
        result = ChunkExtractionResult(
            chunk_index=0,
            field_items={
                "location": FieldItem("Zurich", 0.8, "based in Zurich", 0.5, _loc()),
            },
        )
        gated = await apply_grounding_gate(
            result, "source text", mock_verifier, field_types=_STRING_TYPES
        )
        assert "location" in gated.field_items
        assert gated.field_items["location"].quote == "actual verbatim quote"
        assert gated.field_items["location"].grounding == 0.95
        mock_verifier.rescue_quote.assert_called_once()

    @pytest.mark.asyncio
    async def test_rescue_borderline_failure(self, mock_verifier):
        """Borderline field rescue fails → field is dropped."""
        mock_verifier.rescue_quote.return_value = RescueResult(
            quote=None, grounding=0.0, latency=0.2
        )
        result = ChunkExtractionResult(
            chunk_index=0,
            field_items={
                "location": FieldItem("Zurich", 0.8, "based in Zurich", 0.5, _loc()),
            },
        )
        gated = await apply_grounding_gate(
            result, "source text", mock_verifier, field_types=_STRING_TYPES
        )
        assert "location" not in gated.field_items

    @pytest.mark.asyncio
    async def test_mixed_fields(self, mock_verifier):
        """Mix of keep, drop, and rescue fields."""
        mock_verifier.rescue_quote.return_value = RescueResult(
            quote="rescued quote", grounding=0.9, latency=0.1
        )
        result = ChunkExtractionResult(
            chunk_index=0,
            field_items={
                "keep": FieldItem("v1", 0.9, "q1", 0.9, _loc()),
                "drop": FieldItem("v2", 0.9, "q2", 0.1, _loc()),
                "rescue": FieldItem("v3", 0.9, "q3", 0.5, _loc()),
            },
        )
        gated = await apply_grounding_gate(
            result, "source text", mock_verifier, field_types=_STRING_TYPES
        )
        assert "keep" in gated.field_items
        assert "drop" not in gated.field_items
        assert "rescue" in gated.field_items


class TestFieldTypeAwareGating:
    """Borderline non-required fields are kept, not rescued or dropped."""

    @pytest.mark.asyncio
    async def test_boolean_borderline_kept_without_rescue(self, mock_verifier):
        """Boolean fields (grounding_mode=semantic) with borderline score are kept as-is."""
        result = ChunkExtractionResult(
            chunk_index=0,
            field_items={
                "is_public": FieldItem(True, 0.9, "publicly traded", 0.5, _loc()),
            },
        )
        gated = await apply_grounding_gate(
            result, "source text", mock_verifier,
            field_types={"is_public": "boolean"},
        )
        assert "is_public" in gated.field_items
        assert gated.field_items["is_public"].grounding == 0.5  # unchanged
        mock_verifier.rescue_quote.assert_not_called()

    @pytest.mark.asyncio
    async def test_text_borderline_kept_without_rescue(self, mock_verifier):
        """Text fields (grounding_mode=none) with borderline score are kept as-is."""
        result = ChunkExtractionResult(
            chunk_index=0,
            field_items={
                "description": FieldItem("A company", 0.9, "q", 0.5, _loc()),
            },
        )
        gated = await apply_grounding_gate(
            result, "source text", mock_verifier,
            field_types={"description": "text"},
        )
        assert "description" in gated.field_items
        mock_verifier.rescue_quote.assert_not_called()

    @pytest.mark.asyncio
    async def test_summary_borderline_kept_without_rescue(self, mock_verifier):
        """Summary fields (grounding_mode=none) with borderline score are kept."""
        result = ChunkExtractionResult(
            chunk_index=0,
            field_items={
                "overview": FieldItem("Summary text", 0.9, "q", 0.4, _loc()),
            },
        )
        gated = await apply_grounding_gate(
            result, "source text", mock_verifier,
            field_types={"overview": "summary"},
        )
        assert "overview" in gated.field_items
        mock_verifier.rescue_quote.assert_not_called()

    @pytest.mark.asyncio
    async def test_required_borderline_gets_rescued(self, mock_verifier):
        """String fields (grounding_mode=required) in borderline band DO get rescue."""
        mock_verifier.rescue_quote.return_value = RescueResult(
            quote="rescued", grounding=0.9, latency=0.1
        )
        result = ChunkExtractionResult(
            chunk_index=0,
            field_items={
                "company_name": FieldItem("ABB", 0.9, "q", 0.5, _loc()),
            },
        )
        gated = await apply_grounding_gate(
            result, "source text", mock_verifier,
            field_types={"company_name": "string"},
        )
        assert "company_name" in gated.field_items
        mock_verifier.rescue_quote.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_field_types_treats_all_as_required(self, mock_verifier):
        """When field_types is None, all fields default to 'required' mode."""
        mock_verifier.rescue_quote.return_value = RescueResult(
            quote="rescued", grounding=0.9, latency=0.1
        )
        result = ChunkExtractionResult(
            chunk_index=0,
            field_items={
                "unknown_field": FieldItem("val", 0.9, "q", 0.5, _loc()),
            },
        )
        gated = await apply_grounding_gate(result, "source text", mock_verifier)
        assert "unknown_field" in gated.field_items
        mock_verifier.rescue_quote.assert_called_once()


class TestListItemGating:
    @pytest.mark.asyncio
    async def test_keep_high_grounding_list(self, mock_verifier):
        result = ChunkExtractionResult(
            chunk_index=0,
            list_items={
                "products": [
                    ListValueItem("gearbox", 0.9, "gearbox quote", 1.0, _loc()),
                    ListValueItem("motor", 0.9, "motor quote", 0.95, _loc()),
                ],
            },
        )
        gated = await apply_grounding_gate(result, "source", mock_verifier)
        assert len(gated.list_items["products"]) == 2

    @pytest.mark.asyncio
    async def test_drop_low_grounding_list(self, mock_verifier):
        result = ChunkExtractionResult(
            chunk_index=0,
            list_items={
                "products": [
                    ListValueItem("gearbox", 0.9, "gearbox quote", 1.0, _loc()),
                    ListValueItem("fabricated", 0.9, "no quote", 0.05, _loc()),
                ],
            },
        )
        gated = await apply_grounding_gate(result, "source", mock_verifier)
        assert len(gated.list_items["products"]) == 1
        assert gated.list_items["products"][0].value == "gearbox"

    @pytest.mark.asyncio
    async def test_all_list_items_dropped(self, mock_verifier):
        """If all list items are dropped, the key is absent."""
        result = ChunkExtractionResult(
            chunk_index=0,
            list_items={
                "products": [
                    ListValueItem("fake1", 0.9, "q", 0.05, _loc()),
                    ListValueItem("fake2", 0.9, "q", 0.1, _loc()),
                ],
            },
        )
        gated = await apply_grounding_gate(result, "source", mock_verifier)
        assert "products" not in gated.list_items

    @pytest.mark.asyncio
    async def test_list_borderline_boolean_kept(self, mock_verifier):
        """List items with non-required type in borderline band are kept."""
        result = ChunkExtractionResult(
            chunk_index=0,
            list_items={
                "flags": [
                    ListValueItem(True, 0.9, "q", 0.5, _loc()),
                ],
            },
        )
        gated = await apply_grounding_gate(
            result, "source", mock_verifier,
            field_types={"flags": "boolean"},
        )
        assert "flags" in gated.list_items
        mock_verifier.rescue_quote.assert_not_called()


class TestEntityItemGating:
    @pytest.mark.asyncio
    async def test_keep_high_grounding_entity(self, mock_verifier):
        result = ChunkExtractionResult(
            chunk_index=0,
            entity_items={
                "products": [
                    EntityItem({"name": "Motor X"}, 0.9, "Motor X series", 1.0, _loc()),
                ],
            },
        )
        gated = await apply_grounding_gate(result, "source", mock_verifier)
        assert len(gated.entity_items["products"]) == 1

    @pytest.mark.asyncio
    async def test_drop_low_grounding_entity(self, mock_verifier):
        result = ChunkExtractionResult(
            chunk_index=0,
            entity_items={
                "products": [
                    EntityItem({"name": "Motor X"}, 0.9, "Motor X", 1.0, _loc()),
                    EntityItem({"name": "Fake"}, 0.9, "no quote", 0.05, _loc()),
                ],
            },
        )
        gated = await apply_grounding_gate(result, "source", mock_verifier)
        assert len(gated.entity_items["products"]) == 1
        assert gated.entity_items["products"][0].fields["name"] == "Motor X"

    @pytest.mark.asyncio
    async def test_rescue_borderline_entity(self, mock_verifier):
        mock_verifier.rescue_quote.return_value = RescueResult(
            quote="rescued entity quote", grounding=0.9, latency=0.1
        )
        result = ChunkExtractionResult(
            chunk_index=0,
            entity_items={
                "products": [
                    EntityItem({"name": "Motor X"}, 0.9, "partial match", 0.5, _loc()),
                ],
            },
        )
        gated = await apply_grounding_gate(result, "source", mock_verifier)
        assert len(gated.entity_items["products"]) == 1
        assert gated.entity_items["products"][0].quote == "rescued entity quote"

    @pytest.mark.asyncio
    async def test_borderline_entity_without_name_dropped(self, mock_verifier):
        """Entities without name/entity_id/id fields are dropped in borderline band."""
        result = ChunkExtractionResult(
            chunk_index=0,
            entity_items={
                "products": [
                    EntityItem(
                        {"model_number": "ABC-123", "spec": "500W"},
                        0.9, "partial", 0.5, _loc(),
                    ),
                ],
            },
        )
        gated = await apply_grounding_gate(result, "source", mock_verifier)
        assert "products" not in gated.entity_items
        mock_verifier.rescue_quote.assert_not_called()


class TestGatePreservesMetadata:
    @pytest.mark.asyncio
    async def test_preserves_chunk_index(self, mock_verifier):
        result = ChunkExtractionResult(chunk_index=5, truncated=True)
        gated = await apply_grounding_gate(result, "source", mock_verifier)
        assert gated.chunk_index == 5
        assert gated.truncated is True

    @pytest.mark.asyncio
    async def test_empty_result_passthrough(self, mock_verifier):
        result = ChunkExtractionResult(chunk_index=0)
        gated = await apply_grounding_gate(result, "source", mock_verifier)
        assert gated.field_items == {}
        assert gated.list_items == {}
        assert gated.entity_items == {}
