# Pipeline Review: Knowledge Extraction

**Date**: 2026-03-02
**Scope**: LLM-based extraction pipeline - classification, chunking, field group extraction, merging, validation, embedding

---

## 1. Overview

The extraction pipeline transforms raw web content into structured, searchable knowledge. It supports two extraction paths:

| Path | Trigger | Process |
|------|---------|---------|
| **Schema-based** | Project has `extraction_schema` | Multi-pass orchestration with field groups |
| **Generic facts** | No schema defined | Single-pass fact extraction |

Both paths produce `Extraction` records with embeddings in Qdrant for semantic search.

```
Source Content → [Domain Dedup] → [Classification] → Chunking → LLM Extraction
    → Merge → [Validation] → Embedding → Qdrant → Entity Extraction
```

Brackets indicate optional stages.

---

## 2. Pipeline Entry Points

### ExtractionWorker (`src/services/extraction/worker.py`)

Job dispatcher that routes to the correct pipeline:

```python
# Decision logic:
if project.extraction_schema:
    SchemaExtractionPipeline.extract_project(...)  # Path A
else:
    ExtractionPipelineService.process_job(...)      # Path B (generic)
```

**Lifecycle**:
1. Check for early cancellation
2. Mark job RUNNING
3. Extract payload (project_id, source_ids, profile, force)
4. Route to appropriate pipeline
5. Checkpoint progress between batches
6. Handle completion/failure states

**Checkpoint/Resume**:
- Checkpoint callback updates `job.payload.checkpoint` with:
  - `processed_source_ids`, `last_checkpoint_at`, totals
- `_get_resume_state()` recovers from prior failed runs
- Allows restart without re-processing completed sources

---

## 3. Schema-Based Extraction (Path A)

### 3.1 SchemaExtractionPipeline (`src/services/extraction/pipeline.py`)

Orchestrates the full extraction workflow for projects with schemas.

**Batch Processing**:
- Processes sources in 20-source chunks
- Cancellation check between chunks
- Flushes DB after each chunk to persist extraction IDs
- Batch embeds all extractions from chunk before commit

**Per-Source Flow**:
```
For each source in chunk:
  1. SchemaExtractionOrchestrator.extract_all_groups(source)
     → Returns list of extraction dicts
  2. Create Extraction records in DB
  3. Batch embed extractions (bge-m3 → Qdrant)
  4. Entity extraction (if embeddings succeeded)
  5. Update source.status = EXTRACTED / PARTIAL / SKIPPED
```

**Content Selection** (when `domain_dedup_enabled=True`):
```python
content = source.cleaned_content or source.content  # Prefer deduped
```

### 3.2 SchemaExtractionOrchestrator (`src/services/extraction/schema_orchestrator.py`)

Multi-pass orchestrator that extracts all field groups from one source.

**Step 1: Page Classification** (optional)

Two classification modes:

| Mode | Implementation | Speed |
|------|---------------|-------|
| Rule-based | `PageClassifier` - URL/title pattern matching | Fast |
| Smart | `SmartClassifier` - embedding + reranker | Slower, more accurate |

Rule-based skip patterns: `/career`, `/job`, `/privacy`, `/login`, `/sitemap`, etc.

Smart classification (3-tier confidence):
- **High (>= 0.75)**: Skip reranker, use matched groups directly
- **Medium (0.4 - 0.75)**: Use reranker (bge-reranker-v2-m3) to confirm
- **Low (< 0.4)**: Use top 80% of groups by score (minimum 2)

```
Page summary (title + URL + content[:6000])
  → Embed via bge-m3
  → Cosine similarity vs cached field group embeddings
  → [Reranker if medium confidence]
  → Classification result (page_type, relevant_groups, skip_extraction)
```

**Step 2: Document Chunking**

```python
chunks = chunk_document(
    content,
    max_tokens=5000,      # Default (configurable)
    overlap_tokens=500    # Default (configurable)
)
```

