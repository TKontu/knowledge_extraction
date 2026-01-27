"""Tests for EntityExtractor.

Note: EntityExtractor now delegates LLM calls to LLMClient.extract_entities().
The prompt building and LLM communication are handled by LLMClient.
"""

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

from orm_models import Entity
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


class TestNormalize:
    """Test EntityExtractor._normalize() method."""

    def test_normalize_plan_lowercase_and_strip(self) -> None:
        """Should normalize plan names to lowercase and strip whitespace."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        result = extractor._normalize("plan", "  Pro Plan  ")
        assert result == "pro plan"

    def test_normalize_feature_lowercase(self) -> None:
        """Should normalize features to lowercase."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        result = extractor._normalize("feature", "SSO")
        assert result == "sso"

    def test_normalize_limit_extracts_numeric(self) -> None:
        """Should normalize limits by extracting numeric value and unit."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        result = extractor._normalize("limit", "10,000 requests/min")
        assert result == "10000_per_minute"

    def test_normalize_pricing_extracts_amount(self) -> None:
        """Should normalize pricing by extracting amount in microcents and period."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        result = extractor._normalize("pricing", "$99.99/month")
        # $99.99 = 99,990,000 microcents
        assert result == "99990000_microcents_per_month"

    def test_normalize_unknown_type_default(self) -> None:
        """Should use default normalization for unknown types."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        result = extractor._normalize("unknown_type", "  Some Value  ")
        assert result == "some value"


class TestNormalizePricingPrecision:
    """Tests for pricing normalization precision (microcents)."""

    def test_normalize_sub_cent_price_preserves_precision(self) -> None:
        """Should preserve sub-cent prices using microcents ($0.001 -> 1000 microcents)."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        # $0.001/request = 1000 microcents
        result = extractor._normalize("pricing", "$0.001/request")
        assert result == "1000_microcents_per_request"

    def test_normalize_very_small_price(self) -> None:
        """Should handle very small prices ($0.0001)."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        # $0.0001/request = 100 microcents
        result = extractor._normalize("pricing", "$0.0001/request")
        assert result == "100_microcents_per_request"

    def test_normalize_regular_cents_still_works(self) -> None:
        """Should handle regular cents ($0.05)."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        # $0.05/call = 50000 microcents
        result = extractor._normalize("pricing", "$0.05/call")
        assert result == "50000_microcents_per_call"

    def test_normalize_dollar_amounts(self) -> None:
        """Should handle dollar amounts ($99.99)."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        # $99.99/month = 99,990,000 microcents
        result = extractor._normalize("pricing", "$99.99/month")
        assert result == "99990000_microcents_per_month"

    def test_normalize_whole_dollars(self) -> None:
        """Should handle whole dollar amounts ($10)."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        # $10/month = 10,000,000 microcents
        result = extractor._normalize("pricing", "$10/month")
        assert result == "10000000_microcents_per_month"

    def test_normalize_free_price(self) -> None:
        """Should handle free price ($0)."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        # $0/month = 0 microcents
        result = extractor._normalize("pricing", "$0/month")
        assert result == "0_microcents_per_month"

    def test_normalize_price_with_comma_separator(self) -> None:
        """Should handle prices with comma separator ($1,000.50)."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        # $1,000.50/year = 1,000,500,000 microcents
        result = extractor._normalize("pricing", "$1,000.50/year")
        assert result == "1000500000_microcents_per_year"


