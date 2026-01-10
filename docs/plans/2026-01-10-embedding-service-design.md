# Embedding Service Design

**Date:** 2026-01-10
**Status:** Approved
**Related:** TODO_storage.md Phase 4

## Overview

Design for the EmbeddingService component that generates text embeddings via BGE-large-en model through vLLM. This service provides a consistent interface for converting text to 1024-dimensional vectors for semantic search.

## Architecture & Components

### Service Class

**File:** `pipeline/services/storage/embedding.py`

**Class:** `EmbeddingService` - main interface for generating embeddings

### Configuration

Uses existing Settings fields:
- `openai_embedding_base_url` - vLLM endpoint URL
- `openai_api_key` - API authentication key
- `rag_embedding_model` - model name (bge-large-en)

### Public API

```python
class EmbeddingService:
    def __init__(self, settings: Settings)
    async def embed(self, text: str) -> list[float]
    async def embed_batch(self, texts: list[str]) -> list[list[float]]
    @property
    def dimension(self) -> int  # Returns 1024
```

### Dependencies

- `openai` (AsyncOpenAI) - already in project
- `tenacity` - for retry logic (already used by LLMClient)

### Design Pattern

Follows the same pattern as `LLMClient`:
- Constructor injection of Settings
- Async methods for I/O operations
- Retry decorators for resilience
- Google-style docstrings

This ensures consistency across the codebase and makes it easy for developers familiar with LLMClient to use EmbeddingService.

## Implementation Details

### Retry Strategy

- **Decorator:** `@retry` from tenacity on both `embed()` and `embed_batch()`
- **Configuration:** `stop_after_attempt(3)`, `wait_exponential(multiplier=2, min=4, max=60)`
- **Rationale:** Same as LLMClient for consistency and handling transient failures

### Error Handling

- Let OpenAI client exceptions propagate (caller decides how to handle)
- Validate response structure before returning
- Handle empty input gracefully (empty list returns empty list)
- No custom exception wrapping - use standard library exceptions

### Batch Processing

- No automatic chunking - caller controls batch size
- The OpenAI embeddings API already supports batching efficiently
- Return embeddings in same order as input texts
- Batch size management is caller's responsibility

### Input Validation

- Accept empty strings (let API handle it)
- Empty list for batch returns empty list immediately (no API call)
- No text preprocessing (embeddings should be raw)
- Trust input validity - no sanitization

### Type Hints

- Full type annotations following project standards
- Return type `list[float]` for single embedding
- Return type `list[list[float]]` for batch embeddings
- Use modern Python 3.10+ syntax (`list[float]` not `List[float]`)

## Testing Strategy

### Test File

**File:** `pipeline/tests/test_embedding_service.py`

### Test Organization

Class-based organization following project conventions:

#### TestEmbeddingServiceEmbed (single text)

- `test_embed_returns_1024_dimension_vector` - verify dimension
- `test_embed_returns_list_of_floats` - type validation
- `test_embed_with_empty_string` - edge case handling
- `test_embed_retries_on_failure` - retry behavior (mocked)

#### TestEmbeddingServiceEmbedBatch (multiple texts)

- `test_embed_batch_returns_correct_count` - output count matches input
- `test_embed_batch_preserves_order` - order validation
- `test_embed_batch_with_empty_list` - returns empty list
- `test_embed_batch_with_single_item` - works with batch of 1

#### TestEmbeddingServiceIntegration (if vLLM available)

- `test_integration_embed_real_text` - actual API call
- `test_integration_batch_efficiency` - verify batching works

### Fixtures

```python
@pytest.fixture
def embedding_service():
    """Create EmbeddingService with test settings."""
    from config import settings
    return EmbeddingService(settings)
```

### Mocking Strategy

- Use `unittest.mock` to mock AsyncOpenAI for unit tests
- Mock the `embeddings.create()` response structure
- Integration tests use real settings (skip if vLLM not available)
- Mock retry decorator behavior for failure testing

### Coverage Target

- All public methods tested
- Edge cases covered (empty inputs, single items, etc.)
- Retry logic verified
- Integration with real API verified (when available)

## Implementation Checklist

- [ ] Create `pipeline/services/storage/embedding.py`
- [ ] Implement `EmbeddingService` class with `__init__`
- [ ] Implement `embed()` method with retry decorator
- [ ] Implement `embed_batch()` method with retry decorator
- [ ] Add `dimension` property
- [ ] Create `pipeline/tests/test_embedding_service.py`
- [ ] Write unit tests for `embed()` method
- [ ] Write unit tests for `embed_batch()` method
- [ ] Write integration tests (with skip decorator)
- [ ] Verify all tests pass
- [ ] Update TODO_storage.md to mark Phase 4 complete

## Integration Points

### QdrantRepository

Will use `EmbeddingService.dimension` to validate vector sizes match collection configuration.

### SearchService

Will use `EmbeddingService.embed()` to convert search queries to vectors.

### Future Extraction Pipeline

Will use `EmbeddingService.embed_batch()` to efficiently process multiple extractions.

## Non-Goals

- **No automatic batching:** Caller manages batch sizes
- **No text preprocessing:** Embeddings use raw text
- **No caching:** Caching happens at higher layers if needed
- **No rate limiting:** vLLM handles this at the API level
