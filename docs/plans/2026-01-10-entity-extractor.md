# EntityExtractor Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Implement LLM-based entity extraction from extraction data using project-configured entity types with deduplication.

**Architecture:** Two-pass extraction pattern - entities are extracted from existing extractions (not raw text). Uses project.entity_types configuration for dynamic entity type support. EntityRepository.get_or_create provides automatic deduplication scoped by project/source_group/type/normalized_value.

**Tech Stack:** Python 3.12, AsyncOpenAI (LLM client), SQLAlchemy ORM, Pytest with AsyncMock

---

## Task 1: Create EntityExtractor Class Skeleton

**Files:**
- Create: `pipeline/services/knowledge/__init__.py`
- Create: `pipeline/services/knowledge/extractor.py`
- Create: `tests/test_entity_extractor.py`

**Step 1: Write the failing test**

Create `tests/test_entity_extractor.py`:

```python
"""Tests for EntityExtractor."""

import pytest
from uuid import uuid4
from unittest.mock import AsyncMock
from services.knowledge.extractor import EntityExtractor
from services.storage.repositories.entity import EntityRepository


class TestEntityExtractor:
    """Test EntityExtractor initialization."""

    def test_init_requires_llm_client_and_entity_repo(self):
        """Should initialize with LLM client and entity repository."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)

        extractor = EntityExtractor(
            llm_client=llm_client,
            entity_repo=entity_repo,
        )

        assert extractor._llm_client == llm_client
        assert extractor._entity_repo == entity_repo
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_entity_extractor.py::TestEntityExtractor::test_init_requires_llm_client_and_entity_repo -v`

Expected: FAIL with "ModuleNotFoundError: No module named 'services.knowledge'"

**Step 3: Write minimal implementation**

Create `pipeline/services/knowledge/__init__.py`:

```python
"""Knowledge extraction services (entities and relations)."""
```

Create `pipeline/services/knowledge/extractor.py`:

```python
"""Entity extraction from extractions using LLM."""

from __future__ import annotations
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from services.llm.client import LLMClient
    from services.storage.repositories.entity import EntityRepository


class EntityExtractor:
    """Extracts entities from extraction data using LLM."""

    def __init__(
        self,
        llm_client: LLMClient,
        entity_repo: EntityRepository,
    ):
        """Initialize entity extractor.

        Args:
            llm_client: LLM client for entity extraction
            entity_repo: Entity repository for storage and deduplication
        """
        self._llm_client = llm_client
        self._entity_repo = entity_repo
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_entity_extractor.py::TestEntityExtractor::test_init_requires_llm_client_and_entity_repo -v`

Expected: PASS

**Step 5: Commit**

```bash
git add pipeline/services/knowledge/__init__.py pipeline/services/knowledge/extractor.py tests/test_entity_extractor.py
git commit -m "feat: add EntityExtractor class skeleton

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 2: Build Entity Extraction Prompt

**Files:**
- Modify: `pipeline/services/knowledge/extractor.py`
- Modify: `tests/test_entity_extractor.py`

**Step 1: Write the failing test**

Add to `tests/test_entity_extractor.py`:

```python
class TestBuildPrompt:
    """Test EntityExtractor._build_prompt() method."""

    def test_build_prompt_includes_extraction_data(self):
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

    def test_build_prompt_specifies_json_output_format(self):
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
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_entity_extractor.py::TestBuildPrompt -v`

Expected: FAIL with "AttributeError: 'EntityExtractor' object has no attribute '_build_prompt'"

**Step 3: Write minimal implementation**

Add to `pipeline/services/knowledge/extractor.py`:

```python
    def _build_prompt(
        self,
        extraction_data: dict,
        entity_types: list[dict],
        source_group: str,
    ) -> dict[str, str]:
        """Build prompts for entity extraction.

        Args:
            extraction_data: Extraction data dictionary
            entity_types: List of entity type definitions from project
            source_group: Source grouping identifier (e.g., company name)

        Returns:
            Dictionary with 'system' and 'user' prompts
        """
        # Build entity type documentation
        entity_docs = []
        for et in entity_types:
            name = et["name"]
            desc = et.get("description", "")
            entity_docs.append(f"- {name}: {desc}")
        entity_types_doc = "\n".join(entity_docs)

        # Get primary text field from extraction data
        text_content = extraction_data.get("fact_text") or extraction_data.get("text") or str(extraction_data)

        system_prompt = f"""Extract entities from this extracted data. Return JSON with entities found.

Source Group: "{source_group}"

Entity types to extract:
{entity_types_doc}

Output format:
{{
  "entities": [
    {{
      "type": "entity_type_name",
      "value": "original text",
      "normalized": "normalized_value",
      "attributes": {{}}
    }}
  ]
}}

Guidelines:
- Only extract entities explicitly mentioned in the data
- Do not infer or guess entities not present
- Normalize values for deduplication (lowercase, canonical form)
- For limits: extract numeric values and units in attributes
- For pricing: extract amounts and periods in attributes"""

        user_prompt = f"""Extract entities from this data:

{text_content}"""

        return {"system": system_prompt, "user": user_prompt}
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_entity_extractor.py::TestBuildPrompt -v`

Expected: PASS

**Step 5: Commit**

```bash
git add pipeline/services/knowledge/extractor.py tests/test_entity_extractor.py
git commit -m "feat: add entity extraction prompt builder

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 3: Implement Entity Normalization