class TestStoreEntities:
    """Test EntityExtractor._store_entities() method."""

    async def test_store_entities_creates_new(self) -> None:
        """Should create new entities via repository."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        project_id = uuid4()
        source_group = "test_company"
        entities = [
            {"type": "plan", "value": "Pro Plan", "attributes": {}},
        ]

        # Mock repository to return created entity
        mock_entity = Entity(
            id=uuid4(),
            project_id=project_id,
            source_group=source_group,
            entity_type="plan",
            value="Pro Plan",
            normalized_value="pro plan",
            attributes={},
        )
        entity_repo.get_or_create = AsyncMock(return_value=(mock_entity, True))

        results = await extractor._store_entities(entities, project_id, source_group)

        assert len(results) == 1
        entity, created = results[0]
        assert entity == mock_entity
        assert created is True
        entity_repo.get_or_create.assert_called_once()

    async def test_store_entities_deduplicates(self) -> None:
        """Should return existing entity without creating duplicate."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        project_id = uuid4()
        source_group = "test_company"
        entities = [
            {"type": "plan", "value": "Pro Plan", "attributes": {}},
        ]

        # Mock repository to return existing entity
        mock_entity = Entity(
            id=uuid4(),
            project_id=project_id,
            source_group=source_group,
            entity_type="plan",
            value="Pro Plan",
            normalized_value="pro plan",
            attributes={},
        )
        entity_repo.get_or_create = AsyncMock(return_value=(mock_entity, False))

        results = await extractor._store_entities(entities, project_id, source_group)

        assert len(results) == 1
        entity, created = results[0]
        assert entity == mock_entity
        assert created is False

    async def test_store_entities_uses_normalized_value(self) -> None:
        """Should apply normalization before storing."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        project_id = uuid4()
        source_group = "test_company"
        entities = [
            {"type": "plan", "value": "  Pro Plan  ", "attributes": {}},
        ]

        mock_entity = Entity(
            id=uuid4(),
            project_id=project_id,
            source_group=source_group,
            entity_type="plan",
            value="  Pro Plan  ",
            normalized_value="pro plan",
            attributes={},
        )
        entity_repo.get_or_create = AsyncMock(return_value=(mock_entity, True))

        await extractor._store_entities(entities, project_id, source_group)

        # Verify normalized_value was passed to get_or_create
        call_args = entity_repo.get_or_create.call_args
        assert call_args.kwargs["normalized_value"] == "pro plan"
        assert call_args.kwargs["value"] == "  Pro Plan  "


class TestExtract:
    """Test EntityExtractor.extract() main method.

    Note: extract() now delegates LLM calls to llm_client.extract_entities().
    These tests mock extract_entities() instead of llm_client.client.chat.completions.create().
    """

    async def test_extract_full_pipeline(self) -> None:
        """Should run full extraction pipeline end-to-end."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        extraction_id = uuid4()
        project_id = uuid4()
        extraction_data = {"fact_text": "Pro plan supports SSO"}
        entity_types = [
            {"name": "plan", "description": "Pricing tier"},
            {"name": "feature", "description": "Product capability"},
        ]
        source_group = "test_company"

        # Mock LLMClient.extract_entities() response
        llm_client.extract_entities = AsyncMock(return_value=[
            {"type": "plan", "value": "Pro plan", "attributes": {}}
        ])

        # Mock entity storage
        mock_entity = Entity(
            id=uuid4(),
            project_id=project_id,
            source_group=source_group,
            entity_type="plan",
            value="Pro plan",
            normalized_value="pro plan",
            attributes={},
        )
        entity_repo.get_or_create = AsyncMock(return_value=(mock_entity, True))
        # link_to_extraction returns (ExtractionEntity, bool) tuple
        mock_link = MagicMock()
        entity_repo.link_to_extraction = AsyncMock(return_value=(mock_link, True))

        entities = await extractor.extract(
            extraction_id=extraction_id,
            extraction_data=extraction_data,
            project_id=project_id,
            entity_types=entity_types,
            source_group=source_group,
        )

        assert len(entities) == 1
        assert entities[0] == mock_entity
        llm_client.extract_entities.assert_called_once_with(
            extraction_data=extraction_data,
            entity_types=entity_types,
            source_group=source_group,
        )
        entity_repo.get_or_create.assert_called_once()

    async def test_extract_links_entities_to_extraction(self) -> None:
        """Should link entities to extraction after storing."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        extraction_id = uuid4()
        project_id = uuid4()
        extraction_data = {"fact_text": "Pro plan supports SSO"}
        entity_types = [{"name": "plan", "description": "Pricing tier"}]
        source_group = "test_company"

        # Mock LLMClient.extract_entities() response
        llm_client.extract_entities = AsyncMock(return_value=[
            {"type": "plan", "value": "Pro", "attributes": {}}
        ])

        # Mock entity storage
        mock_entity = Entity(
            id=uuid4(),
            project_id=project_id,
            source_group=source_group,
            entity_type="plan",
            value="Pro",
            normalized_value="pro",
            attributes={},
        )
        entity_repo.get_or_create = AsyncMock(return_value=(mock_entity, True))
        # link_to_extraction returns (ExtractionEntity, bool) tuple
        mock_link = MagicMock()
        entity_repo.link_to_extraction = AsyncMock(return_value=(mock_link, True))

        await extractor.extract(
            extraction_id=extraction_id,
            extraction_data=extraction_data,
            project_id=project_id,
            entity_types=entity_types,
            source_group=source_group,
        )

        # Verify link_to_extraction was called
        entity_repo.link_to_extraction.assert_called_once_with(
            entity_id=mock_entity.id, extraction_id=extraction_id
        )

    async def test_extract_returns_entities(self) -> None:
        """Should return list of Entity objects."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        extraction_id = uuid4()
        project_id = uuid4()
        extraction_data = {"fact_text": "Test"}
        entity_types = [{"name": "plan", "description": "Pricing tier"}]
        source_group = "test_company"

        # Mock LLMClient.extract_entities() response
        llm_client.extract_entities = AsyncMock(return_value=[
            {"type": "plan", "value": "Pro", "attributes": {}}
        ])

        mock_entity = Entity(
            id=uuid4(),
            project_id=project_id,
            source_group=source_group,
            entity_type="plan",
            value="Pro",
            normalized_value="pro",
            attributes={},
        )
        entity_repo.get_or_create = AsyncMock(return_value=(mock_entity, True))
        # link_to_extraction returns (ExtractionEntity, bool) tuple
        mock_link = MagicMock()
        entity_repo.link_to_extraction = AsyncMock(return_value=(mock_link, True))

        entities = await extractor.extract(
            extraction_id=extraction_id,
            extraction_data=extraction_data,
            project_id=project_id,
            entity_types=entity_types,
            source_group=source_group,
        )

        assert isinstance(entities, list)
        assert len(entities) == 1
        assert isinstance(entities[0], Entity)

    async def test_extract_handles_no_entities(self) -> None:
        """Should return empty list when LLM finds no entities."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        extraction_id = uuid4()
        project_id = uuid4()
        extraction_data = {"fact_text": "Test"}
        entity_types = [{"name": "plan", "description": "Pricing tier"}]
        source_group = "test_company"

        # Mock LLMClient.extract_entities() response with no entities
        llm_client.extract_entities = AsyncMock(return_value=[])

        # link_to_extraction returns (ExtractionEntity, bool) tuple
        mock_link = MagicMock()
        entity_repo.link_to_extraction = AsyncMock(return_value=(mock_link, True))

        entities = await extractor.extract(
            extraction_id=extraction_id,
            extraction_data=extraction_data,
            project_id=project_id,
            entity_types=entity_types,
            source_group=source_group,
        )

        assert entities == []
        entity_repo.get_or_create.assert_not_called()
        entity_repo.link_to_extraction.assert_not_called()

    async def test_extract_with_multiple_entity_types(self) -> None:
        """Should extract multiple entity types from same data."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        extraction_id = uuid4()
        project_id = uuid4()
        extraction_data = {
            "fact_text": "Pro plan supports SSO with 10,000 requests/min"
        }
        entity_types = [
            {"name": "plan", "description": "Pricing tier"},
            {"name": "feature", "description": "Product capability"},
            {"name": "limit", "description": "Quota or threshold"},
        ]
        source_group = "test_company"

        # Mock LLMClient.extract_entities() response with multiple entities
        llm_client.extract_entities = AsyncMock(return_value=[
            {"type": "plan", "value": "Pro", "attributes": {}},
            {"type": "feature", "value": "SSO", "attributes": {}},
            {"type": "limit", "value": "10,000 requests/min", "attributes": {}},
        ])

        # Mock entity storage for each entity
        mock_entities = [
            Entity(
                id=uuid4(),
                project_id=project_id,
                source_group=source_group,
                entity_type="plan",
                value="Pro",
                normalized_value="pro",
                attributes={},
            ),
            Entity(
                id=uuid4(),
                project_id=project_id,
                source_group=source_group,
                entity_type="feature",
                value="SSO",
                normalized_value="sso",
                attributes={},
            ),
            Entity(
                id=uuid4(),
                project_id=project_id,
                source_group=source_group,
                entity_type="limit",
                value="10,000 requests/min",
                normalized_value="10000_per_minute",
                attributes={},
            ),
        ]
        entity_repo.get_or_create = AsyncMock(
            side_effect=[(e, True) for e in mock_entities]
        )
        # link_to_extraction returns (ExtractionEntity, bool) tuple
        mock_link = MagicMock()
        entity_repo.link_to_extraction = AsyncMock(return_value=(mock_link, True))

        entities = await extractor.extract(
            extraction_id=extraction_id,
            extraction_data=extraction_data,
            project_id=project_id,
            entity_types=entity_types,
            source_group=source_group,
        )

        assert len(entities) == 3
        assert entities[0].entity_type == "plan"
        assert entities[1].entity_type == "feature"
        assert entities[2].entity_type == "limit"
        assert entity_repo.link_to_extraction.call_count == 3