Chunking algorithm:
1. Split by H2+ headers (preserving H1)
2. Assemble sections until `max_tokens` exceeded
3. Large sections split by paragraphs, then by words
4. Overlap: prepend tail of previous chunk (paragraph-aligned)

Token counting: ~4 chars/token for Latin, ~1.5 for CJK

**Step 3: Parallel Field Group Extraction**

All field groups extracted concurrently using semaphore:

```python
semaphore = asyncio.Semaphore(extraction_max_concurrent_chunks)  # Default 80

for group in relevant_groups:
    for chunk in chunks:
        async with semaphore:
            result = await schema_extractor.extract_with_limit(chunk, group)
```

Continuous flow - no batch-and-wait; new requests start as old ones complete.

**Step 4: Chunk Result Merging**

Per-field merge strategies based on field type:

| Field Type | Default Strategy | Behavior |
|------------|-----------------|----------|
| `bool` | `majority_vote` | Most common value wins |
| `int`, `float` | `highest_confidence` | Value from highest-confidence chunk |
| `str` (enum) | `highest_confidence` | Value from highest-confidence chunk |
| `str` (text) | `highest_confidence` | Longest non-null from top chunk |
| `list` | `merge_dedupe` | Union of all chunk results, deduplicated |

Additional merge behaviors:
- Confidence: averaged across chunks with non-null values
- Source quotes (`_quotes`): kept from highest-confidence chunk per field
- Entity lists: deduplicated by normalized value, quotes preserved

**Step 5: Conflict Detection** (optional, `extraction_conflict_detection_enabled`)

Detects disagreements between chunk results:

| Type | Conflict Condition |
|------|-------------------|
| Boolean | >1 unique value |
| Numeric | Relative spread > 10% |
| Text/Enum | >1 unique value |

Stored in `_conflicts` metadata:
```json
{
  "field_name": {
    "values": [{"chunk": 0, "value": "A"}, {"chunk": 1, "value": "B"}],
    "resolution": "highest_confidence",
    "resolved_value": "A"
  }
}
```

**Step 6: Empty Result Handling**

- If < 20% of fields populated: flag as empty
- Cap confidence at 0.1 for empty results
- Prevents low-quality extractions from polluting search

### 3.3 SchemaExtractor (`src/services/extraction/schema_extractor.py`)

Handles single field group extraction from content via LLM.

**Two Operating Modes**:
- **Direct**: Calls LLM directly with AsyncOpenAI client
- **Queue**: Submits to Redis queue for batched processing

**Prompting Strategy**:

System prompt includes:
- Field specifications with types and descriptions
- Confidence scoring rules (0.0-1.0 guidance)
- Source quoting instructions (if enabled)
- Entity list emphasis ("most significant items, skip nav lists")

User prompt includes:
- Content (cleaned via Layer 1 junk removal)
- Content limit enforcement (EXTRACTION_CONTENT_LIMIT = 20000 chars)
- Field group hint for context

**LLM Call with Retry**:
```
Attempts: max 3 (configurable)
Backoff: 2s → 4s → 8s (exponential, capped at 30s)
Temperature: 0.1 + 0.05 * attempt (varies to avoid hallucination loops)
Max tokens: 8192
Timeout: 120 seconds
```

**Response Processing**:
1. Parse JSON from LLM response
2. `try_repair_json()` on parse failures (handles code fences, trailing commas, unmatched brackets)
3. Apply defaults for missing fields
4. Handle truncation (`finish_reason='length'`) gracefully

### 3.4 SchemaValidator (`src/services/extraction/schema_validator.py`)

Applied after merge, before storage. Four validation tiers:

**Tier 1: Confidence Gating**
- If `confidence < min_threshold`: suppress all fields (return nulls + metadata)

**Tier 2: Type Coercion**

