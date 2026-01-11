# TODO: Complete EntityExtractor Implementation

**Agent:** entity-extractor
**Branch:** `feat/entity-extractor-complete`
**Priority:** high
**Assigned:** 2026-01-11

## Context

The EntityExtractor class extracts structured entities (plan, feature, limit, pricing, etc.) from extraction data using LLM. The skeleton and prompt builder are already implemented in `services/knowledge/extractor.py`. You need to complete the remaining methods.

**Already implemented:**
- `__init__()` - Accepts LLMClient and EntityRepository
- `_build_prompt()` - Builds system/user prompts for entity extraction

**Existing tests:** 3 tests in `tests/test_entity_extractor.py`

**Dependencies available:**
- `LLMClient` (`services/llm/client.py`) - Has `extract_facts()` method with retry logic
- `EntityRepository` (`services/storage/repositories/entity.py`) - Has `get_or_create()` for deduplication and `link_to_extraction()` for linking

## Objective

Complete the EntityExtractor class to extract entities from extraction data, store them with deduplication, and link them to extractions.

## Tasks

### 1. Implement `_normalize()` method

**File(s):** `src/services/knowledge/extractor.py`

**Requirements:**
- Static or instance method that normalizes entity values for deduplication
- Normalization rules by entity type:
  - `plan`: lowercase, strip whitespace (e.g., "Pro Plan" -> "pro plan")
  - `feature`: lowercase, strip whitespace (e.g., "SSO" -> "sso")
  - `limit`: extract numeric value + unit (e.g., "10,000/min" -> "10000_per_minute")
  - `pricing`: extract numeric value + period (e.g., "$99/month" -> "99_per_month")
  - Default: lowercase, strip whitespace
- Return normalized string suitable for deduplication matching

**Test cases to cover:**
- `test_normalize_plan_lowercase_and_strip` - "  Pro Plan  " -> "pro plan"
- `test_normalize_feature_lowercase` - "SSO" -> "sso"
- `test_normalize_limit_extracts_numeric` - "10,000 requests/min" -> "10000_per_minute"
- `test_normalize_pricing_extracts_amount` - "$99.99/month" -> "9999_per_month" (cents)
- `test_normalize_unknown_type_default` - Uses default lowercase behavior

### 2. Implement `_call_llm()` method

**File(s):** `src/services/knowledge/extractor.py`

**Requirements:**
- Async method that calls LLM with the built prompt
- Use the OpenAI client from `self._llm_client.client` directly (not `extract_facts`)
- Use `response_format={"type": "json_object"}` for structured output
- Parse JSON response and return list of entity dictionaries
- Handle JSON parse errors gracefully (return empty list, log warning)
- Use low temperature (0.1) for consistent extraction

**Signature:**
```python
async def _call_llm(self, prompt: dict[str, str]) -> list[dict]:
    """Call LLM and parse entity response."""
```

**Test cases to cover:**
- `test_call_llm_returns_parsed_entities` - Mock LLM returns valid JSON
- `test_call_llm_handles_empty_response` - LLM returns `{"entities": []}`
- `test_call_llm_handles_invalid_json` - LLM returns malformed JSON, returns []
- `test_call_llm_uses_json_mode` - Verify response_format is set

### 3. Implement `_store_entities()` method

**File(s):** `src/services/knowledge/extractor.py`

**Requirements:**
- Async method that stores entities using EntityRepository
- Use `get_or_create()` for deduplication
- Return list of (Entity, created) tuples

**Signature:**
```python
async def _store_entities(
    self,
    entities: list[dict],
    project_id: UUID,
    source_group: str,
) -> list[tuple[Entity, bool]]:
    """Store entities with deduplication."""
```

**Test cases to cover:**
- `test_store_entities_creates_new` - New entity is created
- `test_store_entities_deduplicates` - Existing entity is returned (created=False)
- `test_store_entities_uses_normalized_value` - Normalization is applied before storage

### 4. Implement main `extract()` method

**File(s):** `src/services/knowledge/extractor.py`

**Requirements:**
- Main async method that orchestrates the full extraction pipeline
- Accept extraction data, project config, and extraction_id for linking
- Steps:
  1. Build prompt using `_build_prompt()`
  2. Call LLM using `_call_llm()`
  3. Store entities using `_store_entities()`
  4. Link entities to extraction using `entity_repo.link_to_extraction()`
- Return list of created/retrieved Entity objects

**Signature:**
```python
async def extract(
    self,
    extraction_id: UUID,
    extraction_data: dict,
    project_id: UUID,
    entity_types: list[dict],
    source_group: str,
) -> list[Entity]:
    """Extract entities from extraction data and link to extraction."""
```

**Test cases to cover:**
- `test_extract_full_pipeline` - End-to-end with mocked LLM
- `test_extract_links_entities_to_extraction` - Verifies link_to_extraction called
- `test_extract_returns_entities` - Returns list of Entity objects
- `test_extract_handles_no_entities` - LLM finds no entities, returns []
- `test_extract_with_multiple_entity_types` - Extracts plan, feature, limit from same data

### 5. Add imports and type hints

**File(s):** `src/services/knowledge/extractor.py`

**Requirements:**
- Add UUID import
- Add Entity import from orm_models
- Add Optional, Any from typing as needed
- Add structlog for logging
- Ensure all methods have proper type hints

## Constraints

- Do NOT modify `EntityRepository` - it's complete and tested
- Do NOT modify `LLMClient` - use its client directly for custom calls
- Do NOT modify any files outside `services/knowledge/` and `tests/test_entity_extractor.py`
- Do NOT add new dependencies
- Keep the existing `_build_prompt()` implementation unchanged
- Use TDD: write tests first, then implement

## Verification

Before creating PR, confirm:
- [ ] All 5 tasks above completed
- [ ] `pytest tests/test_entity_extractor.py -v` - All tests pass
- [ ] `pytest` - All 417+ tests still pass
- [ ] `ruff check src/` clean
- [ ] `ruff format src/` applied
- [ ] No new warnings

## Notes

**LLM Client Usage:**
The existing `LLMClient.extract_facts()` is specialized for fact extraction. For entity extraction, access the underlying OpenAI client directly:

```python
response = await self._llm_client.client.chat.completions.create(
    model=self._llm_client.model,
    messages=[
        {"role": "system", "content": prompt["system"]},
        {"role": "user", "content": prompt["user"]},
    ],
    response_format={"type": "json_object"},
    temperature=0.1,
)
```

**Entity Repository Pattern:**
```python
entity, created = await self._entity_repo.get_or_create(
    project_id=project_id,
    source_group=source_group,
    entity_type=entity["type"],
    value=entity["value"],
    normalized_value=self._normalize(entity["type"], entity["value"]),
    attributes=entity.get("attributes", {}),
)
```
