# Deep Pipeline Review: Extraction Pipeline — Design & Hardcoding Issues

**Scope**: Full extraction pipeline end-to-end: API → Worker → Orchestrator → Extractor → LLM → Merge → Validate → Embed → Store
**Focus**: Hardcoded implementations, bad design, significant real issues
**Date**: 2026-03-04
**Re-verified**: 2026-03-04 (all findings confirmed against actual code)

## Flow

```
API: POST /projects/{id}/extract → Job(QUEUED)
  → scheduler.py:_run_extract_worker [poll loop]
    → worker.py:ExtractionWorker.process_job
      ├─ HAS schema → _create_schema_pipeline → SchemaExtractionPipeline.extract_project
      │   ├─ load & validate schema (fallback to DEFAULT_EXTRACTION_TEMPLATE if invalid)
      │   ├─ query sources (status filtering, source_groups)
      │   ├─ for each chunk of 20 sources:
      │   │   ├─ extract_source → orchestrator.extract_all_groups
      │   │   │   ├─ classify page (smart: embed+rerank, or rule-based)
      │   │   │   ├─ chunk_document (H2+ splitting, CJK-aware)
      │   │   │   ├─ for each field_group (parallel):
      │   │   │   │   ├─ for each chunk (semaphore-controlled):
      │   │   │   │   │   └─ extractor.extract_field_group → LLM call (direct or queue)
      │   │   │   │   └─ merge_chunk_results (strategy per field type)
      │   │   │   └─ schema validation (coercion, enum, confidence gating)
      │   │   ├─ store Extraction ORM objects
      │   │   ├─ flush → embed_and_upsert → Qdrant
      │   │   └─ commit (with checkpoint)
      │   └─ return summary dict
      └─ NO schema → ExtractionPipelineService.process_project_pending (legacy fact path)
```

---

## Critical

### 1. `merge_dedupe` not in VALID_MERGE_STRATEGIES — can't configure the default list strategy

**Verified**: `field_groups.py:7-9`, `schema_orchestrator.py:327,380`, `schema_adapter.py:348-355`

- `VALID_MERGE_STRATEGIES` = `{"highest_confidence", "max", "min", "concat", "majority_vote"}`
- `_get_merge_strategy()` returns `"merge_dedupe"` for list fields (orchestrator:327)
- `_merge_chunk_results()` handles `merge_dedupe` in its branch logic (orchestrator:380)
- `validate_extraction_schema()` rejects any `merge_strategy` not in `VALID_MERGE_STRATEGIES` (adapter:351)

**The bug**: `merge_dedupe` is the actual runtime strategy for list fields, but schema validation rejects it if explicitly configured. A template author who writes `"merge_strategy": "merge_dedupe"` gets an error, yet that's the exact strategy the system uses by default.

### 2. Silent fallback to DEFAULT_EXTRACTION_TEMPLATE hides schema corruption

**Verified**: `pipeline.py:528-542`

```python
if not schema:
    schema = DEFAULT_EXTRACTION_TEMPLATE["extraction_schema"]  # line 533

validation = adapter.validate_extraction_schema(schema)
if not validation.is_valid:
    schema = DEFAULT_EXTRACTION_TEMPLATE["extraction_schema"]  # line 542
```

When a project's `extraction_schema` is invalid (corrupted, manually edited, migration broke it), the system:
1. Logs a warning
2. Silently substitutes the default generic template
3. Extracts using completely wrong field groups (e.g., generic `company_info`, `products` instead of what the user configured)
4. Stores these wrong-schema extractions as if they're correct
5. The job reports "completed" with normal-looking extraction counts

This should be a hard failure (`JobStatus.FAILED`), not a silent substitution that produces wrong data.

---

## Important

### 3. `schema_orchestrator.py` bypasses typed config facades — 11 direct reads from global singleton

**Verified**: `schema_orchestrator.py:13` imports `from config import settings`, then reads it directly at lines 87, 89, 116, 143, 144, 182, 184, 231, 409, 424.

The typed config facade migration (all 7 phases complete) was designed to eliminate direct `settings` access in services. The orchestrator — the most complex component in the pipeline — was missed. Makes it impossible to test with different config values without monkeypatching the global.

### 4. Schema pipeline returns untyped dict — type safety lost at critical boundary

**Verified**: `pipeline.py:495` returns `-> dict`, `worker.py:307-320` unpacks with `.get()` calls.

The dict uses magic string keys (`"sources_processed"`, `"extractions_created"`, `"error"`, `"cancelled"`, etc.) with no type contract. The generic pipeline returns the typed `BatchPipelineResult` dataclass — the schema pipeline should do the same. A typo or key rename silently produces zero counts.

