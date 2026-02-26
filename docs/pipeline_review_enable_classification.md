# Pipeline Review: Enable Classification (Phase 1A)

Reviewed: 2026-02-26
Scope: All files modified in the extraction reliability work (Phases 0-3 + 1A enablement)

## Flow

```
schema_orchestrator.extract_all_groups()
├─ IF classification_enabled AND source_url:
│  ├─ SmartClassifier.classify()
│  │  ├─ _resolve_skip_patterns() → rule-based skip check
│  │  ├─ clean_markdown_for_embedding(content) [Layer 1+2]
│  │  ├─ Embed page content (6000 chars) via embedding.py
│  │  ├─ Score against field group embeddings (cached, include prompt_hint)
│  │  ├─ High (>0.75) → use matched groups
│  │  ├─ Medium (0.4-0.75) → rerank
│  │  └─ Low (<0.4) → top 80% of scores, min 2
│  └─ IF skip_extraction AND classification_skip_enabled → return empty
│
├─ chunk_document(markdown) → chunks (always 1 chunk on current data)
│
└─ FOR EACH field_group:
   ├─ schema_extractor.extract_field_group()
   │  ├─ strip_structural_junk(content) [Layer 1 only]
   │  ├─ Truncate to EXTRACTION_CONTENT_LIMIT (20000)
   │  └─ LLM call → JSON parse → _apply_defaults()
   │
   └─ _merge_chunk_results() → _is_empty_result() → confidence recalibration
```

## Critical (must fix)

- [x] **schema_orchestrator.py:183 — Confidence scaling penalizes focused/authoritative pages** *(fixed: removed population scaling, raw confidence passes through)*
  `group_result["confidence"] = raw_confidence * (0.5 + 0.5 * populated_ratio)`

  The scaling conflates "how many fields this page populated" with "how reliable this data is."
  This is backwards: a dedicated Certifications page that only populates 1 of 5 fields is the
  **most authoritative** source for certifications — yet it gets the lowest confidence.

  **Concrete example** (company_info group: name, HQ, employees, is_public, certifications):

  | Page | Populated | Ratio | LLM Conf | Final | Quality |
  |------|-----------|-------|----------|-------|---------|
  | /certifications (dedicated) | 1/5 | 0.20 | 0.8 | **0.48** | Best source for certs |
  | /about (mentions certs) | 4/5 | 0.80 | 0.8 | **0.72** | Passing mention of certs |

  At smart_merge, both compete for the `certifications` column. The dedicated page (0.48) loses
  to the About page (0.72) — even though it's the authoritative source. The merge LLM prompt
  (smart_merge.py:210-213) says "consider confidence scores from extraction", reinforcing this bias.

  **Root cause**: Confidence is per-group but merge is per-column. The `populated_ratio` multiplier
  penalizes page focus/specificity, which actually correlates with authority, not unreliability.

  **The empty detection** (`is_empty` path, line 180-181: cap at 0.1) is correct and sufficient
  for Phase 3A's goal of filtering empty-but-high-confidence hallucinations. The `else` branch
  scaling (line 183) goes beyond that and actively harms merge quality.

  **Fix**: Remove the population-based scaling. Keep only the empty detection:
  ```python
  if is_empty:
      group_result["confidence"] = min(raw_confidence, 0.1)
  else:
      group_result["confidence"] = raw_confidence
  ```

## Important (should fix)

- [x] **EXTRACTION_CONTENT_LIMIT duplicated in two files** *(fixed: worker.py now imports from schema_extractor.py)*

## Minor

- [x] **embedding.py:97,129 — `extra={}` logging works but is inconsistent with project style** *(fixed: switched to structlog with kwargs)*

## Assessed & Not Issues

The following were evaluated and found to be non-issues:

1. **Classification sees Layer 1+2 cleaned content, extraction sees Layer 1 only** — This is intentional and correct. Classification needs clean content for accurate embedding similarity. Extraction preserves all content (Layer 1 only removes truly non-extractable junk). A page could theoretically be classified as irrelevant but contain extractable content buried past nav — but the dynamic fallback (1D: top 80%, min 2 groups) ensures at least 2 groups always proceed, mitigating total misses.

2. **Entity list truncation returns 0.0 confidence on repair failure** — Correct behavior. Unrecoverable truncation = no usable data. Successfully repaired truncated JSON retains LLM's confidence, which is appropriate since the LLM saw the content.

3. **Config boolean interaction complexity** — 4 booleans with defined semantics. The orchestrator checks them in a clear if/elif chain (lines 87-122). The `_resolve_skip_patterns()` logic is correct. Naming could be better but isn't a bug.

4. **Multiple confidence fallback points (0.5)** — The 0.5 fallback at `_merge_chunk_results:350` and `_merge_entity_lists:405` is conservative and correct for when LLM omits the confidence field entirely. This is different from the recalibration at line 183 (which is the actual issue above).
