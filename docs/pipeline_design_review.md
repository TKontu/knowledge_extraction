# Pipeline Review: Design & Hardcoded Implementation Issues

Scope: Main extraction pipeline — from API route through scheduler, worker, LLM extraction, merge, storage, and reporting. Focus on hardcoded values, bad design patterns, and implementations that will cause real problems.

All findings verified by tracing actual code paths. Each finding marked with a verdict:
- **CONFIRMED** — real issue affecting production today
- **LATENT** — real code problem, not triggered by current config/templates but will fire when enabled
- **DOWNGRADED** — finding is real but impact is overstated or benign
- **REMOVED** — finding was wrong

## Flow

```
API (extraction.py) → Job(type="extract") → scheduler.py:_run_extract_worker
  → worker.py:ExtractionWorker.process_job
    → pipeline.py:SchemaExtractionPipeline.extract_source
      → schema_extractor.py:extract_field_group → LLM (via client.py or worker.py)
      → schema_orchestrator.py:_merge_chunk_results
    → pipeline.py: store Extraction in DB
  → reports/service.py → schema_table_generator.py
```

---

## Critical (must fix)

### 1. `max_tokens` silently differs between direct mode (8192) and queue mode (4096) — LATENT

**Files**: `llm/worker.py:57`, `config.py:100`, `scheduler.py:131-140`

```python
# config.py:100 — used by direct-mode LLMClient
llm_max_tokens: int = Field(default=8192, ...)

# llm/worker.py:57 — queue-mode worker constructor default
max_tokens: int = 4096,
```

**Verified**: `scheduler.py:131-140` instantiates `LLMWorker` without passing `max_tokens`, so the worker uses `4096`. The config value `8192` is never passed through. When queue mode is enabled (`llm_queue_enabled=True`), all LLM calls get half the response budget — extraction quality silently degrades.

**Currently safe**: `llm_queue_enabled` defaults to `False` and is not set in `.env`. The moment someone enables queue mode, this fires.

### 2. Pagination loads entire resultset into Python, then slices — CONFIRMED

**File**: `api/v1/extraction.py:226-233`

```python
all_extractions = extraction_repo.list(filters)
total = len(all_extractions)
paginated_extractions = all_extractions[offset : offset + limit]
```

**Verified**: `ExtractionRepository.list()` already accepts `limit` and `offset` parameters and pushes them to SQL (`query.offset(offset)`, `query.limit(limit)`). These parameters exist and work. The API endpoint simply doesn't pass them, loading ALL matching extractions into memory to return 50. For a project with 10,000+ extractions, this loads 10,000 ORM objects on every page request. This is a real performance bug affecting production.

### 3. No status enums — 43 bare string literals across 16 files — CONFIRMED

**Files**: scheduler, worker, pipeline, extraction API, source repo, job repo, orm_models

**Verified**: Grep found 43 occurrences of bare status strings across 16 files. No `Enum`, `StrEnum`, or module-level constants exist anywhere. Status values include `"queued"`, `"running"`, `"completed"`, `"failed"`, `"cancelling"`, `"pending"`, `"ready"`, `"extracted"`, `"processing"`. Same for job types (`"extract"`, `"scrape"`, `"crawl"`).

No DB-level `CHECK` constraints on `Job.status` or `Source.status` (`orm_models.py:74, 307`) — the DB accepts any string. A typo creates a silent bug: queries match nothing, jobs get stuck, sources never process.

---

## Important (should fix)

### 4. `EXTRACTION_CONTENT_LIMIT = 20000` hardcoded, not from config, cross-layer import — CONFIRMED

**Files**: `schema_extractor.py:25`, imported in `llm/worker.py:12`

**Verified**: No equivalent exists in `config.py`. Cannot be overridden via `.env` without code changes. The cross-layer import (`services/llm/` importing from `services/extraction/`) creates a compile-time coupling where infrastructure depends on domain.

The worker's use is in backward-compat fallback paths only (when pre-built prompts aren't in the payload), so the cross-layer import affects maintenance more than runtime. The real issue is that the constant is not configurable at all — changing the LLM model's context window requires a code edit.

### 5. Retry hint copy-pasted 4+ times — CONFIRMED (but loops are not identical)

**Files**: `llm/client.py` (3 methods), `schema_extractor.py:_extract_direct`

