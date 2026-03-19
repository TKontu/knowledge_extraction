# Extraction & Grounding Pipeline -- Detailed Process Reference

**Generated from code review, 2026-03-07. Source of truth: the code, not this document.**

This document provides a fine-grained walkthrough of how a single source page goes from raw markdown to a grounded, validated extraction record. It covers every decision point, configuration variable, data transformation, and code path.

---

## Table of Contents

1. [Per-Source Extraction Entry](#1-per-source-extraction-entry)
2. [Content Selection & Domain Dedup](#2-content-selection--domain-dedup)
3. [Page Classification](#3-page-classification)
4. [Document Chunking](#4-document-chunking)
5. [LLM Extraction (Per Chunk)](#5-llm-extraction-per-chunk)
6. [Inline Grounding (Per Chunk)](#6-inline-grounding-per-chunk)
7. [Source Grounding Verification & Retry](#7-source-grounding-verification--retry)
8. [Chunk Merge](#8-chunk-merge)
9. [Schema Validation](#9-schema-validation)
10. [Empty Result Detection](#10-empty-result-detection)
11. [Extraction Record Creation](#11-extraction-record-creation)
12. [Grounding Backfill (Post-Extraction)](#12-grounding-backfill-post-extraction)
13. [Configuration Reference](#13-configuration-reference)

---

## 1. Per-Source Extraction Entry

**Code**: `SchemaExtractionPipeline.extract_source()` (`src/services/extraction/pipeline.py:68`)

This method is called once per source inside `extract_project()`'s batched parallel loop. The caller controls concurrency via `asyncio.Semaphore(max_concurrent_sources)`.

**Inputs**:
- `source`: Source ORM object (has `.content`, `.cleaned_content`, `.uri`, `.title`, `.source_group`, `.project_id`)
- `source_context`: String identifier (typically `source.source_group`, e.g. a company name)
- `field_groups`: Pre-converted `FieldGroup` objects from the project schema (computed once per project, reused for all sources)
- `schema_name`: Name string from `extraction_schema.name`

**Guards**:
- Returns `[]` if `source.content` is None or empty
- Returns `[]` if `field_groups` is empty (with error log)

**Output**: List of `Extraction` ORM objects (one per field group), flushed but not committed.

---

## 2. Content Selection & Domain Dedup

**Code**: `get_extraction_content()` (`src/services/extraction/content_selector.py:4`)

```python
def get_extraction_content(source, *, domain_dedup_enabled: bool = True) -> str:
    if domain_dedup_enabled:
        return source.cleaned_content if source.cleaned_content is not None else source.content
    return source.content
```

**Decision**: When `domain_dedup_enabled=True` (default), the pipeline uses `cleaned_content` -- a version of the page with domain-level boilerplate removed. Falls back to raw `content` if `cleaned_content` hasn't been computed yet.

### How `cleaned_content` is Produced (Prerequisite Step)

**Code**: `DomainDedupService` (`src/services/extraction/domain_dedup.py`)

This is a separate analysis step triggered via API (`POST /projects/{id}/analyze-boilerplate`) *before* extraction:

1. **Block splitting**: Content split on double-newlines, blocks < 50 chars discarded
2. **Hashing**: Each block whitespace-normalized + lowercased, then SHA-256 hashed (16 hex char prefix)
3. **Frequency counting**: Two passes:
   - Pass 1 (domain-level): Blocks appearing on >= 70% of pages are boilerplate
   - Pass 2 (section-level): Groups pages by URL path prefix (depth=1), applies same threshold with floor=3
4. **Cleaning**: Removes boilerplate blocks from each source, stores result in `Source.cleaned_content`

**Config**:

| Setting | Default | Config Key |
|---------|---------|------------|
| Threshold percentage | 70% | `domain_dedup_threshold_pct` |
| Minimum pages for analysis | 5 | `domain_dedup_min_pages` |
| Minimum block chars | 50 | `domain_dedup_min_block_chars` |
| Feature enabled | True | `domain_dedup_enabled` |

---

## 3. Page Classification

**Code**: `SchemaExtractionOrchestrator.extract_all_groups()` (`src/services/extraction/schema_orchestrator.py:143`, lines 177-226)

Classification determines *which field groups to extract* and *whether to skip the page entirely*. It runs only when `classification.enabled=True` AND a URL is available.

### Decision Tree

```
Is classification enabled?  ──No──>  Use all field groups
        |
       Yes
        |
Is smart_classification enabled AND SmartClassifier available?
        |                    |
       Yes                  No
        |                    |
  SmartClassifier        PageClassifier (rule-based)
        |                    |
        v                    v
  ClassificationResult  ClassificationResult
        |
  skip_extraction=True AND skip_enabled=True?  ──Yes──>  Return [], skip source
        |
       No
        |
  relevant_groups non-empty?
        |           |
       Yes         No
        |           |
  Filter field    Use all
  groups          groups
```

### Rule-Based Classification (`PageClassifier`)

**Code**: `src/services/extraction/page_classifier.py:31`

Matches URL against skip patterns (regex). Default skip patterns:
- `/career|/job|/employ|/vacanc|/recruiting|/openings`
- `/privacy|/terms|/legal|/cookie|/gdpr|/imprint|/impressum`
- `/login|/account|/cart|/checkout|/register|/signup`
- `/sitemap|/search|/404|/error|/tag/|/category/|/author/`
- `/event-calendar|/webinar-registration|/trade-show`

Customizable via `classification_config.skip_patterns` in project schema (can be `[]` to disable all skip patterns).

### Smart Classification (`SmartClassifier`)

**Code**: `src/services/extraction/smart_classifier.py:38`

Three-tier flow:

**Tier 1 -- Rule-based skip check** (fast path):
- Runs `PageClassifier.classify()` with resolved skip patterns first
- If skip: return immediately (no embedding work)

**Tier 2 -- Embedding similarity**:
1. Create text representation of each field group: `"{name}: {description}\n\nFields:\n- {field_name}: {field_description}\n..."`
2. Embed field group texts (cached in Redis with TTL, keyed by SHA-256 hash of `{model}:{text}`)
3. Create page summary: `"Title: {title}\nURL: {url}\n\n{cleaned_truncated_content}"`
4. Embed page summary
5. Compute cosine similarity between page and each field group

**Decision thresholds**:

| Score Range | Action | Config Key |
|-------------|--------|------------|
| >= 0.75 (high) | Use matched groups directly | `classification_embedding_high_threshold` |
| 0.40-0.75 (medium) | Send to reranker for confirmation | between high and low |
| < 0.40 (low) | Use top groups within 80% of top score (min 2) | `classification_embedding_low_threshold` |

**Tier 3 -- Reranker confirmation** (medium confidence only):
1. Clean content via Layer 2 (`clean_markdown_for_embedding`)
2. Truncate to `classification_content_limit` (default 6000 chars) at word boundary
3. Call `EmbeddingService.rerank()` with bge-reranker-v2-m3
4. Groups with reranker score >= `classification_reranker_threshold` (0.5) are confirmed
5. If no groups above threshold: use dynamic threshold (top 80% of scores, minimum 2 groups)
6. Falls back to embedding scores if reranker fails

**Config**:

| Setting | Default | Config Key |
|---------|---------|------------|
| Classification enabled | True | `classification_enabled` |
| Skip enabled | True | `classification_skip_enabled` |
| Smart classification | True | `smart_classification_enabled` |
| Embedding high threshold | 0.75 | `classification_embedding_high_threshold` |
| Embedding low threshold | 0.40 | `classification_embedding_low_threshold` |
| Reranker threshold | 0.50 | `classification_reranker_threshold` |
| Reranker model | bge-reranker-v2-m3 | `reranker_model` |
| Content limit for classifier | 6000 chars | `classification_content_limit` |
| Cache TTL | 86400s (24h) | `classification_cache_ttl` |
| Use default skip patterns | True | `classification_use_default_skip_patterns` |

### Classification Output

Stored on Source ORM object:
- `source.page_type`: "skip", "product", "service", "about", "general", etc.
- `source.relevant_field_groups`: List of matched group names (JSON)
- `source.classification_method`: "rule" or "smart"
- `source.classification_confidence`: 0.0-1.0

---

## 4. Document Chunking

**Code**: `chunk_document()` (`src/services/llm/chunking.py:245`)

Called by the orchestrator after classification filtering. The full source content is chunked for processing by the LLM.

### Chunking Algorithm

1. **Split on H2+ headers** (`split_by_headers`): Regex `(?=^#{2,} )` splits before any header level >= 2. H1 is NOT a split point (it's the page title).

2. **Merge small sections**: Iterate sections, accumulate into a chunk until adding the next section would exceed `max_tokens`. Start a new chunk when budget exceeded.

3. **Split oversized sections** (`split_large_section`): If a single section exceeds `max_tokens`:
   - Extract header (if starts with `#`), reduce budget by header size
   - Split by double-newlines (paragraphs)
   - If a single paragraph exceeds budget, split by words
   - Prepend section header to every sub-chunk

4. **Apply overlap** (if `overlap_tokens > 0`): Each chunk after the first gets the paragraph-aligned tail of the previous chunk prepended. Tail selection: takes whole paragraphs from the end until token budget reached, hard-caps at `max_chars`.

### Token Counting

**Code**: `count_tokens()` (`src/services/llm/chunking.py:8`)

CJK-aware approximation:
- Latin/English: `len(text) // 4` (~4 chars per token)
- CJK characters: `cjk_count / 1.5` (~1.5 chars per token)
- Detects CJK by Unicode code point ranges (Unified Ideographs, Hiragana, Katakana, Hangul)

### Output

List of `DocumentChunk` objects:
```python
DocumentChunk(
    content: str,        # Chunk text
    chunk_index: int,    # 0-based
    total_chunks: int,   # Total chunks in document
    header_path: list[str]  # Breadcrumb headers found in chunk
)
```

**Config**:

| Setting | Default | Config Key | Notes |
|---------|---------|------------|-------|
| Max tokens per chunk | 5000 | `extraction_chunk_max_tokens` | ~20K chars. The orchestrator subtracts `overlap_tokens` from this to compute `effective_max`. |
| Overlap tokens | 200 | `extraction_chunk_overlap_tokens` | Paragraph-aligned overlap prepended to subsequent chunks. 0=disabled. |

**Important arithmetic**: The orchestrator (`schema_orchestrator.py:233-238`) calculates:
```python
overlap = self._extraction.chunk_overlap_tokens       # 200
effective_max = self._extraction.chunk_max_tokens - overlap  # 5000 - 200 = 4800
chunks = chunk_document(markdown, max_tokens=effective_max, overlap_tokens=overlap)
```
So the actual chunk size passed to `chunk_document` is `chunk_max_tokens - chunk_overlap_tokens`.

---

## 5. LLM Extraction (Per Chunk)

**Code**: `SchemaExtractor.extract_field_group()` (`src/services/extraction/schema_extractor.py:96`)

This is the core LLM call. Called once per (chunk, field_group) pair. The orchestrator runs all chunks for a field group concurrently via semaphore.

### Concurrency Model

```
For each field group (parallel via asyncio.gather):
    semaphore = Semaphore(max_concurrent_chunks)  # default 80
    For each chunk (all launched immediately, semaphore limits concurrency):
        async with semaphore:
            result = await extractor.extract_field_group(chunk.content, group, ...)
```

This "continuous flow" model keeps the vLLM KV cache utilized -- new requests start as old ones complete, rather than batch-and-wait.

### Prompt Construction

**System prompt** (for regular field groups, `_build_system_prompt`, line 379):
```
You are extracting {field_group.description} from {source_type}.

Fields to extract:
- "{field.name}" ({field.field_type}): {field.description} [options: ...] [REQUIRED]
...

{field_group.prompt_hint}

RULES:
- Extract ONLY from the content provided below. Do NOT use outside knowledge.
- If the content does not contain information for a field, return null.
- If the content is not relevant to {description}, return null for ALL fields.
- For boolean fields, return true ONLY if there is explicit evidence. Default to false.
- For list fields, return empty list [] if no items found.

Output JSON with exactly these fields and a "confidence" field (0.0-1.0):
- 0.0 if the content has no relevant information
- 0.5-0.7 if only partial information found
- 0.8-1.0 if the content is clearly relevant with good data

Include a "_quotes" object mapping each non-null field to a brief verbatim excerpt
(15-50 chars) from the source that supports the value.
```

**System prompt** (for entity lists, `_build_entity_list_system_prompt`, line 436):
```
You are extracting {description} from {source_type}.

For each {entity_singular} found, extract:
- "{field.name}" ({field.field_type}): {field.description}
...

IMPORTANT RULES:
- Extract ONLY from the content provided below. Do NOT use outside knowledge.
- Extract ONLY the most relevant/significant items (max {max_items} items)
- If this content does not contain any {entity_singular} information, return empty list.
- Skip generic lists that are just navigation or coverage info.

Output JSON with structure:
{
  "{group_name}": [
    {"{id_field}": "...", ...},
    ...
  ],
  "confidence": 0.0-1.0
}

For each entity, include a "_quote" field with a brief verbatim excerpt (15-50 chars)
from the source that identifies this entity.
```

**User prompt** (`_build_user_prompt`, line 526):
```
{source_label}: {source_context}

Extract {field_group.name} information from ONLY the content below:

---
{cleaned_content[:content_limit]}
---
```

Content is first cleaned via `strip_structural_junk()` (Layer 1 only -- removes empty-alt images, bare nav links, skip-to-content links, bare images), then truncated to `content_limit`.

### Strict Quoting Mode

When source grounding verification triggers a retry (see Section 7), the system prompt changes:
```
CRITICAL QUOTING REQUIREMENT:
Include a "_quotes" object mapping each non-null field to an EXACT verbatim excerpt
(15-50 chars) copied directly from the source text.
The quote MUST appear word-for-word in the source content. Do NOT paraphrase, translate,
or fabricate quotes.
If you cannot find an exact quote in the source for a field, set that field to null
rather than inventing a quote.
```

### LLM Call Parameters

| Parameter | Value | Notes |
|-----------|-------|-------|
| `model` | `Qwen3-30B-A3B-it-4bit` | From `LLM_MODEL` env var |
| `response_format` | `{"type": "json_object"}` | Forces JSON output |
| `temperature` | 0.1 (attempt 1) | `llm_base_temperature` + `(attempt-1) * retry_temperature_increment` |
| `max_tokens` | 8192 | `llm_max_tokens` |
| Content limit | 20000 chars | `extraction_content_limit` |
| HTTP timeout | 120s | `llm_http_timeout` |
| API URL | `http://192.168.0.247:9003/v1` | `OPENAI_BASE_URL` env var |

### Retry Strategy

On failure, retries with exponential backoff + temperature variation:

```python
for attempt in range(1, max_retries + 1):        # max_retries default: 3
    temperature = base_temp + (attempt - 1) * temp_increment  # 0.1, 0.15, 0.2
    wait_time = min(backoff_min * (2 ** (attempt - 1)), backoff_max)  # 2s, 4s (capped at 30s)
    if attempt > 1:
        system_prompt += "\n\nIMPORTANT: Be concise. Output valid JSON only."
```

Temperature variation on retries avoids getting stuck in the same hallucination loop.

### Truncation Handling

When `finish_reason == "length"` (LLM hit `max_tokens`):
- **Entity lists**: Try `try_repair_json()`. If repair fails, return `{group_name: [], confidence: 0.0, _truncated: True}`. The `_truncated` flag propagates through merge and is recorded on the Extraction's `chunk_context`.
- **Regular fields**: Try `try_repair_json()`. If repair succeeds, continue normally.

### JSON Repair (`try_repair_json`)

**Code**: `src/services/llm/json_repair.py:23`

Repair strategies applied in order:
1. Direct parse (fast path for valid JSON)
2. Strip markdown code fences (` ```json ... ``` `)
3. Fix unterminated strings
4. Balance braces/brackets
5. Remove trailing commas
6. Fix single quotes to double quotes

### Default Application

After successful parse, `_apply_defaults()` fills in missing fields:
- Fields with explicit `default` value: use that default
- Boolean fields without default: `False`
- List fields without default: `[]`

---

## 6. Inline Grounding (Per Chunk)

**Code**: `extract_chunk_with_semaphore()` inside `_extract_chunks_batched()` (`schema_orchestrator.py:329-405`)

Immediately after each chunk's LLM call returns, two grounding functions run:

### 6a. Field Group Grounding (`compute_chunk_grounding`)

**Code**: `grounding.py:540`

For each field in the result's `_quotes` dict:
1. Coerce quote to string (handles LLM returning lists/dicts/ints as quotes)
2. Call `verify_quote_in_source(quote, chunk_content)`:

**Multi-tier matching** (`grounding.py:441`):
- **Tier 1**: Normalized substring match (lowercase, collapse whitespace) -> score 1.0
- **Tier 2**: Strip all punctuation + retry substring -> score 0.95
- **Tier 3**: Word-level sliding window:
  - Split both quote and content into words
  - Slide an N-word window (N = quote word count) across content
  - At each position, count what fraction of quote words appear in window
  - Return best ratio found (0.0-1.0)
  - Optimization: Only recounts when entering/leaving words are in the quote set

Result stored as `result["_source_grounding"] = {field_name: score, ...}`

### 6b. Entity List Grounding (`compute_chunk_grounding_entities`)

**Code**: `grounding.py:571`

For each entity list in the result (identified by being a `list` value with non-metadata key):
1. For each entity dict, get its `_quote` value
2. Call `verify_quote_in_source(quote, chunk_content)` per entity
3. Average all entity scores -> single score for the entity list key

Result merged into `result["_source_grounding"]` alongside field scores.

---

## 7. Source Grounding Verification & Retry

**Code**: `extract_chunk_with_semaphore()` (`schema_orchestrator.py:361-405`)

After inline grounding, if `source_quoting_enabled=True`, the orchestrator checks whether the overall extraction has acceptable source grounding:

### Grounding Ratio Calculation

**Code**: `_source_grounding_ratio()` (`schema_orchestrator.py:81`)

1. Collect all quotes from the result (both field-level `_quotes` and entity-level `_quote`)
2. For each quote, call `verify_quote_in_source(quote, chunk_content)`
3. Count quotes with score >= `_SOURCE_GROUNDING_THRESHOLD` (0.8, hardcoded)
4. Ratio = grounded_count / total_quotes (1.0 if no quotes exist)

### Retry Decision

```python
if sg_ratio < source_grounding_min_ratio:  # default 0.5
    # Too many fabricated quotes -- retry with strict_quoting=True
    retry_result = await extractor.extract_field_group(..., strict_quoting=True)
    # Compute grounding on retry result
    if retry_ratio > sg_ratio:
        return retry_result  # Use improved result
    return result  # Keep original if retry didn't help
```

**Key thresholds**:
- `_SOURCE_GROUNDING_THRESHOLD = 0.8` (hardcoded in `schema_orchestrator.py:37`): Minimum score for a single quote to count as "grounded"
- `source_grounding_min_ratio = 0.5` (configurable): Minimum fraction of grounded quotes before retry triggers

This means: if more than 50% of the LLM's quotes don't actually appear in the source text, the chunk is re-extracted with stricter instructions.

---

## 8. Chunk Merge

**Code**: `_merge_chunk_results()` (`schema_orchestrator.py:482`) and `_merge_entity_lists()` (`schema_orchestrator.py:616`)

After all chunks complete for a field group, their results are merged into one.

### 8a. Regular Field Groups

For each field in the field group definition:

| Field Type | Default Strategy | How It Works |
|-----------|-----------------|-------------|
| `boolean` | `majority_vote` | Actually implements any-true: if ANY chunk returned `True`, result is `True`. If any returned `False` and none returned `True`, result is `False`. Rationale in code comment: "LLMs return explicit False when a chunk lacks evidence, not when evidence contradicts, so majority vote biases toward False." |
| `integer` | `highest_confidence` | Value from the chunk with highest `confidence` score |
| `float` | `highest_confidence` | Same as integer |
| `text` | `highest_confidence` | Same |
| `enum` | `highest_confidence` | Same |
| `list` | `merge_dedupe` | Flatten all chunks' lists, deduplicate. For hashable items (strings): `dict.fromkeys()`. For dicts: `json.dumps(sort_keys=True)` hash comparison. |

Override: Any field can specify `merge_strategy` in the schema. Valid strategies: `highest_confidence`, `max`, `min`, `concat`, `majority_vote`, `merge_dedupe`.

**Confidence**: Average of all chunks that returned a confidence value. Chunks with `confidence=None` are excluded to avoid diluting the average. Falls back to 0.5 if no chunks reported confidence.

**Quotes merge**: For each field, keeps the quote from the chunk with the highest confidence.

**Conflict detection** (when `conflict_detection_enabled=True` and > 1 chunk):
- Boolean: conflict if chunks disagree (both True and False seen)
- Numeric: conflict if relative spread > 10% (`(max - min) / max_abs > 0.1`)
- Text/enum: conflict if unique values > 1
- Stored in result as `_conflicts = {field_name: {values: [...], resolution: strategy, resolved_value: ...}}`

### 8b. Entity List Merge

**Code**: `_merge_entity_lists()` (`schema_orchestrator.py:616`)

1. Determine entity key (prefers `group.name`, falls back to scanning for first list value, defaults to `"entities"`)
2. Collect all entities from all chunks
3. Deduplicate by entity ID:
   - Check `entity_id_fields` in order: `["entity_id", "name", "id"]`
   - Normalize: `str(raw_id).strip().lower()`
   - If no ID field: hash entire entity dict via SHA-256
4. Average confidence across chunks (same logic as regular fields)
5. Propagate `_truncated` flag if any chunk was truncated
6. Compute entity grounding score: average of per-chunk entity grounding scores

### 8c. Grounding Score Propagation

For regular fields:
```python
for field_name, quote in merged_quotes.items():
    # Find the chunk this quote came from
    for result in chunk_results:
        if chunk_quotes.get(field_name) == quote:
            grounding_scores[field_name] = result["_source_grounding"].get(field_name, 0.0)
            break
    else:
        grounding_scores[field_name] = 0.0  # Quote not matched to any chunk
```

For entity lists:
- Average the per-chunk entity key scores from `_source_grounding`

These scores are stored as `result["_grounding_scores"]` and eventually persisted as `Extraction.grounding_scores`.

---

## 9. Schema Validation

**Code**: `SchemaValidator.validate()` (`src/services/extraction/schema_validator.py:28`)

Runs after merge, before confidence adjustment. Only when `validation_enabled=True`.

### Operations

1. **Confidence gating**: If `confidence < validation_min_confidence` (default 0.3), records a violation but does NOT nullify data (preserves it for consolidation weighting).

2. **Type coercion per field**:
   - String `"42"` -> int `42`
   - String `"true"` / `"True"` / `"1"` -> bool `True`
   - String `"3.14"` -> float `3.14`
   - Single value for list field -> `[value]` (list wrapping)

3. **Enum validation**: Case-insensitive match against `enum_values`. If no match found, value set to `None`.

4. **Violations**: Recorded in `data["_validation"]` as a list of `{field, issue, detail}` dicts.

**Config**:

| Setting | Default | Config Key |
|---------|---------|------------|
| Validation enabled | True | `extraction_validation_enabled` |
| Min confidence threshold | 0.3 | `extraction_validation_min_confidence` |

---

## 10. Empty Result Detection

**Code**: `_is_empty_result()` (`schema_orchestrator.py:781`)

After validation, checks whether the extraction produced meaningful data:

**Regular fields**: Counts fields with non-default, non-null, non-empty values. If < 20% of fields are populated, the result is considered "empty".

**Entity lists**: Checks if any entity list has > 0 items. If all lists are empty, considered "empty".

**Effect**: If empty, confidence is capped at `min(raw_confidence, 0.1)`. This ensures empty results don't pollute consolidation with high-confidence null data.

---

## 11. Extraction Record Creation

**Code**: `extract_source()` return path (`pipeline.py:125-151`)

For each field group result from the orchestrator:

```python
extraction = Extraction(
    project_id=source.project_id,
    source_id=source.id,
    data=result["data"],                      # Extracted fields (includes _quotes, _conflicts, _validation)
    extraction_type=result["extraction_type"], # Field group name
    source_group=context_value,               # Company name / source group
    confidence=result.get("confidence"),       # 0.0-1.0 (post-empty-detection cap)
    grounding_scores=result.get("grounding_scores"),  # {field: score} from chunk grounding
    profile_used=schema_name,                 # Schema name for tracking
    chunk_context={"truncated": True} if _truncated else None,
)
```

**Truncation tracking**: If any chunk was truncated (`_truncated` flag), `chunk_context` records this. The `_truncated` key is popped from `data` before storage (line 128).

After creating all Extraction objects for the source, the caller updates `source.status`:
- `SKIPPED` if `page_type == "skip"`
- `EXTRACTED` otherwise

The flush happens at the batch level (not per-source) so that extraction IDs are assigned before the embedding step.

---

## 12. Grounding Backfill (Post-Extraction)

Two additional grounding capabilities exist outside the inline pipeline:

### 12a. Value-vs-Quote Grounding (String Match)

**API**: `POST /projects/{id}/backfill-grounding` (`src/api/v1/projects.py:303`)
**Script**: `scripts/backfill_grounding_scores.py`

**Code**: `compute_grounding_scores()` (`grounding.py:169`)

This is a *different* grounding check from the inline one. Inline grounding checks "does the quote exist in the source?" This checks "does the extracted value appear in the quote?"

| Field Type | Verification Function | What It Does |
|-----------|----------------------|-------------|
| `integer`/`float` | `verify_numeric_in_quote` | Extracts all numbers from quote (handles 1,000 / 1.000 / 1 000 / European decimals), checks if extracted value matches any |
| `string`/`enum` | `verify_string_in_quote` | Normalized substring match (1.0), stripped punctuation (0.8), multi-word partial (up to 0.7) |
| `list` | `verify_list_items_in_quote` | Fraction of list items found in quote text. Handles string lists and entity dicts (uses `name`/`product_name`/`id` key) |
| `boolean` | Skipped | Grounding mode = "semantic" |
| `text` | Skipped | Grounding mode = "none" |

### 12b. LLM Grounding Verification

**API**: `POST /projects/{id}/backfill-grounding` (with `--llm` flag in script)
**Code**: `LLMGroundingVerifier` (`src/services/extraction/llm_grounding.py:53`)

Only verifies fields where:
- String-match grounding score == 0.0
- A non-empty quote exists
- Field type is NOT boolean (35% false rejection rate in trials)
- Grounding mode == "required"

Asks the LLM: "Does this quote support this claimed value?" with specific rules for numeric matching, unit conversions, multilingual support.

Updates score to 1.0 (supported) or keeps at 0.0 (rejected). `supported=None` (LLM error) leaves score unchanged.

---

## 13. Configuration Reference

### All Variables That Affect Extraction & Grounding

#### LLM Configuration (`settings.llm` / `LLMConfig`)

| Variable | Env Key | Default | Effect |
|----------|---------|---------|--------|
| `base_url` | `OPENAI_BASE_URL` | `http://192.168.0.247:9003/v1` | vLLM API endpoint |
| `model` | `LLM_MODEL` | `Qwen3-30B-A3B-Instruct-4bit` | Extraction model (.env overrides to `Qwen3-30B-A3B-it-4bit`) |
| `max_tokens` | `LLM_MAX_TOKENS` | 8192 | Max LLM response tokens |
| `http_timeout` | `LLM_HTTP_TIMEOUT` | 120s | HTTP timeout for LLM requests |
| `max_retries` | `LLM_MAX_RETRIES` | 3 | Retry attempts on LLM failure |
| `base_temperature` | `LLM_BASE_TEMPERATURE` | 0.1 | Starting temperature |
| `retry_temperature_increment` | `LLM_RETRY_TEMPERATURE_INCREMENT` | 0.05 | Temperature increase per retry |
| `retry_backoff_min` | `LLM_RETRY_BACKOFF_MIN` | 2s | Minimum retry wait |
| `retry_backoff_max` | `LLM_RETRY_BACKOFF_MAX` | 30s | Maximum retry wait (caps exponential backoff) |

#### Extraction Configuration (`settings.extraction` / `ExtractionConfig`)

| Variable | Env Key | Default | Effect |
|----------|---------|---------|--------|
| `content_limit` | `EXTRACTION_CONTENT_LIMIT` | 20000 | Max chars of source content sent to LLM per chunk |
| `chunk_max_tokens` | `EXTRACTION_CHUNK_MAX_TOKENS` | 5000 | Max tokens per chunk (~20K chars). Effective max = this - overlap. |
| `chunk_overlap_tokens` | `EXTRACTION_CHUNK_OVERLAP_TOKENS` | 200 | Paragraph-aligned overlap between chunks. 0=disabled. |
| `max_concurrent_chunks` | `EXTRACTION_MAX_CONCURRENT_CHUNKS` | 80 | Semaphore for parallel chunk LLM calls (per field group) |
| `max_concurrent_sources` | `EXTRACTION_MAX_CONCURRENT_SOURCES` | 20 | Semaphore for parallel source processing (per batch) |
| `extraction_batch_size` | `EXTRACTION_BATCH_SIZE` | 20 | Sources per commit batch |
| `source_quoting_enabled` | `EXTRACTION_SOURCE_QUOTING_ENABLED` | True | Include `_quotes` in LLM output |
| `conflict_detection_enabled` | `EXTRACTION_CONFLICT_DETECTION_ENABLED` | True | Record merge conflicts between chunks |
| `validation_enabled` | `EXTRACTION_VALIDATION_ENABLED` | True | Run SchemaValidator after merge |
| `validation_min_confidence` | `EXTRACTION_VALIDATION_MIN_CONFIDENCE` | 0.3 | Confidence below this triggers a violation (but data preserved) |
| `source_grounding_min_ratio` | `SOURCE_GROUNDING_MIN_RATIO` | 0.5 | If fewer than this fraction of quotes are source-grounded, retry with strict quoting |
| `domain_dedup_enabled` | `DOMAIN_DEDUP_ENABLED` | True | Use `cleaned_content` for extraction |
| `schema_embedding_enabled` | `SCHEMA_EXTRACTION_EMBEDDING_ENABLED` | True | Embed extractions to Qdrant after extraction |

#### Classification Configuration (`settings.classification` / `ClassificationConfig`)

| Variable | Env Key | Default | Effect |
|----------|---------|---------|--------|
| `enabled` | `CLASSIFICATION_ENABLED` | True | Enable page classification before extraction |
| `skip_enabled` | `CLASSIFICATION_SKIP_ENABLED` | True | Actually skip pages classified as irrelevant |
| `smart_enabled` | `SMART_CLASSIFICATION_ENABLED` | True | Use embedding-based classifier |
| `embedding_high_threshold` | `CLASSIFICATION_EMBEDDING_HIGH_THRESHOLD` | 0.75 | Above this: use matched groups directly |
| `embedding_low_threshold` | `CLASSIFICATION_EMBEDDING_LOW_THRESHOLD` | 0.40 | Below this: use all groups (conservative) |
| `reranker_threshold` | `CLASSIFICATION_RERANKER_THRESHOLD` | 0.50 | Reranker confirmation threshold |
| `reranker_model` | `RERANKER_MODEL` | bge-reranker-v2-m3 | Reranker model |
| `classifier_content_limit` | `CLASSIFICATION_CONTENT_LIMIT` | 6000 | Max chars for embedding/reranking |
| `cache_ttl` | `CLASSIFICATION_CACHE_TTL` | 86400 | Field group embedding cache TTL |

#### Hardcoded Constants

| Constant | Value | Location | Purpose |
|----------|-------|----------|---------|
| `_SOURCE_GROUNDING_THRESHOLD` | 0.8 | `schema_orchestrator.py:37` | Minimum score for a quote to count as "source-grounded" |
| `_ENTITY_RESERVED_KEYS` | `{confidence, _quotes, _truncated, _conflicts, _validation}` | `schema_orchestrator.py:40` | Keys excluded from entity list scanning |
| `_METADATA_KEYS` | `{confidence, _quotes, _conflicts, _validation, _quote}` | `grounding.py:30` | Keys excluded from grounding scoring |
| `entity_id_fields` | `["entity_id", "name", "id"]` | `schema_adapter.py:199` | Default fields for entity deduplication |
| `LLM_RETRY_HINT` | `"\n\nIMPORTANT: Be concise. Output valid JSON only."` | `constants.py:43` | Appended to system prompt on retries |
| Empty result threshold | 20% | `schema_orchestrator.py:823` | `ratio < 0.2` marks result as empty |
| Empty result confidence cap | 0.1 | `schema_orchestrator.py:289` | `min(raw_confidence, 0.1)` for empty results |
| Quote length guidance | 15-50 chars | `schema_extractor.py:413` | In prompt: "brief verbatim excerpt (15-50 chars)" |
| Default confidence fallback | 0.5 | `schema_orchestrator.py:450,569` | When chunk doesn't report confidence |

#### Per-Schema Configuration (in `project.extraction_schema`)

These are set per field in the schema JSONB, not in env vars:

| Field Key | Valid Values | Effect |
|-----------|-------------|--------|
| `merge_strategy` | `highest_confidence`, `max`, `min`, `concat`, `majority_vote`, `merge_dedupe` | Overrides type-based default for chunk merge |
| `grounding_mode` | `required`, `semantic`, `none` | Overrides type-based default for grounding |
| `consolidation_strategy` | `frequency`, `weighted_frequency`, `weighted_median`, `any_true`, `longest_top_k`, `union_dedup` | Overrides type-based default for consolidation |
| `max_items` | 1-200 (int) | Max entities per chunk for entity lists |

---

## Data Flow Diagram

```
Source.content / Source.cleaned_content
    |
    v
[Content Selection] -- domain_dedup_enabled? -> cleaned_content or content
    |
    v
[Classification] -- enabled? -> skip or filter field groups
    |
    v                               Config: chunk_max_tokens, chunk_overlap_tokens
[Chunking] -- split on H2+, merge, split oversized, apply overlap
    |
    v                               Config: max_concurrent_chunks
[Per Chunk x Per Field Group]       (parallel, semaphore-limited)
    |
    |-- [Content Cleaning] -- strip_structural_junk (Layer 1)
    |-- [Truncation] -- content[:content_limit]
    |-- [LLM Call] -- system+user prompt, json_object mode
    |       |
    |       |-- temperature: base + (attempt-1)*increment
    |       |-- retry with backoff on failure
    |       |-- JSON repair on malformed output
    |       |-- truncation handling (_truncated flag)
    |       |-- default application for missing fields
    |
    |-- [Inline Grounding] -- compute_chunk_grounding (quote vs source)
    |       |
    |       |-- verify_quote_in_source: 3-tier matching
    |       |-- stored as result["_source_grounding"]
    |
    |-- [Source Grounding Check]
    |       |
    |       |-- _source_grounding_ratio < 0.5?
    |       |       -> retry with strict_quoting=True
    |       |       -> keep better result
    |
    v
[Chunk Merge] -- per-field strategies, entity dedup, confidence averaging
    |
    |-- grounding_scores: from selected quote's chunk
    |-- _conflicts: disagreements between chunks
    |-- _quotes: highest-confidence chunk's quotes
    |
    v
[Schema Validation] -- type coercion, enum validation, confidence gating
    |
    v
[Empty Result Detection] -- <20% populated? cap confidence at 0.1
    |
    v
Extraction ORM Record
    data: {field values + _quotes + _conflicts + _validation}
    grounding_scores: {field_name: float}
    confidence: float
    extraction_type: field group name
    source_group: company/entity name
```
