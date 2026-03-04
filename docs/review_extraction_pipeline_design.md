# Extraction Pipeline Design Review

**Date:** 2026-03-04
**Scope:** Full extraction pipeline — worker, orchestrator, pipeline, LLM client, chunking, classification, content processing, config
**Focus:** Hardcoded values, bad design, real production issues (not theoretical)
**Verified:** Every issue cross-checked against source code. False alarms removed.

---

## Critical

### 1. `source_groups` from job payload is silently dropped
**`src/services/extraction/worker.py:326-331, 359-368`**

```python
payload = job.payload or {}
project_id = payload.get("project_id")
source_ids = payload.get("source_ids")
profile_name = payload.get("profile", "general")
force = payload.get("force", False)
# source_groups is NEVER read from payload
```

The `_process_with_schema_pipeline` method (line 252) accepts `source_groups` and passes it through to the SQL query filter — but `process_job` never reads it from the payload. **Any `source_groups` filter a caller provides is silently ignored**, and extraction runs on ALL sources in the project.

**Impact:** Users filtering extraction by source group get no filtering — all sources extracted, wasting LLM tokens.

---

## High

### 2. `EXTRACTION_CONTENT_LIMIT` frozen at import time, mismatches queue worker
**`src/services/extraction/schema_extractor.py:26`**

```python
EXTRACTION_CONTENT_LIMIT = _settings_singleton.extraction_content_limit
```

Captured once at import time as a module constant. Imported by `src/services/llm/worker.py:13` and used at lines 412, 469, 471 for truncation in the queue path. The `SchemaExtractor` class correctly accepts `content_limit` via constructor (line 42), but the queue worker uses this frozen constant — creating a truncation mismatch between direct mode and queue mode.

**Impact:** Content truncation inconsistency between direct and queue-based extraction paths.

---

### 3. `extract_project` bypasses DI — reads global settings + uses legacy query API
**`src/services/extraction/pipeline.py:588, 600, 640-646`**

```python
semaphore = asyncio.Semaphore(app_settings.extraction.max_concurrent_sources)
chunk_size = app_settings.extraction.extraction_batch_size
embed_enabled = app_settings.schema_extraction_embedding_enabled and ...
```

Despite the codebase having migrated to typed config facades, this method reads directly from the global `app_settings` singleton. Also uses legacy `self._db.query(Source).filter(...)` (lines 588, 600) instead of modern `select()` + `session.execute()`. Untestable without monkeypatching.

**Impact:** Typed config facade migration is incomplete here; unit tests cannot override these values.

---

### 4. `content_selector.py` — the one file not migrated to typed config
**`src/services/extraction/content_selector.py:3, 15`**

```python
from config import settings as app_settings

def get_extraction_content(source) -> str:
    if app_settings.extraction.domain_dedup_enabled:
```

Plain function reading from global singleton. No way to inject config. Every other service was migrated to typed facades. Additionally, `source` parameter is untyped.

**Impact:** Untestable without monkeypatching; impossible to have per-project dedup toggle.

---

### 5. Orchestrator silently falls back to global singleton when config is None
**`src/services/extraction/schema_orchestrator.py:54, 58-59`**

```python
from config import settings
...
self._extraction = extraction_config or settings.extraction
self._classification = classification_config or settings.classification
```

When `None` is passed, the orchestrator uses the global singleton. `ExtractionWorker.__init__` (worker.py:74-75) defaults both to `None`. This means the DI architecture has a silent fallback hole — callers think they're injecting config but the orchestrator may ignore it.

**Impact:** Config facade injection is silently bypassed.

---

### 6. Schema embedding failures silently swallowed — returns 0, no alerting
**`src/services/extraction/embedding_pipeline.py:115-121`**

```python
except Exception as e:
    logger.warning("extraction_embedding_failed", ...)
    return 0
```

When Qdrant embedding fails, the entire batch is silently lost. The pipeline commits extractions to PostgreSQL (line 741) without their Qdrant embeddings. No retry, no status marker on affected extractions. `search_knowledge` MCP tool will silently miss these extractions forever. The caller at line 729-731 cannot distinguish "0 to embed" from "Qdrant is down."

Contrast with the generic pipeline path (pipeline.py lines 197-214) which has proper alerting via `get_alert_service()`.

**Impact:** Extractions committed to PG are invisible to semantic search with no way to discover or recover.

---

