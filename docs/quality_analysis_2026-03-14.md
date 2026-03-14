# Drivetrain Quality Analysis — Post-Grounding Simplification
**Date:** 2026-03-14
**Project:** 99a19141-9268-40a8-bc9e-ad1fa12243da (238 source groups)

## Consolidated Records Overview

| Group | Total | Empty | Has Data | Avg Sources | Avg Grounded |
|-------|-------|-------|----------|-------------|-------------|
| company_info | 231 | 0 | 231 | 31.2 | 25.9 |
| manufacturing | 212 | 0 | 212 | 22.8 | 14.6 |
| services | 222 | 0 | 222 | 26.4 | 7.5 |
| company_meta | 228 | 226 | 2 | 30.6 | 0.0 |
| products_gearbox | 216 | 95 | 121 | 29.6 | 6.6 |
| products_motor | 222 | 94 | 128 | 32.8 | 6.8 |
| products_accessory | 224 | 35 | 189 | 37.4 | 14.3 |

## Per-Field Fill Rates (Consolidated)

### company_info (231 records)

| Field | Filled | Empty | Fill % |
|-------|--------|-------|--------|
| company_name | 227 | 4 | 98.3% |
| headquarters_location | 221 | 10 | 95.7% |
| employee_count | 78 | 153 | 33.8% |
| employee_count_range | 21 | 210 | 9.1% |
| number_of_sites | 106 | 125 | 45.9% |

### manufacturing (212 records)

| Field | True | False/Empty | True % |
|-------|------|-------------|--------|
| manufactures_gearboxes | 141 | 71 | 66.5% |
| manufactures_motors | 73 | 139 | 34.4% |
| manufactures_drivetrain_accessories | 142 | 70 | 67.0% |
| manufacturing_details | 186 filled | 26 empty | 87.7% |

### services (222 records)

| Field | True | False/Empty | True % |
|-------|------|-------------|--------|
| provides_services | 159 | 63 | 71.6% |
| services_gearboxes | 124 | 98 | 55.9% |
| services_motors | 76 | 146 | 34.2% |
| services_drivetrain_accessories | 100 | 122 | 45.0% |
| provides_field_service | 105 | 117 | 47.3% |
| service_types | 0 | 222 | 0.0% |

### company_meta (228 records)

| Field | Filled | Empty | Fill % |
|-------|--------|-------|--------|
| certifications | 2 | 226 | 0.9% |
| locations | 2 | 226 | 0.9% |

### Entity Lists (products)

| Group | Total | Empty | Has Entities | Avg Entities (when present) |
|-------|-------|-------|-------------|---------------------------|
| products_gearbox | 216 | 95 | 121 (56%) | 26.0 |
| products_motor | 222 | 94 | 128 (58%) | 27.2 |
| products_accessory | 224 | 35 | 189 (84%) | 59.7 |

## Grounding Quality (Raw Extractions, v2)

| Field | Avg Grounding | High (>=0.8) | Low (<0.3) | Total Scored |
|-------|--------------|-------------|------------|-------------|
| company_name | 0.790 | 5,458 (81%) | 1,293 (19%) | 6,751 |
| headquarters_location | 0.242 | 1,443 (22%) | 5,184 (78%) | 6,627 |
| manufacturing_details | 0.637 | 3,087 (64%) | 1,756 (36%) | 4,843 |

## Analysis & Concerns

### RED FLAGS

1. **company_meta: 99.1% empty (226/228)**
   - Only 2 records have certifications or locations data
   - This group has `list` type fields (certifications, locations)
   - avg_grounded = 0.0 means NO extractions passed the grounding gate
   - Root cause: list fields use `required` grounding mode, but extracted lists of certifications/locations likely don't have verbatim quote matches → all grounded out
   - **This is a real problem** — companies DO have certifications and locations

2. **service_types: 0% fill (0/222)**
   - Same issue as company_meta — list field with required grounding
   - Service types like "repair, maintenance, refurbishment" are being grounded out
   - **This is a real problem** — 159 companies provide services but none have service_types

3. **headquarters_location: avg grounding 0.242 (78% low)**
   - Most HQ locations fail grounding because the LLM synthesizes "City, Country" but the source text may only mention the city or country separately
   - Consolidated fill is still 95.7% (221/231) — rescue/frequency voting saves most
   - Moderate concern: low grounding means lower confidence in the values

4. **employee_count_range: 9.1% fill**
   - Expected to be low — most companies don't publish ranges
   - employee_count (33.8%) captures the exact number when available
   - **Likely legitimate** — many small companies don't publish headcount

5. **products_gearbox/motor: ~44% empty**
   - Expected — not all 238 companies manufacture gearboxes or motors
   - products_accessory is 84% filled which makes sense (broader category)
   - **Likely legitimate**

### ROOT CAUSES IDENTIFIED

1. **BUG (FIXED): Consolidation ignored v2 list field items**
   - `consolidation.py` line ~465: v2 list fields store data as `{"items": [...]}` not `{"value": ...}`
   - The consolidation code only looked for `field_data.get("value")` → always None for list fields
   - **Fix**: Added v2 list item extraction in `consolidate_extractions()` — extracts items, computes per-item weights, feeds into `union_dedup`
   - **Impact**: certifications (965 raw extractions have items) will now consolidate correctly
   - service_types likely same fix (need to verify raw data has items)

2. **EXTRACTION GAP: locations field — LLM returns empty items for ALL 6,964 extractions**
   - Not a consolidation or grounding issue — the LLM simply doesn't extract locations
   - Field description "List of {city, country, site_type} objects" may be too complex for structured list extraction
   - Needs prompt improvement or schema restructuring (Phase C)

3. **MODERATE: headquarters_location grounding is low (avg 0.242) but consolidation fill is OK (95.7%)**
   - LLM synthesizes "City, Country" but source text may mention them separately
   - Not urgent since frequency voting saves most values

### ACTIONS NEEDED

1. **Deploy consolidation fix** — commit + deploy, then re-consolidate drivetrain
2. **Verify service_types** raw extraction data has items (same bug)
3. **Later**: Fix locations extraction (prompt/schema change)