**Verified**: There are 4 retry loops but they are NOT identical copies. They share the same structure (temperature ramp, backoff, final raise) but have meaningful differences:
- `_extract_direct` has ~100 lines of unique truncation handling and entity-list logic
- `_complete_direct` is significantly shorter, no `last_error` tracking
- `_extract_entities_direct` has split `except` clause for JSONDecodeError

The retry hint string `"\n\nIMPORTANT: Be concise. Output valid JSON only."` is genuinely copy-pasted verbatim across all locations. A change to retry behavior requires editing 4 files.

**Downgraded from "identical copy-paste" to "shared structure with divergent details"**. The hint string duplication and general pattern remain real maintenance hazards.

### 6. `entity_id_fields` hardcoded independently in two locations — CONFIRMED (latent)

**Files**: `schema_adapter.py:192` + `schema_extractor.py:397-400`

**Verified**: `SchemaExtractor` has access to `self.context.entity_id_fields` but `_build_entity_list_system_prompt` uses a hardcoded tuple `("product_name", "entity_id", "name", "id")` instead. If a template declares custom `entity_id_fields` in its `extraction_context`, the prompt ignores them.

**Currently safe**: All deployed templates use the default field names, so the hardcoded list matches. Breaks silently when a custom template uses different ID field names.

### 7. Scheduler recreates full dependency graph per job — CONFIRMED

**File**: `scheduler.py:386-412`

**Verified**: Dependencies are instantiated only when `if job:` is true (per job found, not every 5s poll). However, every extraction job creates a new `LLMClient` (with new `AsyncOpenAI` connection pool), `EmbeddingService`, `QdrantRepository`, etc. These are stateless wrappers over shared infrastructure (`qdrant_client` is global, `self._llm_queue` survives across jobs). Could be initialized once in `start()`.

**Downgraded frequency** from "every 5s" to "per job", but the wasteful re-creation is real.

### 8. Pipeline bypasses repository pattern — PARTIALLY CONFIRMED

**File**: `pipeline.py:609, 647-668`

**Verified**:
- **Project fetch** (`line 609`): `ProjectRepository.get()` does exactly this query — bypass is unnecessary. **CONFIRMED.**
- **Source fetch** (`lines 647-668`): Multi-status IN query with content-not-null filter has no equivalent in `SourceRepository`. The repository only accepts a single status string. **NOT a bypass — the repo lacks the method.**

**Downgraded**: Only the project lookup is a genuine bypass. The source query is a missing repository method, not a pattern violation.

### 9. LLM queue config copy-pasted between API and scheduler — CONFIRMED

**Files**: `api/v1/extraction.py:340-344`, `scheduler.py:122-124`

```python
LLMRequestQueue(redis=..., stream_key="llm:requests", max_queue_depth=1000, backpressure_threshold=500)
```

**Verified**: Same 3 magic values in 2 locations. `stream_key` is the critical one — a mismatch between producer and consumer would cause complete silent failure. None of these come from `config.py`.

### 10. Domain-specific unit map in generic generator — CONFIRMED (benign)

**File**: `schema_table_generator.py:207-222`

**Verified**: `_infer_unit` is called unconditionally for all templates — no template-type gate. However, the unit suffixes (`_kw`, `_nm`, `_rpm`) only match drivetrain template field names. Non-drivetrain templates have no fields ending in these suffixes, so the map fires but never matches.

**Downgraded**: This is a misplaced responsibility (domain knowledge in generic code) but causes zero wrong behavior for other templates. Maintainability concern, not a production defect.

---

## Minor

### 11. `LLMExtractionError` defined as two independent classes — THEORETICAL

**Files**: `llm/client.py:22`, `schema_extractor.py:28`

**Verified**: The two classes exist in completely separate call stacks. `LLMClient` methods raise `client.LLMExtractionError` and it's caught by bare `except Exception` in the orchestrator. `SchemaExtractor` raises `schema_extractor.LLMExtractionError` and it's caught by bare `except Exception` in its own callers. No code path exists where one is raised and the other is caught by name. The mismatch would only matter if someone adds a named `except LLMExtractionError` against the wrong import.

**Downgraded from Critical**: The call stacks are fully separated, so no actual catch mismatch occurs in production.

### 12. Source `upsert()` clock race — CONFIRMED logic flaw, ZERO functional impact

**File**: `repositories/source.py:294`

**Verified**: `created` flag is used only to increment `sources_created` (a log counter) and emit a debug log. No code branches on it. No conditional processing depends on `created` vs `updated`. If `created` is wrong, the only effect is a miscounted log metric.

