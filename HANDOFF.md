# Handoff: Extraction Reliability — All Code Complete

Updated: 2026-02-26

## Completed

### Planning & Analysis
- **Pipeline review** — traced full extraction pipeline, identified 3 critical gaps + 4 important issues
  - `docs/pipeline_review_extraction_reliability.md`
- **Content Quality Audit** — 12,069 real pages, 38% nav-dominated
- **Plan v3.2** — `docs/TODO_extraction_reliability.md`
- **Plan review** — `docs/Plan_review.md` (gap analysis)

### Phase 0: Model & Infrastructure
- 0A: Embedding model bge-large-en → bge-m3
- 0B: MAX_EMBED_CHARS=28000 truncation guard
- 7 new tests

### Phase 1: Classification Quality (classification NOT yet enabled)
- 1B: prompt_hint in field group embeddings
- 1C: Classification window 2000→6000 chars
- 1D: Dynamic fallback (80% threshold, min 2 groups)
- 1E: `content_cleaner.py` (Layer 1 patterns + Layer 2 density windowing)
- 39 new tests

### Phase 2: Prompts + Extraction Window
- 2A: Grounding rules + confidence guidance in both prompt paths
- 2B: Content window 8K→20K (`EXTRACTION_CONTENT_LIMIT=20000`)
- 2C: Layer 1 cleaning on extraction input before truncation
- 17 new tests

### Phase 3: Post-Extraction Fixes
- 3A: `_is_empty_result()` + confidence recalibration
- 3B: Boolean majority vote (replaces `any()`)
- 3C: Fixed confidence=None bypass in smart_merge
- 20 new tests

### Post-Implementation Review Fixes
Pipeline review (`docs/pipeline_review_phase0123.md`) found 12 residual issues. Fixed the 4 real ones:
- **Fix #1**: Updated `_merge_chunk_results` docstring (any() → majority vote)
- **Fix #3**: Added `strip_structural_junk()` to worker.py fallback paths (both `_extract_facts` and `_extract_field_group`)
- **Fix #4**: Added confidence calibration guidance to entity-list prompt (0.0/0.5-0.7/0.8-1.0 scale)
- **Fix #7**: Changed confidence fallback 0.8→0.5 in both `_merge_chunk_results` and `_merge_entity_lists`

## In Progress

Nothing — all code changes complete.

## Next Steps

- [ ] **Phase 1A: Enable classification** — flip 4 config booleans to True in `src/config.py`
- [ ] Re-extract David Brown Santasalo to validate improvements
- [ ] Commit all changes (nothing committed yet — ~1,124 lines added across 15 files + 5 new files)

## Key Files Modified

| File | Changes |
|------|---------|
| `src/config.py` | bge-m3 default |
| `src/services/storage/embedding.py` | MAX_EMBED_CHARS truncation guard |
| `src/services/extraction/content_cleaner.py` | **NEW** — Layer 1 + Layer 2 cleaning |
| `src/services/extraction/smart_classifier.py` | prompt_hint, 6000 window, dynamic fallback, cleaning integration |
| `src/services/extraction/schema_extractor.py` | Grounding rules, confidence guidance (both prompts), EXTRACTION_CONTENT_LIMIT=20000, Layer 1 cleaning |
| `src/services/llm/worker.py` | EXTRACTION_CONTENT_LIMIT=20000, strip_structural_junk in fallback paths |
| `src/services/extraction/schema_orchestrator.py` | _is_empty_result(), confidence recalibration, majority vote, 0.5 fallback |
| `src/services/reports/smart_merge.py` | Fixed confidence=None bypass |
| `.env.example`, `stack.env` | RAG_EMBEDDING_MODEL=bge-m3 |

## Test Coverage

| Test File | Tests | Status |
|-----------|-------|--------|
| `test_embedding_service.py` | 24 | Pass |
| `test_content_cleaner.py` | 27 | Pass |
| `test_smart_classifier.py` | 53 | Pass |
| `test_schema_orchestrator.py` | 20 | Pass |
| `test_smart_merge.py` | 4 | Pass |
| `test_schema_extractor.py` | 22 | Pass |
| `test_schema_extractor_queue.py` | 8 | Pass |
| **Total** | **158** | **All pass** |

## Context

- All changes are **unstaged** — nothing committed yet
- Phase 1A (enable classification) is deliberately last — it's the only MEDIUM risk step
- The 4 config booleans for Phase 1A: `classification_enabled`, `classification_skip_enabled`, `smart_classification_enabled`, `classification_filter_enabled`
- DBS project ID for verification: `b0cd5830-92b0-4e5e-be07-1e16598e6b78`
- Pipeline review issues #2, #5, #6, #8-12 were assessed as not worth fixing (dead code guards, theoretical, or low-impact)
