# TODO: Embedding Batching Performance

**Agent:** agent-perf
**Branch:** `feat/embedding-batching`
**Priority:** high

## Context

The extraction pipeline processes embeddings ONE AT A TIME despite having batch APIs available. This causes 10-20x more API calls than necessary.

**Current code in `pipeline.py:172-182`:**
```python
for fact in result.facts:
    embedding = await self._embedding_service.embed(fact.fact)  # SINGLE call
    await self._qdrant_repo.upsert(...)  # SINGLE call
```

**Batch methods that ALREADY EXIST but are NEVER CALLED:**
- `EmbeddingService.embed_batch(texts: list[str])` in `src/services/storage/embedding.py:59-78`
- `QdrantRepository.upsert_batch(items: list[EmbeddingItem])` in `src/services/storage/qdrant/repository.py:99-127`

## Objective

Refactor `ExtractionPipelineService.process_source()` to use batch embedding and batch upsert methods instead of processing facts one at a time.

## Tasks

### 1. Refactor `process_source()` to Batch Embeddings

**File:** `src/services/extraction/pipeline.py`

**Requirements:**
- Collect all non-duplicate facts FIRST before embedding
- Call `self._embedding_service.embed_batch(texts)` once with all fact texts
- Call `self._qdrant_repo.upsert_batch(items)` once with all embedding items
- Preserve the existing deduplication logic (skip duplicates before embedding)
- Preserve the existing entity extraction logic (run after embeddings stored)
- Handle errors gracefully - if batch embedding fails, log error and continue with entity extraction for facts that were stored

**Implementation approach:**
```python
# Phase 1: Deduplicate and collect
facts_to_embed = []
fact_extractions = []  # Track (fact, extraction) pairs

for fact in result.facts:
    dedup_result = await self._deduplicator.check_duplicate(...)
    if dedup_result.is_duplicate:
        extractions_deduplicated += 1
        continue

    extraction = await self._extraction_repo.create(...)
    extractions_created += 1
    facts_to_embed.append(fact.fact)
    fact_extractions.append((fact, extraction))

# Phase 2: Batch embed
if facts_to_embed:
    embeddings = await self._embedding_service.embed_batch(facts_to_embed)

    # Phase 3: Batch upsert to Qdrant
    from services.storage.qdrant.repository import EmbeddingItem
    items = [
        EmbeddingItem(
            extraction_id=extraction.id,
            embedding=embedding,
            payload={...}
        )
        for (fact, extraction), embedding in zip(fact_extractions, embeddings)
    ]
    await self._qdrant_repo.upsert_batch(items)

# Phase 4: Entity extraction (unchanged, per-extraction)
for fact, extraction in fact_extractions:
    entities = await self._entity_extractor.extract(...)
```

### 2. Add Import for EmbeddingItem

**File:** `src/services/extraction/pipeline.py`

**Requirements:**
- Add `from services.storage.qdrant.repository import EmbeddingItem` to imports

### 3. Write Tests for Batch Embedding

**File:** `tests/test_extraction_pipeline.py` (add to existing file)

**Requirements:**
- Test that `embed_batch` is called instead of `embed` when processing multiple facts
- Test that `upsert_batch` is called instead of `upsert` when processing multiple facts
- Test that single fact still works (batch of 1)
- Test that empty facts list doesn't call batch methods
- Test that deduplication still works (duplicates excluded from batch)

## Constraints

- Do NOT modify `EmbeddingService` or `QdrantRepository` - they already have the batch methods
- Do NOT change the function signatures of `process_source()` or `process_batch()`
- Do NOT modify entity extraction logic - it must still run per-extraction
- Do NOT run full test suite - only run tests in Test Scope below
- Do NOT lint entire codebase - only lint files in Lint Scope below

## Test Scope

**ONLY run these tests - do NOT run `pytest` without arguments:**

```bash
pytest tests/test_extraction_pipeline.py -v
pytest tests/test_embedding_service.py -v
pytest tests/test_qdrant_repository.py -v
```

## Lint Scope

**ONLY lint these files - do NOT run `ruff check src/`:**

```bash
ruff check src/services/extraction/pipeline.py
ruff format src/services/extraction/pipeline.py
```

## Verification

Before creating PR:

1. `pytest tests/test_extraction_pipeline.py -v` - Must pass
2. `pytest tests/test_embedding_service.py -v` - Must pass
3. `pytest tests/test_qdrant_repository.py -v` - Must pass
4. `ruff check src/services/extraction/pipeline.py` - Must be clean

## Definition of Done

- [ ] `process_source()` uses `embed_batch()` instead of `embed()` in loop
- [ ] `process_source()` uses `upsert_batch()` instead of `upsert()` in loop
- [ ] EmbeddingItem import added
- [ ] Tests verify batch methods are called
- [ ] All scoped tests pass
- [ ] Lint clean (scoped)
- [ ] PR created with title: `feat: use batch embedding for extraction pipeline`
