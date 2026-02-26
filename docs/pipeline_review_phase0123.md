# Pipeline Review: Extraction Reliability Changes (Phases 0, 1-quality, 2, 3)

Date: 2026-02-26

## Flow

```
pipeline.py:extract_source()
  → orchestrator.extract_all_groups(markdown=source.content)
    → [classification: smart_classifier.classify() or page_classifier.classify()]
    → chunk_document(markdown) → chunks
    → per chunk: extractor.extract_field_group(content=chunk.content)
      → _build_user_prompt(): strip_structural_junk(content)[:20000]
      → _build_system_prompt(): grounding rules + confidence guidance
      → LLM call → JSON result with confidence
    → _merge_chunk_results() → merged dict with averaged confidence
    → _is_empty_result() → recalibrate confidence
  → store Extraction(confidence=...)
  → [report path]: service.py → MergeCandidate(confidence=...) → smart_merge.merge_column()
```

---

## Critical (must fix)

### 1. Docstring says `any()` but code does majority vote
**`src/services/extraction/schema_orchestrator.py:289-290`**

The `_merge_chunk_results` docstring still says `boolean: True if ANY chunk says True` but the code now uses majority vote (line 314-315). The docstring is misleading and will confuse future readers.

```python
# Line 290 says:
#   - boolean: True if ANY chunk says True
# Line 314-315 actually does:
#   true_count = sum(1 for v in values if v is True)
#   merged[field.name] = true_count > len(values) / 2
```

### 2. Queue mode sends raw content in payload — wasteful and inconsistent
**`src/services/extraction/schema_extractor.py:131-153`**

In `_extract_via_queue()`, the payload includes both:
- `"content": content` (the raw, uncleaned, full-length content)
- `"user_prompt": user_prompt` (already cleaned and truncated to 20K)

The raw content is **never used** in the normal path because the worker prefers `user_prompt` from the payload (worker.py:448-450). However:
- It bloats Redis queue messages unnecessarily (raw content can be 50K+ chars)
- If the worker's fallback path fires (line 453: `if not system_prompt or not user_prompt`), it will use `content[:EXTRACTION_CONTENT_LIMIT]` **without** `strip_structural_junk()` — so the fallback path skips content cleaning

### 3. Worker fallback path has no content cleaning
**`src/services/llm/worker.py:453-470`**

When the worker falls back to building prompts internally (because `system_prompt` or `user_prompt` is missing from payload), it uses `content[:EXTRACTION_CONTENT_LIMIT]` directly without calling `strip_structural_junk()`. This means:
- Normal path (direct mode or queue with prompts): content IS cleaned
- Worker fallback path: content is NOT cleaned

This is the same raw-vs-clean inconsistency. In practice, the fallback should never fire for `extract_field_group` because `_extract_via_queue` always sends both prompts, but it's a latent bug.

---

## Important (should fix)

### 4. Non-entity prompt instructs confidence; entity-list prompt does not provide guidance scale
**`src/services/extraction/schema_extractor.py:372-376` vs `418-434`**

The non-entity-list system prompt (Phase 2A) now instructs the LLM:
```
Output JSON with exactly these fields and a "confidence" field (0.0-1.0):
- 0.0 if the content has no relevant information
- 0.5-0.7 if only partial information found
- 0.8-1.0 if the content is clearly relevant with good data
```

The entity-list system prompt still only says:
```
"confidence": 0.0-1.0
```
...in the JSON structure example, with no guidance on what values mean. The LLM may use different calibration for entity-list vs non-entity-list extractions, undermining Phase 3A's recalibration which assumes consistent confidence semantics across both paths.

### 5. `_is_empty_result` doesn't account for entity-list confidence already popped
**`src/services/extraction/schema_orchestrator.py:174-175`**

```python
raw_confidence = merged.pop("confidence", None)
is_empty, populated_ratio = self._is_empty_result(merged, group)
```

For entity lists, `_is_empty_result` (line 426-432) iterates `data.items()` and skips `key == "confidence"`. But confidence was already `.pop()`ed, so this guard is dead code for entity lists. Not a bug per se, but the confidence key will never be in `data` at that point — the guard is misleading.

### 6. `_is_empty_result` for entity lists does not provide a meaningful populated_ratio
**`src/services/extraction/schema_orchestrator.py:426-432`**

For entity lists, the function returns either `(False, 1.0)` or `(True, 0.0)` — binary. This means the recalibration formula at line 183:
```python
group_result["confidence"] = raw_confidence * (0.5 + 0.5 * populated_ratio)
```
always computes `raw_confidence * 1.0` for non-empty entity lists, regardless of whether the list has 1 entity or 20. An entity list with a single low-quality entity gets the same confidence boost as a list with 15 well-populated entities.

### 7. `_merge_chunk_results` confidence fallback defaults to 0.8
**`src/services/extraction/schema_orchestrator.py:350`**

```python
confidences = [r.get("confidence", 0.8) for r in chunk_results]
```

If the LLM returns JSON without a `confidence` field, the merge defaults to 0.8. This is the old pre-Phase-2A behavior. Now that Phase 2A instructs the LLM to include confidence in its response, this fallback should rarely fire. But when it does, 0.8 is too generous — it feeds into recalibration as if the LLM was highly confident. A more conservative fallback (e.g., 0.5) would be safer.

### 8. `strip_structural_junk` called once per chunk per field group (redundant)
**`src/services/extraction/schema_extractor.py:454`**

`strip_structural_junk(content)` is called inside `_build_user_prompt()`, which is called once per chunk per field group. For a source with 1 chunk and 5 field groups, the same content is cleaned 5 times with identical results. The cleaning is cheap (~ms), but it could be called once at the orchestrator level before passing content to the extractor.

---

## Minor

### 9. `entity_singular` uses naive `.rstrip("s")` for singularization
**`src/services/extraction/schema_extractor.py:409`**

```python
entity_singular = field_group.name.rstrip("s")
```

This produces wrong results for group names ending in double-s (e.g., `"business"` → `"busine"`) or names not ending in `s` (e.g., `"staff"` → `"staff"`, which is fine, but `"analyses"` → `"analyse"`). Pre-existing issue, not introduced by Phase 2, but now more visible in the grounding rule at line 421: `"If this content does not contain any {entity_singular} information"`.

### 10. `smart_merge.py` still uses `or 0.8` fallbacks after confidence=None is filtered
**`src/services/reports/smart_merge.py:103, 113`**

```python
confidence=c.confidence or 0.8,  # line 103
avg_conf = sum(c.confidence or 0.8 for c in non_null) / len(non_null)  # line 113
```

After the Phase 3C fix (line 81-83), candidates with `confidence=None` are already filtered out. So `c.confidence` should never be `None` in the `non_null` list. The `or 0.8` fallback is dead code — it can never trigger. Not harmful, but misleading about what values are actually possible at this point.

### 11. `_extract_via_queue` logs `content_length=len(content)` — logs raw length, not cleaned
**`src/services/extraction/schema_extractor.py:164`**

The log message reports the raw content length, but the actual content sent to the LLM is cleaned + truncated. This could be confusing when debugging extraction issues ("log says 45K chars but content window is 20K").

### 12. Inconsistent `content[:300]` preview in error logs
**`src/services/extraction/schema_extractor.py:318, 334`**

Error logs use `content[:300]` for preview, which is the raw content. After Phase 2C, the actual content seen by the LLM is `strip_structural_junk(content)[:20000]`. For debugging, the raw preview is still useful but could be confusing if the junk at the start was the cause of the issue.
