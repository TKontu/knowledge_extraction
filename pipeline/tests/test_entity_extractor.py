"""Tests for EntityExtractor."""

import pytest
from unittest.mock import AsyncMock
from services.knowledge.extractor import EntityExtractor
from services.storage.repositories.entity import EntityRepository


class TestEntityExtractor:
    """Test EntityExtractor initialization."""

    def test_init_requires_llm_client_and_entity_repo(self) -> None:
        """Should initialize with LLM client and entity repository."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)

        extractor = EntityExtractor(
            llm_client=llm_client,
            entity_repo=entity_repo,
        )

        assert extractor._llm_client == llm_client
        assert extractor._entity_repo == entity_repo


class TestBuildPrompt:
    """Test EntityExtractor._build_prompt() method."""

    def test_build_prompt_includes_extraction_data(self) -> None:
        """Should include extraction data in prompt."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        extraction_data = {
            "fact_text": "Pro plan supports 10,000 API calls per minute",
            "category": "api"
        }
        entity_types = [
            {"name": "plan", "description": "Pricing tier"},
            {"name": "limit", "description": "Quota or threshold"},
        ]
        source_group = "acme_corp"

        prompt = extractor._build_prompt(
            extraction_data=extraction_data,
            entity_types=entity_types,
            source_group=source_group,
        )

        assert "Pro plan supports 10,000 API calls per minute" in prompt["user"]
        assert "plan" in prompt["system"]
        assert "limit" in prompt["system"]
        assert "Pricing tier" in prompt["system"]

    def test_build_prompt_specifies_json_output_format(self) -> None:
        """Should specify JSON output format in system prompt."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        extraction_data = {"fact_text": "Test fact"}
        entity_types = [{"name": "feature", "description": "Product capability"}]

        prompt = extractor._build_prompt(
            extraction_data=extraction_data,
            entity_types=entity_types,
            source_group="test_company",
        )

        assert "entities" in prompt["system"]
        assert "type" in prompt["system"]
        assert "value" in prompt["system"]
        assert "normalized" in prompt["system"]
