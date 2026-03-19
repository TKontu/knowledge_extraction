# Pipeline Quality & Precision Issues

**Generated from code review, 2026-03-07. All findings verified against actual code.**

Issues are ranked by impact on result quality. Each includes the root cause, where it manifests, and a suggested fix.

---

## Critical: Directly degrades result precision

### 1. `frequency` consolidation ignores grounding weights for primary ranking

**Location**: `consolidation.py:98-100`

```python
best_key = max(
    groups, key=lambda k: (len(groups[k]), sum(v.weight for v in groups[k]))
)
```

**Problem**: The `frequency` strategy -- default for `string` and `enum` fields (company_name, headquarters, etc.) -- picks the most *frequent* value first, using weight only as a tiebreaker. Three ungrounded hallucinated extractions (weight 0.05 each) beat two well-grounded correct extractions (weight 0.9 each).

**Impact**: This is the single biggest quality problem for string consolidation. The entire grounding pipeline computes per-field scores, flows them through merge, stores them in the DB, and feeds them to consolidation -- but for the most common field types, those scores barely matter. With 10-26 extractions per entity, LLM hallucinations that appear consistently across irrelevant pages dominate over correct data from relevant pages.

**Example**: If 15 pages from a company website mention "XYZ Group" in the footer but only 3 pages have the actual legal name "XYZ Corporation AG" on the about page, `frequency` picks "XYZ Group" because 15 > 3, regardless of grounding.

**Fix**: Change default for string fields from `frequency` to `weighted_frequency`, or make `frequency` weight-aware by sorting on `(total_weight, count)` instead of `(count, total_weight)`:
```python
best_key = max(
    groups, key=lambda k: (sum(v.weight for v in groups[k]), len(groups[k]))
)
```

---

### 2. Grounding scores measure wrong thing for consolidation weighting

**Location**: `schema_orchestrator.py:352-358`, `consolidation.py:408`