**Files:**
- Modify: `pipeline/services/knowledge/extractor.py`
- Modify: `tests/test_entity_extractor.py`

**Step 1: Write the failing test**

Add to `tests/test_entity_extractor.py`:

```python
class TestNormalize:
    """Test EntityExtractor._normalize() method."""

    def test_normalize_converts_to_lowercase(self):
        """Should convert values to lowercase."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        assert extractor._normalize("Pro Plan") == "pro_plan"
        assert extractor._normalize("SSO") == "sso"

    def test_normalize_replaces_spaces_with_underscores(self):
        """Should replace spaces with underscores."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        assert extractor._normalize("API Access") == "api_access"

    def test_normalize_removes_special_characters(self):
        """Should remove/replace special characters."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        assert extractor._normalize("$99/month") == "99_month"
        assert extractor._normalize("10,000/min") == "10000_min"

    def test_normalize_strips_whitespace(self):
        """Should strip leading/trailing whitespace."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        assert extractor._normalize("  Enterprise  ") == "enterprise"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_entity_extractor.py::TestNormalize -v`

Expected: FAIL with "AttributeError: 'EntityExtractor' object has no attribute '_normalize'"

**Step 3: Write minimal implementation**

Add to `pipeline/services/knowledge/extractor.py`:

```python
import re


class EntityExtractor:
    # ... existing code ...

    def _normalize(self, value: str) -> str:
        """Normalize entity value for deduplication.

        Args:
            value: Original entity value

        Returns:
            Normalized value (lowercase, underscores, no special chars)
        """
        # Strip whitespace
        normalized = value.strip()

        # Convert to lowercase
        normalized = normalized.lower()

        # Remove special characters except alphanumeric, spaces, and slashes
        # Keep slashes for things like "requests/min"
        normalized = re.sub(r'[^a-z0-9\s/]', '', normalized)

        # Replace spaces and slashes with underscores
        normalized = re.sub(r'[\s/]+', '_', normalized)

        # Remove consecutive underscores
        normalized = re.sub(r'_+', '_', normalized)

        # Strip leading/trailing underscores
        normalized = normalized.strip('_')

        return normalized
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_entity_extractor.py::TestNormalize -v`

Expected: PASS

**Step 5: Commit**

```bash
git add pipeline/services/knowledge/extractor.py tests/test_entity_extractor.py
git commit -m "feat: add entity value normalization

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 4: Implement LLM Entity Extraction Call

**Files:**
- Modify: `pipeline/services/knowledge/extractor.py`
- Modify: `tests/test_entity_extractor.py`

**Step 1: Write the failing test**

Add to `tests/test_entity_extractor.py`:

```python
import json
from unittest.mock import MagicMock


class MockChatCompletion:
    """Mock OpenAI chat completion response."""
    def __init__(self, content: str):
        self.choices = [MagicMock(message=MagicMock(content=content))]


