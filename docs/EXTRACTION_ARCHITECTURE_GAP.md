# Extraction Architecture Gap Analysis

**2026-03-07. Analysis of proposed per-item extraction model vs. current implementation.**

## Proposed Principles

1. Each extraction = independent data item with own metadata (quote, chunk ref, confidence)
2. Each chunk analyzed per data item separately
3. Multi-answer handling: extract first viable, then re-analyze if more exist
4. Grounding confirmed for every data item (type -> extraction -> item + quote + confidence -> grounding)
5. Compound data types (e.g., Product + attributes) extracted as a unit; grounding at entity level

---

## Principle 1: Independent Data Items with Own Metadata

**Proposed**: Each extracted data item carries its own quote, chunk reference, and confidence score independently.

**Current**: Extraction is per-field-group-per-source. One `Extraction` ORM record stores:
- `data`: JSON blob with ALL fields from the group merged across chunks (`pipeline.py:139`)
- `confidence`: single float — average of per-chunk confidences (`schema_orchestrator.py:568`)
- `grounding_scores`: dict of field_name -> score, but only for fields that have quotes (`schema_orchestrator.py:599-612`)
- `chunk_index`: always None (never set — `orm_models.py:287`)
- No per-field confidence
- No per-field chunk reference

**Gap: LARGE**

