# Pipeline Review #4 — Grounding & Consolidation Architecture

**Date:** 2026-03-06
**Scope:** Full assessment of grounding verification + consolidation pipeline implementation against planned architecture in `TODO_grounded_extraction.md` and `TODO_downstream_trials.md`
**Method:** 4 parallel code review agents (grounding, consolidation, pipeline integration, schema/config), synthesized findings. All findings re-verified against source code — 2 false positives removed, 1 overstated finding downgraded.
**Prior reviews:** `PIPELINE_REVIEW_3.md` — 15 issues (7 fixed in uncommitted changes)

---

## Executive Summary

The grounding and consolidation implementation is **architecturally sound, production-ready, and faithful to the spec**. All planned features from Phases 1-2 of the revised architecture are implemented. The primary gaps are in **template-agnostic configurability** — grounding modes and consolidation strategies work via hardcoded defaults rather than per-field schema declarations. This is acceptable for v1 but blocks multi-template deployments.

| Area | Verdict | Details |
|------|---------|---------|
| String-match grounding | Production-ready | All spec requirements met, 25+ tests, multilingual numeric formats |
| LLM quote verification | Production-ready | Qwen3-30B integration complete, proper async/error handling, 12+ tests |
| Inline grounding (orchestrator) | Production-ready | Per-chunk scoring with fallback, aligned value+quote pairs |
| 6 consolidation strategies | Production-ready | All correct, 72 pure-function tests |
| Consolidation DB service | Production-ready | SAVEPOINT isolation, upsert, stale cleanup, 13 tests |
| Schema-driven config | Partially implemented | Hardcoded defaults work; per-field overrides not wired through schema |
| Pipeline data flow | Complete with gaps | Entity lists lack per-item grounding; embedding tracking imprecise |

**Total test coverage:** ~85 consolidation + ~47 grounding = ~132 tests across the subsystem.

---

## Spec Alignment Matrix

### Phase 1: Two-Tier Grounding Verification

| Spec Requirement | Status | Location | Notes |
|-----------------|--------|----------|-------|
| String-match numeric (locale-aware: 1000/1,000/1.000/1 000) | DONE | `grounding.py:246-330` | European dot, French space, negatives, decimals all handled |
| String-match string (case, hyphen, whitespace normalization) | DONE | `grounding.py:60-96` | Comprehensive normalization |
| String-match list (fraction of items grounded) | DONE | `grounding.py:99-126` | Dict/name key handling for entity lists |
| Three grounding modes (required/semantic/none) | DONE | `grounding.py:19-27` | Correct defaults by type |
| Sensible defaults by type | DONE | `grounding.py:19-27` | string/int/float/enum/list=required, boolean=semantic, text=none |
| LLM quote verification (Qwen3-30B) | DONE | `llm_grounding.py:53-124` | YES/NO with reason, json_object response format |
| LLM skips booleans (35% false rejection) | DONE | `llm_grounding.py:159-163` | Correctly filters by grounding_mode |
| LLM skips already-grounded (score >= 0.5) | DONE | `llm_grounding.py:151` | Threshold documented in code |
| Scores: string-match 0.0-1.0, LLM binary 0.0/1.0 | DONE | `grounding.py`, `llm_grounding.py:171-181` | Correct |
| Grounding scores stored on Extraction (JSONB) | DONE | `orm_models.py:296-297` | Per-field dict |
| Inline scoring during extraction | DONE | `schema_orchestrator.py:474-508` | Per-chunk alignment, fallback to merged |
| Retroactive backfill script | DONE | `scripts/backfill_grounding_scores.py` | String-match + LLM passes, dry-run, batching |
| DB migration for grounding_scores column | DONE | `alembic/versions/20260305_add_grounding_scores.py` | JSONB, nullable |

### Phase 2: Grounding-Weighted Consolidation