@pytest.mark.asyncio
class TestExtractEntitiesFromLLM:
    """Test EntityExtractor._extract_entities_from_llm() method."""

    async def test_extract_calls_llm_with_prompts(self):
        """Should call LLM with built prompts."""
        llm_response = {
            "entities": [
                {
                    "type": "plan",
                    "value": "Pro",
                    "normalized": "pro",
                    "attributes": {}
                }
            ]
        }

        llm_client = AsyncMock()
        llm_client.client.chat.completions.create = AsyncMock(
            return_value=MockChatCompletion(json.dumps(llm_response))
        )
        entity_repo = AsyncMock(spec=EntityRepository)

        extractor = EntityExtractor(llm_client, entity_repo)

        extraction_data = {"fact_text": "Pro plan supports 10,000 requests"}
        entity_types = [{"name": "plan", "description": "Pricing tier"}]

        entities = await extractor._extract_entities_from_llm(
            extraction_data=extraction_data,
            entity_types=entity_types,
            source_group="test_company",
        )

        assert len(entities) == 1
        assert entities[0]["type"] == "plan"
        assert entities[0]["value"] == "Pro"

    async def test_extract_returns_empty_list_on_no_entities(self):
        """Should return empty list when LLM finds no entities."""
        llm_response = {"entities": []}

        llm_client = AsyncMock()
        llm_client.client.chat.completions.create = AsyncMock(
            return_value=MockChatCompletion(json.dumps(llm_response))
        )
        entity_repo = AsyncMock(spec=EntityRepository)

        extractor = EntityExtractor(llm_client, entity_repo)

        entities = await extractor._extract_entities_from_llm(
            extraction_data={"fact_text": "No entities here"},
            entity_types=[{"name": "plan"}],
            source_group="test_company",
        )

        assert entities == []
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_entity_extractor.py::TestExtractEntitiesFromLLM -v`

Expected: FAIL with "AttributeError: 'EntityExtractor' object has no attribute '_extract_entities_from_llm'"

**Step 3: Write minimal implementation**

Add to `pipeline/services/knowledge/extractor.py`:

```python
import json