### 7. `DomainDedupService.analyze_domain` loads ALL source content into memory twice
**`src/services/extraction/domain_dedup.py:349-370`**

```python
sources = self._source_repo.get_by_project_and_domain(project_id, domain)
pages = [s.content for s in sources if s.content]
...
pages_with_uris = [(s.content, s.uri) for s in sources if s.content and s.uri]
```

Loads every source row plus full content for a domain, then duplicates content strings in `pages_with_uris`. For 500 pages at 50KB each = ~50MB in memory. No batching or streaming. ORM objects persist for the entire method.

**Impact:** Memory pressure under large domains.

---

## Medium

### 8. `temperature=0.0` treated as falsy in `LLMClient.complete()`
**`src/services/llm/client.py:707`**

```python
base_temp = temperature or self._llm.base_temperature
```

`0.0` is falsy in Python. Explicitly requesting deterministic output (`temperature=0.0`) silently falls through to the default (0.1). Should be `temperature if temperature is not None else ...`.

**Impact:** Impossible to get deterministic LLM output through the `complete()` path.

---

### 9. `_complete_via_queue` hardcodes timeout=300, ignores `self._request_timeout`
**`src/services/llm/client.py:765, 771`**

Both `_extract_facts_via_queue` and `_extract_entities_via_queue` correctly use `self._request_timeout`, but `_complete_via_queue` hardcodes `300` in both `timeout_at` and `wait_for_result`. Copy-paste oversight.

**Impact:** Timeout config is silently ignored for the `complete()` queue path.

---

### 10. Entity dedup is case-sensitive — LLM casing varies between chunks
**`src/services/extraction/schema_orchestrator.py:477-484`**

```python
entity_id = None
for id_field in self._context.entity_id_fields:
    if entity.get(id_field):
        entity_id = entity.get(id_field)
        break

if entity_id and entity_id not in seen_ids:
    seen_ids.add(entity_id)
```

- Case-sensitive: `"Acme Motor"` vs `"acme motor"` from different chunks treated as different entities
- Falsy check: `entity.get(id_field)` is falsy for `0`, `""`, `False` — these fall through to content-hash path unnecessarily

**Impact:** Missed dedup in real extraction results where LLM casing varies between chunks.

---

### 11. `total_deduplicated` and `total_entities` always 0 in schema pipeline results
**`src/services/extraction/pipeline.py:757-765`**

`SchemaPipelineResult` has `total_deduplicated: int = 0` and `total_entities: int = 0` (lines 76-77), but `extract_project` never sets them in the return statement (lines 757-765). The worker at lines 430, 465 reads these values for job results.

**Impact:** Job results always report 0 for these metrics — misleading to operators.

---

### 12. `confidence` popped from `data` dict before storage
**`src/services/extraction/schema_orchestrator.py:198-200`**

```python
group_result["data"] = merged       # line 198: assigns reference
raw_confidence = merged.pop("confidence", 0.0)  # line 200: mutates the same dict
```

Since `group_result["data"]` is a reference to `merged`, the `.pop()` removes `confidence` from the stored `data`. The extraction stored in PG has `confidence` as a separate column but it's missing from `data` JSONB. Any downstream code reading `extraction.data["confidence"]` gets a KeyError.

**Impact:** Data structure contract violation for downstream consumers.

---

### 13. Redis cache failures logged at debug level — invisible performance degradation
**`src/services/extraction/smart_classifier.py:419-425, 453-458`**

```python
except Exception as e:
    logger.debug("cache_batch_read_failed", ...)
    cached_values = [None] * len(field_groups)
```

When Redis is down, every classification re-embeds all field groups (extra latency + embedding API load). Only logged at `debug` level — likely suppressed in production (`LOG_LEVEL=INFO`). No alerts.

**Impact:** Invisible N-fold performance degradation when Redis is down.

---

### 14. Cache key ignores embedding model version
**`src/services/extraction/smart_classifier.py:462-475`**

```python
def _get_cache_key(self, group: FieldGroup) -> str:
    group_text = self._create_group_text(group)
    text_hash = hashlib.sha256(group_text.encode()).hexdigest()[:16]
    return f"{self.CACHE_KEY_PREFIX}{text_hash}"
```

Key based solely on field group text. After embedding model change (e.g., `bge-large-en` to `bge-m3`, which already happened in this project), cached embeddings from old model served until TTL expires (24h). Dimensions may differ, causing incorrect cosine similarity.

