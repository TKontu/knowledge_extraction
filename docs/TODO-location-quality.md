# TODO: Improve Location & Field Extraction Quality

**Status:** Fix 1 deployed, Fix 2 cancelled, Fix 3 deployed, Fix 4 ready to deploy
**Priority:** High
**Created:** 2026-03-16

## Context

Post-consolidation quality analysis of the drivetrain project (238 companies, 58K extractions, 1793 consolidated records) revealed systematic data quality issues. All root causes are prompt/template issues, not pipeline bugs.

## Investigation Findings

| Issue | Severity | Root Cause |
|-------|----------|------------|
| City field contains country names (11.7%) | HIGH | Template prompt says "still extract it" but doesn't say WHERE |
| Country field empty 63% of the time | HIGH | LLM doesn't infer country; `default: ""` bypasses grounding |
| model_number empty ~50% across products | MODERATE | Websites lack model numbers; prompt doesn't guide extraction |
| service_types empty for 44 companies | MODERATE | Field desc reads like enum list; LLM parrots it or ignores it |
| 10 companies missing company_meta | LOW | Sources genuinely lack data |

## Fix 1: Rewrite company_locations prompt_hint — DONE ✓

**File:** `src/services/projects/templates/drivetrain_company.yaml`

**Deployed:** Prompt B (field placement rules) — the A/B trial winner.

### A/B Trial Results (91 sources, 25 companies)

| Metric | OLD (A) | NEW (B) | Change |
|--------|---------|---------|--------|
| Country-in-city errors | 7 (4.4%) | 1 (0.8%)* | **-86%** |
| Region-in-city errors | 1 | 0 | **-100%** |
| Country fill rate | 100% | 100% | same |
| Country-only entries (city=null) | 16 | 34 | correct behavior |

*Remaining 1 is "Lebanon, Indiana" — actual US city, false positive in detector.

**Specific fixes confirmed:**
- Bauergears: `city="Luxembourg"` → fixed
- OMEngineering: `city="India"` → fixed
- Wikov: `city="Canada"` → fixed, `city="North America"` → skipped (region)
- Ydgear: `city="Taiwan"` (×3) → all fixed
- Dbsantasalo: `country="global"` → 12 real country offices extracted
- Flender: `country="33 countries"` → correctly skipped

**Changes applied:**
- prompt_hint: Added FIELD PLACEMENT RULES section with clear city/country guidance
- city description: "Municipality/town name ONLY — never a country, region, or continent"
- country: `required: false` (was true), removed `default: ""` (was causing grounding issues)
- country description: "Sovereign nation name... Infer from context if possible."

### Trial scripts
- `scripts/trial_location_prompts.py` — wide A/B test (OLD vs NEW on N companies)
- `scripts/trial_location_prompts_v2.py` — iterative multi-variant test (A/B/C side-by-side)

## Fix 2: Improve product model_number prompt hints — CANCELLED ✗

**A/B trial showed negligible impact.** The data gap is real, not a prompt problem.

| Product Group | OLD fill rate | NEW fill rate | Change |
|---------------|--------------|--------------|--------|
| Gearbox | 92.9% | 92.9% | 0% |
| Motor | 47.1% | 46.9% | -0.2% |
| Accessory | 47.7% | 42.7% | -5% (worse) |

### Trial script
- `scripts/trial_model_number_prompts.py` — A/B test per product group

## Fix 3: Add quality detection to analyze_quality.py — DONE ✓

**File:** `scripts/analyze_quality.py`

**Deployed.** The script already contains:
- Country names in `city` field detection (lines 488–516, checks against common country list)
- Sentinel value detection — "unknown", "N/A", "not specified", etc. (lines 504–515)
- City-filled-but-country-empty detection (lines 539–545)

Parroted `service_types` detection (all items match the enum list) was not verified as present.

## Fix 4: Improve service_types field description — READY TO DEPLOY

**File:** `src/services/projects/templates/drivetrain_company.yaml` (services field group)

**Root cause:** The current `service_types` field description reads `"Types: repair, maintenance, refurbishment, installation, commissioning, field service"` — the LLM treats this as an enum list and parrots it verbatim instead of extracting actual terms from the page.

### A/B Trial Results (235 sources, 60 companies)

| Metric | A (current) | B (enriched) | Change |
|--------|-------------|-------------|--------|
| provides_services agreement | — | — | **96.2%** |
| provides_services = True | 110 | 113 | +3 |
| Total service_types items | 597 | **1182** | **+98%** |
| Novel items (page-specific) | 85 (14.2%) | **830 (70.2%)** | **+876%** |
| Enum-only items (parroted) | 512 | 352 | -160 |
| **Parrot detections** (all-enum, ≥5) | **67 (60.9%)** | **0 (0%)** | **-100%** |
| Avg types per detection | 5.4 | **10.5** | +94% |
| Regressions | — | — | **0** |

**Prompt B is strictly better across all dimensions. Zero regressions.**

B extracts actual page-specific terms (e.g., `"vibration analysis"`, `"gearbox overhauling"`, `"event support"`) instead of echoing the same 6 enum values. Works across languages (Spanish, Portuguese, German, French).

**Change:** Update service_types field description from:
```yaml
description: "Types: repair, maintenance, refurbishment, installation, commissioning, field service"
```
To:
```yaml
description: "List of specific service types offered. Examples: repair, maintenance, refurbishment, installation, commissioning, field service, overhaul, spare parts, technical support, inspection, testing. Extract the actual terms used on the page, in any language."
```

### Trial scripts
- `scripts/trial_service_types_prompts.py` — iterative multi-variant test (A/B/C/D)
- `scripts/trial_service_types_wide.py` — wide A vs B test (235 sources, 60 companies)

## NOT Fixing (data availability, not bugs)

- **site_type 12.9%** — websites rarely state site types explicitly
- **10 missing company_meta** — sources genuinely lack certifications
- **voltage/ratio empty** — technical specs vary by manufacturer
- **model_number ~50% empty** — confirmed by A/B trial as real data gap

## Remaining Steps

1. ~~Deploy Fix 1 template (company_locations)~~ ✓
2. ~~Run template tests~~ ✓ (33 passed)
3. Deploy Fix 4 template (service_types description)
4. Run template tests again
5. Re-extract company_locations for all companies
6. Re-extract services for all companies
7. Reconsolidate both
8. Implement Fix 3 (quality detection in analyze_quality.py)