class EntityExtractor:
    # ... existing code ...

    async def _extract_entities_from_llm(
        self,
        extraction_data: dict,
        entity_types: list[dict],
        source_group: str,
    ) -> list[dict]:
        """Extract entities from extraction data using LLM.

        Args:
            extraction_data: Extraction data dictionary
            entity_types: List of entity type definitions
            source_group: Source grouping identifier

        Returns:
            List of extracted entity dictionaries
        """
        # Build prompts
        prompts = self._build_prompt(
            extraction_data=extraction_data,
            entity_types=entity_types,
            source_group=source_group,
        )

        # Call LLM
        response = await self._llm_client.client.chat.completions.create(
            model=self._llm_client.model,
            messages=[
                {"role": "system", "content": prompts["system"]},
                {"role": "user", "content": prompts["user"]},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,  # Low temperature for consistent extraction
        )

        # Parse response
        result_text = response.choices[0].message.content
        result_data = json.loads(result_text)

        return result_data.get("entities", [])
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_entity_extractor.py::TestExtractEntitiesFromLLM -v`

Expected: PASS

**Step 5: Commit**

```bash
git add pipeline/services/knowledge/extractor.py tests/test_entity_extractor.py
git commit -m "feat: implement LLM entity extraction call

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 5: Implement Entity Storage with Deduplication

**Files:**
- Modify: `pipeline/services/knowledge/extractor.py`
- Modify: `tests/test_entity_extractor.py`

**Step 1: Write the failing test**

Add to `tests/test_entity_extractor.py`:

```python
@pytest.mark.asyncio
class TestStoreEntities:
    """Test EntityExtractor._store_entities() method."""

    async def test_store_entities_calls_get_or_create(self):
        """Should call EntityRepository.get_or_create for each entity."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)

        # Mock get_or_create to return entity and created flag
        mock_entity = MagicMock()
        mock_entity.id = uuid4()
        entity_repo.get_or_create = AsyncMock(return_value=(mock_entity, True))

        extractor = EntityExtractor(llm_client, entity_repo)

        entities = [
            {
                "type": "plan",
                "value": "Pro",
                "normalized": "pro",
                "attributes": {}
            }
        ]
        project_id = uuid4()
        source_group = "test_company"

        stored = await extractor._store_entities(
            entities=entities,
            project_id=project_id,
            source_group=source_group,
        )

        assert len(stored) == 1
        assert stored[0].id == mock_entity.id
        entity_repo.get_or_create.assert_called_once()

    async def test_store_entities_uses_llm_normalized_or_generates(self):
        """Should use LLM-provided normalized value or generate one."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)

        mock_entity = MagicMock()
        entity_repo.get_or_create = AsyncMock(return_value=(mock_entity, True))

        extractor = EntityExtractor(llm_client, entity_repo)

        entities = [
            {
                "type": "feature",
                "value": "SSO Support",
                "normalized": "",  # Empty, should auto-generate
                "attributes": {}
            }
        ]

        await extractor._store_entities(
            entities=entities,
            project_id=uuid4(),
            source_group="test_company",
        )

        # Check that get_or_create was called with generated normalized value
        call_kwargs = entity_repo.get_or_create.call_args.kwargs
        assert call_kwargs["normalized_value"] == "sso_support"

    async def test_store_entities_passes_attributes(self):
        """Should pass attributes dict to repository."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)

        mock_entity = MagicMock()
        entity_repo.get_or_create = AsyncMock(return_value=(mock_entity, True))

        extractor = EntityExtractor(llm_client, entity_repo)

        entities = [
            {
                "type": "limit",
                "value": "10,000/min",
                "normalized": "10000_min",
                "attributes": {"numeric_value": 10000, "unit": "requests", "period": "minute"}
            }
        ]

        await extractor._store_entities(
            entities=entities,
            project_id=uuid4(),
            source_group="test_company",
        )

        call_kwargs = entity_repo.get_or_create.call_args.kwargs
        assert call_kwargs["attributes"]["numeric_value"] == 10000
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_entity_extractor.py::TestStoreEntities -v`

Expected: FAIL with "AttributeError: 'EntityExtractor' object has no attribute '_store_entities'"

**Step 3: Write minimal implementation**

Add to `pipeline/services/knowledge/extractor.py`:

```python
from uuid import UUID


class EntityExtractor:
    # ... existing code ...

    async def _store_entities(
        self,
        entities: list[dict],
        project_id: UUID,
        source_group: str,
    ) -> list:
        """Store entities using EntityRepository with deduplication.

        Args:
            entities: List of entity dictionaries from LLM
            project_id: Project UUID
            source_group: Source grouping identifier

        Returns:
            List of created/retrieved Entity ORM objects
        """
        stored_entities = []

        for entity_data in entities:
            # Get normalized value from LLM or generate it
            normalized = entity_data.get("normalized", "")
            if not normalized:
                normalized = self._normalize(entity_data["value"])

            # Store entity with get_or_create for deduplication
            entity, created = await self._entity_repo.get_or_create(
                project_id=project_id,
                source_group=source_group,
                entity_type=entity_data["type"],
                value=entity_data["value"],
                normalized_value=normalized,
                attributes=entity_data.get("attributes", {}),
            )

            stored_entities.append(entity)

        return stored_entities
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_entity_extractor.py::TestStoreEntities -v`

Expected: PASS

**Step 5: Commit**

```bash
git add pipeline/services/knowledge/extractor.py tests/test_entity_extractor.py
git commit -m "feat: implement entity storage with deduplication

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 6: Implement Entity-Extraction Linking

**Files:**
- Modify: `pipeline/services/knowledge/extractor.py`
- Modify: `tests/test_entity_extractor.py`

**Step 1: Write the failing test**

Add to `tests/test_entity_extractor.py`:

```python
@pytest.mark.asyncio
class TestLinkEntitiesToExtraction:
    """Test EntityExtractor._link_entities_to_extraction() method."""

    async def test_link_creates_extraction_entity_links(self):
        """Should create ExtractionEntity links for each entity."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        entity_repo.link_to_extraction = AsyncMock()

        extractor = EntityExtractor(llm_client, entity_repo)

        mock_entity1 = MagicMock()
        mock_entity1.id = uuid4()
        mock_entity2 = MagicMock()
        mock_entity2.id = uuid4()

        entities = [mock_entity1, mock_entity2]
        extraction_id = uuid4()

        await extractor._link_entities_to_extraction(
            entities=entities,
            extraction_id=extraction_id,
        )

        assert entity_repo.link_to_extraction.call_count == 2

        # Verify both entities were linked
        calls = entity_repo.link_to_extraction.call_args_list
        assert calls[0].kwargs["extraction_id"] == extraction_id
        assert calls[0].kwargs["entity_id"] == mock_entity1.id
        assert calls[1].kwargs["entity_id"] == mock_entity2.id

    async def test_link_uses_mention_role_by_default(self):
        """Should use 'mention' role by default."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        entity_repo.link_to_extraction = AsyncMock()

        extractor = EntityExtractor(llm_client, entity_repo)

        mock_entity = MagicMock()
        mock_entity.id = uuid4()

        await extractor._link_entities_to_extraction(
            entities=[mock_entity],
            extraction_id=uuid4(),
        )

        call_kwargs = entity_repo.link_to_extraction.call_args.kwargs
        assert call_kwargs["role"] == "mention"
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_entity_extractor.py::TestLinkEntitiesToExtraction -v`

Expected: FAIL with "AttributeError: 'EntityExtractor' object has no attribute '_link_entities_to_extraction'"

**Step 3: Write minimal implementation**

Add to `pipeline/services/knowledge/extractor.py`:

```python
class EntityExtractor:
    # ... existing code ...

    async def _link_entities_to_extraction(
        self,
        entities: list,
        extraction_id: UUID,
        role: str = "mention",
    ) -> None:
        """Link entities to extraction via ExtractionEntity junction table.

        Args:
            entities: List of Entity ORM objects
            extraction_id: Extraction UUID to link to
            role: Role of entities in extraction (default: "mention")
        """
        for entity in entities:
            await self._entity_repo.link_to_extraction(
                extraction_id=extraction_id,
                entity_id=entity.id,
                role=role,
            )
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_entity_extractor.py::TestLinkEntitiesToExtraction -v`

Expected: PASS

**Step 5: Commit**

```bash
git add pipeline/services/knowledge/extractor.py tests/test_entity_extractor.py
git commit -m "feat: implement entity-extraction linking

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 7: Implement Main extract() Method

**Files:**
- Modify: `pipeline/services/knowledge/extractor.py`
- Modify: `tests/test_entity_extractor.py`

**Step 1: Write the failing test**

Add to `tests/test_entity_extractor.py`:

```python
@pytest.mark.asyncio
class TestExtract:
    """Test EntityExtractor.extract() main method."""

    async def test_extract_end_to_end(self):
        """Should extract, store, and link entities end-to-end."""
        # Setup mocked LLM response
        llm_response = {
            "entities": [
                {
                    "type": "plan",
                    "value": "Pro",
                    "normalized": "pro",
                    "attributes": {}
                },
                {
                    "type": "limit",
                    "value": "10,000/min",
                    "normalized": "10000_min",
                    "attributes": {"numeric_value": 10000}
                }
            ]
        }

        llm_client = AsyncMock()
        llm_client.client.chat.completions.create = AsyncMock(
            return_value=MockChatCompletion(json.dumps(llm_response))
        )

        entity_repo = AsyncMock(spec=EntityRepository)

        # Mock entity storage
        mock_entity1 = MagicMock()
        mock_entity1.id = uuid4()
        mock_entity2 = MagicMock()
        mock_entity2.id = uuid4()

        entity_repo.get_or_create = AsyncMock(
            side_effect=[(mock_entity1, True), (mock_entity2, True)]
        )
        entity_repo.link_to_extraction = AsyncMock()

        extractor = EntityExtractor(llm_client, entity_repo)

        # Mock extraction object
        extraction = MagicMock()
        extraction.id = uuid4()
        extraction.data = {"fact_text": "Pro plan supports 10,000 requests per minute"}
        extraction.project_id = uuid4()
        extraction.source_group = "acme_corp"

        entity_types = [
            {"name": "plan", "description": "Pricing tier"},
            {"name": "limit", "description": "Quota or threshold"},
        ]

        result = await extractor.extract(
            extraction=extraction,
            entity_types=entity_types,
        )

        # Verify entities were extracted and stored
        assert len(result) == 2
        assert result[0].id == mock_entity1.id
        assert result[1].id == mock_entity2.id

        # Verify entities were linked to extraction
        assert entity_repo.link_to_extraction.call_count == 2

    async def test_extract_returns_empty_list_when_no_entities(self):
        """Should return empty list when LLM finds no entities."""
        llm_response = {"entities": []}

        llm_client = AsyncMock()
        llm_client.client.chat.completions.create = AsyncMock(
            return_value=MockChatCompletion(json.dumps(llm_response))
        )
        entity_repo = AsyncMock(spec=EntityRepository)

        extractor = EntityExtractor(llm_client, entity_repo)

        extraction = MagicMock()
        extraction.data = {"fact_text": "No entities here"}
        extraction.project_id = uuid4()
        extraction.source_group = "test_company"

        result = await extractor.extract(
            extraction=extraction,
            entity_types=[{"name": "plan"}],
        )

        assert result == []
        entity_repo.link_to_extraction.assert_not_called()

    async def test_extract_skips_empty_entity_types_list(self):
        """Should return empty list when entity_types is empty."""
        llm_client = AsyncMock()
        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        extraction = MagicMock()
        extraction.data = {"fact_text": "Test"}

        result = await extractor.extract(
            extraction=extraction,
            entity_types=[],
        )

        assert result == []
        llm_client.client.chat.completions.create.assert_not_called()
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_entity_extractor.py::TestExtract -v`

Expected: FAIL with "AttributeError: 'EntityExtractor' object has no attribute 'extract'"

**Step 3: Write minimal implementation**

Add to `pipeline/services/knowledge/extractor.py`:

```python
class EntityExtractor:
    # ... existing code ...

    async def extract(
        self,
        extraction,
        entity_types: list[dict],
    ) -> list:
        """Extract entities from an extraction using LLM.

        This is the main public method for entity extraction.

        Args:
            extraction: Extraction ORM object with id, data, project_id, source_group
            entity_types: List of entity type definitions from project.entity_types

        Returns:
            List of Entity ORM objects that were extracted and stored
        """
        # Skip if no entity types configured
        if not entity_types:
            return []

        # Extract entities using LLM
        entity_dicts = await self._extract_entities_from_llm(
            extraction_data=extraction.data,
            entity_types=entity_types,
            source_group=extraction.source_group,
        )

        # Return early if no entities found
        if not entity_dicts:
            return []

        # Store entities with deduplication
        stored_entities = await self._store_entities(
            entities=entity_dicts,
            project_id=extraction.project_id,
            source_group=extraction.source_group,
        )

        # Link entities to extraction
        await self._link_entities_to_extraction(
            entities=stored_entities,
            extraction_id=extraction.id,
        )

        return stored_entities
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_entity_extractor.py::TestExtract -v`

Expected: PASS

**Step 5: Commit**

```bash
git add pipeline/services/knowledge/extractor.py tests/test_entity_extractor.py
git commit -m "feat: implement main extract() method

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 8: Add Error Handling and Retry Logic

**Files:**
- Modify: `pipeline/services/knowledge/extractor.py`
- Modify: `tests/test_entity_extractor.py`

**Step 1: Write the failing test**

Add to `tests/test_entity_extractor.py`:

```python
from tenacity import RetryError


@pytest.mark.asyncio
class TestErrorHandling:
    """Test EntityExtractor error handling."""

    async def test_extract_retries_on_llm_failure(self):
        """Should retry on transient LLM failures."""
        llm_response = {"entities": [{"type": "plan", "value": "Pro", "normalized": "pro", "attributes": {}}]}

        llm_client = AsyncMock()
        # Fail twice, then succeed
        llm_client.client.chat.completions.create = AsyncMock(
            side_effect=[
                Exception("Temporary error"),
                Exception("Another error"),
                MockChatCompletion(json.dumps(llm_response)),
            ]
        )

        entity_repo = AsyncMock(spec=EntityRepository)
        mock_entity = MagicMock()
        mock_entity.id = uuid4()
        entity_repo.get_or_create = AsyncMock(return_value=(mock_entity, True))
        entity_repo.link_to_extraction = AsyncMock()

        extractor = EntityExtractor(llm_client, entity_repo)

        extraction = MagicMock()
        extraction.id = uuid4()
        extraction.data = {"fact_text": "Test"}
        extraction.project_id = uuid4()
        extraction.source_group = "test_company"

        result = await extractor.extract(
            extraction=extraction,
            entity_types=[{"name": "plan"}],
        )

        assert len(result) == 1
        assert llm_client.client.chat.completions.create.call_count == 3

    async def test_extract_handles_invalid_json_gracefully(self):
        """Should handle invalid JSON from LLM."""
        llm_client = AsyncMock()
        llm_client.client.chat.completions.create = AsyncMock(
            return_value=MockChatCompletion("invalid json{")
        )

        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        extraction = MagicMock()
        extraction.data = {"fact_text": "Test"}
        extraction.project_id = uuid4()
        extraction.source_group = "test_company"

        # Should return empty list, not raise
        result = await extractor.extract(
            extraction=extraction,
            entity_types=[{"name": "plan"}],
        )

        assert result == []

    async def test_extract_handles_missing_entities_key(self):
        """Should handle LLM response missing 'entities' key."""
        llm_client = AsyncMock()
        llm_client.client.chat.completions.create = AsyncMock(
            return_value=MockChatCompletion('{"results": []}')
        )

        entity_repo = AsyncMock(spec=EntityRepository)
        extractor = EntityExtractor(llm_client, entity_repo)

        extraction = MagicMock()
        extraction.data = {"fact_text": "Test"}
        extraction.project_id = uuid4()
        extraction.source_group = "test_company"

        result = await extractor.extract(
            extraction=extraction,
            entity_types=[{"name": "plan"}],
        )

        assert result == []
```

**Step 2: Run test to verify it fails**

Run: `pytest tests/test_entity_extractor.py::TestErrorHandling -v`

Expected: FAIL with errors not being handled gracefully

**Step 3: Write minimal implementation**

Add to top of `pipeline/services/knowledge/extractor.py`:

```python
from tenacity import retry, stop_after_attempt, wait_exponential
import structlog

logger = structlog.get_logger(__name__)
```

Update the `_extract_entities_from_llm` method:

```python
    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=60),
    )
    async def _extract_entities_from_llm(
        self,
        extraction_data: dict,
        entity_types: list[dict],
        source_group: str,
    ) -> list[dict]:
        """Extract entities from extraction data using LLM.

        Args:
            extraction_data: Extraction data dictionary
            entity_types: List of entity type definitions
            source_group: Source grouping identifier

        Returns:
            List of extracted entity dictionaries

        Raises:
            Exception: If LLM call fails after retries
        """
        try:
            # Build prompts
            prompts = self._build_prompt(
                extraction_data=extraction_data,
                entity_types=entity_types,
                source_group=source_group,
            )

            # Call LLM
            response = await self._llm_client.client.chat.completions.create(
                model=self._llm_client.model,
                messages=[
                    {"role": "system", "content": prompts["system"]},
                    {"role": "user", "content": prompts["user"]},
                ],
                response_format={"type": "json_object"},
                temperature=0.1,
            )

            # Parse response
            result_text = response.choices[0].message.content
            result_data = json.loads(result_text)

            return result_data.get("entities", [])

        except json.JSONDecodeError as e:
            logger.warning("invalid_json_from_llm", error=str(e))
            return []
        except Exception as e:
            logger.error("entity_extraction_failed", error=str(e))
            raise
