# Extraction-Grounding-Consolidation Pipeline Architecture

**Generated from code review, 2026-03-07. Source of truth: the code, not this document.**

---

## Overview

The pipeline transforms raw web content into consolidated, grounded knowledge records. It has four major phases:

```
Sources (scraped pages)
    |
    v
[1. EXTRACTION] -- LLM extracts structured data per field group per chunk
    |
    v
[2. INLINE GROUNDING] -- String-match verifies quotes against source content
    |
    v
[3. EMBEDDING] -- Extractions vectorized and stored in Qdrant
    |
    v
[4. CONSOLIDATION] -- N extractions per entity merged into 1 record with provenance
```

---

## Phase 1: Extraction

### Entry Point

**API**: `POST /api/v1/projects/{project_id}/extract` (`src/api/v1/extraction.py:38`)

Creates a `Job` record with `type=extract`, `status=queued`, payload containing `project_id`, `source_ids`, `force`, `source_groups`. Returns immediately with `job_id`.

**Background**: `JobScheduler._run_extract_worker()` (`src/services/scraper/scheduler.py:305`) polls for queued extract jobs using `SELECT FOR UPDATE SKIP LOCKED` and dispatches to `ExtractionWorker.process_job()`.

### ExtractionWorker (`src/services/extraction/worker.py`)

The worker is the top-level coordinator for a single extraction job:

1. Marks job as `RUNNING`
2. Loads project for classification config
3. Creates a `SchemaExtractionPipeline` with all dependencies (LLM config, embedding service, classification config)
4. Calls `pipeline.extract_project()` with cancellation check (time-throttled, 5s minimum between DB checks) and checkpoint callback
5. Updates job with results or error

### SchemaExtractionPipeline (`src/services/extraction/pipeline.py`)

Manages the project-level extraction flow:

1. **Load project schema** via `ProjectRepository.get()` -- falls back to `DEFAULT_EXTRACTION_TEMPLATE` if missing
2. **Validate schema** via `SchemaAdapter.validate_extraction_schema()` -- checks field types, duplicates, limits (max 20 groups, 30 fields/group)
3. **Convert to FieldGroups** via `SchemaAdapter.convert_to_field_groups()` -- produces `FieldGroup` dataclass objects with `FieldDefinition` children
4. **Query sources** -- filters by `status IN (ready, pending)` with content not null; when `force=True`, includes `extracted` status
5. **Process in batched chunks** with concurrency semaphore (`max_concurrent_sources`):
   - Commits after each batch for durability
   - Calls checkpoint callback to persist progress in job payload
   - Supports resume from checkpoint (skips already-processed source IDs)
   - Checks for cancellation between batches

### Per-Source Extraction (`pipeline.py:extract_source`)

For each source:

1. **Content selection** (`content_selector.py:get_extraction_content`): Uses `cleaned_content` (domain-deduped) when available and enabled, otherwise raw `content`
2. **Orchestrator dispatch**: Calls `SchemaExtractionOrchestrator.extract_all_groups()`
3. **Store classification** on source ORM object (`page_type`, `relevant_field_groups`, `classification_method`, `classification_confidence`)
4. **Create Extraction ORM records** -- one per field group result, with `data`, `extraction_type`, `source_group`, `confidence`, `grounding_scores`, `profile_used`
5. **Flush** to assign IDs (needed for embedding step)

### SchemaExtractionOrchestrator (`src/services/extraction/schema_orchestrator.py`)

Manages classification, chunking, and field group extraction for a single source:

#### Step 1: Page Classification (optional)

If classification is enabled and URL is available:

- **Smart classifier** (`SmartClassifier`): Embedding-based semantic similarity (uses bge-m3 + Redis cache)
- **Rule-based fallback** (`PageClassifier`): URL pattern matching against skip patterns (careers, legal, login, etc.) and optional field group patterns
- If classification says `skip_extraction=True` and `skip_enabled=True`: returns empty results
- If classification identifies `relevant_groups`: filters field groups to only those

#### Step 2: Chunking

`chunk_document()` (`src/services/llm/chunking.py:245`):

- Splits markdown on H2+ headers (`split_by_headers`)
- Merges small sections until `max_tokens` budget reached (default 5000 tokens ~20K chars)
- Splits oversized sections by paragraphs, then by words as last resort
- Optional overlap: prepends paragraph-aligned tail of previous chunk
- CJK-aware token counting (1.5 chars/token for CJK vs 4 chars/token for Latin)

#### Step 3: Parallel Field Group Extraction

All field groups run in parallel via `asyncio.gather`. For each field group:

