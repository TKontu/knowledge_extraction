# Pipeline Review: Extraction Pipeline Deep Dive (Verified)

## Flow
```
API: extraction.py:create_extraction_job
  → Job(status=QUEUED) persisted to DB
  → worker.py:ExtractionWorker.process_job picks up job
    → _has_extraction_schema() → selects pipeline:
      A) Schema path: SchemaExtractionPipeline.extract_project
         → SchemaExtractionOrchestrator.extract_all_groups
           → SmartClassifier.classify (embedding + reranker)
           → chunk_document() → SchemaExtractor.extract_field_group (per chunk, per group)
           → _merge_chunk_results() → SchemaValidator.validate()
         → Store Extraction rows, commit per 20-source chunk
      B) Generic path: ExtractionPipelineService.process_source
         → ExtractionOrchestrator.extract → LLMClient.extract_facts
         → Dedup → Embed → Qdrant upsert → Entity extraction
```

---

## Verification Notes

The following findings from the initial review were **disproved** and removed:

- ~~LLMClient sends unlimited content to LLM~~ — **NOT REAL**. `ExtractionOrchestrator.extract()` always calls `chunk_document(markdown)` before passing to `LLMClient.extract_facts`, so content is bounded by chunk size.
- ~~LLMWorker max_tokens default (4096) disagrees with config (8192)~~ — **NOT REAL**. `scheduler.py:153` explicitly passes `max_tokens=settings.llm_max_tokens` when instantiating LLMWorker. The constructor default is never used.
- ~~Hardcoded private LAN IPs as config defaults~~ — **NOT REAL in practice**. While the code defaults are LAN IPs (`config.py:73-79`), `.env` always overrides them via Pydantic Settings. This project has a single deployment target and `.env` is always present. The defaults are cosmetically ugly but never hit production.

---

## Critical (must fix)

### 1. Schema pipeline extractions are not searchable
**`src/services/extraction/pipeline.py` — SchemaExtractionPipeline vs `src/services/storage/search.py`**

The schema pipeline (used for ALL template-based projects) stores extractions in Postgres but **never generates embeddings or upserts to Qdrant**. Meanwhile, `search_knowledge()` in `search.py` queries exclusively via Qdrant vector search. This means:

- All schema-based extractions (the primary production path) are invisible to `search_knowledge()`
- The MCP tool `search_knowledge` returns zero results for schema projects
- Only the generic pipeline (rarely used — requires projects without `extraction_schema`) produces searchable extractions

This is not theoretical — every project created via templates has an extraction_schema, so this affects all real usage.

### 2. Numeric field merge always takes `max()` — wrong for most fields
**`src/services/extraction/schema_orchestrator.py:317-318`**

```python
elif field.field_type in ("integer", "float"):
    merged[field.name] = max(values)
```

When multiple chunks produce different numeric values for the same field, `max()` is always used. This is actively wrong for:
- `year_founded`: chunk 1 says 1985, chunk 2 says 2005 → picks 2005 (wrong)
- `price`: chunk 1 says $500, chunk 2 says $1200 for different contexts → picks $1200 (arbitrary)
- Any field where "most recent" or "primary" matters more than "largest"

The enum merge strategy already uses highest-confidence chunk (lines 340-351). Numeric fields should do the same. This is confirmed to run on real data — any multi-chunk source with numeric fields hits this path.

### 3. Text field merge blindly concatenates with "; "
**`src/services/extraction/schema_orchestrator.py:353-361`**

```python
else:  # text
    unique_texts = list(dict.fromkeys(str(v) for v in values if v is not None))
    if len(unique_texts) > 1:
        merged[field.name] = "; ".join(unique_texts)
```

Confirmed real: when different chunks produce different text for the same field (e.g., "company_description"), all unique values are concatenated with "; ". The dedup is exact string equality only — near-duplicates like "We build motors" and "We build electric motors" both pass through.

Real output example pattern:
> "We manufacture industrial motors; We are a leading manufacturer of electric motors for industrial applications; Our motors power the world"

For single-value text fields (description, headquarters, CEO name), this produces garbage. Should use highest-confidence value like the enum strategy does.

---

## Important (should fix)

### 4. Hardcoded "max 20 items" in entity list extraction prompt
**`src/services/extraction/schema_extractor.py:431`**

```python
- Extract ONLY the most relevant/significant items (max 20 items)
```

This is baked into every entity list extraction prompt sent to the LLM. Confirmed real impact:
- Product catalog pages with 50+ products → silently drops 60%+ of data
- Not configurable per field group or per schema
- The number 20 was chosen arbitrarily, not based on max_tokens budget or schema requirements

### 5. `EXTRACTION_CONTENT_LIMIT` captured at import time, not runtime
**`src/services/extraction/schema_extractor.py:27`**

```python
EXTRACTION_CONTENT_LIMIT = global_settings.extraction_content_limit
```