```

Update the `extract` method to handle errors:

```python
    async def extract(
        self,
        extraction,
        entity_types: list[dict],
    ) -> list:
        """Extract entities from an extraction using LLM.

        This is the main public method for entity extraction.

        Args:
            extraction: Extraction ORM object with id, data, project_id, source_group
            entity_types: List of entity type definitions from project.entity_types

        Returns:
            List of Entity ORM objects that were extracted and stored
        """
        # Skip if no entity types configured
        if not entity_types:
            return []

        try:
            # Extract entities using LLM
            entity_dicts = await self._extract_entities_from_llm(
                extraction_data=extraction.data,
                entity_types=entity_types,
                source_group=extraction.source_group,
            )

            # Return early if no entities found
            if not entity_dicts:
                return []

            # Store entities with deduplication
            stored_entities = await self._store_entities(
                entities=entity_dicts,
                project_id=extraction.project_id,
                source_group=extraction.source_group,
            )

            # Link entities to extraction
            await self._link_entities_to_extraction(
                entities=stored_entities,
                extraction_id=extraction.id,
            )

            return stored_entities

        except Exception as e:
            logger.error(
                "entity_extraction_failed",
                extraction_id=str(extraction.id),
                error=str(e),
            )
            # Return empty list on failure (graceful degradation)
            return []
