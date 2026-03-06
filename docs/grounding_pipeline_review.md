# Pipeline Review: Grounding & Consolidation

## Flow
```
backfill endpoint → compute_grounding_scores() → score_field() → verify_*_in_quote()
schema_orchestrator._merge_chunks() → score_field() → verify_*_in_quote()
llm_grounding.verify_extraction() → verify_quote() (LLM call)
consolidation_service → consolidate_extractions() → strategy functions
```

## Critical (must fix)

Root cause: **`_quotes` values in extraction data are not always strings** — the LLM
sometimes returns lists or other JSON types. Every verify function assumes `str`.

- [ ] `grounding.py:128-148` — `score_field()` accepts non-string quotes from callers (line 188: `quotes.get(field_name, "")` returns arbitrary JSON). `if not quote` on line 139 passes non-empty lists. **Fix here covers all downstream sites.**
- [ ] `grounding.py:123` — `_normalize_string(quote)` in `verify_list_items_in_quote` → `list.lower()` crash. **Confirmed: production crash.**
- [ ] `grounding.py:73` — `_normalize_string(quote)` in `verify_string_in_quote` → same crash. Guards at lines 65-69 don't catch non-string truthy values.
- [ ] `grounding.py:41-50` — `verify_numeric_in_quote` → `re.finditer(pattern, list)` → TypeError. Guard `quote is None or quote == ""` doesn't catch lists.
- [ ] `schema_orchestrator.py:495,503` — `chunk_quote`/`merged_quote` passed to `score_field()` from `_quotes` dict. Same non-string issue. **Covered by score_field fix.**

## Important (should fix)

- [ ] `consolidation_service.py:95` — `ext.confidence or 0.5` treats confidence `0.0` as `0.5` (falsy bug). `0.0` is a real value (default in `schema_orchestrator.py:219`). Inflates weight for zero-confidence extractions. Fix: `ext.confidence if ext.confidence is not None else 0.5`.
- [ ] `llm_grounding.py:155-169` — Non-string quote reaches `verify_quote()`, producing garbled LLM prompt (`Quote from source: "['text', ...]"`). Won't crash but wastes an LLM call and likely returns incorrect "not supported". Fix: coerce quote to str before the `if not quote` check.

## Minor

- [ ] `consolidation.py:91-93` — `frequency()` groups dicts by `str(dict)` which is insertion-order-dependent. Theoretically two identical entities with different key ordering won't dedup. **Low risk**: `frequency` strategy is only used for string/enum fields by default; dicts use `union_dedup`.
- [ ] `consolidation.py:322-328` — `consolidate_field` agreement calculation same `str()` comparison. Same low risk as above.
- [ ] `grounding.py:241-243` — `_normalize_string` has no type guard. Not directly exploitable (all callers would crash first) but a defensive `str(s)` would harden it.

## Removed (false positives from initial review)

- ~~`grounding.py:60-68` verify_string_in_quote "garbage string comparison for dict values"~~ — `score_field` only routes dicts (via list field_type) to `verify_list_items_in_quote`, not `verify_string_in_quote`. For non-list types, `str(value)` on a dict is ugly but not a crash or logic bug — it just scores low (0.0), which is correct.
- ~~`consolidation.py:108` mypy flag on `form_counts.get`~~ — Works correctly at runtime, not a bug.
