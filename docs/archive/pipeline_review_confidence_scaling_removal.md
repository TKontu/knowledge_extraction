# Pipeline Review: Confidence Scaling Removal

Reviewed: 2026-02-26
Scope: Impact of removing population-based confidence scaling at `schema_orchestrator.py:183`

## Flow

```
schema_extractor (LLM → 0.0-1.0)
  → _merge_chunk_results (avg, 0.5 fallback)
    → extract_group:
        raw_confidence = merged.pop("confidence")
        if is_empty → min(raw, 0.1)
        else → raw_confidence  ← CHANGED (was: raw * (0.5 + 0.5 * ratio))
          → stored as Extraction.confidence
            → service.py: flattened into _column_confidences per column
              → smart_merge: MergeCandidate.confidence (filter ≥ 0.3)
                → merge decision (LLM or short-circuit)
```

## Critical (must fix)

None found.

## Important (should fix)

None found.

## Minor

None found.

## Assessed & Not Issues

1. **schema_orchestrator.py:178 — `None` confidence fallback uses population scaling** — Dead code. `_merge_chunk_results` (line 351) and `_merge_entity_lists` (line 410) always set `merged["confidence"]` to a float (averaging chunk confidences with 0.5 fallback). Therefore `merged.pop("confidence", None)` at line 174 always returns a float, never `None`. The `if raw_confidence is None` branch at line 177-178 cannot execute. Not a real issue, though the dead code could be cleaned up for clarity.

2. **smart_merge.py:81-83 — `confidence is None` candidates dropped** — Cannot trigger in current pipeline. The orchestrator always sets `confidence` on results (line 159 initializes to 0.0, lines 180-183 always assign a float). Downstream, `_column_confidences` is populated per-column (service.py:628-629) and `avg_confidence` is always a float (service.py:632-633) because `confidences` is always non-empty. The only theoretical case — a source with zero extractions — would produce all-null values, filtered by the `non_null` check at smart_merge.py:87 before confidence matters.

3. **service.py:711-716 — Column confidence falls back to `avg_confidence`** — For columns without extracted data, `row.get(col_name)` returns `None`, so `MergeCandidate.value=None`, which is filtered by smart_merge.py:87 `non_null` check. The fallback confidence is never used in a merge decision.

4. **Validator min_confidence=0.5 threshold** — The FactValidator is used for the legacy fact-based extraction pipeline, not for schema extraction. Schema extractions flow through `pipeline.py:559` directly to storage without validator filtering. No impact from this change.

5. **Smart merge min_confidence=0.3 threshold** — With raw LLM confidence passing through (typically 0.5-0.9), more candidates will pass the 0.3 floor than before (when scaled values could drop to 0.48). This is the intended behavior: focused pages no longer get filtered out. The 0.3 threshold still catches truly low-confidence extractions.

6. **`avg_confidence` in reports is now higher** — Since raw confidence passes through unscaled, the `avg_confidence` column in generated reports will show higher numbers. This is cosmetic — it reflects the actual LLM confidence rather than a penalized score. No downstream logic depends on absolute `avg_confidence` values.

7. **Synthesis confidence (synthesis.py:201,228)** — The synthesis module computes its own confidence from `SynthesisResult` objects, not from extraction confidence. Unaffected.

8. **Empty detection (`is_empty` path)** — Unchanged. Still caps at 0.1 for truly empty results. The `_is_empty_result` threshold (<20% populated) is appropriate.