```

**Step 4: Run test to verify it passes**

Run: `pytest tests/test_entity_extractor.py::TestErrorHandling -v`

Expected: PASS

**Step 5: Commit**

```bash
git add pipeline/services/knowledge/extractor.py tests/test_entity_extractor.py
git commit -m "feat: add error handling and retry logic

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 9: Run Full Test Suite

**Files:**
- Run: All tests

**Step 1: Run all EntityExtractor tests**

Run: `pytest tests/test_entity_extractor.py -v`

Expected: ALL PASS

**Step 2: Run all project tests to ensure no regressions**

Run: `pytest -v`

Expected: ALL PASS (or pre-existing failures only)

**Step 3: Check code coverage**

Run: `pytest tests/test_entity_extractor.py --cov=services.knowledge.extractor --cov-report=term-missing`

Expected: >90% coverage

**Step 4: If any failures, fix and rerun**

Address any test failures or coverage gaps.

**Step 5: Final commit**

```bash
git add .
git commit -m "test: verify EntityExtractor test suite passes

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Task 10: Add Documentation and Type Hints

**Files:**
- Modify: `pipeline/services/knowledge/extractor.py`
- Create: `pipeline/services/knowledge/README.md`

**Step 1: Add comprehensive docstrings**

Ensure all methods have complete Google-style docstrings with:
- Description
- Args with types
- Returns with type
- Raises (if applicable)
- Examples (for public methods)

**Step 2: Verify type hints are complete**

Run: `mypy pipeline/services/knowledge/extractor.py --strict`

Fix any type hint issues.

**Step 3: Create README**

Create `pipeline/services/knowledge/README.md`:

```markdown
# Knowledge Extraction Services

