# Pipeline Review: entity_id_fields Implementation

## Flow
worker.py → schema_orchestrator.py → grounding.py → consolidation.py → consolidation_service.py → reports/service.py

## Critical (must fix)

- [x] `src/services/reports/service.py:177,202` — **Fixed**: Naive pluralization produced "Companys". Now uses `"{source_label} Table ({n})"` pattern — no pluralization needed.

## Important (should fix)

- [ ] `src/services/reports/service.py:166` — **Extra DB query in `generate()`**: Added `self._get_project_schema(project_id)` for `source_label`, but the TABLE path calls `_generate_table_report()` which calls `_get_project_schema()` again at line 1069. Two identical queries per table report. Fix: pass schema down or cache on the instance.

## Minor

(None found — remaining changes are structurally sound.)

## Verified Safe

- `score_field` callers at lines 248, 302, 1072, 1160 in grounding.py: all operate on scalars or flat string lists, never hit the dict/entity branch where `id_field_names` matters. No `id_field_names` needed.
- `consolidate_field` at line 539 (flat field path): no `entity_id_fields` passed, but flat `union_dedup` calls `_dedup_strings` not `_dedup_dicts`. Safe.
- `_filter_entity_fields` default `frozenset(("entity_id", "name", "id"))`: drivetrain passes its own set from context so default is never hit in production.
- Worker handles `project is None` correctly (line 259 guard).
- `fact_text` fallback at line 373: operator precedence parses as `(fact_text or str(data)) if data else ""` — correct for all cases.
- `_dedup_dicts` delegates to `_entity_match_key` — identical logic to old inline code.
