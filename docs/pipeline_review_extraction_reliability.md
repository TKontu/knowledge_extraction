# Pipeline Review: Extraction Reliability Plan vs Actual Implementation

**Date**: 2026-02-06
**Scope**: End-to-end trace of the 4-phase plan in `docs/TODO_extraction_reliability.md`
**Verdict**: Plan addresses real problems, but has **3 critical gaps**, **4 important issues**, and **2 design assumptions** that need correction before implementation.

---

## Flow Traced

```
API (extraction.py:29) → Worker (worker.py:276) → Pipeline (pipeline.py:568)
  → Orchestrator (schema_orchestrator.py:50) → Classifier (smart_classifier.py:110)
  → Extractor (schema_extractor.py:67) → Chunk Merge (schema_orchestrator.py:274)
  → DB Storage (pipeline.py:550) → Report (service.py:175)
  → Source Aggregation (service.py:520) → Domain Merge (service.py:644)
  → SmartMerge (smart_merge.py:59) → Final Report
```

---

## Critical (must fix before implementation)

### 1. EMBEDDING TOKEN LIMIT — Plan will cause crashes, not silent truncation

**Files**: `smart_classifier.py:510`, `embedding.py:77-94`

The plan increases the classification window from 2000 to 4000 chars. The assumption (TODO line 142) is: "BGE-large-en handles truncation internally." **This is wrong.**

**Evidence (verified against live embedding server 2026-02-06)**:
- `embedding.py:90-93` — No truncation parameter passed to the API
- BGE-large-en has a hard 512-token limit (BERT positional encoding)
- vLLM embedding server does NOT auto-truncate — returns **HTTP 400** on inputs >512 tokens
- Verified experimentally:
  - 2000 chars → 345 tokens → OK
  - 2500 chars → 436 tokens → OK
  - 3000 chars → ~520 tokens → **CRASH (HTTP 400)**
  - 4000 chars → ~690 tokens → **CRASH (HTTP 400)**
- Safe ceiling: ~2800 chars for typical English content
- The `_create_page_summary()` adds URL + title prefix (~60 chars), so effective safe content window is ~2740 chars

**Impact**: Increasing to 4000 chars will cause **HTTP 400 errors on every single page**. The `@retry` decorator (3 attempts) will burn through retries, then the classifier falls back to rule-based (line 150-163), effectively disabling smart classification and wasting ~30 seconds per page on failed retries.

**Fix options**:
- A) Add truncation in `EmbeddingService.embed()` before API call — cap at ~2700 chars (safe, applies everywhere)
- B) Keep 2000-char window (verified safe at 345 tokens) + add truncation safety net in EmbeddingService
- C) **Use a longer-context embedding model** (e.g., `bge-m3` supports 8192 tokens, 1024 dims). The server already runs vLLM and supports multiple models — adding bge-m3 alongside bge-large-en is straightforward. This gives 16x more context for classification.
- Available models on server (192.168.0.136): `bge-large-en`, `bge-reranker-v2-m3`, plus multiple LLMs. Server supports adding new models.

### 2. CONFIDENCE=NONE BYPASSES MERGE FILTER — Structural hole in the defense

**Files**: `smart_merge.py:83`, `schema_orchestrator.py:339`

The merge filter at `smart_merge.py:83` explicitly allows `confidence=None` candidates through:
```python
if c.confidence is None or c.confidence >= self._min_confidence
```

The current system never lets None reach this point because the chunk merge defaults to 0.8 (`schema_orchestrator.py:339`). But the chain of 0.8 fallbacks is fragile:

```
LLM returns no confidence → _apply_defaults ignores it (not a schema field)
  → chunk merge: r.get("confidence", 0.8) → 0.8
    → orchestrator: merged.pop("confidence", 0.8) → 0.8
      → Phase 3A: min(0.8, 0.1) = 0.1 for empty extractions ✓
```

Phase 3A works **by accident** — because the 0.8 default feeds into `min(0.8, 0.1)`. But:
- If someone changes the chunk merge default, Phase 3A breaks
- If confidence somehow arrives as None at the merge, it passes all filters
- The plan's Phase 3A code `min(raw_confidence, 0.1)` would crash with `TypeError` if raw_confidence were None