Entity and relation extraction from structured data.

## EntityExtractor

Extracts entities from extraction data using LLM and project-configured entity types.

### Usage

\`\`\`python
from services.llm.client import LLMClient
from services.storage.repositories.entity import EntityRepository
from services.knowledge.extractor import EntityExtractor

# Initialize
llm_client = LLMClient(settings)
entity_repo = EntityRepository(session)
extractor = EntityExtractor(llm_client, entity_repo)

# Extract entities from extraction
entities = await extractor.extract(
    extraction=extraction_obj,  # Extraction ORM object
    entity_types=project.entity_types,
)
\`\`\`

### Features

- **LLM-based extraction**: Uses configured LLM to identify entities
- **Automatic deduplication**: Uses EntityRepository.get_or_create
- **Project-scoped**: Entity types from project.entity_types
- **Retry logic**: Automatic retry on transient failures
- **Graceful degradation**: Returns empty list on errors

### Entity Normalization

Values are normalized for deduplication:
- Lowercase conversion
- Special character removal
- Space/slash to underscore conversion

Examples:
- "Pro Plan" → "pro_plan"
- "$99/month" → "99_month"
- "10,000/min" → "10000_min"
```

**Step 4: Run linter**

Run: `ruff check pipeline/services/knowledge/`

Fix any linting issues.