This module-level assignment captures the config value once at import time. The truncation in `_build_user_prompt` (line 475) uses this stale value:
```python
{cleaned[:EXTRACTION_CONTENT_LIMIT]}
```

Confirmed real: test fixtures that override `settings.extraction_content_limit` don't affect extraction because the module-level constant was already captured. The class has `self.settings` available but doesn't use it for truncation.

### 6. No config validation that overlap < chunk_max_tokens
**`src/services/extraction/schema_orchestrator.py:144-146`**

```python
overlap = settings.extraction_chunk_overlap_tokens     # max 1000
effective_max = settings.extraction_chunk_max_tokens - overlap  # could go negative
```

Confirmed: config allows `extraction_chunk_max_tokens=500` (min=500 in config) and `extraction_chunk_overlap_tokens=1000` (max=1000), producing `effective_max=-500`. This gets passed to `chunk_document(max_tokens=-500)`, where every paragraph becomes its own chunk since no text fits in a negative budget. No Pydantic validator cross-checks these two values.

### 7. Chunker only splits on H2 headers
**`src/services/llm/chunking.py:74`**

```python
pattern = r"(?=^## )"
sections = re.split(pattern, markdown, flags=re.MULTILINE)
```

Confirmed real: `split_by_headers` only recognizes `## ` (H2) as section boundaries. Scraped pages with H3/H4 structure (API docs, spec sheets, detailed product pages) produce single oversized sections that fall through to `split_large_section`, which splits on paragraph boundaries and loses header context.

The fallback does work — it won't crash — but produces lower-quality chunks because:
- Sub-section headers aren't preserved as chunk boundaries
- The `header_path` breadcrumb only captures headers it finds within each chunk, missing structural context

### 8. Source marked EXTRACTED even when extraction had errors (generic pipeline)
**`src/services/extraction/pipeline.py:288`**

```python
self._source_repo.update_status(source_id, SourceStatus.EXTRACTED)
```

In the generic pipeline's `process_source()`, the source is always marked EXTRACTED regardless of errors. The `errors` list (returned in `PipelineResult`) may contain failures from embedding, entity extraction, or fact processing, but the source status doesn't reflect partial failure.

**Severity note**: The generic pipeline is the fallback path (projects without extraction_schema). Most production projects use the schema pipeline. However, this path is still reachable and the behavior is objectively wrong — a source with embedding failures is indistinguishable from a fully-processed source.

### 9. `count_tokens` is `len(text) // 4` — wrong for non-English
**`src/services/llm/chunking.py:8-17`**

```python
def count_tokens(text: str) -> int:
    return len(text) // 4
```

Confirmed real concern for this system: it processes international company websites. For CJK text, 1 character ≈ 1-2 tokens (not 0.25), so this underestimates by 4-8x. A 20K-char Japanese page would be estimated at 5K tokens but actually consume 20K+ tokens, potentially exceeding Qwen3's 32K context with prompt overhead.

However, note that the EXTRACTION_CONTENT_LIMIT (20K chars) provides a safety net — content is truncated before chunking in the schema path. The risk is specifically in chunking decisions: chunks may be 4x larger than intended for non-English, but still within the model's context window due to the char-level truncation.

---

## Minor

### 10. Source validation uses N+1 queries
**`src/api/v1/extraction.py:82-93`**

```python
for source_uuid in source_uuids:
    source = source_repo.get(source_uuid)
```

Confirmed real: each source_id is individually queried. For typical batches (<50 sources) this is negligible. For large batches (500+) it's wasteful but not broken.

### 11. Entity dedup silently includes duplicates when no ID field found
**`src/services/extraction/schema_orchestrator.py:435-437`**

```python
elif not entity_id:
    # No ID field - include but can't dedupe
    all_entities.append(entity)
```

Confirmed real: entities without a matching ID field are always included. If the LLM extracts "Acme Motor X100" from 3 chunks but uses `"model"` as the key instead of one of the configured ID field names (`product_name`, `entity_id`, `name`, `id`), you get 3 duplicates. The comment acknowledges this but doesn't warn or log.

### 12. `_infer_page_type` uses simple string matching on group names
**`src/services/extraction/smart_classifier.py:544-568`**

```python
for group in groups:
    group_lower = group.lower()
    if "product" in group_lower: return "product"
    if "service" in group_lower: return "service"
```

Confirmed real but low impact: only recognizes 4 patterns (product, service, company/about, contact). Field groups like "fleet_overview", "motor_specifications", "employee_directory" all return "general". The page_type is stored on the source but is currently only informational — not used for downstream logic.

### 13. `split_by_headers` merges preamble into first section
**`src/services/llm/chunking.py:81-83`**

```python
if len(sections) > 1 and not sections[0].startswith("## "):
    sections[0] = sections[0] + "\n\n" + sections[1]
    sections.pop(1)
```

Confirmed real: a long preamble (e.g., 3000 tokens of intro text before the first H2) gets merged with the first H2 section, potentially creating an oversized section. The `split_large_section` fallback handles this gracefully but with lower quality.