**Fix**: Make Phase 3A explicitly handle None:
```python
raw_confidence = merged.pop("confidence", None)
if raw_confidence is None:
    raw_confidence = 0.0 if is_empty else 0.5 * populated_ratio
```

AND fix the merge filter to treat None as low confidence:
```python
if c.confidence is not None and c.confidence >= self._min_confidence
```

### 3. PHASE 4 SOLVES A MOSTLY NON-EXISTENT PROBLEM

**Files**: `schema_orchestrator.py:124-128`, `service.py:520-642`

Phase 1 filters field groups **before extraction** (`schema_orchestrator.py:127`). After Phase 1, if classification says a page is relevant for `["products_gearbox", "manufacturing"]`, then only those field groups are extracted. **No `company_meta` extraction exists for that source.**

Therefore, Phase 4's plan to dampen `company_meta` confidence from product pages is pointless — that extraction doesn't exist.

Phase 4 only helps in edge cases:
- Pre-existing data (before classification was deployed)
- Classification disabled/failed
- Low-confidence fallback

**Recommendation**: Don't implement Phase 4 as planned. Instead:
- Add a data migration to re-classify existing sources
- Focus effort on making Phase 1 classification more accurate
- If Phase 4 is kept, document it as a "legacy data safety net" only

---

## Important (should fix)

### 4. TOP-3 FALLBACK CAN LOSE DATA ON COMPREHENSIVE PAGES

**File**: `smart_classifier.py:250-266`

Phase 1D changes the low-confidence fallback from "all groups" to "top 3 groups." This is dangerous for single-page sites or comprehensive pages (e.g., small company with one "About" page containing company info + products + locations + certifications).

If embedding similarity is uniformly low across all groups (0.30-0.38), the top 3 limit arbitrarily excludes 4 field groups and **loses real data**.

**Fix**: Use a dynamic threshold instead of fixed top-N:
```python
# Include all groups within 80% of the top score
top_score = sorted_groups[0][1]
cutoff = top_score * 0.8
top_groups = [name for name, score in sorted_groups if score >= cutoff]
# But cap at minimum 2, maximum len(field_groups)
top_groups = top_groups[:max(2, len(top_groups))]
```

Or simpler: keep "all groups" for low confidence (current behavior) and rely on Phase 3A (confidence recalibration) to filter the noise. The two phases together achieve the same goal without data loss.

### 5. BOOLEAN MAJORITY VOTE NEEDS A SUBTLETY

**Files**: `schema_orchestrator.py:303-304`, `schema_extractor.py:443-456`

The majority vote fix is **correct and necessary**. `_apply_defaults()` converting null → False actually helps (creates `[True, False, False]` instead of `[True]`).

However, the proposed `true_count > len(values) / 2` has a tie-breaking edge case:
- 2 chunks: `[True, False]` → `1 > 1.0` → False (correct, conservative)
- 4 chunks: `[True, True, False, False]` → `2 > 2.0` → False (correct, conservative)

But there's a deeper issue: **if only 1 out of 5 chunks has ANY relevant content, but that content clearly says "has_manufacturing: true", the majority vote will say False.** The 4 irrelevant chunks all default to False and outvote the one real chunk.

**Consideration**: Weight the vote by chunk confidence, not just count. Or better: if Phase 3A's `_is_empty_result()` detects that a chunk is empty, exclude it from the boolean vote entirely.

### 6. ENTITY-LIST PROMPT HAS DOMAIN-SPECIFIC TEXT

**File**: `schema_extractor.py:407`

The plan correctly identifies that line 407 (`"For locations: focus on headquarters..."`) violates template-agnostic design. But line 408 (`"For products: focus on main product lines..."`) also needs removal.

Both lines should be deleted:
```python
# REMOVE both:
# - For locations: focus on headquarters, manufacturing sites, main offices - NOT delivery areas or coverage lists
# - For products: focus on main product lines, not every variant or option
```

### 7. PLAN LINE NUMBER REFERENCES ARE STALE

**File**: `service.py`

