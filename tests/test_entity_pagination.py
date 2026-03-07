"""Tests for entity pagination (v2)."""

from unittest.mock import AsyncMock

from services.extraction.field_groups import FieldDefinition, FieldGroup
from services.extraction.schema_orchestrator import SchemaExtractionOrchestrator


def _make_entity_group(max_items=10):
    return FieldGroup(
        name="products",
        description="products manufactured",
        fields=[
            FieldDefinition("name", "text", "Product name"),
            FieldDefinition("type", "text", "Product type"),
        ],
        prompt_hint="Extract all products.",
        is_entity_list=True,
        max_items=max_items,
    )


def _make_orchestrator(extractor_mock):
    from config import ClassificationConfig, ExtractionConfig

    extraction_config = ExtractionConfig(
        content_limit=20000,
        chunk_max_tokens=5000,
        chunk_overlap_tokens=200,
        max_concurrent_chunks=4,
        max_concurrent_sources=2,
        extraction_batch_size=10,
        source_quoting_enabled=True,
        conflict_detection_enabled=False,
        validation_enabled=False,
        validation_min_confidence=0.0,
        embedding_max_concurrent=4,
        schema_embedding_enabled=False,
        domain_dedup_enabled=False,
        domain_dedup_threshold_pct=0.7,
        domain_dedup_min_pages=5,
        domain_dedup_min_block_chars=50,
        source_grounding_min_ratio=0.5,
        data_version=2,
    )
    classification_config = ClassificationConfig(
        enabled=False,
        skip_enabled=False,
        smart_enabled=False,
        reranker_model="",
        embedding_high_threshold=0.7,
        embedding_low_threshold=0.3,
        reranker_threshold=0.5,
        cache_ttl=300,
        use_default_skip_patterns=True,
        classifier_content_limit=5000,
    )
    return SchemaExtractionOrchestrator(
        extractor_mock,
        extraction_config=extraction_config,
        classification_config=classification_config,
    )


class TestEntityPagination:
    async def test_three_pages(self):
        """Pagination loop: 3 pages of entities."""
        group = _make_entity_group(max_items=50)
        responses = [
            {
                "products": [
                    {"name": "A", "type": "gear", "_confidence": 0.9, "_quote": "A"},
                    {"name": "B", "type": "gear", "_confidence": 0.8, "_quote": "B"},
                ],
                "has_more": True,
            },
            {
                "products": [
                    {"name": "C", "type": "bearing", "_confidence": 0.9, "_quote": "C"},
                ],
                "has_more": True,
            },
            {
                "products": [
                    {"name": "D", "type": "motor", "_confidence": 0.7, "_quote": "D"},
                ],
                "has_more": False,
            },
        ]

        extractor = AsyncMock()
        extractor.extract_field_group = AsyncMock(side_effect=responses)
        orch = _make_orchestrator(extractor)

        entities, capped, truncated = await orch._extract_entities_paginated(
            "content", group, "ctx"
        )
        names = [e.get("fields", e).get("name") for e in entities]
        assert len(names) == 4
        assert "A" in names and "D" in names
        assert not capped
        assert not truncated

    async def test_stall_detection(self):
        """LLM returns same entities twice → stops."""
        group = _make_entity_group(max_items=50)
        duplicate_response = {
            "products": [
                {"name": "A", "type": "gear", "_confidence": 0.9, "_quote": "A"},
            ],
            "has_more": True,
        }

        extractor = AsyncMock()
        # First call returns A (new), second and third return A (stalls)
        extractor.extract_field_group = AsyncMock(
            side_effect=[
                duplicate_response,
                duplicate_response,
                duplicate_response,
            ]
        )
        orch = _make_orchestrator(extractor)

        entities, _, _ = await orch._extract_entities_paginated("content", group, "ctx")
        assert len(entities) == 1  # Only first A kept

    async def test_empty_response_stops(self):
        """Empty response → stops immediately."""
        group = _make_entity_group()
        extractor = AsyncMock()
        extractor.extract_field_group = AsyncMock(
            return_value={
                "products": [],
                "has_more": False,
            }
        )
        orch = _make_orchestrator(extractor)

        entities, _, _ = await orch._extract_entities_paginated("content", group, "ctx")
        assert entities == []

    async def test_max_items_cap(self):
        """Stops when max_items is reached."""
        group = _make_entity_group(max_items=3)
        extractor = AsyncMock()
        extractor.extract_field_group = AsyncMock(
            side_effect=[
                {
                    "products": [
                        {
                            "name": f"P{i}",
                            "type": "t",
                            "_confidence": 0.9,
                            "_quote": f"P{i}",
                        }
                        for i in range(3)
                    ],
                    "has_more": True,
                },
            ]
        )
        orch = _make_orchestrator(extractor)

        entities, capped, _ = await orch._extract_entities_paginated(
            "content", group, "ctx"
        )
        assert len(entities) == 3
        assert capped

    async def test_extraction_error_stops(self):
        """Error during extraction → stops gracefully."""
        group = _make_entity_group()
        extractor = AsyncMock()
        extractor.extract_field_group = AsyncMock(side_effect=Exception("LLM error"))
        orch = _make_orchestrator(extractor)

        entities, _, _ = await orch._extract_entities_paginated("content", group, "ctx")
        assert entities == []