**Problem**: The `grounding_scores` stored on each Extraction are **quote-in-source** scores (does the LLM's quote string exist in the source content?). Consolidation uses these to weight values via `effective_weight(confidence, grounding_score, ...)`. But this score tells you the *quote is real*, not that the *extracted value matches the quote*.

A perfectly grounded quote can support a wrong value. Example:
- Quote: "We have over 35 employees in the factory" (score 1.0 -- exists in source)
- Extracted value: `employee_count = 35000` (wrong -- LLM misinterpreted)
- Consolidation weight: `confidence * max(1.0, 0.1)` = high weight for wrong value

The **value-in-quote** verification (`compute_grounding_scores` in backfill) catches exactly this, but it's not part of inline extraction -- it only runs via the backfill endpoint/script.

**Impact**: Without running backfill, consolidation trusts values with high confidence + real quotes, even when the value doesn't match the quote. Numeric fields (employee_count, revenue, etc.) are most affected.

**Fix**: Run `compute_grounding_scores` (value-in-quote) inline during extraction and combine with the source grounding score. Or at minimum, make backfill a required step before consolidation (enforce via check or automate).

---

### 3. Per-chunk confidence is LLM self-reported and field-agnostic

**Location**: `schema_orchestrator.py:433-454` (merge), `consolidation.py:269-287` (weighting)

**Problem**: The entire pipeline's quality signal depends on a single per-chunk `confidence` score self-reported by the LLM. This drives:
- **Chunk merge** (`highest_confidence`): Value from the highest-confidence chunk wins for integer/float/text/enum fields
- **Quote selection**: Quote from the highest-confidence chunk is kept per field
- **Consolidation weight**: `confidence * max(grounding, 0.1)`

Two fundamental problems:
1. **LLMs are poorly calibrated**: A hallucinated value often comes with high confidence. Qwen3-30B doesn't reliably distinguish "I found this in the text" (legitimate 0.9) from "I'm making this up because the prompt asked" (should be 0.1 but often reports 0.7+).
2. **No per-field granularity**: A chunk might correctly extract company_name (deserves high confidence) but hallucinate employee_count (deserves low confidence). Both get the same chunk-level confidence. The `highest_confidence` merge then picks BOTH values from the same high-confidence chunk, even if another chunk had a better employee_count extraction.

**Impact**: Affects all field types using `highest_confidence` merge (integer, float, text, enum -- everything except boolean and list). Means hallucinated values from "confident" chunks consistently win over correct values from "cautious" chunks.

**Fix options**:
- Ask the LLM for per-field confidence scores in the prompt instead of/in addition to the overall confidence
- Weight the merge by grounding score instead of (or in addition to) confidence: pick the value from the chunk with the highest `_source_grounding` score for THAT field, not the highest overall confidence
- Use the value-in-quote grounding score (currently only in backfill) as a per-field quality signal during merge

---

### 4. Quote and value can come from different chunks after merge

**Location**: `schema_orchestrator.py:555-558` (value selection), `schema_orchestrator.py:572-588` (quote selection)

**Problem**: For `highest_confidence` merge, the value is picked by `_pick_highest_confidence(field_name, chunk_results)` -- value from the chunk where **that field is not None** and confidence is highest. The quote is picked separately -- from the chunk with the highest confidence **that has a quote for that field**.

These can be different chunks when:
- Chunk A: `company_name="Acme"`, confidence=0.8, no quote for company_name
- Chunk B: `company_name="ACME Inc."`, confidence=0.7, quote for company_name
- Chunk C: `company_name=None`, confidence=0.9, has a stale quote for company_name from a different passage

Result: value="Acme" (from chunk A), quote=from chunk C (highest confidence with a quote), grounding_score=chunk C's score. The grounding score doesn't relate to the selected value at all.

**Impact**: Grounding scores after merge can be meaningless -- they describe a quote that doesn't support the selected value. This propagates into consolidation weights.

**Fix**: Tie quote selection to value selection. When `highest_confidence` picks a value from chunk X, also use chunk X's quote and grounding score for that field. Simplest implementation: in `_pick_highest_confidence`, return (value, chunk_index), then use that index for quote and grounding lookup.

---

## Important: Significant quality degradation under common conditions

### 5. Chunk-level boolean merge implements any_true, not majority_vote

**Location**: `schema_orchestrator.py:514-523`

```python
if strategy == "majority_vote":
    if any(v is True for v in values):
        merged[field.name] = True
    elif any(v is False for v in values):
        merged[field.name] = False
```

**Problem**: Despite the strategy name `majority_vote`, a single chunk returning `True` makes the entire result `True`. The code comment explains the rationale (LLMs return False for "no evidence" not "evidence against"), but this creates false positives.

For a 20-page source chunked into 5 chunks, if 1 chunk's content vaguely relates to a boolean field (e.g., `has_iso_certification`), the LLM might return True. The other 4 chunks return False because they have no relevant content. Result: True. The actual page might not contain any ISO certification information -- the LLM just hallucinated from ambiguous content in one chunk.

**Impact**: Boolean fields will have systematically elevated false-positive rates. The consolidation `any_true` strategy (min_count=3) provides some protection at the cross-extraction level, but within a single extraction, one ambiguous chunk is enough.

**Fix**: Implement actual majority vote (or at minimum require 2+ True chunks) for chunk merge. The consolidation-level `any_true` with `min_count=3` already handles the cross-extraction case correctly -- the chunk-level merge should at least require > 1 True chunk to avoid a single hallucination propagating.

---

### 6. Empty result confidence cap creates a cliff at 20%

**Location**: `schema_orchestrator.py:781-823, 288-291`

```python
if total == 0:
    return True, 0.0
ratio = populated / total
return ratio < 0.2, ratio
```

If `ratio < 0.2`: confidence capped at `min(raw_confidence, 0.1)`
If `ratio >= 0.2`: full raw confidence preserved

**Problem**: Hard cliff at 20% creates a discontinuity. An extraction with 1/10 fields (10%) gets confidence 0.1. An extraction with 2/10 fields (20%) keeps its full confidence (say 0.8). This 8x weight difference from adding one more field is not proportional to the quality improvement.

**Impact**: Extractions with exactly 2/10 or 2/9 fields populated get disproportionately high weight in consolidation. A page that only has `company_name` + `website` (2 fields) gets full confidence, while a page with only `company_name` (1 field out of 10) gets nearly zero weight even if the company_name extraction is correct and well-grounded.

**Fix**: Use a continuous confidence scaling based on populated ratio instead of a cliff:
```python
adjusted_confidence = raw_confidence * max(ratio, 0.1)
```

---

### 7. Fields without quotes get uniformly downweighted regardless of reason

**Location**: `consolidation.py:283-285`

```python
gs = grounding_score if grounding_score is not None else 0.0
return confidence * max(gs, 0.1)
```

**Problem**: If a field has no entry in `grounding_scores`, `grounding_score` is None -> 0.0 -> floor to 0.1. This happens when:
1. The LLM didn't produce a quote for the field (common -- LLMs sometimes skip `_quotes` entries)
2. The field isn't present in the merged `_quotes` (e.g., boolean/text fields with grounding mode "semantic"/"none")

Case 1 is a real quality signal (no provenance -> downweight). But case 2 is by design -- boolean and text fields SHOULD NOT have grounding scores, yet they're treated identically to "quote missing, suspicious."

However, looking more carefully: `compute_chunk_grounding` only scores fields that have quotes, and `_merge_chunk_results` only propagates scores for fields with quotes. So fields without quotes (including boolean/text) simply don't appear in `grounding_scores`. In consolidation, `GROUNDING_DEFAULTS` maps boolean -> "semantic" and text -> "none", and `effective_weight` returns `confidence` (no grounding penalty) for those modes. So this is actually handled correctly by the grounding_mode check.

**Revised assessment**: The real issue is narrower: string/integer/float/enum fields where the LLM just didn't provide a quote. These get `grounding_score=None -> 0.0 -> floor 0.1`, meaning weight = `confidence * 0.1`. This is a 10x penalty for a field that might have correct data -- the LLM just didn't follow the quoting instruction. This is a significant penalty from a prompt-following failure, not a data quality issue.

**Fix**: Distinguish "field has no quote" (penalize moderately, e.g., floor 0.3) from "field has quote but quote doesn't match source" (penalize heavily, floor 0.1). Currently both get floor 0.1.

---

### 8. Confidence averaging across chunks dilutes signal from relevant chunks

**Location**: `schema_orchestrator.py:563-570`

```python
confidences = [
    r["confidence"]
    for r in chunk_results
    if r.get("confidence") is not None
]
merged["confidence"] = (
    sum(confidences) / len(confidences) if confidences else 0.5
)
```

**Problem**: A 20-page document might have 1 highly relevant chunk (confidence 0.9) and 4 irrelevant chunks (confidence 0.1 each). Average confidence: `(0.9 + 0.1*4) / 5 = 0.26`. This extraction gets low weight in consolidation despite having one excellent chunk.

For `highest_confidence` merge, the VALUE comes from the high-confidence chunk -- but the CONFIDENCE of the merged result is the average across all chunks. The high-quality value gets dragged down by irrelevant chunks.

**Impact**: Long documents with concentrated relevant content get unfairly low confidence. Short, single-chunk documents (where average = the chunk's confidence) are systematically favored. This biases consolidation toward shorter/simpler pages.

**Fix**: Use max confidence instead of average, or use a weighted approach (e.g., confidence of the chunk that contributed the most field values):
```python
merged["confidence"] = max(confidences) if confidences else 0.5
```
Or: for `highest_confidence` merge, use the confidence of the chunk that the most fields came from.

---

## Moderate: Quality impact under specific conditions

### 9. Entity dedup uses exact normalized string match -- no fuzzy matching

**Location**: `schema_orchestrator.py:659-664`

```python
entity_id = str(raw_id).strip().lower()
if entity_id and entity_id not in seen_ids:
    seen_ids.add(entity_id)
    all_entities.append(entity)
```

**Problem**: Entity dedup during chunk merge uses exact case-insensitive string match. "Synchronous Motor" and "Synchronous Motors" are treated as different entities. "ABB Drives" and "ABB drives division" are different entities. This leads to duplicates in entity lists.

**Impact**: Entity lists (products, services, etc.) accumulate near-duplicates from different chunks. These propagate through to consolidation where `union_dedup` (`consolidation.py:498-532`) has the same problem: `_dedup_dicts` uses `str(name).strip().lower()` exact match.

**Fix**: Add fuzzy matching for entity dedup. Even simple normalization would help:
- Strip trailing "s" (naive singularization -- already exists in `_singularize`)
- Strip common suffixes (", Inc.", ", Ltd.", "GmbH")
- Normalize whitespace and punctuation
- Consider a threshold-based string similarity (e.g., Levenshtein ratio > 0.85)

---

### 10. Source grounding retry only fires once and uses ratio, not per-field analysis

**Location**: `schema_orchestrator.py:364-405`

**Problem**: The source grounding check computes an overall ratio (fraction of quotes that are grounded). If it's below 0.5, the entire chunk is re-extracted once with strict quoting. But:

1. **Single retry**: Only one retry attempt. If the retry also fails grounding (which is common -- if the content doesn't support good quotes, strict mode won't help), the original result is kept.

2. **All-or-nothing**: The ratio is computed across ALL fields. If a chunk has 5 fields and 3 have perfect quotes but 2 are fabricated, ratio = 0.6 -- above threshold. The 2 fabricated quotes survive even though they're clearly bad.

3. **No per-field action**: The retry re-extracts everything, even the fields that had good quotes. It doesn't selectively fix the bad quotes.

**Impact**: Fabricated quotes that happen alongside enough real quotes pass the ratio check. These fabricated quotes get grounding_score 0.0 (which is correct), but the field values they're associated with might still be wrong. Since value-in-quote verification doesn't happen inline, these wrong values get into consolidation with `weight = confidence * 0.1` (floor). For the `frequency` strategy, the weight barely matters -- count matters more (Issue #1).

---

### 11. Overlap content is extracted twice without dedup

**Location**: `chunking.py:312-320`, `schema_orchestrator.py:250-293`

When `chunk_overlap_tokens > 0`, the tail of chunk N is prepended to chunk N+1. Both chunks are extracted independently. If both chunks extract the same entity or field value from the overlapping text, the merge stage will see it twice.

For `highest_confidence`: not a problem (picks one).
For `merge_dedupe` (lists): the list dedup should catch exact duplicates, but near-duplicates from slightly different extractions of the same content won't be caught.
For entity lists: SHA-256 dedup or ID-based dedup should catch exact matches, but if the LLM extracts slightly different attributes from the same entity in the overlap, both versions are kept.

**Impact**: Minor for most field types. Can cause near-duplicate entities in entity lists when overlap contains entity data.

---

## Design Observations (Not Bugs, But Questionable Decisions)

### D1. `majority_vote` strategy name is misleading

The chunk-level `majority_vote` implements any_true. The consolidation-level `any_true` has `min_count=3`. The naming creates confusion about what each layer actually does. Consider renaming the chunk-level strategy to `any_true_chunk` or documenting the discrepancy more prominently.

### D2. Consolidation `grounded_count` uses weight > 0, not grounding_score > 0

**Location**: `consolidation.py:336`
```python
grounded_count = sum(1 for v in values if v.weight > 0)
```

Since `effective_weight` has a floor of 0.1, `weight > 0` is ALWAYS true for any non-None extraction with confidence > 0. So `grounded_count` equals `source_count` in practice. The provenance metadata claims "grounded_count: 20" when really 0 of those 20 had actual grounding. This makes the provenance misleading for quality assessment.

**Fix**: Count extractions where `grounding_score is not None and grounding_score > 0`:
```python
grounded_count = sum(1 for v in values if v.weight > v_confidence * 0.1)  # weight exceeds floor
```
Or pass grounding_score through to WeightedValue and check it directly.

### D3. `_apply_defaults` fills boolean with False and list with [] before merge

**Location**: `schema_extractor.py:552-565`

When the LLM doesn't return a boolean field, the extractor fills it with `False`. This happens BEFORE chunk merge. So a chunk that has no information about a boolean field contributes an explicit `False` to the merge. Combined with the any_true chunk merge (Issue #5), this creates an asymmetry: True requires explicit LLM output, False is the default for every chunk that doesn't mention the field.

For consolidation, this means most extractions have `False` for boolean fields (from irrelevant pages), which is mostly correct behavior. But it also means pages where the LLM failed to extract a True value (JSON parsing error, truncation, etc.) contribute False instead of being excluded.

---

## Summary: Priority Fix Order

| # | Issue | Impact | Effort |
|---|-------|--------|--------|
| 1 | frequency ignores grounding weights | Highest -- affects all string/enum consolidated values | Low (1-line sort change) |
| 2 | grounding_scores are quote-in-source only | High -- consolidation weights don't reflect value correctness | Medium (run value-in-quote inline) |
| 3 | Per-chunk confidence is field-agnostic | High -- wrong values from "confident" chunks win | Medium (per-field confidence in prompt) |
| 4 | Quote/value from different chunks | High -- grounding scores meaningless after merge | Low (tie selections together) |
| 8 | Confidence averaging dilutes signal | Medium-High -- long docs penalized | Low (use max instead of average) |
| 5 | Boolean any_true false positives | Medium -- boolean fields inflate | Low (require 2+ True chunks) |
| 6 | Empty result 20% cliff | Medium -- discontinuous weighting | Low (continuous scaling) |
| 7 | Missing quotes get same penalty as bad quotes | Medium -- penalizes LLM formatting failures | Low (differentiate floors) |
| D2 | grounded_count always = source_count | Low -- misleading provenance | Low |