The plan references `_aggregate_by_source()` lines 627-629, but the actual confidence tracking code is at different lines. The `ext_confidence` tracking happens around lines 580-629 with the composite confidence logic needing to be inserted around line 615/629 (the `column_confidences[col_name] = ext_confidence` assignments).

Not a code issue, but agents following the plan will look at wrong locations.

---

## Design Observations

### 8. CLASSIFICATION + RECALIBRATION MAKES PHASE 4 REDUNDANT

The layered defense has redundancy that's worth acknowledging:

| What catches the problem | Phase 1 | Phase 2 | Phase 3A | Phase 3B | Phase 4 |
|---|---|---|---|---|---|
| Product page → no company_meta extraction | ✅ | - | - | - | - |
| LLM hallucinates on relevant page | - | ✅ | ✅ | ✅ | - |
| Empty extraction stored as real | - | - | ✅ | - | - |
| One hallucinating chunk | - | - | - | ✅ | - |
| Merge drowns real in noise | ✅ | - | ✅ | - | (edge) |

Phase 4 only catches a scenario that Phases 1+3A already handle. It adds complexity (threading metadata through report pipeline) for marginal benefit.

### 9. SMALL LLM IS THE ROOT CAUSE — CONSIDER UPGRADING

The entire 4-phase plan exists because the small LLM hallucinates. All the structural guardrails are **compensating for model weakness**. The user mentioned new models can be added. Consider:

**For extraction** (currently Qwen3-30B / gemma3-12b-awq):
- A larger model (e.g., Qwen3-32B full precision, Llama-3.1-70B-4bit, or Mistral-Small-3.2-24B) would reduce hallucination at the source
- Even with a better model, Phases 1-3 are still valuable — but the urgency drops significantly
- A larger model can follow nuanced instructions (the grounding rules in Phase 2 would work much better)

**For embeddings** (currently BGE-large-en, 512 tokens):
- Upgrading to a longer-context embedding model (bge-m3 at 8192 tokens, or nomic-embed-text-v1.5 at 8192) would fix the classification window problem entirely
- No need for the 2000→4000 char increase — just use the full page content
- Better embeddings = better classification = less noise reaching the LLM

**For reranking** (currently bge-reranker-v2-m3):
- This is already a good model, no change needed

---

## Summary Table

| # | Severity | Issue | Phase | Fix Effort |
|---|----------|-------|-------|------------|
| 1 | 🔴 Critical | Embedding token limit — 4000 chars will crash | 1C | Model upgrade or add truncation |
| 2 | 🔴 Critical | confidence=None bypasses merge filter | 3A/merge | Small code fix |
| 3 | 🔴 Critical | Phase 4 solves non-existent problem (post Phase 1) | 4 | Remove/redesign |
| 4 | 🟠 Important | Top-3 fallback loses data on comprehensive pages | 1D | Dynamic threshold |
| 5 | 🟠 Important | Boolean majority vote can be outvoted by empty chunks | 3B | Weight by content |
| 6 | 🟠 Important | Line 408 also domain-specific (not just 407) | 2 | Delete both lines |
| 7 | 🟠 Important | Plan line number references are stale | All | Update plan |
| 8 | 🟡 Design | Phase 4 redundant with Phases 1+3A | 4 | Accept or remove |
| 9 | 🟡 Design | Small LLM is root cause — model upgrade reduces all issues | All | Evaluate models |

---

## Recommended Revised Plan

**If adding new models is an option:**

1. **Upgrade embedding model** to bge-m3 or nomic-embed-text (8192 tokens) — fixes #1 completely, improves classification dramatically
2. **Consider upgrading extraction LLM** — reduces hallucination at source, makes Phases 2-3 less critical
3. **Implement Phase 1** (enable classification) — low risk, high value, already built
4. **Implement Phase 2** (stronger prompts) — low risk, helps even with better model
5. **Implement Phase 3A+3B** (recalibration + majority vote) — with fixes for #2 and #5
6. **Skip Phase 4** — Phase 1+3A make it unnecessary. Revisit only if problems persist after re-extraction.
7. **Add truncation safety** to EmbeddingService regardless — defensive programming