**Step 5: Commit**

```bash
git add pipeline/services/knowledge/
git commit -m "docs: add EntityExtractor documentation and type hints

Co-Authored-By: Claude Sonnet 4.5 <noreply@anthropic.com>"
```

---

## Completion Checklist

After completing all tasks:

- [ ] All tests pass (`pytest tests/test_entity_extractor.py -v`)
- [ ] No regressions in existing tests (`pytest -v`)
- [ ] Code coverage >90% for EntityExtractor
- [ ] Type hints verified with mypy
- [ ] Code passes ruff linting
- [ ] Documentation complete (docstrings + README)
- [ ] All commits follow conventional commit format
- [ ] Ready for integration with extraction pipeline

---

## Integration Notes

**Next Steps (Not in this plan):**

1. **Pipeline Integration**: Call EntityExtractor after extraction
2. **Batch Processing**: Process multiple extractions efficiently
3. **API Endpoint**: Create endpoint to trigger entity extraction
4. **Monitoring**: Add metrics for entity extraction performance

**Dependencies:**

- EntityRepository (already implemented)
- LLMClient (already implemented)
- Extraction ORM model (already implemented)
- Project entity_types configuration (already implemented)

**File Locations:**

- Implementation: `pipeline/services/knowledge/extractor.py`
- Tests: `tests/test_entity_extractor.py`
- README: `pipeline/services/knowledge/README.md`