### 5. Drivetrain-specific defaults in "template-agnostic" components

**Verified at all three locations:**

**a) `smart_classifier.py:545-552`** — `_PAGE_TYPE_PATTERNS`:
Keywords "motor", "equipment", "fleet", "engineering" are drivetrain-specific. For recipe/job/academic templates these patterns are meaningless.

**b) `page_classifier.py:186-191`** — `_infer_page_type`:
Hardcodes `"services"` and `"company_info"` as exact field group name matches from the drivetrain template.

**c) `schema_adapter.py:191-193`** — `ExtractionContext.entity_id_fields`:
Default includes `"product_name"` — drivetrain-specific. Duplicated at `schema_extractor.py:398-399` as a fallback tuple.

### 6. Qdrant collection dimension hardcoded to 1024 with stale comment

**Verified**: `repository.py:66-74`

```python
# Create collection with BGE-large-en configuration    ← stale comment (model is bge-m3)
vectors_config=VectorParams(
    size=1024,  # BGE-large-en dimension               ← stale comment
    distance=Distance.COSINE,
),
```

The dimension `1024` is hardcoded in the actual Qdrant collection creation. The comment says "BGE-large-en" but the deployed model is bge-m3 (which also happens to be 1024 dims, so it works, but the comment is wrong). Switching to a different-dimension model would require finding this hardcoded value.

Note: `EmbeddingService.dimension` property (`embedding.py:80-86`) is also hardcoded to 1024 but is **dead code** — never called in any production path (only tested in `test_embedding_service.py:57`).

---

## Minor

### 7. Hardcoded `chunk_size = 20` in schema extraction pipeline

**Verified**: `pipeline.py:609`

Controls commit batch size, cancellation check frequency, and crash-loss window. Not configurable via settings.

### 8. Hardcoded `6000` char truncation for embedding/reranking

**Verified**: `smart_classifier.py:309,533`

Model-specific assumption ("6000 chars safe with bge-m3's 8192 token limit"). Switching models would silently degrade classification quality.

### 9. Recovery endpoint creates services outside ServiceContainer

**Verified**: `extraction.py:284-327`

Creates its own `EmbeddingService` and `QdrantRepository` instances. Works correctly (class-level semaphore is shared), but inconsistent with the ServiceContainer pattern used everywhere else.

### 10. MD5 vs SHA-256 inconsistency in entity dedup

**Verified**: `schema_orchestrator.py:478` uses `hashlib.md5()`, while domain dedup and smart classifier use SHA-256. Not a security issue, but inconsistent.

---

## Downgraded from original report

### ~~EXTRACTION_CONTENT_LIMIT module-level capture~~ → Dead fallback code

**Verified as non-issue**: `schema_extractor.py:26` captures `EXTRACTION_CONTENT_LIMIT` at import time, and `worker.py:412,469` uses it. However, both `SchemaExtractor._extract_via_queue()` (lines 156-157) and `LLMClient` (lines 141-142, 440-441) **always** send `system_prompt`/`user_prompt` in the payload. The LLMWorker fallback paths that use `EXTRACTION_CONTENT_LIMIT` (lines 457-472) are **never reached** in the current codebase — all callers provide prompts. The constant is imported dead code.

---

## Summary

| # | Severity | File | Issue | Verified |
|---|----------|------|-------|----------|
| 1 | **Critical** | field_groups.py / schema_adapter.py | `merge_dedupe` not in VALID_MERGE_STRATEGIES — blocks explicit configuration | Yes |
| 2 | **Critical** | pipeline.py:528-542 | Silent fallback to default template on invalid schema — wrong extractions stored | Yes |
| 3 | **Important** | schema_orchestrator.py | 11 direct `settings` reads bypass typed config facades | Yes |
| 4 | **Important** | pipeline.py:495 / worker.py:307 | Schema pipeline returns untyped dict instead of dataclass | Yes |
| 5 | **Important** | smart_classifier / page_classifier / schema_adapter | Drivetrain-specific defaults in "template-agnostic" components | Yes |
| 6 | **Important** | repository.py:73 | Qdrant dimension hardcoded to 1024, stale "BGE-large-en" comment | Yes |
| 7 | Minor | pipeline.py:609 | `chunk_size = 20` hardcoded, not configurable | Yes |
| 8 | Minor | smart_classifier.py:309,533 | 6000 char truncation tied to bge-m3 model | Yes |
| 9 | Minor | extraction.py:284-327 | Recovery endpoint bypasses ServiceContainer | Yes |
| 10 | Minor | schema_orchestrator.py:478 | MD5 vs SHA-256 inconsistency | Yes |
