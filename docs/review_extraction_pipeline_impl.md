# Pipeline Review: Extraction Pipeline Implementation

**Review Date:** 2026-03-04
**Scope:** Full extraction pipeline from API entry through LLM extraction to storage
**Verdict:** Functional, production-quality. No significant issues found.

---

## Process Flow

```
API/MCP Entry
  └─ POST /extract → creates Job(QUEUED) in DB
  └─ MCP extract_knowledge → wraps HTTP client call

Job Scheduler (background)
  └─ ExtractionWorker.process_job()
      ├─ Validates project, checks for extraction_schema
      ├─ Routes: schema pipeline (if field_groups) OR generic pipeline (fallback)
      └─ Updates Job status (RUNNING → COMPLETED/FAILED/CANCELLED)

Schema Pipeline (primary path)
  └─ SchemaExtractionPipeline.extract_project()
      ├─ SchemaAdapter.validate_extraction_schema() → FieldGroups
      ├─ For each source chunk (batch_size=20):
      │   ├─ ContentSelector: raw vs cleaned_content (domain dedup)
      │   ├─ SchemaExtractionOrchestrator.extract_all_groups()
      │   │   ├─ PageClassifier / SmartClassifier (3-tier: skip → embed → rerank)
      │   │   ├─ chunk_document() (H2+ splitting, CJK-aware, overlap)
      │   │   ├─ SchemaExtractor.extract_field_group() × N groups × M chunks (semaphore)
      │   │   │   └─ LLM call (direct or queue mode) → JSON → repair → validate
      │   │   ├─ _merge_chunk_results() (per-field strategy: majority_vote/max/concat/merge_dedupe/highest_confidence)
      │   │   ├─ SchemaValidator.validate() (type coercion, confidence gating)
      │   │   └─ _is_empty_result() (filters noise extractions)
      │   ├─ Store Extraction ORM objects → flush()
      │   ├─ ExtractionEmbeddingService.embed_and_upsert() → Qdrant
      │   ├─ checkpoint_callback() (mutates job.payload in same session)
      │   └─ commit() [atomic: extractions + checkpoint in one transaction]
      └─ Return SchemaPipelineResult

Generic Pipeline (fallback, no schema)
  └─ ExtractionPipelineService.process_batch()
      ├─ LLMClient.extract_facts() per source
      ├─ Deduplication → store Extraction → embed → extract entities
      └─ Return BatchPipelineResult
```

---

## Implementation Quality Assessment

### Strengths
- **Well-decomposed**: Pipeline split into focused services (content_selector, backpressure, embedding_pipeline, smart_classifier, schema_validator, domain_dedup)
- **Typed throughout**: Frozen config dataclasses, typed return values (SchemaPipelineResult, EmbeddingResult, ClassificationResult)
- **Graceful degradation**: SmartClassifier falls back to rule-based; queue mode falls back to direct; domain dedup falls back to raw content
- **Per-chunk durability**: Schema pipeline commits after each chunk — checkpoint + extractions atomic in same transaction
- **Robust error handling**: process_job() has top-level try/except, jobs marked FAILED with error details, DB rollback on unexpected errors
- **Adaptive concurrency**: LLMWorker auto-scales semaphore based on timeout rate
- **Template-agnostic**: No hardcoded domain assumptions
- **CJK-aware chunking**: Proper token estimation for multilingual content
- **1738 tests passing**: Extensive coverage

---

## Findings

### Minor (low priority, not blocking)

- [ ] **DLQ has no TTL or cleanup** — `src/services/llm/worker.py`
  - Dead letter queue items accumulate indefinitely in Redis list (`llm:dlq`)
  - Only fires when all LLM retries exhausted (rare in normal operation)
  - Slow accumulation, not urgent — add TTL or periodic cleanup eventually

- [ ] **Vestigial module constant** — `src/services/extraction/schema_extractor.py:25-27`
  - `EXTRACTION_CONTENT_LIMIT` frozen at import, already marked "Deprecated" in comment
  - Unused by primary code path (worker injects content_limit via constructor)
  - Remove when convenient to reduce confusion

---

## False Alarms Investigated and Dismissed

| Claim | Why it's not a problem |
|-------|----------------------|
| SmartClassifier Redis failure crashes worker | `process_job()` has top-level `try/except Exception` (line 491) — job marked FAILED, worker continues. Also, Redis down = LLM queue down too. |
| AsyncOpenAI client resource leak | GC cleans up httpx.AsyncClient promptly in CPython. One per job, goes out of scope when job finishes. `__aenter__`/`__aexit__` don't exist on SchemaExtractor. |
| Checkpoint saved before commit = data loss | Checkpoint mutates `job.payload` in same DB session. `commit()` on line 761 atomically commits both. If commit fails, both roll back — resume correctly re-processes. |
| Entity list truncation is silent | Logged at WARNING level (`schema_extraction_truncated`). Unrecoverable case returns `confidence: 0.0`. Successful repair preserves partial data. |
| Embedding partial failure not tracked | `embed_and_upsert` is all-or-nothing per chunk, logged at ERROR. Extractions still in DB, just not vector-searchable. Recovery endpoint handles this. |
| Backpressure wait time uncapped | Max 10 retries then QueueFullError. Long waits during backpressure are intentional. |
| SchemaExtractor retry no jitter | Concurrent extractions staggered by semaphore — natural timing variance provides de-facto jitter. |
| Temperature exceeds valid range | Default config: max temp = 0.1 + 2×0.05 = 0.2. Well within bounds. |

---

## Overall Assessment

| Dimension | Rating |
|-----------|--------|
| **Functionality** | Complete — both pipelines work end-to-end |
| **Error Handling** | Robust — top-level catch, graceful degradation, structured logging |
| **Transaction Safety** | Correct — per-chunk atomic commits, checkpoint in same transaction |
| **Concurrency** | Well-controlled — semaphores, backpressure, adaptive worker scaling |
| **Testability** | Excellent — 1738 tests, typed DI, pure functions, repository pattern |
| **Code Quality** | High — well-decomposed, typed, minimal duplication |

**Bottom line**: The extraction pipeline is production-ready with no significant issues.