**Downgraded from Critical**: The logic is genuinely broken (slow DB → wrong result), but the consequence is a wrong log count, not a functional failure.

### 13. Naive `rstrip("s")` pluralization — THEORETICAL

**Files**: `schema_extractor.py:416`, `schema_adapter.py:435`

**Verified**: No currently deployed template has an entity-list group name ending in `s`. `products_gearbox`, `products_motor`, `products_accessory` — none end in `s`. Non-drivetrain templates have no entity-list groups at all. `"address".rstrip("s")` → `"addre"` is real Python behavior, but no template triggers it today.

**Downgraded**: Only fires for custom templates with problematic names. Note: `schema_adapter.py:435` has a guard (`if name.endswith("s") else name`) but `schema_extractor.py:416` uses raw `rstrip("s")` with no guard — this is the worse of the two.

### 14. Entity key probe list in reports — DOWNGRADED

**File**: `reports/service.py:595-598`

**Verified**: The probe list includes `ext_type` (the field group name) as the 3rd element. Since the LLM is instructed to output under the field group name, the probe hits `ext_type` for any correctly-structured response. A group named `"employees"` would be found because `ext_type == "employees"`. Silent data loss only occurs with malformed LLM output or if `extraction_type` diverges from the group name — neither happens currently.

### 15. Version string `"v1.3.1"` hardcoded 3× + FastAPI `"0.1.0"` — CONFIRMED

**File**: `main.py:90, 263, 287, 166`

OpenAPI docs show `0.1.0`; `/health` and `/` return `v1.3.1`. Two different version strings for the same service.

### 16. IP addresses and credentials in config defaults — CONFIRMED

**File**: `config.py:74, 79, 36`

Private network IPs and DB credentials committed as code defaults.

### 17. Redis response TTL `300` hardcoded twice — CONFIRMED

**File**: `llm/worker.py:315, 719`

Matches `settings.llm_request_timeout` (default 300) by coincidence, not by reference.

### 18. `entities_deduplicated` counter never incremented — CONFIRMED

**Files**: `pipeline.py`, `worker.py`

Always reports `0` — misleading metrics in results.

### 19. Pure Python cosine similarity on 1024-dim — CONFIRMED

**File**: `smart_classifier.py:544-569`

Manual dot product in Python loop. `numpy.dot` would be ~100× faster. Called per field group × per page during classification.

### 20. Config embedded in `extraction_schema` JSONB — CONFIRMED

**File**: `api/v1/projects.py:279-285`

Operational config stuffed into schema column to avoid migration. Comment admits it.

### ~~21. json_repair bug~~ — REMOVED

Line 285 correctly chains from line 284's `result`. Not a bug.

---

## Summary

| # | Finding | Verdict | Severity |
|---|---------|---------|----------|
| 1 | `max_tokens` 4096 vs 8192 between modes | LATENT | Critical |
| 2 | Pagination loads all extractions into Python | CONFIRMED | Critical |
| 3 | No status enums — 43 bare strings, 16 files | CONFIRMED | Critical |
| 4 | `EXTRACTION_CONTENT_LIMIT` hardcoded, cross-layer | CONFIRMED | Important |
| 5 | Retry structure shared 4×, hint string copy-pasted | CONFIRMED | Important |
| 6 | `entity_id_fields` hardcoded in 2 locations | LATENT | Important |
| 7 | Scheduler recreates deps per job | CONFIRMED | Important |
| 8 | Pipeline project fetch bypasses repository | CONFIRMED | Important |
| 9 | LLM queue config copy-pasted 2 locations | CONFIRMED | Important |
| 10 | Unit map in generic generator | CONFIRMED (benign) | Minor |
| 11 | Duplicate `LLMExtractionError` classes | THEORETICAL | Minor |
| 12 | Source upsert clock race | CONFIRMED (no impact) | Minor |
| 13 | `rstrip("s")` pluralization | THEORETICAL | Minor |
| 14 | Entity key probe list | DOWNGRADED | Minor |
| 15 | Version strings diverge | CONFIRMED | Minor |
| 16 | IPs/credentials in defaults | CONFIRMED | Minor |
| 17 | Redis TTL hardcoded | CONFIRMED | Minor |
| 18 | `entities_deduplicated` dead counter | CONFIRMED | Minor |
| 19 | Pure Python cosine similarity | CONFIRMED | Minor |
| 20 | Config in schema JSONB | CONFIRMED | Minor |

**3 Critical** (1 latent, 2 active today), **6 Important**, **11 Minor** (2 removed/theoretical).