| Spec Requirement | Status | Location | Notes |
|-----------------|--------|----------|-------|
| `frequency` — most-frequent, case-insensitive | DONE | `consolidation.py:71-106` | Ties broken by total weight |
| `weighted_frequency` — sum(conf x grounding) per value | DONE | `consolidation.py:109-134` | Correct |
| `weighted_median` — grounding-weighted, exclude ungrounded | DONE | `consolidation.py:137-189` | Fallback to unweighted when all weights=0 |
| `any_true` — N+ True at min confidence+grounding | DONE | `consolidation.py:192-213` | Default min_count=2 |
| `longest_top_k` — longest from top-K by weight | DONE | `consolidation.py:216-232` | Default k=3 |
| `union_dedup` — union + dedup by normalized name | DONE | `consolidation.py:235-259` | Dict attribute merging across occurrences |
| `effective_weight = confidence * grounding_score` | DONE | `consolidation.py:265-283` | See design deviation below |
| Pure function (no side effects) | DONE | `consolidation.py` | 100% pure, stdlib only |
| Provenance (source_count, agreement, grounded_count, top_sources) | DONE | `consolidation.py:48-56`, `consolidation_service.py:191-196` | Stored in JSONB |
| ConsolidatedExtraction table | DONE | `orm_models.py:418-459`, migration | Unique on (project_id, source_group, extraction_type) |
| Upsert with stale record cleanup | DONE | `consolidation_service.py:56-63, 201-220` | DELETE before INSERT per source_group |
| SAVEPOINT per source group | DONE | `consolidation_service.py:157-169` | Error isolation |
| Idempotent/re-runnable | DONE | Tested in `test_consolidation_service.py:306-341` | Run twice = same result |
| API endpoint POST /projects/{id}/consolidate | DONE | `api/v1/projects.py:303-343` | Optional source_group param |
| Reconsolidate endpoint | DONE | `consolidation_service.py:133-141` | Delegates to same logic |

### Phases 3-6 (Not Yet Implemented — Expected)

| Phase | Status | Notes |
|-------|--------|-------|
| Phase 3: Multilingual handling | NOT STARTED | Language detection + LLM product dedup — deferred per plan |
| Phase 4: Report integration | NOT STARTED | Reports still read raw extractions |
| Phase 5: LLM skip-gate | NOT STARTED | Spec ready in `TODO_classification_robustness.md` |
| Phase 6: Prompt improvements | DEFERRED | Trials proved 47-80% recall loss — correctly shelved |

---

## Design Deviations from Spec

### D1. Grounding weight uses continuous floor (0.1) instead of cliff at 0.5

**Spec says:** `required + grounding < 0.5 -> weight 0.0` (exclude entirely)
**Implementation:** `confidence * max(grounding_score, 0.1)` — continuous with 0.1 floor

**Location:** `consolidation.py:265-283`

**Rationale (documented in code):** Prevents producing `None` when ALL extractions are ungrounded. The spec acknowledges "some data > no data" but doesn't reconcile with the 0.0 cliff.

**Assessment:** This is a **correct design improvement** over the spec. The cliff at 0.5 would cause `weighted_median` and `weighted_frequency` to return None for fields where string-match grounding fails globally (e.g., multilingual numbers before LLM pass). The continuous floor keeps the pipeline functional while still strongly deprioritizing ungrounded values (0.09 vs 0.81 for a grounded value).

**Risk:** Low. The 11x weight difference between grounded (0.9 * 0.9 = 0.81) and ungrounded (0.9 * 0.1 = 0.09) is sufficient for consolidation strategies to prefer grounded values.

**Action:** None required. Document in spec update.

### D2. `any_true` default min_count=2, not 3

**Spec says:** `any_true(min=3, min_conf=0.7)` as recommended from Trial 2A
**Implementation:** `min_count=2` default in `consolidation.py:192`

**Assessment:** Minor. The consolidation_service passes schema-driven params, so this is the fallback default. min_count=2 is more permissive (higher recall, slightly more false positives). Configurable per call.

