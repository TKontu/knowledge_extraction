# Pipeline Review: Source Grounding Implementation

## Flow
```
schema_orchestrator.extract_chunk_with_semaphore()
  → extractor.extract_field_group(chunk.content)
  → _source_grounding_ratio(result, chunk.content)
    → verify_quote_in_source(quote, content) per quoted field
  → IF ratio < min_ratio: retry with strict_quoting=True
    → extractor.extract_field_group(chunk.content, strict_quoting=True)
    → _source_grounding_ratio(retry_result, chunk.content)
    → pick better result
```

## Re-verification Results

Each finding re-verified against actual code. False positives removed.

## Critical

### 1. ~~Retry holds semaphore — doubles latency for bad chunks~~ → FALSE POSITIVE / OVERSTATED
`schema_orchestrator.py:301-352` — The review claimed retries "double total extraction time" by holding semaphore slots. But `max_concurrent_chunks` defaults to **80** (verified in config.py). With 80 slots, a source typically has 4-8 chunks. Even if all chunks retry, they all fit within the semaphore. This would only matter if max_concurrent_chunks were set very low (3-5), which is not the case.

**Verdict**: Not impactful with current config. No fix needed.

### 2. Entity list extractions bypass source grounding entirely → REAL
`schema_orchestrator.py:294-352` — Entity lists DO go through `extract_chunk_with_semaphore` and therefore through `_source_grounding_ratio`. However, entity list results have per-entity `_quote` (singular) inside each entity object, not a top-level `_quotes` dict. `_source_grounding_ratio` checks `result.get("_quotes", {})` — this is empty for entity list results → returns 1.0 → never triggers retry.

Entity list result structure from LLM:
```json
{"products": [{"name": "Motor X", "_quote": "Motor X series"}, ...], "confidence": 0.8}
```

No top-level `_quotes` key exists. `_source_grounding_ratio` sees no quotes → returns 1.0 → always passes.

**Impact**: Entity list extractions (products, services, locations) — often the most hallucination-prone — are never source-grounded.

**Fix**: `_source_grounding_ratio` needs to handle entity list structure: iterate items, check each `_quote` against content.

### 3. Strict quoting prompt not applied to entity list system prompt → REAL
`schema_extractor.py:383-384` — `_build_system_prompt()` checks `is_entity_list` and calls `_build_entity_list_system_prompt(field_group)` **without passing `strict_quoting`**. Even if issue #2 were fixed, the retry would use the same prompt as the original extraction.

**Verified at line 383-384**:
```python
if field_group.is_entity_list:
    return self._build_entity_list_system_prompt(field_group)  # strict_quoting ignored!
```

**Impact**: Even if entity lists were checked for source grounding, the retry couldn't improve anything.

**Fix**: Pass `strict_quoting` through to `_build_entity_list_system_prompt` and add stricter instruction for entity `_quote` fields.

## Important

### 4. Tier 2 re-runs `_STRIP_PUNCT_RE.sub` on full content every call → REAL but low impact
`grounding.py:396-399` — `_STRIP_PUNCT_RE.sub("", norm_content)` runs over the entire normalized content for every field's quote in `verify_quote_in_source`. With 5 quoted fields per chunk, this is 5 redundant regex passes over ~20K chars.

**Impact**: Measurable but not a bottleneck for inline use (regex on 20K is ~1ms). Would matter more in the backfill endpoint processing 47K extractions, where each extraction triggers per-field content stripping. Worth fixing if backfill performance becomes an issue.

**Fix**: Pre-compute stripped content once per chunk/extraction in the calling function.

### 5. `_word_window_similarity` counts word presence, not position → REAL, by design
`grounding.py:426-463` — The algorithm checks if each quote word appears *anywhere* in the N-word window, not in order. Quote "ABB has 500 employees" against window "employees has 500 ABB" would score 1.0.

**Impact**: Marginal. Complete word-order scrambling within a quote-sized window (15-50 chars) is unrealistic in real text. Tiers 1 and 2 catch exact/near-exact matches; tier 3 is the fallback for cases where punctuation differences prevent substring matching. The false-positive risk is theoretical — real content doesn't have scrambled word order in small windows.

**Verdict**: Acceptable design tradeoff. Not a bug.

### 6. Backfill endpoint timeout risk for large projects → NOT CURRENTLY AN ISSUE
`api/v1/projects.py:303-378` — The backfill endpoint processes extractions synchronously. Currently it only does value-vs-quote grounding (CPU-only, ~1ms per extraction). The 47K extraction backfill completed in seconds.

If extended to include source-grounding (requiring source content joins), it would become slow. But that's a future concern.

**Verdict**: Noted for future. No action needed now.

## Minor

### 7. `_word_window_similarity` early-exit threshold mismatch → REAL, no impact
`grounding.py:439,460` — Early exits at `>= 0.95` but `_SOURCE_GROUNDING_THRESHOLD` is `0.8`. The function continues sliding even after finding a 0.85 match that would pass. Not a bug — the function returns the best score for reporting/analysis — but could short-circuit earlier for inline use.

**Verdict**: Minor optimization opportunity. Not worth fixing now.

### 8. Strict quoting prompt says "set field to null" — trades recall for precision → BY DESIGN
`schema_extractor.py:405` — "If you cannot find an exact quote in the source for a field, set that field to null rather than inventing a quote." This may cause the LLM to null out fields that ARE in the content if it can't produce a verbatim quote.

**Verdict**: This is the intended tradeoff (precision over recall on retry). The retry only triggers when the original extraction had fabricated quotes. Losing a field is better than keeping a hallucinated one. Monitoring recommended but no fix needed.

## Summary

| # | Finding | Verdict | Action |
|---|---------|---------|--------|
| 1 | Semaphore double-hold | False positive (80 slots) | None |
| 2 | Entity lists bypass source grounding | **REAL** | Fix needed |
| 3 | Entity list strict_quoting ignored | **REAL** | Fix needed |
| 4 | Redundant content stripping | Real, low impact | Optional optimization |
| 5 | Word presence vs position | By design | None |
| 6 | Backfill timeout risk | Future concern | None |
| 7 | Early-exit threshold | Minor | None |
| 8 | Null-on-no-quote tradeoff | By design | Monitor |

**Priority fixes**: #2 and #3 together — entity list source grounding support.
