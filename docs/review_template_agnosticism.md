## RESOLVED — commit 1905d62 (2026-03-18)

All C1–C3 and I1–I2 fixes applied. Zero occurrences of hardcoded `product_name` remain in `schema_orchestrator.py`, `consolidation.py`, or `grounding.py`. Report titles use dynamic `source_label`. Kept for historical reference.

> **Note:** The analysis below is pre-fix documentation. All issues (C1–C3, I1–I2) were resolved in commit 1905d62.

---

# Pipeline Review: Template Agnosticism

**Date:** 2026-03-16
**Scope:** Extraction → Consolidation → Reporting — how well does the pipeline work with arbitrary templates?

## Verdict

The architecture is **fundamentally template-agnostic** — schemas drive extraction, grounding uses field types not names, consolidation strategies are data-type-aware. But there are **8 locations** where `product_name` is hardcoded as a fallback ID field, and **2 locations** where reporting assumes drivetrain semantics.

## Flow

```
schema_adapter.py → schema_extractor.py → schema_orchestrator.py → grounding.py → consolidation.py → reports/service.py
        ↓                    ↓                      ↓                    ↓               ↓               ↓
  ExtractionContext    prompt from schema     grounding gate      score fields      dedup entities    generate report
  entity_id_fields     (template-agnostic)    id_fields HARDCODED  id HARDCODED     id HARDCODED      "companies" HARDCODED
```

---

## Critical (must fix)

### C1. Hardcoded `product_name` in grounding gate ID protection
- **File:** `src/services/extraction/schema_orchestrator.py:147`
- **Code:** `id_fields = frozenset({"name", "entity_id", "id", "product_name"})`
- **Problem:** This set determines which fields are **never nulled** by the grounding gate. A recipe template's `recipe_name` or job template's `job_title` would NOT be protected — meaning the identity field gets nulled on low grounding, destroying the entity.
- **Fix:** Use `self._context.entity_id_fields` (already available on the orchestrator)

### C2. Hardcoded `product_name` in entity dedup key (consolidation)
- **File:** `src/services/extraction/consolidation.py:667`
- **Code:** `name = entity.get("name") or entity.get("product_name") or entity.get("id", "")`
- **Also:** `src/services/extraction/consolidation.py:759` (same pattern)
- **Problem:** Entity deduplication across sources uses this key. Templates with `recipe_name` or `job_title` as identity fall through to hash-based dedup, producing duplicates instead of merging.
- **Fix:** Accept `entity_id_fields` parameter, iterate those first

### C3. Hardcoded `product_name` in grounding verification
- **File:** `src/services/extraction/grounding.py:117` (`verify_list_items_in_quote`)
- **Also:** `src/services/extraction/grounding.py:174` (`score_field` for dict items)
- **Code:** `name = item.get("name") or item.get("product_name") or item.get("id")`
- **Problem:** When verifying entity values against source text, non-drivetrain entity names won't be found, producing 0.0 grounding scores for valid entities.
- **Fix:** Accept `id_field_names` parameter or extract all string values (the fallback at line 121-123 partially covers this)

---

## Important (should fix)

### I1. Hardcoded "companies" in report titles
- **File:** `src/services/reports/service.py:171`
- **Code:** `title = request.title or f"Table: {len(source_groups)} companies"`
- **Also:** `src/services/reports/service.py:193` (comparison report)
- **Problem:** Default report titles say "companies" for every template. A recipe extraction report would read "Table: 50 companies".
- **Fix:** Use `extraction_context.source_label` or a generic term like "sources"/"entries"

### I2. Hardcoded "fact" field in single report generation
- **File:** `src/services/reports/service.py:355-356`
- **Code:** `fact_text = item.get("data", {}).get("fact", str(item.get("data", "")))`
- **Problem:** Assumes a `fact` field exists. Fallback is `str(data)` — a raw dict dump.
- **Fix:** Use first text-type field from schema, or iterate fields intelligently

### I3. Inconsistent ID field defaults across modules
- **Files:**
  - `schema_adapter.py:200` → `["entity_id", "name", "id"]`
  - `schema_orchestrator.py:147` → `{"name", "entity_id", "id", "product_name"}`
  - `grounding.py:256` → `("entity_id", "name", "id")`
  - `chunk_merge.py:165` → `["entity_id", "name", "id"]`
  - `consolidation.py:667` → tries `name`, `product_name`, `id`
- **Problem:** Five different fallback lists. Some include `product_name`, some don't. Behavior diverges depending on which module processes the entity.
- **Fix:** Single source of truth — `ExtractionContext.entity_id_fields` should flow through every function

---

## Minor

### M1. Schema validation warns about missing "common" ID fields
- **File:** `src/services/extraction/schema_adapter.py:301-303`
- **Code:** `common_id_fields = ["entity_id", "name", "id", "product_name"]`
- **Problem:** Templates using `recipe_name` or `job_title` get spurious warnings
- **Fix:** Make it informational ("no standard ID field found, ensure entity_id_fields is configured")

### M2. Grounding `compute_entity_list_grounding_scores` doesn't accept id_fields
- **File:** `src/services/extraction/grounding.py:256`
- **Code:** `id_field_names = ("entity_id", "name", "id")` — hardcoded, no parameter
- **Problem:** Can't be configured per-template
- **Fix:** Add `id_field_names` parameter, default from context

---

## What's Already Good (no changes needed)

| Component | Why it's template-agnostic |
|-----------|--------------------------|
| **Schema adapter** | Loads field definitions from YAML/JSON schemas dynamically |
| **Prompt construction** | Built from schema field descriptions, not hardcoded |
| **Grounding defaults** | Type-based (`text`→required, `summary`→none), not field-name-based |
| **Consolidation strategies** | Data-type-aware (integer→weighted_median, text→weighted_frequency) |
| **Page classifier** | Universal skip patterns (careers, legal, login) |
| **Smart classifier** | Embeds field group descriptions from template |
| **Domain dedup** | Content-based, not template-aware |
| **Table report generation** | Schema-driven columns via `SchemaAdapter` |
| **Consolidated report builder** | Uses `source_label` from schema context |
| **API routes** | Generic request/response models throughout |
| **Worker** | Schema-driven via `SchemaExtractionPipeline` |

---

## Recommended Fix Order

| # | Item | Risk | Effort | Impact |
|---|------|------|--------|--------|
| 1 | C1 — grounding gate `id_fields` | Entity identity destroyed | 5 min | Any non-drivetrain template |
| 2 | C2 — consolidation dedup key | Duplicate entities | 15 min | Any entity-list template |
| 3 | C3 — grounding verification | Bad grounding scores | 15 min | Any entity-list template |
| 4 | I3 — unify ID field defaults | Inconsistent behavior | 30 min | All modules |
| 5 | I1 — report titles | Wrong labels | 5 min | All reports |
| 6 | I2 — "fact" field | Broken single reports | 10 min | Non-fact templates |
| 7 | M1 — validation warning | Spurious warnings | 5 min | Schema authoring |
| 8 | M2 — grounding id_fields param | Inflexible scoring | 10 min | Entity grounding |

**Total estimated effort:** ~1.5 hours for full fix. Items 1-3 are the ones that would actually **break** non-drivetrain templates.