**Action:** Consider changing default to 3 to match trial findings, or document the choice.

---

## Issues Found

### CRITICAL — None

No critical issues found. The architecture is sound.

### HIGH — Architectural Gaps

#### H1. Schema-driven grounding mode NOT wired through templates

**Files:** `schema_adapter.py:404-443`, `grounding.py:19-27`

`FieldDefinition` dataclass has no `grounding_mode` field. Templates cannot declare per-field grounding modes — only hardcoded `GROUNDING_DEFAULTS` by type are used.

**Impact:** Cannot customize grounding for specific fields (e.g., `company_description` is type `string` but should be `none`). All strings are treated as `required` grounding.

**Current mitigation:** The defaults cover the common case correctly. For the DBS template specifically, string fields that are synthesized (descriptions) are typed as `text` which defaults to `none`.

**Fix:** Add `grounding_mode: str | None = None` to `FieldDefinition`, parse from schema in `schema_adapter.py`, fall back to `GROUNDING_DEFAULTS` when None.

**Effort:** Small. ~20 lines across 2 files.

#### H2. Entity lists lack per-item grounding scores

**Files:** `schema_orchestrator.py:474-508`, `grounding.py:99-126`

Grounding scores are computed for scalar fields but NOT for individual entity list items. The list-level score checks if items appear in the combined quote, but per-entity grounding (which entity is well-sourced vs fabricated) is lost.

**Impact:** Consolidation's `union_dedup` cannot weight individual entities by grounding quality. All entities in a list are treated equally.

**Current mitigation:** Entity lists are already lower hallucination risk (names are usually grounded at 98.8%). The main entity quality issue is multilingual duplication, not hallucination.

**Fix:** Extend grounding to score per-entity items using their individual `_quote` values. Store as nested dict: `{field_name: {entity_name: score}}`.

**Effort:** Medium. Requires changes to score storage format and consolidation weighting.

#### H3. Embedding tracking is all-or-nothing per chunk

**File:** `pipeline.py:400-403`

```python
if embed_result.embedded_count == len(chunk_extractions):
    for e in chunk_extractions:
        e.embedded = True
```

If 10/11 extractions embed successfully, ALL remain `embedded=False`. The `get_unembedded()` query then re-processes all 11, including the 10 that already succeeded.

**Impact:** Re-embedding wastes work on already-embedded extractions. Could cause duplicate vectors in Qdrant if the re-embed doesn't check for existing IDs.

**Fix:** Mark individual extractions as `embedded=True` based on which ones actually succeeded, not all-or-nothing. Requires `embed_and_upsert()` to return per-extraction success status.

**Effort:** Medium. Requires changes to embedding pipeline return type.

### MEDIUM — Correctness Concerns

#### ~~M1. `grounding_llm_verify_model` config field referenced but doesn't exist~~ FALSE POSITIVE

Verified: field exists at `config.py:609-612`. Backfill script reads it correctly at line 164. No issue.

#### ~~M2. LLM rejection counter is misleading in backfill~~ DOWNGRADED TO LOW

Logic at `backfill_grounding_scores.py:223` is correct: LLM was called, score stayed 0.0, counter tracks "LLM confirmed ungrounded." The variable name `rejected` is slightly ambiguous but the comment is accurate. Not a bug.

#### M3. `grounded_count` on ConsolidatedExtraction uses max() across fields

**File:** `consolidation_service.py:198`

```python
total_grounded = max(total_grounded, field.grounded_count)
```

Takes the maximum `grounded_count` across all fields for the record-level column. This means "at least N sources were grounded for *some* field" rather than an average or per-field breakdown.

**Impact:** The DB column is informational only — the real per-field grounded_count is in the provenance JSONB. But the column value could mislead if used in queries without understanding the semantics.

**Fix:** Add a comment documenting the semantics, or use average.

**Effort:** Trivial.