1. **Chunk extraction** with semaphore-controlled concurrency (`max_concurrent_chunks`):
   - `SchemaExtractor.extract_field_group()` -- LLM call per chunk
   - **Inline source grounding** after each chunk: `compute_chunk_grounding()` + `compute_chunk_grounding_entities()` verify quotes against chunk content
   - **Source quoting verification**: If `source_grounding_ratio` < threshold, retries with stricter quoting prompt. Keeps better result.

2. **Merge chunk results** (`_merge_chunk_results`):
   - Per-field merge strategies based on field type (overridable per field):
     - `boolean` -> `majority_vote` (actually any-true at chunk level -- see code comment)
     - `integer/float/enum/text` -> `highest_confidence`
     - `list` -> `merge_dedupe` (flatten + deduplicate)
   - Entity lists: merge all entities, deduplicate by ID fields (case-insensitive) or content hash
   - Confidence: average across chunks (skips chunks without confidence to avoid dilution)
   - Quotes: keeps quote from highest-confidence chunk per field
   - Conflict detection: records disagreements between chunks in `_conflicts`
   - **Grounding scores propagation**: for each field, takes the score from the chunk whose quote was selected

3. **Schema validation** (`SchemaValidator`): type coercion, enum validation, list wrapping, confidence gating
4. **Empty result detection**: if <20% of fields populated, caps confidence at 0.1

### SchemaExtractor (`src/services/extraction/schema_extractor.py`)

The LLM caller. Two modes:

- **Direct mode**: Calls vLLM API directly via OpenAI-compatible client
- **Queue mode**: Submits to Redis-backed `LLMRequestQueue`

For direct mode:
1. Builds system prompt with field specs, quoting instructions, extraction rules
2. Builds user prompt: strips structural junk (`content_cleaner.strip_structural_junk` -- Layer 1 only), truncates to `content_limit` (20K chars)
3. Calls LLM with `response_format={"type": "json_object"}`, `temperature=0.0` (incremented on retries)
4. JSON repair via `try_repair_json` for malformed responses
5. Retries with exponential backoff + temperature variation (avoids same failure mode)
6. Handles truncation (`finish_reason="length"`): for entity lists, returns empty with `_truncated=True`

**Prompt structure**:
- System: "You are extracting {description} from {source_type}" + field specs + quoting instructions + rules
- User: "{source_label}: {source_context}\n\nExtract {group_name} from ONLY the content below:\n---\n{content}\n---"

### Content Cleaning (`src/services/extraction/content_cleaner.py`)

Two layers:
- **Layer 1** (`strip_structural_junk`): Universal safe patterns -- empty-alt images, skip-to-content links, bare nav links, bare images. Used for LLM input.
- **Layer 2** (`clean_markdown_for_embedding`): Layer 1 + line-density windowing (finds where real content begins by looking for consecutive low-link-density lines). Used only for classification/embedding input.

---

## Phase 2: Inline Grounding (`src/services/extraction/grounding.py`)

Grounding happens **during extraction**, not as a separate step. Two layers:

### Layer A: Quote-vs-Source Verification (in orchestrator)

After each chunk extraction, the orchestrator computes:

- `compute_chunk_grounding(result, chunk_content)`: For field group results -- verifies each field's `_quotes` string exists in the chunk content
- `compute_chunk_grounding_entities(result, chunk_content)`: For entity lists -- verifies each entity's `_quote` string exists in chunk content

These scores flow through merge and are stored as `Extraction.grounding_scores` in the DB.

### Layer B: Value-vs-Quote Verification (backfill)

`compute_grounding_scores(data, field_types)`: Verifies extracted **values** against their **quotes**:
- Numeric: `verify_numeric_in_quote` -- handles international formats (1,000 / 1.000 / 1 000)
- String: `verify_string_in_quote` -- normalized substring, stripped punctuation, multi-word partial
- List: `verify_list_items_in_quote` -- fraction of items found in quote

**Grounding modes** per field type:
- `required` (string, integer, float, enum, list): Must be verifiable
- `semantic` (boolean): Skip string-match, defer to LLM
- `none` (text): Not grounded (synthesized content)

### Quote-in-Source Verification (`verify_quote_in_source`)

Multi-tier matching with increasing leniency:
1. Normalized substring (lowercase, collapsed whitespace) -> 1.0
2. Punctuation-stripped substring -> 0.95
3. Word-level sliding window -> best overlap ratio (0.0-1.0)

Threshold: >= 0.8 means quote is source-grounded.

### LLM Grounding Verification (`src/services/extraction/llm_grounding.py`)

`LLMGroundingVerifier`: For fields where string-match scored 0.0 but a quote exists:
- Asks LLM: "Does this quote support this claimed value?"
- Handles multilingual quotes, paraphrases
- Skips boolean fields (35% false rejection rate)
- Updates score to 1.0 (verified) or keeps at 0.0 (rejected)