**Impact:** Stale/wrong classification for up to 24 hours after model change.

---

### 15. `ClassificationConfig` name collision — two different classes
**`src/config.py` vs `src/services/extraction/schema_adapter.py`**

Two different classes named `ClassificationConfig`:
1. `config.ClassificationConfig` — frozen dataclass for app-level settings
2. `schema_adapter.ClassificationConfig` — mutable dataclass for per-template skip patterns

Both imported in `worker.py` (line 21 via TYPE_CHECKING, line 189 at runtime), with the adapter version **shadowing** the config version inside `_create_schema_pipeline`. Works by accident today.

**Impact:** Maintenance trap — wrong class used silently on future changes.

---

### 16. No jitter in backpressure exponential backoff
**`src/services/extraction/backpressure.py:56`**

```python
wait_time = self._wait_base * (1.5**attempt)
```

Deterministic backoff. Multiple workers hitting backpressure simultaneously retry at identical times — thundering herd.

**Impact:** Retry storms under concurrent backpressure events.

---

### 17. Entity list key detection picks first list arbitrarily
**`src/services/extraction/schema_validator.py:106-110`**

```python
for key, value in data.items():
    if key not in _METADATA_KEYS and isinstance(value, list):
        entity_key = key
        break
```

Picks the first dict key that happens to be a list. If LLM returns multiple list-valued keys, only the first is treated as the entity list. No validation against field group schema. Same pattern in `schema_orchestrator.py:452-458`.

**Impact:** Wrong entity list selected when LLM returns extra list fields.

---

### 18. Missing confidence defaults to 0.0 — suppresses extractions when gating enabled
**`src/services/extraction/schema_validator.py:43`**

```python
confidence = data.get("confidence", 0.0)
```

If LLM omits the `confidence` key, it defaults to 0.0. Any positive `min_confidence` threshold suppresses these extractions. Safe default should arguably be 1.0.

**Impact:** Legitimate extractions silently dropped when confidence gating is active and LLM doesn't output confidence.

---

### 19. `analyze_project` commits internally — breaks caller-managed transaction pattern
**`src/services/extraction/domain_dedup.py:501`**

```python
self._session.commit()
```

The codebase convention is "flush, let caller commit." `analyze_domain` correctly uses `flush()` at line 440, but `analyze_project` (which calls it in a loop) commits internally. The API layer cannot roll back on error.

**Impact:** Partial work committed on failure; caller cannot manage transaction.

---

### 20. Facade properties allocate new dataclass on every access
**`src/config.py:776-925`**

Every `settings.llm`, `settings.extraction`, etc. call creates a new frozen dataclass instance with 10-17 fields. In hot paths (e.g., pipeline.py lines 640-641 accessing `app_settings.extraction` twice), this creates unnecessary GC pressure. Should use `@cached_property` or equivalent.

**Impact:** Performance overhead in tight loops (not correctness).

---

### 21. `_complete_direct` missing backoff cap and retry hint
**`src/services/llm/client.py:738-739, 710-740`**

Backoff: `self._llm.retry_backoff_min * (2 ** (attempt - 1))` — missing `min(..., backoff_max)` cap present in the other two retry loops (e.g., line 284). Also missing `LLM_RETRY_HINT` prompt append on retries (present at line 233-234). Copy-paste oversights.

**Impact:** Unbounded backoff if max_retries increased; retries less effective without hint.

---

### 22. Malformed facts silently discarded with no logging
**`src/services/llm/client.py:318-320`**

```python
except (KeyError, TypeError):
    # Skip facts with missing required fields
    continue
```

If 8 of 10 LLM-returned facts are malformed, only 2 are returned with no warning logged. No observability into extraction quality.

**Impact:** Silent data loss with no way to detect extraction quality issues.

---

## Low-Medium

### 23. `extract_header_path` ignores H4+ headers entirely
**`src/services/llm/chunking.py:103-135`**

The function handles H1, H2, and H3 headers. H4+ headers (e.g., `#### Subsection`) are simply **ignored** — they don't match any branch because `"#### ".startswith("### ")` is False (position 3 is `#` not ` `). H4+ headers are common in technical documentation but are silently dropped from breadcrumbs.

**Impact:** Incomplete breadcrumb paths for deeply nested documents. Minor context loss for LLM.

---

### 24. `DEFAULT_PROFILE` categories hardcoded for tech products
**`src/services/extraction/pipeline.py:28-34`**