#### M4. LLM response type check could be more defensive

**File:** `llm_grounding.py:95-102`

Checks `if supported is None` to detect parsing failure, but doesn't check `isinstance(supported, bool)`. A malformed LLM response returning `supported: "yes"` (string instead of bool) would be treated as truthy.

**Impact:** Low — json_object mode enforces schema structure, and the prompt explicitly asks for boolean.

**Fix:** Add `if not isinstance(supported, bool):` check.

**Effort:** Trivial.

#### M5. Consolidation strategy not validated in schema adapter

**File:** `schema_adapter.py:222-380`

Templates can include `consolidation_strategy` on fields (and `consolidate_extractions()` reads it), but `schema_adapter.py` doesn't validate the value against known strategies. An invalid strategy name silently falls back to `frequency`.

**Impact:** Template authors won't get validation errors for typos. Silent fallback could produce unexpected consolidation results.

**Fix:** Add validation in `schema_adapter.py` against `STRATEGY_DEFAULTS` keys + the 6 strategy names.

**Effort:** Small.

### LOW — Minor Improvements

#### L1. No integration test for end-to-end grounding flow

No test creates an extraction via the pipeline and verifies grounding_scores appear in the DB. Unit tests cover each layer independently but don't verify the full flow.

#### ~~L2. Backfill batch size for LLM hardcoded at 100~~ FALSE POSITIVE

Wrong line reference (file is ~260 lines). No such cap found in actual code.

#### L3. Version number regex edge case

`grounding.py:262` — Pattern matches version-like strings (e.g., "1.5.3" → extracts "1.5"). Acceptable because source content context disambiguates.

#### L4. Consolidation agreement for lists always 1.0

`consolidation.py:330` — `union_dedup` results always report 100% agreement since the union inherently "agrees." This is technically correct but uninformative. Consider tracking overlap ratio instead.

---

## Architecture Assessment

### Strengths

1. **Layered verification is correct.** String-match first (free, 83% detection), LLM second (3.2s/claim, handles multilingual). Matches trial findings exactly.

2. **Post-extraction, not prompt-based.** Correctly implements the key trial finding: prompt-based grounding causes 47-80% recall loss. The implementation never modifies extraction prompts for grounding.

3. **Grounding as weight, not filter.** Values are never deleted — they're deprioritized via `effective_weight`. This preserves data for edge cases where all sources are ungrounded.

4. **Pure function consolidation.** All 6 strategies are side-effect-free, independently testable. The DB service wraps them cleanly with proper transaction management.

5. **SAVEPOINT isolation.** Consolidation failures in one source_group don't destroy others. This is a critical correctness fix from PIPELINE_REVIEW_3 (C1).

6. **Provenance tracking.** Every consolidated field records strategy, source_count, agreement, grounded_count, top_sources. This enables downstream audit and confidence indicators.

7. **Idempotent reconsolidation.** DELETE-before-INSERT pattern ensures reconsolidation produces identical results. Safe to re-run after schema changes, new extractions, or grounding score updates.

### Weaknesses

1. **Schema-driven config is incomplete.** Grounding modes and consolidation strategies are hardcoded defaults. Templates can't override per-field without code changes. This is the biggest architectural gap for multi-template support.

2. **Entity-level grounding is missing.** Lists are scored at the aggregate level, not per-item. This means entity consolidation (`union_dedup`) treats all entities equally regardless of individual grounding quality.

3. **No feedback loop.** Grounding scores are computed once (during extraction or backfill) and never updated. If LLM verification runs later, there's no trigger to reconsolidate affected source_groups automatically.

4. **Phases 3-6 are unimplemented.** Multilingual dedup, report integration, skip-gate, and search are all pending. The architecture supports them but they don't exist yet.

### Reliability Assessment