**Not called inline** -- available via backfill script (`scripts/backfill_grounding_scores.py --llm`) or API endpoint (`POST /projects/{id}/backfill-grounding`).

---

## Phase 3: Embedding (`src/services/extraction/embedding_pipeline.py`)

Happens **per batch** during extraction (after flush, before commit):

1. `ExtractionEmbeddingService.extraction_to_text()`: Converts extraction data to embeddable text (strips `_`-prefixed metadata keys and `confidence`)
2. `EmbeddingService.embed_batch()`: Calls bge-m3 on 192.168.0.136:9003
3. `QdrantRepository.upsert_batch()`: Stores vectors with metadata payload (`project_id`, `source_id`, `source_group`, `extraction_type`)
4. Marks extractions as `embedded=True`

Only runs when `schema_embedding_enabled=True` and `ExtractionEmbeddingService` is available.

---

## Phase 4: Consolidation (`src/services/extraction/consolidation.py` + `consolidation_service.py`)

**Not part of the extraction pipeline.** Triggered separately via:
- API: `POST /projects/{project_id}/consolidate` (`src/api/v1/projects.py:399`)
- With optional `source_group` query param for single-group consolidation

### ConsolidationService (`src/services/extraction/consolidation_service.py`)

DB orchestration layer:

1. **Get distinct source groups** from extractions table
2. **Per source group** (with SAVEPOINT isolation -- failure in one group doesn't roll back others):
   a. Delete existing consolidated records for this source group (prevents stale rows)
   b. Load all extractions for `(project_id, source_group)`
   c. Group by `extraction_type`
   d. Load field definitions from project schema
   e. Call `consolidate_extractions()` for each type
   f. Upsert to `consolidated_extractions` table (PostgreSQL `ON CONFLICT DO UPDATE`)

### Consolidation Strategies (`src/services/extraction/consolidation.py`)

Pure functions that merge N extractions into 1 record. Strategy defaults by field type:

| Field Type | Default Strategy | Description |
|-----------|-----------------|-------------|
| `string` | `frequency` | Most frequent value, case-insensitive, ties broken by total weight |
| `integer` | `weighted_median` | Weighted median excluding zero-weight values |
| `float` | `weighted_median` | Same as integer |
| `boolean` | `any_true` | True if 3+ weighted-True values; False if all False |
| `text` | `longest_top_k` | Longest string from top-3 by weight |
| `list` | `union_dedup` | Flatten + deduplicate by normalized name |
| `enum` | `frequency` | Same as string |

Additional strategy: `weighted_frequency` -- sum weights per unique value, pick highest.

### Weight Calculation

```
effective_weight(confidence, grounding_score, grounding_mode):
    if grounding_mode == "required":
        return confidence * max(grounding_score, 0.1)  # floor of 0.1
    else:
        return confidence  # semantic/none: grounding doesn't affect weight
```

The 0.1 floor ensures ungrounded data still contributes when nothing better exists.

### Entity List Consolidation

For `is_entity_list=True` field groups:
- Collects entity lists from all extractions
- Weights each extraction's entities by `confidence * max(grounding_score, 0.1)`
- Strips `_quote` metadata from entities
- Applies `union_dedup`: deduplicates by entity name (case-insensitive), merges attributes across duplicates (first occurrence is canonical, later ones fill gaps)

### Output: ConsolidatedExtraction

Stored in `consolidated_extractions` table with:
- `data`: Consolidated field values (one value per field)
- `provenance`: Per-field metadata -- strategy used, source_count, grounded_count, agreement ratio, top_sources
- `source_count`: Total extractions that contributed
- `grounded_count`: Max grounded count across fields
- Unique constraint on `(project_id, source_group, extraction_type)`

---

## Data Model

### Key Tables

| Table | Purpose | Key Columns |
|-------|---------|-------------|
| `projects` | Configuration | `extraction_schema` (JSONB with field_groups) |
| `sources` | Scraped pages | `content`, `cleaned_content`, `source_group`, `status`, `page_type` |
| `extractions` | Raw per-source results | `data` (JSONB), `grounding_scores` (JSONB), `confidence`, `extraction_type`, `source_group` |
| `consolidated_extractions` | Merged per-entity results | `data`, `provenance`, `source_count`, `grounded_count` |
| `domain_boilerplate` | Per-domain fingerprints | `boilerplate_hashes`, threshold params |

### Schema Structure (project.extraction_schema)

```json
{
  "name": "drivetrain_companies",
  "field_groups": [
    {
      "name": "company_info",
      "description": "Basic company information",
      "fields": [
        {
          "name": "company_name",
          "field_type": "string",
          "description": "Official company name",
          "required": true,
          "default": null,
          "merge_strategy": null,
          "grounding_mode": null,
          "consolidation_strategy": null
        }
      ],
      "prompt_hint": "Look for...",
      "is_entity_list": false,
      "max_items": null
    },
    {
      "name": "products",
      "description": "Products manufactured",
      "fields": [...],
      "is_entity_list": true,
      "max_items": 20
    }
  ],
  "classification_config": {
    "skip_patterns": ["/careers", "/legal"]
  }
}
```

---

## Execution Flow Summary

```
API Request (POST /projects/{id}/extract)
  |
  v
Job queued in DB (type=extract, status=queued)
  |
  v
JobScheduler polls -> ExtractionWorker.process_job(job)
  |
  v
ExtractionWorker creates SchemaExtractionPipeline
  |
  v
pipeline.extract_project(project_id)
  |-- Load project + validate schema
  |-- Query sources (ready/pending with content)
  |-- For each batch of sources:
  |     |
  |     |-- For each source (parallel, semaphore-limited):
  |     |     |
  |     |     |-- get_extraction_content() -> cleaned_content or content
  |     |     |
  |     |     |-- orchestrator.extract_all_groups()
  |     |     |     |
  |     |     |     |-- Classify page (smart/rule-based) -> skip or filter groups
  |     |     |     |-- chunk_document() -> N chunks
  |     |     |     |
  |     |     |     |-- For each field group (parallel):
  |     |     |     |     |
  |     |     |     |     |-- For each chunk (semaphore-limited):
  |     |     |     |     |     |-- SchemaExtractor.extract_field_group() -> LLM call
  |     |     |     |     |     |-- compute_chunk_grounding() -> per-field scores
  |     |     |     |     |     |-- Source grounding check -> retry if too many fabricated quotes
  |     |     |     |     |
  |     |     |     |     |-- _merge_chunk_results() -> merged data + grounding_scores
  |     |     |     |     |-- SchemaValidator.validate() -> type coercion
  |     |     |     |     |-- _is_empty_result() -> cap confidence if empty
  |     |     |
  |     |     |-- Create Extraction ORM records (one per field group)
  |     |     |-- Update source.status = EXTRACTED/SKIPPED
  |     |
  |     |-- DB flush (assign extraction IDs)
  |     |-- Embed extractions -> Qdrant (if enabled)
  |     |-- Checkpoint callback (update job.payload)
  |     |-- DB commit
  |
  v
Job marked COMPLETED/FAILED

--- Later, triggered separately ---

POST /projects/{id}/consolidate
  |
  v
ConsolidationService.consolidate_project()
  |-- For each source_group (with SAVEPOINT):
  |     |-- Load all extractions for (project_id, source_group)
  |     |-- Group by extraction_type
  |     |-- consolidate_extractions() per type
  |     |     |-- Build WeightedValues (confidence * grounding)
  |     |     |-- Apply strategy per field (frequency/weighted_median/any_true/etc.)
  |     |     |-- Compute agreement ratio
  |     |-- Upsert ConsolidatedExtraction (data + provenance)
```

---

## Key Configuration (from config.py facades)

| Setting | Default | Purpose |
|---------|---------|---------|
| `extraction.content_limit` | 20000 | Max chars sent to LLM per chunk |
| `extraction.chunk_max_tokens` | 5000 | Max tokens per chunk (~20K chars) |
| `extraction.chunk_overlap_tokens` | 0 | Overlap between chunks |
| `extraction.max_concurrent_sources` | semaphore | Parallel sources per batch |
| `extraction.max_concurrent_chunks` | semaphore | Parallel chunks per field group |
| `extraction.extraction_batch_size` | configurable | Sources per commit batch |
| `extraction.source_quoting_enabled` | True | Include _quotes in LLM output |
| `extraction.source_grounding_min_ratio` | threshold | Min quote grounding before retry |
| `extraction.domain_dedup_enabled` | True | Use cleaned_content over raw |
| `extraction.schema_embedding_enabled` | configurable | Embed extractions to Qdrant |
| `extraction.validation_enabled` | configurable | Run SchemaValidator |
| `classification.enabled` | configurable | Enable page classification |
| `classification.smart_enabled` | configurable | Use embedding-based classifier |
| `classification.skip_enabled` | configurable | Actually skip classified pages |
| `llm.model` | gemma3-12b-awq | Extraction model |
| `llm.base_temperature` | 0.0 | Temperature (incremented on retries) |
| `llm.max_retries` | configurable | LLM retry attempts |
