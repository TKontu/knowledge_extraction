# Pipeline Review: Grounding Gate & LLM Rescue

## Flow
```
worker.py:_create_schema_pipeline
  → schema_orchestrator.py:_extract_group_v2
    → extract_chunk_v2 (per chunk, semaphore-limited)
      → _extract_entity_chunk_v2 OR _extractor.extract_field_group + _parse_chunk_to_v2
      → source-grounding retry (if quoting enabled)
      → apply_grounding_gate (if verifier configured)
    → chunk_merge.py:merge_chunk_results_v2
  → consolidation.py:effective_weight (downstream)
```

## Important (should fix)

- [ ] **schema_orchestrator.py:1149 — `_parse_entity_chunk_v2` is dead code**
  In `extract_chunk_v2` (line 1001), entity lists branch to `_extract_entity_chunk_v2` directly. Non-entity-lists branch to `_parse_chunk_to_v2`. Inside `_parse_chunk_to_v2`, line 1149 checks `if group.is_entity_list: return self._parse_entity_chunk_v2(...)` — but this is unreachable because `_parse_chunk_to_v2` is only called when `group.is_entity_list is False`. The negation filter added at line 1219 inside `_parse_entity_chunk_v2` never executes. Entities rely on grounding gate to drop fabricated items. Maintenance hazard: someone may add logic to `_parse_entity_chunk_v2` thinking it runs for entity lists.

- [ ] **schema_orchestrator.py:1057-1062 — Gate holds extraction semaphore during LLM rescue**
  `apply_grounding_gate()` runs inside `async with semaphore:` (line 999) where semaphore is `Semaphore(max_concurrent_chunks=80)`. Each rescue call is an LLM request that can take 1-3s. While rescue is running, it holds an extraction concurrency slot, reducing throughput for new chunk extractions. With `rescue_sem=Semaphore(3)` per gate call, worst case = 80 chunks × 3 rescue calls = 240 concurrent rescue LLM calls blocking extraction slots. Practical impact is low (only ~5% of fields trigger rescue) but architecturally suboptimal — gate should run outside the extraction semaphore.

## Minor

- [ ] **llm_grounding.py:93 — `_model` parameter stored but never used**
  `LLMGroundingVerifier.__init__` accepts `model: str | None` and stores it as `self._model`, but neither `verify_quote` nor `rescue_quote` pass it to `self._llm.complete()`. The LLMClient always uses its default model. Dead parameter that could mislead callers into thinking they can select a rescue model.

- [ ] **llm_grounding.py:164 — Stale docstring for `rescue_quote`**
  Docstring says "truncated to ~4000 chars" but actual truncation at line 174 is 16000 chars (updated during review fixes). Docstring is stale.

- [ ] **schema_orchestrator.py:227 — Entity rescue fallback sends dict repr as value**
  When entity has no "name" or "entity_id" field, `_maybe_rescue_entity` uses `str(entity.fields)[:100]` as the rescue value. This produces a confusing prompt like `Claimed value: {'model': 'MX-100', 'power_ra`. Rescue will likely fail (safe default) but wastes an LLM call on an unrecoverable prompt.