| Target Type | Coercion Examples |
|-------------|-------------------|
| `bool` | `"true"/"yes"/"1"` → True, `"false"/"no"/"0"` → False |
| `int` | `"42"` / `"42.5"` → 42, strips commas |
| `float` | `"3.14"` → 3.14 |
| `enum` | Case-insensitive match, nullify if invalid |

**Tier 3: List Wrapping**
- Single value for list field → `[value]`

**Tier 4: Violation Tracking**
- All coercions logged to `_validation` array:
```json
[{"field": "weight", "issue": "coerced", "detail": "string '42.5' → int 42"}]
```

### 3.5 SchemaAdapter (`src/services/extraction/schema_adapter.py`)

Converts JSONB extraction schema to `FieldGroup` / `FieldDefinition` dataclasses:

```python
class FieldDefinition:
    name: str
    field_type: str       # "str", "int", "float", "bool", "list"
    description: str
    required: bool
    enum_values: list[str] | None
    merge_strategy: str   # "highest_confidence", "majority_vote", etc.

class FieldGroup:
    name: str
    description: str
    fields: list[FieldDefinition]
    is_entity_list: bool  # Controls extraction prompt style
```

### 3.6 Content Cleaning (`src/services/extraction/content_cleaner.py`)

Two-layer cleaning approach:

**Layer 1 (Universal, safe for extraction)**:
- Empty-alt images: `![](url)` (logos, tracking pixels)
- Bare nav links: `* [Link](url)` with nothing after
- Skip-to-content accessibility links
- Bare image lines alone on a line

**Layer 2 (Density windowing, for classification/embedding only)**:
- Finds first run of 3+ consecutive content-dense lines
- Removes high-link-density header/nav sections
- Conservative: returns 0 offset if content starts immediately

Usage split:
- `strip_structural_junk()` → Layer 1 only (extraction input)
- `clean_markdown_for_embedding()` → Layer 1 + Layer 2 (classification/search)

### 3.7 Domain Boilerplate Deduplication (`src/services/extraction/domain_dedup.py`)

Two-pass domain analysis:

**Pass 1 (Domain level)**:
- Split content on `\n\n` (paragraph boundaries)
- Hash blocks: SHA-256, whitespace-normalized + lowercased, first 16 hex chars
- Block on >= 70% of pages → boilerplate
- Gate: requires min 5 pages per domain

**Pass 2 (Section level)**:
- Group pages by URL path prefix (depth=1: `/products`, `/services`)
- Same fingerprinting per section
- Floor: minimum 3 pages per section

**Merge**: Union of domain + section hashes per source

**Storage**: `cleaned_content` field on Source (original `content` untouched)

---

## 4. Generic Extraction (Path B)

For projects without `extraction_schema`, uses `ExtractionPipelineService`:

- Extracts unstructured facts with confidence scores
- Single-pass (no field groups or classification)
- Simpler deduplication
- Same embedding and entity extraction downstream

---

## 5. Embedding & Vector Storage

After extraction, results are embedded for semantic search:

```
Extraction data → bge-m3 (1024 dims) → Qdrant upsert
  - Metadata: project_id, source_group, extraction_type
  - extraction.embedding_id set to Qdrant point ID
```

Batch processing: up to 50 concurrent embedding requests per chunk.

---

## 6. Entity Extraction

Runs after successful embedding:

```
Extraction data + entity_types → EntityExtractor
  → Extract named entities (products, people, specs, etc.)
  → Deduplicate by normalized_value per (project, source_group, type)
  → Create Entity + ExtractionEntity link
```

Entity deduplication is scoped to `(project_id, source_group, entity_type, normalized_value)`.

---

## 7. Configuration

