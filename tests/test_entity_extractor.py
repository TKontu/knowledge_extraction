"""Tests for EntityExtractor."""

from unittest.mock import AsyncMock
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


class TestBuildPrompt:
    """Test EntityExtractor._build_prompt() method."""

    def test_build_prompt_includes_extraction_data(self) -> None:
        """Should include extraction data in prompt."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        extraction_data = {
            "fact_text": "Pro plan supports 10,000 API calls per minute",
            "category": "api",
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
        """Should normalize pricing by extracting amount in cents and period."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        result = extractor._normalize("pricing", "$99.99/month")
        assert result == "9999_per_month"

    def test_normalize_unknown_type_default(self) -> None:
        """Should use default normalization for unknown types."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        result = extractor._normalize("unknown_type", "  Some Value  ")
        assert result == "some value"


class TestCallLLM:
    """Test EntityExtractor._call_llm() method."""

    async def test_call_llm_returns_parsed_entities(self) -> None:
        """Should call LLM and return parsed entities."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        # Mock LLM response
        mock_response = AsyncMock()
        mock_response.choices = [
            AsyncMock(
                message=AsyncMock(
                    content='{"entities": [{"type": "plan", "value": "Pro Plan"}]}'
                )
            )
        ]
        llm_client.client.chat.completions.create = AsyncMock(
            return_value=mock_response
        )
        llm_client.model = "gpt-4"

        prompt = {"system": "system prompt", "user": "user prompt"}
        entities = await extractor._call_llm(prompt)

        assert len(entities) == 1
        assert entities[0]["type"] == "plan"
        assert entities[0]["value"] == "Pro Plan"

    async def test_call_llm_handles_empty_response(self) -> None:
        """Should handle empty entity list from LLM."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        # Mock LLM response with no entities
        mock_response = AsyncMock()
        mock_response.choices = [
            AsyncMock(message=AsyncMock(content='{"entities": []}'))
        ]
        llm_client.client.chat.completions.create = AsyncMock(
            return_value=mock_response
        )
        llm_client.model = "gpt-4"

        prompt = {"system": "system prompt", "user": "user prompt"}
        entities = await extractor._call_llm(prompt)

        assert entities == []

    async def test_call_llm_handles_invalid_json(self) -> None:
        """Should handle malformed JSON by returning empty list."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        # Mock LLM response with invalid JSON
        mock_response = AsyncMock()
        mock_response.choices = [AsyncMock(message=AsyncMock(content="not valid json"))]
        llm_client.client.chat.completions.create = AsyncMock(
            return_value=mock_response
        )
        llm_client.model = "gpt-4"

        prompt = {"system": "system prompt", "user": "user prompt"}
        entities = await extractor._call_llm(prompt)

        assert entities == []

    async def test_call_llm_uses_json_mode(self) -> None:
        """Should call LLM with JSON response format."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        # Mock LLM response
        mock_response = AsyncMock()
        mock_response.choices = [
            AsyncMock(message=AsyncMock(content='{"entities": []}'))
        ]
        llm_client.client.chat.completions.create = AsyncMock(
            return_value=mock_response
        )
        llm_client.model = "gpt-4"

        prompt = {"system": "system prompt", "user": "user prompt"}
        await extractor._call_llm(prompt)

        # Verify response_format was set to json_object
        call_args = llm_client.client.chat.completions.create.call_args
        assert call_args.kwargs["response_format"] == {"type": "json_object"}
        assert call_args.kwargs["temperature"] == 0.1
        assert call_args.kwargs["model"] == "gpt-4"


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
    """Test EntityExtractor.extract() main method."""

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

        # Mock LLM response
        mock_response = AsyncMock()
        mock_response.choices = [
            AsyncMock(
                message=AsyncMock(
                    content='{"entities": [{"type": "plan", "value": "Pro plan", "attributes": {}}]}'
                )
            )
        ]
        llm_client.client.chat.completions.create = AsyncMock(
            return_value=mock_response
        )
        llm_client.model = "gpt-4"

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
        entity_repo.link_to_extraction = AsyncMock()

        entities = await extractor.extract(
            extraction_id=extraction_id,
            extraction_data=extraction_data,
            project_id=project_id,
            entity_types=entity_types,
            source_group=source_group,
        )

        assert len(entities) == 1
        assert entities[0] == mock_entity
        llm_client.client.chat.completions.create.assert_called_once()
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

        # Mock LLM response
        mock_response = AsyncMock()
        mock_response.choices = [
            AsyncMock(
                message=AsyncMock(
                    content='{"entities": [{"type": "plan", "value": "Pro", "attributes": {}}]}'
                )
            )
        ]
        llm_client.client.chat.completions.create = AsyncMock(
            return_value=mock_response
        )
        llm_client.model = "gpt-4"

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
        entity_repo.link_to_extraction = AsyncMock()

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

        # Mock LLM response with entity
        mock_response = AsyncMock()
        mock_response.choices = [
            AsyncMock(
                message=AsyncMock(
                    content='{"entities": [{"type": "plan", "value": "Pro", "attributes": {}}]}'
                )
            )
        ]
        llm_client.client.chat.completions.create = AsyncMock(
            return_value=mock_response
        )
        llm_client.model = "gpt-4"

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
        entity_repo.link_to_extraction = AsyncMock()

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

        # Mock LLM response with no entities
        mock_response = AsyncMock()
        mock_response.choices = [
            AsyncMock(message=AsyncMock(content='{"entities": []}'))
        ]
        llm_client.client.chat.completions.create = AsyncMock(
            return_value=mock_response
        )
        llm_client.model = "gpt-4"

        entity_repo.link_to_extraction = AsyncMock()

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

        # Mock LLM response with multiple entities
        mock_response = AsyncMock()
        mock_response.choices = [
            AsyncMock(
                message=AsyncMock(
                    content='{"entities": [{"type": "plan", "value": "Pro", "attributes": {}}, {"type": "feature", "value": "SSO", "attributes": {}}, {"type": "limit", "value": "10,000 requests/min", "attributes": {}}]}'
                )
            )
        ]
        llm_client.client.chat.completions.create = AsyncMock(
            return_value=mock_response
        )
        llm_client.model = "gpt-4"

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
        entity_repo.link_to_extraction = AsyncMock()

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