| Dimension | Rating | Evidence |
|-----------|--------|----------|
| Correctness | High | 132+ tests, all strategies validated against trial findings |
| Error handling | High | SAVEPOINT isolation, LLM timeout handling, graceful degradation |
| Data integrity | High | Idempotent upserts, transaction boundaries, no silent data loss |
| Performance | Medium | Batch updates use bulk_update_mappings; LLM verification at ~3.2s/claim is acceptable |
| Observability | Medium | Structured logging throughout, but no metrics/counters for monitoring |
| Configurability | Low | Hardcoded defaults; per-field overrides not wired |

---

## Recommendations (Prioritized)

### Before Production Deploy

1. **Apply pending DB migrations** — 3 migrations need deployment to remote
2. **Run string-match backfill** — Immediate quality improvement on 47K extractions
3. **Run LLM verification pass** — Catches multilingual cases string-match misses
4. **Run consolidation** — POST /projects/{id}/consolidate on main project

### Short-Term Fixes (Next Sprint)

| # | Issue | Effort | Impact |
|---|-------|--------|--------|
| 1 | H1 — Wire grounding_mode through FieldDefinition + schema_adapter | Small | Enables per-field grounding customization |
| 2 | M5 — Validate consolidation_strategy in schema_adapter | Small | Prevents silent strategy fallback on typos |
| 3 | H3 — Per-extraction embedding tracking | Medium | Prevents duplicate vectors on re-embed |
| 4 | M4 — Defensive type check in LLM response parsing | Trivial | Robustness |
| 5 | D2 — Change any_true default min_count to 3 | Trivial | Matches trial findings |

### Medium-Term (Next Feature Work)

| # | Feature | Effort | Impact |
|---|---------|--------|--------|
| 1 | Phase 4: Report integration with consolidation | Medium | Makes reports usable |
| 2 | Phase 5: LLM skip-gate | Medium | Eliminates 57.7% extraction waste |
| 3 | Phase 3: Multilingual product dedup | Medium | Reduces 30-40% false duplicates |
| 4 | H2: Per-entity grounding scores | Medium | Better entity-level quality signals |
| 5 | Automatic reconsolidation trigger after grounding updates | Small | Closes feedback loop |

---

## Files Reviewed

| File | Lines | Role |
|------|-------|------|
| `src/services/extraction/grounding.py` | ~330 | String-match verification (pure functions) |
| `src/services/extraction/llm_grounding.py` | ~200 | LLM quote verification (async) |
| `src/services/extraction/consolidation.py` | ~470 | 6 consolidation strategies (pure functions) |
| `src/services/extraction/consolidation_service.py` | ~230 | DB integration, SAVEPOINT isolation, upsert |
| `src/services/extraction/schema_orchestrator.py` | ~600 | Inline grounding, chunk merge, entity dedup |
| `src/services/extraction/schema_extractor.py` | ~310 | Truncation handling |
| `src/services/extraction/schema_adapter.py` | ~480 | Schema validation (missing grounding/consolidation fields) |
| `src/services/extraction/pipeline.py` | ~410 | Pipeline flow, embedding tracking |
| `src/services/extraction/schema_validator.py` | ~80 | Confidence gate (preserves data) |
| `src/services/storage/repositories/extraction.py` | ~490 | Batch updates, get_unembedded |
| `src/services/scraper/service_container.py` | ~150 | Shutdown resilience |
| `src/orm_models.py` | ~460 | Extraction.grounding_scores, ConsolidatedExtraction |
| `src/api/v1/projects.py` | ~340 | Consolidation endpoint |
| `src/config.py` | ~620 | Grounding config flags |
| `scripts/backfill_grounding_scores.py` | ~310 | Retroactive scoring CLI |
| `tests/test_consolidation.py` | ~660 | 72 pure-function tests |
| `tests/test_consolidation_service.py` | ~540 | 13 DB integration tests |
| `tests/test_grounding.py` | — | 25+ grounding tests |
| `tests/test_llm_grounding.py` | — | 12+ LLM verification tests |