```python
# Content Limits
EXTRACTION_CONTENT_LIMIT = 20000   # chars for LLM input

# Chunking
extraction_chunk_max_tokens = 5000
extraction_chunk_overlap_tokens = 500

# Concurrency
extraction_max_concurrent_chunks = 80   # per field group
extraction_max_concurrent_sources = 20  # batch processing

# LLM
llm_model = "Qwen3-30B-A3B-Instruct-4bit"
llm_http_timeout = 120
llm_max_retries = 3
llm_max_tokens = 8192
llm_retry_backoff_min = 2
llm_retry_backoff_max = 30
llm_base_temperature = 0.1

# Classification
classification_enabled = True
smart_classification_enabled = False
classification_embedding_high_threshold = 0.75
classification_embedding_low_threshold = 0.4

# Reliability (Phase 1A, all default OFF)
extraction_source_quoting_enabled = False
extraction_conflict_detection_enabled = False
extraction_validation_enabled = False
extraction_validation_min_confidence = 0.0

# Domain Dedup
domain_dedup_enabled = True
domain_dedup_threshold_pct = 0.7
domain_dedup_min_pages = 5
domain_dedup_min_block_chars = 50

# Embedding
schema_extraction_embedding_enabled = True
embedding_max_concurrent = 50
```

---

## 8. Data Flow Diagram

```
                     ┌─────────────┐
                     │   Source     │
                     │  (PENDING)  │
                     └──────┬──────┘
                            │
                   ┌────────▼────────┐
                   │ Content Select  │
                   │ cleaned_content │
                   │ or content      │
                   └────────┬────────┘
                            │
                   ┌────────▼────────┐
                   │ Classification  │ ←── SmartClassifier (embedding)
                   │ (optional)      │     or PageClassifier (rules)
                   └────────┬────────┘
                            │
                   ┌────────▼────────┐
                   │   Skip?         │──── YES → status=SKIPPED
                   └────────┬────────┘
                            │ NO
                   ┌────────▼────────┐
                   │ Chunk Document  │──── max_tokens=5000
                   │ (with overlap)  │     overlap=500
                   └────────┬────────┘
                            │
              ┌─────────────┼─────────────┐
              │             │             │
        ┌─────▼─────┐ ┌────▼────┐ ┌─────▼─────┐
        │FieldGroup │ │FieldGr. │ │ FieldGr.  │  ← Parallel extraction
        │     A     │ │    B    │ │     C     │    (semaphore: 80)
        └─────┬─────┘ └────┬────┘ └─────┬─────┘
              │             │             │
              └─────────────┼─────────────┘
                            │
                   ┌────────▼────────┐
                   │  Chunk Merge    │──── Per-field merge strategy
                   │  + Conflicts    │     (highest_confidence, etc.)
                   └────────┬────────┘
                            │
                   ┌────────▼────────┐
                   │  Validation     │──── Type coercion, enum match
                   │  (optional)     │     Confidence gating
                   └────────┬────────┘
                            │
                   ┌────────▼────────┐
                   │  Create         │
                   │  Extraction     │──── DB record with JSONB data
                   └────────┬────────┘
                            │
                   ┌────────▼────────┐
                   │ Batch Embed     │──── bge-m3 → Qdrant upsert
                   └────────┬────────┘
                            │
                   ┌────────▼────────┐
                   │ Entity Extract  │──── Named entities → DB
                   └────────┬────────┘
                            │
                   ┌────────▼────────┐
                   │ Source Status   │
                   │ = EXTRACTED     │
                   └────────────────┘
```

---

## 9. Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Field groups as extraction unit | Allows parallel extraction of independent data domains |
| 80 concurrent chunks | Maximizes vLLM KV cache utilization without overload |
| Temperature variation on retry | Avoids hallucination loops from identical prompts |
| Separate cleaned_content | Preserves original data; dedup is non-destructive |
| Merge strategies per type | Boolean majority vote vs numeric highest-confidence are fundamentally different |
| Reliability features default OFF | Backward compatibility; opt-in activation per project |
| Checkpoint/resume | Large extraction jobs (1000s of sources) can restart without data loss |
| Classification before extraction | Avoids wasting LLM calls on irrelevant pages (careers, login, etc.) |
