# Pipeline Review: Fixes D, F, G, H

**Status:** All issues resolved

## Flow

```
API (projects.py:consolidate_project)
  → [use_llm=true] Job(type=CONSOLIDATE) → 202 → ConsolidationWorker
  → [use_llm=false] ConsolidationService inline
    → consolidate_extractions() [pure, sync]
    → _llm_post_process() [async, Fix D]
    → _upsert_record()

Extraction (schema_orchestrator.py:_extract_entity_chunk_v2)
  → ground_entity_fields() [Fix F, with grounding_mode overrides]
  → score_entity_confidence() [Fix H]
  → apply_grounding_gate() [with grounding_mode_overrides]
    → _filter_entity_fields() [Fix F]
```

---

## Resolved Issues

### ✅ 1. Fix G `grounding_mode` overrides ignored in grounding gate

**Fix**: `gate_grounding_overrides` dict built from `FieldDefinition.grounding_mode`, passed to `apply_grounding_gate()` via new `grounding_mode_overrides` parameter. `_grounding_mode()` helper checks overrides before falling back to `GROUNDING_DEFAULTS`.

### ✅ 2. Fix G `grounding_mode` overrides ignored in `ground_entity_fields()`

**Fix**: `field_defs` dicts now include `"grounding_mode"` key when override is set. `ground_entity_fields()` builds `grounding_mode_map` from field defs and checks it before `GROUNDING_DEFAULTS`.

### ✅ 3. Sequential LLM calls in single HTTP request — timeout risk

**Fix**: `use_llm=True` now creates a `Job(type=CONSOLIDATE)` and returns `202 ACCEPTED` with `job_id`. Processing runs in background via `ConsolidationWorker` (polled by `_run_consolidate_worker()` in scheduler). Non-LLM consolidation remains inline.

### ✅ 4. Fix G template overrides don't propagate to existing projects

**Status**: Documented as post-deploy step in `TODO_consolidation_quality.md`. Existing projects need manual schema update via `PUT /projects/{id}`.