Categories `["general", "features", "technical", "integration"]` are tech-product-oriented. Non-tech templates (recipes, HR, legal) get meaningless categorization. Only affects the generic (non-schema) pipeline fallback path.

---

### 25. Cancellation check hits DB on every chunk
**`src/services/extraction/worker.py:344-345`**

Database query per chunk (every 20 sources). Could cache for a few seconds.

---

### 26. `ProjectRepository` created ad-hoc inside `extract_project`
**`src/services/extraction/pipeline.py:533`**

Not injectable, requires real DB session for testing.

---

### 27. Naive singularization: `.removesuffix("s")`
**`src/services/extraction/schema_extractor.py:421`**

`"addresses"` -> `"addresse"`, `"analysis"` -> `"analysi"`. Goes directly into LLM prompts.

---

### 28. `list_extractions` MCP tool has no pagination offset
**`src/ke_mcp/tools/extraction.py:64-108`**

API supports `limit` + `offset`, but MCP tool only exposes `limit`. Users can only see first page of results.

---

### 29. Hardcoded `[:20]` list truncation in embedding text generation
**`src/services/extraction/embedding_pipeline.py:60`**

Entity lists silently truncated to first 20 items for embedding. Remaining entities unfindable via semantic search.

---

### 30. Dedup defaults (0.7/5/50) duplicated in 3+ places
**`src/services/extraction/domain_dedup.py:335-337, 98-100, 185-188`**

Same magic numbers in `analyze_domain`, `compute_domain_fingerprint`, and `compute_section_fingerprints`. Should be module-level constants.

---

### 31. `_get_tail_text` hard-cap ignores CJK token ratio
**`src/services/llm/chunking.py:58`**

Uses `max_chars = target_tokens * 4` (English ratio) for all text. CJK text at 1.5 chars/token gets 2.67x more tokens than intended in overlap sections.

---

### 32. Page type inference uses substring matching
**`src/services/extraction/page_classifier.py:184-192`**

`"production_metrics"` matches `"product"`. Only used for metadata, not control flow.

---

## Pattern: Incomplete Config Facade Migration

Issues 2, 3, 4, 5 form a systemic pattern. The typed config facade migration is **architecturally incomplete**:

| Location | Problem |
|----------|---------|
| `schema_extractor.py:26` | Module-level constant snapshot |
| `pipeline.py:640-646` | Direct global singleton reads |
| `content_selector.py:15` | Function reads global singleton |
| `schema_orchestrator.py:54-59` | Silent fallback to global singleton |
| `worker.py:74-75` | Config defaults to `None`, triggering fallbacks |

The DI architecture has holes — services appear to accept injected config but silently read globals in several code paths. This defeats testability and per-project configuration.

---

## Summary

| Severity | Count | Key themes |
|----------|-------|------------|
| **Critical** | 1 | source_groups silently dropped |
| **High** | 6 | Frozen config constant; global singleton leakage (3 locations); silent embedding failure; unbounded memory |
| **Medium** | 15 | temperature bug; timeout hardcoded; entity dedup case-sensitive; metrics always 0; confidence pop; Redis silence; cache key; name collision; no jitter; validator bugs; transaction pattern; facade allocation; copy-paste oversights; silent fact loss |
| **Low-Medium** | 10 | H4+ headers ignored; hardcoded profile; DB per chunk; ad-hoc repo; naive singularization; no pagination; list truncation; duplicated defaults; CJK ratio; substring matching |
| **Total** | **32** | |

---

## Verification Notes

All issues verified against source code on 2026-03-04. Two original findings removed:

1. **"Race condition on shared `chunk_extractions` list" — REMOVED (false alarm).** Asyncio is cooperative concurrency — `.extend()` has no `await` between getting results and appending, so no interleaving is possible. Even on free-threaded Python, `asyncio.gather` tasks run on a single event loop thread. The code is a style smell (should collect from gather return values) but is not a race condition.

2. **"Entity dedup false collisions across entity types" — CORRECTED.** `_merge_entity_lists` is called per-group in `extract_group`, never across groups. The flat set concern was wrong. Case-sensitivity and falsy-check concerns remain valid.

3. **"H4+ headers misclassified as H3" — CORRECTED.** `"#### Deep".startswith("### ")` is False (position 3 is `#` not ` `). H4+ headers are simply **ignored entirely**, not misclassified. Downgraded from Medium to Low-Medium.