| Attribute | Proposed | Current |
|-----------|----------|---------|
| Confidence granularity | Per data item | Per field group (single float, averaged across chunks) |
| Quote association | Tied to specific data item | Stored in `data["_quotes"]` dict, can come from different chunk than value (Issue #4 in PIPELINE_QUALITY_ISSUES.md) |
| Chunk reference | Each item knows which chunk it came from | Lost during merge — `chunk_index` on Extraction is always None |
| Independence | Each item is a standalone record | All fields bundled into one JSON blob per field group |

**What would change**: The fundamental storage model. Instead of one `Extraction` per (source, field_group) with a merged JSON blob, we'd need either:
- One `Extraction` per (source, field_group, field, chunk) — explosion of records
- A structured `data` format where each field is `{value, quote, confidence, chunk_index, grounding_score}` instead of just the value
- Or a separate `ExtractionItem` table: `extraction_id, field_name, value, quote, confidence, chunk_index, grounding_score`

**Downstream impact**: Consolidation (`consolidation.py`) currently receives `WeightedValue(value, weight, source_id)` per field. It would instead receive items with richer provenance, enabling weight = `item_confidence * item_grounding` instead of `overall_confidence * field_grounding_score`.

---

## Principle 2: Per-Item Chunk Analysis

**Proposed**: Each chunk is analyzed for each data item separately — the LLM focuses on extracting one field at a time.

**Current**: One LLM call per (chunk, field_group). The prompt lists ALL fields in the group and asks the LLM to extract everything at once (`schema_extractor.py:388-434`).

```
Current: 1 LLM call = 1 chunk x 1 field_group (all fields)
Proposed: 1 LLM call = 1 chunk x 1 field (or 1 compound entity type)
```

**Gap: VERY LARGE — fundamental architecture change**

**Current flow**:
```
chunk -> LLM(all fields) -> {field1: v, field2: v, ..., confidence: 0.8, _quotes: {...}}
```

**Proposed flow**:
```
chunk -> LLM(field1) -> {value, quote, confidence}
chunk -> LLM(field2) -> {value, quote, confidence}
...
```

**Trade-offs**:

| Dimension | Current (batch) | Proposed (per-item) |
|-----------|----------------|---------------------|
| LLM calls | 1 per (chunk, group) | N per (chunk, group) where N = field count |
| Cost/latency | ~5 chunks x 3 groups = 15 calls/source | ~5 chunks x 10 fields = 50 calls/source (3.3x) |
| Confidence quality | Single confidence for all fields (Issue #3) | Per-field confidence — huge quality improvement |
| Quote quality | Per-field quote possible but loose (Issue #4) | Tight value-quote coupling, no cross-chunk contamination |
| KV cache efficiency | Good — one big prompt, one response | Poor — same chunk content sent N times |
| Context utilization | Fields inform each other (e.g., company_name helps disambiguate headquarters) | Fields extracted in isolation — no cross-field signal |

**Hybrid option**: Keep field-group-level LLM calls but restructure the response format to require per-field confidence and quote:

```json
{
  "fields": {
    "company_name": {"value": "Acme Corp", "confidence": 0.95, "quote": "Acme Corp is a..."},
    "headquarters": {"value": "Zurich", "confidence": 0.7, "quote": "headquartered in Zurich"},
    "employee_count": {"value": null, "confidence": 0.0, "quote": null}
  }
}
```

This gets ~80% of the quality benefit (per-field confidence + tied quote-value) at ~0% extra LLM cost. The remaining 20% — fully independent extraction — would only matter if cross-field contamination in the prompt is causing systematic errors (not currently measured).

---

## Principle 3: Multi-Answer Iterative Extraction

**Proposed**: For fields that can have multiple viable answers in a chunk:
1. Extract first/best answer
2. Include a metadata boolean: "does the chunk contain more viable answers?"
3. If yes, re-analyze with already-extracted items in the prompt, forcing the LLM to find the next answer
4. Repeat until no more viable answers

**Current**: Two approaches depending on field type:
- **Scalar fields** (`highest_confidence` merge): Only one value kept per chunk. If chunk has two company names, LLM picks one. No mechanism to discover the second.
- **Entity lists** (`is_entity_list=True`): LLM extracts up to `max_items` (default 20) entities in a single call (`schema_extractor.py:493`). No iterative re-extraction. If the list is truncated (`finish_reason="length"`), the chunk returns empty with `_truncated=True` — data is lost, not retried with a "give me more" prompt.

**Gap: LARGE for entity lists, MODERATE for scalar fields**

**Entity lists (biggest gap)**:

Current entity extraction is one-shot. The LLM dumps everything it finds into a JSON array. Problems:
1. **Truncation = data loss**: If 50 products exist but `max_tokens` runs out after 15, the remaining 35 are lost. Current code returns empty list on unrecoverable truncation (`schema_extractor.py:316-320`).
2. **Quality degrades with quantity**: LLMs are worse at extracting item #20 than item #1 in a single call. Attributes become sparser for later items.
3. **No "are there more?" signal**: The LLM has no mechanism to indicate incomplete extraction.

The proposed iterative approach would:
```
Pass 1: Extract first 5-10 entities + "has_more: true"
Pass 2: "Already found: [A, B, C, D, E]. Extract NEXT entities..." + "has_more: true"
Pass 3: "Already found: [A-J]. Extract NEXT..." + "has_more: false" -> done
```

**Scalar fields (moderate gap)**:

For fields like `company_name`, the current model assumes one answer per source (across all chunks). The `highest_confidence` merge picks the best one. This is usually correct — a source typically has one company name.

But for fields like `certifications` (list type) or even `description` (text), a chunk might contain multiple valid answers. Current `merge_dedupe` (list) and `concat` (text) strategies handle this across chunks but not within a single chunk.

The proposed model would help most for **list-type fields in single-chunk sources** where all items come from one chunk and the LLM must find them all in one pass.

**Implementation complexity**: The iterative approach requires:
- New prompt template with "already extracted" context
- Loop control (max iterations, convergence detection)
- Dedup between passes (LLM might re-extract items despite "already found" list)
- Cost control (each iteration = another LLM call)

---

## Principle 4: Grounding Confirmed for Every Data Item

**Proposed**: Complete grounding chain: `data type -> extraction from chunk -> data item & quote & confidence -> grounding confirmation`. Every item must be grounded.

**Current**: Two separate grounding layers, neither complete:

### Layer A: Quote-in-Source (inline, during extraction)

`compute_chunk_grounding()` (`grounding.py`): After each chunk extraction, verifies that each field's `_quotes` string exists in the chunk content. Multi-tier matching (exact -> punctuation-stripped -> word-level sliding window).

- **Coverage**: Only fields where the LLM provided a quote. LLMs frequently skip `_quotes` entries — those fields get no grounding score at all.
- **What it proves**: The quote is real text from the source. Does NOT prove the extracted value matches the quote.
- **Stored**: `Extraction.grounding_scores` (dict field_name -> float)

### Layer B: Value-in-Quote (backfill only, NOT inline)

`compute_grounding_scores()` (`grounding.py`): Verifies extracted values against their quotes. Numeric format handling, string matching, list item fraction.

- **Coverage**: Only runs via backfill script or API endpoint. NOT part of the extraction pipeline.
- **What it proves**: The extracted value is consistent with the quote.
- **Stored**: Overwrites `Extraction.grounding_scores` when run.

### Layer C: LLM Verification (backfill only)

`LLMGroundingVerifier` (`llm_grounding.py`): For fields where string-match scored 0.0, asks LLM "does this quote support this value?"

- **Coverage**: Only fields that failed string-match grounding. Only via backfill with `--llm` flag.

**Gap: LARGE — grounding chain is incomplete inline**

| Step in proposed chain | Current status |
|----------------------|----------------|
| Data type defines grounding mode | Yes — `GROUNDING_DEFAULTS` maps field_type -> mode (`grounding.py`) |
| Extraction from chunk produces quote | Partially — LLM sometimes skips quotes; no enforcement |
| Quote verified against source | Yes inline — `compute_chunk_grounding` (`schema_orchestrator.py:354`) |
| Value verified against quote | NO inline — only via backfill (`compute_grounding_scores`) |
| LLM fallback verification | NO inline — only via backfill with `--llm` flag |
| Grounding failure triggers action | Partially — if overall ratio < 0.5, re-extract once. No per-field action |

**What "complete grounding for every item" would require**:

1. **Enforce quotes**: If LLM doesn't provide a quote for a field, either:
   - Reject the field value (set to null)
   - Re-extract that specific field with explicit quoting instruction
   - Accept but mark as ungrounded (current behavior via 0.1 floor)

2. **Value-in-quote inline**: Run `compute_grounding_scores` immediately after extraction, not as backfill. This is a straightforward code move — the function exists, it just isn't called in the extraction path.

3. **Per-field grounding action**: When a specific field fails grounding (quote doesn't match value), take action on THAT field:
   - Cap its confidence
   - Re-extract just that field
   - Flag it for review
   Currently, only the overall source_grounding_ratio triggers action, and it re-extracts ALL fields.

4. **Grounding metadata per item**: Each data item carries `{value, quote, source_grounding_score, value_grounding_score, grounding_status}`. Currently all of this is spread across `data["_quotes"]`, `grounding_scores`, and the backfill-only layer.

---

## Principle 5: Compound Data Types as Units

**Proposed**: Product + attributes extracted as a unit. Grounding = "does this product exist in the source?" not "does this attribute value match?"

**Current**: Entity lists (`is_entity_list=True`) are already extracted as compound units. Each entity is a dict with multiple fields. Per-entity `_quote` is requested. Grounding is at the entity level (quote identifies the entity in source).

**Gap: SMALL — mostly aligned**

| Aspect | Proposed | Current |
|--------|----------|---------|
| Compound extraction | Product + attributes as one unit | Yes — entity dict with all fields |
| Entity-level quote | One quote identifying the entity | Yes — `_quote` per entity (`schema_extractor.py:477-491`) |
| Entity-level grounding | "Does this entity exist?" | Partially — `compute_chunk_grounding_entities` checks entity `_quote` vs source |
| Attribute-level grounding | Not required (entity existence is enough) | Correct — no per-attribute grounding. Only entity-level `_quote` checked |
| Attribute independence | Attributes NOT extracted separately | Correct — all fields in one entity dict from one LLM call |

**Minor gaps**:
- Entity grounding scores are averaged across all entities in a chunk (`schema_orchestrator.py:700-710`), not stored per-entity. An entity with a fabricated quote gets hidden by other entities with real quotes.
- After chunk merge, individual entity provenance (which chunk, which quote score) is lost. The merged entity list has one averaged grounding score for the entire list.

---

## Summary: Gap Severity by Principle

| # | Principle | Gap | Core Issue |
|---|-----------|-----|------------|
| 1 | Independent items with metadata | LARGE | Storage model is per-field-group blob, not per-item. Confidence is group-level, quotes can mismatch values. |
| 2 | Per-item chunk analysis | VERY LARGE | One LLM call extracts all fields. No per-field confidence. **But hybrid response format gets 80% of benefit at 0% extra cost.** |
| 3 | Multi-answer iterative extraction | LARGE | Entity lists are one-shot, truncation = data loss. No "has_more?" mechanism. Scalar fields assume single answer per chunk. |
| 4 | Complete grounding chain | LARGE | Value-in-quote verification exists but not inline. Quote enforcement is soft (LLM can skip). No per-field grounding action. |
| 5 | Compound types as units | SMALL | Entity lists already work this way. Minor gap: per-entity grounding scores lost during merge. |

---

## Recommended Implementation Path

Ordered by impact/effort ratio, building incrementally:

### Phase 1: Per-Field Metadata (High impact, Medium effort)

Change LLM response format to per-field structured output:
```json
{
  "fields": {
    "company_name": {"value": "X", "confidence": 0.9, "quote": "..."},
    "employee_count": {"value": 500, "confidence": 0.6, "quote": "..."}
  }
}
```

This fixes Principles 1 & 2 without increasing LLM calls. Changes needed:
- `schema_extractor.py`: New prompt format, response parsing
- `schema_orchestrator.py`: Merge uses per-field confidence instead of chunk-level
- `pipeline.py`: Store per-field metadata in `Extraction.data` (structured format)
- `consolidation.py`: Use per-field confidence for weighting

**Eliminates Issues #3 (field-agnostic confidence) and #4 (quote/value mismatch) from PIPELINE_QUALITY_ISSUES.md.**

### Phase 2: Inline Value-in-Quote Grounding (High impact, Low effort)

Call `compute_grounding_scores()` inline during extraction, right after `compute_chunk_grounding()`:
- `schema_orchestrator.py:354-358`: Add value-in-quote verification
- Combine both scores: `final_grounding = min(quote_in_source, value_in_quote)`
- Store combined score in `_source_grounding`

**Eliminates Issue #2 (grounding measures wrong thing).**

### Phase 3: Quote Enforcement (Medium impact, Low effort)

When LLM omits a quote for a grounding_mode="required" field:
- Set field value to null (strict) OR
- Flag as ungrounded with confidence penalty (lenient)
- Currently the field keeps its value with a 0.1 weight floor

### Phase 4: Iterative Entity Extraction (Medium impact, High effort)

For `is_entity_list=True` groups:
1. Add `"has_more"` boolean to entity extraction response format
2. If `has_more=True` AND extracted count < `max_items`:
   - Re-call LLM with "Already extracted: [names]. Find additional entities."
   - Merge new entities with existing, dedup by ID fields
3. Replace truncation-as-data-loss with pagination

### Phase 5: Per-Entity Grounding Preservation (Low impact, Medium effort)

Store per-entity grounding scores through merge instead of averaging:
- Each entity in merged list carries its own `_grounding_score`
- Consolidation `union_dedup` uses per-entity scores for dedup decisions (keep better-grounded version)

---

## Cost Analysis

| Change | Extra LLM calls | Quality impact |
|--------|-----------------|----------------|
| Phase 1 (per-field metadata) | 0 | HIGH — per-field confidence + tied quotes |
| Phase 2 (inline value grounding) | 0 | HIGH — correct grounding weights |
| Phase 3 (quote enforcement) | 0 (or +1 per missing quote if re-extracting) | MEDIUM — eliminates ungrounded values |
| Phase 4 (iterative entities) | +1-3 per entity-list chunk with many items | MEDIUM — captures long-tail entities |
| Phase 5 (entity grounding) | 0 | LOW — better entity dedup decisions |

Phases 1-3 deliver the largest quality improvements with zero or near-zero additional LLM cost. Phase 4 is the only one that materially increases compute requirements.
