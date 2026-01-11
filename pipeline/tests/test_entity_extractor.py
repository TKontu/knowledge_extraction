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
