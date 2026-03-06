# TODO: Downstream Pipeline Trials

**Created:** 2026-03-05
**Status:** In Progress
**Project:** Industrial Drivetrain Companies 2026 (`99a19141-9268-40a8-bc9e-ad1fa12243da`)

## Problem Statement

The extraction pipeline produces 47,201 extractions across 238 companies, but downstream workflows have major quality gaps:

- **57.7% zero-confidence waste** (27,225 extractions) pollutes reports and search
- **No cross-source consolidation** — avg 26.5 `company_info` extractions per company, up to 137, with contradictory values (e.g., 9 name variants for one company)
- **Conflicting boolean facts** — e.g., Southerngear: 23 pages say "manufactures gears" vs 63 say "doesn't"
- **Entity extraction disconnected** — 0 entities despite infrastructure + 5 configured entity_types
- **Search broken** — 500 errors from vector search
- **Reports stale** — last generated Feb 5, before latest re-extraction

## Trial Plan

### Trial 1: Confidence Threshold Analysis
**Goal:** Determine optimal confidence thresholds per extraction_type.

- [ ] **1A**: Distribution analysis — per-type confidence histograms, field population rates at each threshold
- [ ] **1B**: Precision spot-check — sample 20 extractions at each threshold band, manually assess accuracy
- [ ] **1C**: Per-type optimal thresholds — determine if different types need different cutoffs
- **Metrics:** Fields populated, noise ratio, precision at threshold

### Trial 2: Cross-Source Consolidation
**Goal:** Produce ONE canonical record per (source_group, extraction_type) from N per-source extractions.

- [ ] **2A**: Confidence-weighted voting (no LLM) — pick highest-confidence value per field
- [ ] **2B**: LLM consolidation — feed top-N extractions to LLM, produce canonical record
- [ ] **2C**: Hybrid — voting for simple fields, LLM for text/lists
- **Metrics:** Consistency (1 name per company), accuracy (spot-check 20 companies), field coverage

### Trial 3: Entity Extraction Assessment
**Goal:** Determine if entity extraction adds value beyond field_group extractions.

- [ ] **3A**: Test entity extraction on sample companies
- [ ] **3B**: Assess dedup quality for entity variants
- [ ] **3C**: Compare entity value vs existing field_group data
- **Metrics:** Entity count, dedup accuracy, incremental information gain

### Trial 4: Report Generation Quality
**Goal:** Generate reports with consolidated data and assess quality improvement.

- [ ] **4A**: Generate table reports with confidence-filtered + consolidated data
- [ ] **4B**: Compare domain-merge (LLM) vs voting consolidation
- [ ] **4C**: Manual quality review on 10 well-known companies
- **Metrics:** Accuracy, completeness, consistency

### Trial 5: Search & Reranking (deferred)
**Goal:** Fix search and add reranking.

- [ ] **5A**: Diagnose and fix search 500 errors
- [ ] **5B**: Add bge-reranker-v2-m3 cross-encoder reranking
- [ ] **5C**: Measure search precision with/without reranking

## Execution Order

1. Trial 1A-1C (confidence analysis) — DB queries only, informs all other trials
2. Trial 2A (voting consolidation) — script, establishes baseline
3. Trial 2B (LLM consolidation) — compare against voting
4. Trial 4C (report quality) — validates consolidation
5. Trial 3A (entity sample) — assess incremental value
6. Trial 5A-B (search) — fix and improve

## Findings

### Trial 1: Confidence Threshold Analysis

#### 1A: Confidence Distribution by Type

| Type | Zero (0.0) | Low (<0.3) | Mid-Low (0.3-0.5) | Mid (0.5-0.7) | High (0.7-0.9) | Very High (>=0.9) |
|------|-----------|-----------|-------------------|---------------|----------------|-------------------|
| company_info | 17.2% | 1.2% | 1.4% | 28.3% | **41.7%** | 10.2% |
| company_meta | **59.1%** | 0.6% | 1.5% | 0.8% | 16.4% | 21.6% |
| manufacturing | 38.3% | 1.0% | 1.4% | 3.0% | 18.1% | **38.2%** |
| products_accessory | **60.9%** | 0.5% | 1.2% | 0.4% | 12.2% | 24.9% |
| products_gearbox | **76.9%** | 0.5% | 0.7% | 2.5% | 9.4% | 10.1% |
| products_motor | **78.3%** | 0.3% | 0.5% | 1.4% | 7.6% | 11.9% |
| services | **70.7%** | 1.0% | 0.0% | 0.1% | 5.3% | 22.9% |

**Key observations:**
- Product types have extremely bimodal distributions: ~70-80% zero-confidence, then a jump to 10-25% high/very-high. Almost nothing in between.
- `company_info` is the exception — only 17% zero, and a strong mid-band (28% at 0.5-0.7).
- The 0.3-0.5 band is nearly empty for all types (<2%) — there's a "confidence gap" between zero and useful.
- **Recommended threshold: 0.5** for all types. The 0.3-0.5 band has too few extractions to matter, and at 0.5+ the data is substantially populated.

#### 1A: Field Population (company_info)

| Threshold | Extractions | Has Name | Has HQ | Has Employees | Has Sites |
|-----------|-------------|----------|--------|---------------|-----------|
| >= 0.5 | 5,825 | 5,825 (100%) | 4,774 (82%) | 596 (10%) | 1,379 (24%) |
| < 0.5 | 1,436 | 102 (7%) | 101 (7%) | 23 (2%) | 37 (3%) |

Below 0.5, field population drops to single digits — these extractions are noise.

#### 1A: Field Population (manufacturing, confidence >= 0.5)

| Band | Total | Gears=True | Gears=False | Motors=True | Motors=False | Has Details |
|------|-------|-----------|-------------|-------------|--------------|-------------|
| high (>=0.7) | 2,742 | 1,454 (53%) | 1,288 (47%) | 571 (21%) | 2,171 (79%) | 2,742 (100%) |
| mid (0.5-0.7) | 148 | 2 (1%) | 146 (99%) | 5 (3%) | 143 (97%) | 148 (100%) |

Mid-confidence manufacturing extractions are almost always "false" for boolean fields — they come from pages that mention manufacturing tangentially but aren't about the company's own manufacturing.

#### 1A: Cross-Source Duplication Scale

Per source_group, avg useful (>=0.3) extractions per type:

| Type | Avg Useful/Group | Max/Group | Groups >5 |
|------|-----------------|-----------|-----------|
| company_info | 26.5 | 137 | 163 of 224 |
| products_accessory | 17.5 | 123 | 119 of 186 |
| manufacturing | 16.3 | 88 | 116 of 181 |
| company_meta | 14.3 | 86 | 110 of 198 |
| products_motor | 12.2 | 80 | 61 of 128 |
| products_gearbox | 11.9 | 66 | 68 of 123 |
| services | 10.7 | 89 | 68 of 156 |

Every type has massive cross-source redundancy requiring consolidation.

#### 1A: Company Name Consistency (at confidence >= 0.5)

| Name Variants | Companies | Avg Extractions |
|---------------|-----------|-----------------|
| 1 (consistent) | 64 (29%) | 11.9 |
| 2 variants | 52 (23%) | 17.6 |
| 3-5 variants | 70 (31%) | 35.4 |
| >5 variants | 37 (17%) | 45.3 |

**71% of companies have inconsistent company names across extractions.** The more pages crawled, the more variants appear.

Worst cases: En (17 variants from 99 extractions), Boschrexroth (14 variants from 136), Igwpower (14 variants from 68).

#### 1B: Precision Spot-Check — Highest-Confidence-Wins

Sampled the highest-confidence extraction per source_group for company_info:

| Issue | Examples | Impact |
|-------|----------|--------|
| **Wrong company extracted** | Autservice → "Siemens AG" (0.95), Alnihal → "AirTAC Pneumatics-Taiwan" (0.8), En → "DMG MORI Ultrasonic Lasertec GmbH" (0.95) | LLM extracts vendor/partner name instead of the actual company |
| **Correct company** | ABB, Aisin, Gleason, Bonfiglioli, ZF — all correct at 0.95 | Works for companies with strong brand presence on their own site |
| **Partial HQ** | Some have city+country, others just country, others empty | Inconsistent granularity |

**Critical finding: Highest-confidence-wins is NOT sufficient.** The LLM sometimes extracts vendor/partner info from a page at high confidence. A page about "Autservice sells Siemens products" → LLM extracts "Siemens AG" as company_name with 0.95 confidence.

**This means consolidation MUST use frequency/voting, not just max confidence.** The correct company name usually appears on the most pages.

#### 1B: Boolean Conflict Analysis (manufacturing.manufactures_gearboxes)

Sampled companies with conflicting boolean votes (both true and false at >=0.5):

| Company | True votes | False votes | Avg conf (true) | Avg conf (false) | Majority says | Actually correct? |
|---------|-----------|-------------|-----------------|------------------|---------------|-------------------|
| Southerngear | 23 | 63 | 0.93 | 0.90 | NO | YES (they make gears) |
| Kaiboi | 72 | 2 | 0.94 | 0.88 | YES | YES |
| Rossi | 47 | 1 | 0.91 | 0.60 | YES | YES |
| Venturemfgco | 26 | 38 | 0.92 | 0.86 | NO | YES (they make gears) |

**Problem: Majority vote is wrong for some companies.** Pages that mention "we repair/service/distribute gears" but DON'T manufacture them outnumber the few pages that say "we manufacture gears". The LLM correctly identifies each page's content, but the aggregation logic is wrong.

**Root cause:** Most pages on a gear company's website are about products/services/news, not about "we are a manufacturer." Only a few pages (About Us, Manufacturing) explicitly state they manufacture. Product pages say "we offer gears" → LLM says `manufactures_gearboxes: false` because the page doesn't say they MAKE them.

**Implication:** Boolean fields about company-level facts need a different merge strategy: **"any credible true" should win**, not majority vote. If even 5 pages at high confidence say "yes they manufacture gears", that should override 60 pages that don't mention manufacturing.

#### 1C: Per-Type Recommended Thresholds

| Type | Threshold | Rationale |
|------|-----------|-----------|
| company_info | **0.5** | Strong mid-band; below 0.5 fields are empty |
| company_meta | **0.7** | 59% zero, jump straight to high band |
| manufacturing | **0.7** | Mid-band is almost all false booleans (noise) |
| products_* | **0.7** | 70-80% zero, bimodal → only trust high band |
| services | **0.7** | 70% zero, bimodal |

#### Trial 1 Summary

1. **Confidence threshold of 0.5 is the universal minimum** — below this, fields are empty/noise
2. **Product/service types benefit from 0.7 threshold** — bimodal distribution, mid-band is sparse
3. **Highest-confidence-wins fails** for company identification — frequency-based voting needed
4. **Majority vote fails for boolean company-level facts** — "any credible true" strategy needed
5. **Cross-source consolidation is mandatory** — 10-26 extractions per company per type, 71% have name inconsistencies

### Trial 2: Cross-Source Consolidation

#### 2A: Strategy Comparison — Company Names (20 companies, ground truth)

| Strategy | Accuracy | Notes |
|----------|----------|-------|
| **highest_confidence** | 17/20 (85%) | Wrong: Autservice→"WEG", Timken→"铁姆肯公司", Igwpower→"BMT Group" |
| **frequency_vote** | **20/20 (100%)** | Always picks the correct company — most-mentioned name wins |
| **weighted_frequency** | **20/20 (100%)** | Same result as frequency for company names |

**Key failure modes of highest-confidence:**
- Autservice (0.95 conf) → "WEG" (vendor mentioned on page)
- Timken (0.95 conf) → "铁姆肯公司" (Chinese name from localized page)
- Igwpower (0.9 conf) → "BMT Group" (parent company extracted instead)

**Conclusion:** Frequency voting is strictly superior to highest-confidence for company identification. The correct company name always appears on more pages than any vendor/partner/subsidiary.

#### 2A: Strategy Comparison — Boolean Fields (21 known gear manufacturers)

| Strategy | Accuracy | Wrong Companies |
|----------|----------|-----------------|
| **majority_vote** | 10/21 (48%) | 11 wrong — Southerngear, Gearmotions, Circlegear, Perrygear, Croftsgears, Marplesgears, Highfieldgears, Atagears, Khkgears, Venturemfgco, Commercialgear |
| **any_true (min=3)** | **18/21 (86%)** | 3 wrong — Circlegear, Khkgears, Commercialgear |
| **any_true (min=5)** | 17/21 (81%) | 4 wrong — adds Gearmotions |

**Majority vote fails catastrophically (48%).** Most pages on a gear company's site don't say "we manufacture gears" — they show products, services, news. Only a few pages (About Us, Manufacturing) explicitly state manufacturing. Majority vote is dominated by the non-manufacturing pages.

**Any-true with min_count=3 is best at 86%.** The 3 failures (Circlegear, Khkgears, Commercialgear) likely have <3 pages at 0.7+ confidence that explicitly state manufacturing — may need lower threshold or different extraction prompt.

#### 2A: Product List Consolidation

Product lists from multiple pages accumulate duplicates and near-duplicates:

| Company | Extractions | Total Mentions | Unique Products | Dedup Ratio |
|---------|-------------|----------------|-----------------|-------------|
| Shanthigears | 26 | 646 | 76 | 8.5x |
| Conedrive | 62 | 245 | 163 | 1.5x |
| Rossi | 45 | 213 | 147 | 1.4x |
| Flender | 19 | 105 | 43 | 2.4x |
| Bonfiglioli | 27 | 66 | 62 | 1.1x |

Frequency of mentions is a useful quality signal for products. Shanthigears has heavy duplication (8.5x) suggesting many product pages repeat the same catalog. Dedup by normalized name handles exact duplicates but near-duplicates remain (e.g., "Reductores y motorreductores planetarios" vs "Reductores y motorreductores de ejes paralelos").

#### Trial 2 Summary: Recommended Consolidation Architecture

| Field Type | Strategy | Rationale |
|-----------|----------|-----------|
| **Scalar identity** (company_name) | Frequency vote | 100% vs 85% accuracy |
| **Scalar detail** (headquarters, employee_count) | Weighted frequency (conf × count) | Picks most-agreed + highest-confidence value |
| **Boolean company-level** (manufactures_X) | Any-true (min=3, min_conf=0.7) | 86% vs 48% accuracy |
| **Text** (manufacturing_details) | Longest from top-3 confidence | Preserves detail while filtering noise |
| **Entity lists** (products, certifications) | Union + dedup by normalized name + frequency ranking | Accumulates across pages, dedup removes exact dupes |

**This is a new pipeline step** — "consolidation" sits between extraction and reporting:
```
Extract (per-source) → Consolidate (per-group) → Report/Search/Entity
```

### Trial 3: Entity Extraction

**Infrastructure status:** Complete but disconnected from pipeline.
- Entity types configured: company, site_location, product, service, certification
- 0 entities in DB despite 47K extractions
- `extract_entities()` exists in LLMClient but never called from pipeline
- EntityRepository with `get_or_create()` dedup works but is never invoked
- **Assessment deferred** until consolidation strategy is established

### Trial 4: Report Quality

#### 4A: Source-Level Table Report (no consolidation)

Generated a table report for 3 companies (Flender, Rossi, ZF) at source level (one row per URL). 150 extractions, ~25 rows visible.

**Quality Issues Identified:**

1. **Massive redundancy**: Each URL is a separate row. Flender has ~11 rows, Rossi ~8, ZF ~6. Most fields are "N/A" because each page only has a few relevant fields. The table is 90%+ empty cells.

2. **Multilingual pollution**: Product names appear in Turkish ("EP - Planet redüktörler"), Portuguese ("Redutores e motorredutores planetários"), Polish ("Przekładnie i motoreduktory planetarne"), German ("A-REIHE"), Chinese ("采埃孚"), Japanese ("ZFジャパン"). Same products, different languages — creates false variety.

3. **Event locations mixed with real locations**: Rossi events page lists event venues (Las Vegas, Bengaluru, Rimini) as "site_type: event venue" — these are NOT company locations but pollute the locations column.

4. **Employee count inconsistency**: ZF shows 161,631 employees from multiple pages (correct for global), but "1001-5000" as range (wrong — should be "5000+"). Rossi shows both 175 and 1000 employees from different pages.

5. **False product specs**: All gearbox products show identical placeholder specs like "(0.746kW, 1.356Nm, 95.0%)" — these are NOT real specs, they're default/template values the LLM fills in when actual specs aren't on the page.

6. **Company name from localized pages**: ZF becomes "ZF Automotive Systems Poland Sp. z o.o." from the Poland page, "ZF Japan Co., Ltd." from Japan, "ZF Automotive Korea Co., Ltd." from Korea.

7. **Confidence values not actionable**: Shown as raw floats like "0.39285714285714285" — noisy, unhelpful.

8. **Zero-confidence rows still included**: A row with confidence 0.0 appears (Rossi reducers page) adding no value.

#### 4A: What a Good Consolidated Report Would Look Like

Instead of 25 rows of mostly-empty per-URL data, consolidation would produce:

| Company | HQ | Employees | Manufactures Gears | Manufactures Motors | Products (Gearbox) | Products (Motor) | Certifications | Locations |
|---------|-----|-----------|-------------------|--------------------|--------------------|-----------------|----------------|-----------|
| Flender | Bocholt, Germany | 2,500 | Yes | No | FLENDER ONE; PLANUREX 2/3; NAVILUS; Helical Gear Units; ... | ELECTRIC MOTOR | ISO 9001, ISO 14001, ATEX, ... | Germany (3), India (3), China (1), Australia (2) |
| Rossi | Modena, Italy | ~1,000 | Yes | Yes | G Series; EP Series; H Series; iFit; ... | TX Series; iFit; EP; G; H; ... | ISO 9001 | Italy (3), France (1), Spain (1) |
| ZF | Friedrichshafen, Germany | 161,631 | Yes | Yes | N/A (automotive focus) | Electric motors | ISO 9001, ISO 14001 | 30 countries, 161 sites |

This is the target output. Consolidation must:
- Union product lists, dedup by normalized name, strip language variants
- Pick most-frequent company name
- Any-true for manufacturing booleans
- Filter event venues from real locations
- Round/format confidence values

#### Trial 4 Summary

The current source-level report is **unusable for decision-making**. It's a raw data dump with:
- 90%+ empty cells
- Multilingual duplicates
- Event venues mixed with real locations
- Placeholder product specs
- Contradictory employee counts

**Consolidation is the #1 priority** — without it, reports, search, and entities are all noise.

### Trial 5: Precision Reality Check

After the initial trials showed promising results (100% company name accuracy, 86% boolean accuracy), we stress-tested by examining the FULL dataset, not just the 20-company sample.

#### Company Names — Full Dataset Risk Analysis

The initial 100% accuracy was on 20 hand-picked well-known companies. The full dataset of 223 companies with data reveals:

**24 companies where frequency winner has <50% agreement** — these are the fragile cases:

| Risk Category | Count | Examples | Problem |
|--------------|-------|---------|---------|
| Multi-company domains | ~3-5 | En (17 variants, "DMG MORI" at 49%) | Domain hosts pages for multiple companies |
| Distributors | ~5-8 | Rjeletromotores → "WEG" (40%), Autservice | Sells other brands' products, brand dominates pages |
| Extreme fragmentation | ~10 | Everestrkd (8 variants, 22%), Pemltd (9 variants, 22%) | Too many name forms, no clear winner |

**Realistic accuracy by segment:**

| Segment | Companies | Expected Accuracy |
|---------|-----------|-------------------|
| 1 variant (trivial) | 64 (29%) | ~100% |
| 2 variants (minor) | 52 (23%) | ~95% |
| 3-5 variants | 70 (31%) | ~85-90% |
| >5 variants | 37 (17%) | ~60-70% |
| **Weighted total** | **223** | **~87-90%** |

#### Boolean Fields — False Positive Analysis

The initial trial only measured true-positive rate (21 known gear makers). We then checked the **false-positive risk** — companies that any-true would flag as "manufactures gears" despite being service/repair companies.

Any-true (min=3, conf>=0.7) flags **100 companies** as "manufactures gears". Of those, 12 have true_votes overwhelmingly outnumbered by false_votes. Spot-checking:

| Company | True | False | Actually Makes Gears? | Verdict |
|---------|------|-------|-----------------------|---------|
| PPG Works | 4 | 42 | **Yes** — "We manufacture a full range of Gear types" | Correct |
| Zero Max | 3 | 42 | **Yes** — makes right-angle gearboxes | Correct |
| Johnson Electric | 4 | 9 | **Yes** — makes planetary gearboxes | Correct |
| Gbs International | 4 | 48 | **No** — repair/overhaul only | **False positive** |
| Rotork | 4 | 20 | **Borderline** — actuators with integrated gearboxes | Debatable |
| Stellantis | 4 | 5 | **Borderline** — automotive transmissions, not standalone gearboxes | Debatable |

**Root cause of false positives:** Service/repair companies do in-house machining of replacement parts. The LLM sees "manufacture of a new hollow output shaft" during a repair → flags `manufactures_gearboxes: true`. Any-true then propagates these 3-4 pages into the canonical record.

**Realistic boolean precision:**
- True positive rate: ~86% (3/21 known manufacturers missed — Circlegear, Khkgears, Commercialgear)
- False positive rate: ~10-15% estimated (service/repair companies incorrectly flagged)
- Combined F1: ~85%

#### Product Lists — Multilingual Duplication

Not formally precision-tested, but report inspection reveals:
- Same product appears in Turkish, Portuguese, Polish, German, Chinese, Japanese
- "G SERIES" = "Série G" = "G-Reihe" = "Серия G" = 4 entries after simple dedup
- Placeholder specs "(0.746kW, 1.356Nm, 95.0%)" appear when page has no real specs

**Estimated product list precision: ~60-70%** (many false duplicates from language variants)

#### Location Data — Event Venue Contamination

- Rossi events page lists 15 event venues as locations with `site_type: "event"/"event venue"`
- Filterable but not currently filtered
- Real locations mixed with event venues inflates site count

**Estimated location precision: ~60-70% raw, ~85% with event-venue filtering**

#### Overall Precision Summary

| Field Type | Strategy | Expected Precision | Expected Recall | Confidence Level |
|-----------|----------|-------------------|-----------------|-----------------|
| Company name | frequency | **87-90%** | ~95% | Medium |
| Booleans | any-true(3) | **85-90%** | ~86% | Medium |
| Product lists | union+dedup | **60-70%** | ~90% | Low |
| Locations | union+dedup | **~85%** with event filter | ~85% | Low |
| Text fields | longest-top-3 | **Unknown** | Unknown | None |
| Numeric | weighted-median | **Unknown** | Unknown | None |

#### Known Precision Gaps Requiring Solutions

| Gap | Impact | Mitigation | Difficulty |
|-----|--------|-----------|-----------|
| Multi-company domains | ~5 companies get wrong name | Detect via name variant entropy; flag for review | Medium |
| Distributor sites | ~5-8 companies pick vendor name | Detect via source_group != frequency-winner name | Easy |
| Service vs manufacturing | ~10-15 false positives on booleans | Refine extraction prompt to distinguish repair from manufacturing | Medium (re-extract) |
| Multilingual products | ~30-40% false duplication | Language detection + English-only filtering | Easy-Medium |
| Event venue locations | ~15-20% false locations | Filter site_type containing "event" | Easy |
| Placeholder specs | Unknown % of specs are fake | Detect 0.746/1.356/95.0 patterns | Easy |

**v1 with easy filters → ~85-90% precision**
**v2 with upstream prompt refinement → ~92-95% precision**

### issues

  The Most Significant Quality Bottleneck: Numeric Hallucination                                                                    
   
  Product Specifications                                                                                                            
                                                                                                                                  
  Of 1,304 products with non-zero power ratings at >=0.7 confidence:                                                              
                                                                                                                                    
  ┌──────────────────────────┬───────┬────────────┬─────────────────────┐
  │       Quote Check        │ Count │ Percentage │       Meaning       │
  ├──────────────────────────┼───────┼────────────┼─────────────────────┤
  │ Power value NOT in quote │ 1,058 │ 81%        │ Likely hallucinated │
  ├──────────────────────────┼───────┼────────────┼─────────────────────┤
  │ Power value in quote     │ 246   │ 19%        │ Possibly real       │
  └──────────────────────────┴───────┴────────────┴─────────────────────┘

  81% of non-zero product specs are fabricated by the LLM. Three hallucination patterns:

  1. Unit conversion constants: 0.746 kW (= 1 HP), 1.356 Nm (= 1 ft-lb) — the LLM generates textbook conversion factors as product
  specs
  2. Model numbers as specs: Reintjes "DLG 813-110131" → torque extracted as 110,131 Nm — that's a model number
  3. Made-up plausible values: LLM generates specs from general domain knowledge when page only shows product name

  Employee Counts

  Of 584 employee count values with quotes at >=0.7 confidence:

  ┌────────────────────┬───────┬────────────┐
  │    Quote Check     │ Count │ Percentage │
  ├────────────────────┼───────┼────────────┤
  │ Value NOT in quote │ 353   │ 60%        │
  ├────────────────────┼───────┼────────────┤
  │ Value in quote     │ 231   │ 40%        │
  └────────────────────┴───────┴────────────┘

  Examples of misinterpretation:
  - ABB: 140000 from quote "more than 140-year history" — years → employees
  - ABB: 100000 from "over one million washdown motors installed" — motors → employees
  - Stellantis: 17 different employee counts from 100 to 400,000

  What This Means for Consolidation

  Consolidation operates on a foundation where ~60-80% of numeric values are hallucinated. No consolidation algorithm can fix this:

  - Frequency voting picks the most-common hallucinated value
  - Weighted-frequency picks the highest-confidence hallucinated value
  - Any-true propagates a hallucinated "true" into the canonical record

  For text and boolean fields, extraction quality is reasonable (~85-90%). For numeric fields, it's catastrophic (~20% real data).

  The Real Bottleneck Hierarchy

  ┌──────────┬───────────────────────────────────┬────────────────────────────────────────┬────────────────────────────────────┐
  │ Priority │            Bottleneck             │                 Impact                 │           Where It Lives           │
  ├──────────┼───────────────────────────────────┼────────────────────────────────────────┼────────────────────────────────────┤
  │ #1       │ Numeric field hallucination       │ 60-80% of numeric values are           │ Upstream — extraction prompt +     │
  │          │                                   │ fabricated                             │ schema                             │
  ├──────────┼───────────────────────────────────┼────────────────────────────────────────┼────────────────────────────────────┤
  │ #2       │ No cross-source consolidation     │ Reports unusable, contradictory data   │ Downstream — new pipeline step     │
  ├──────────┼───────────────────────────────────┼────────────────────────────────────────┼────────────────────────────────────┤
  │ #3       │ Zero-confidence waste             │ 57.7% of extraction calls produce      │ Classification — LLM skip-gate     │
  │          │                                   │ nothing                                │                                    │
  ├──────────┼───────────────────────────────────┼────────────────────────────────────────┼────────────────────────────────────┤
  │ #4       │ Multilingual duplication          │ 30-40% false product duplicates        │ Consolidation filter               │
  ├──────────┼───────────────────────────────────┼────────────────────────────────────────┼────────────────────────────────────┤
  │ #5       │ Misattribution                    │ ~5-8 companies get wrong name          │ Consolidation filter               │
  │          │ (vendor/distributor)              │                                        │                                    │
  └──────────┴───────────────────────────────────┴────────────────────────────────────────┴────────────────────────────────────┘

  How to Fix #1 (Numeric Hallucination)

  Option A: Quote verification (no re-extraction, immediate)
  - Post-processing filter on existing data
  - For each numeric value: check if it appears in the _quote field
  - If not → null it out (or flag as "unverified")
  - Catches ~80% of hallucinations
  - Weakness: format mismatches (140,000 vs 140000), and some real values aren't quoted verbatim

  Option B: Schema redesign + prompt improvement (requires re-extraction)
  - Make numeric fields explicitly nullable: "power_rating_kw": float | null
  - Prompt: "ONLY provide numeric values that are explicitly stated as numbers on the page. If no spec is given, use null."
  - Add extraction instruction: "Do NOT convert units, infer values, or use domain knowledge for numeric fields"
  - Expected to eliminate 90%+ of hallucinations at source

  Option C: Hybrid (immediate filter + eventual re-extraction)
  - Apply quote verification NOW on existing data (Option A)
  - Redesign schema and prompts for next extraction run (Option B)
  - Consolidation layer treats null as "unknown" rather than 0

  Revised Recommendation

  The original plan was: build consolidation → improve reports. But given the hallucination findings:

  1. Quote verification filter (immediate, no code in pipeline)
     → Null out unverified numeric values in existing data
     → Reduces hallucinated specs from 80% to ~20%

  2. Schema + prompt fix for numeric fields (requires re-extraction of affected types)
     → Nullable numeric fields + explicit "only extract stated values"
     → Eliminates hallucination at source

  3. THEN build consolidation service
     → Operating on clean(er) data
     → Frequency voting, any-true, union-dedup

  4. THEN improve reports
     → Now based on consolidated, verified data

  The key insight: consolidation on hallucinated data produces confidently-presented hallucinations. That's worse than no
  consolidation at all.

## Architecture Recommendation

Based on all trials, the downstream pipeline needs a **consolidation layer**:

```
Current:  Extract → Store → Report/Search
Proposed: Extract → Store → Consolidate → Report/Search/Entity
```

### Consolidation Service Design

**Input**: All extractions for a (project_id, source_group, extraction_type) with confidence >= threshold
**Output**: One canonical record per (source_group, extraction_type)

**Per-field strategies** (configured per field_group in extraction_schema):

| Field Category | Strategy | Implementation |
|---------------|----------|----------------|
| Identity scalars | `frequency` | Most-frequent non-null value (case-insensitive) |
| Detail scalars | `weighted_frequency` | Sum confidence per unique value, pick highest total |
| Booleans (company-level) | `any_true` | True if N+ extractions say true at high confidence |
| Free text | `longest_top_k` | Longest value from top-K confidence extractions |
| Entity lists | `union_dedup` | Union all, dedup by normalized name, rank by frequency |
| Numeric | `weighted_median` | Confidence-weighted median (handles outliers) |

**Pre-consolidation filters (v1):**
- Confidence threshold (per-type: 0.5 for company_info, 0.7 for products/services)
- Event venue filtering (exclude site_type containing "event" from locations)
- Placeholder spec filtering (detect 0.0/0.746/1.356 pattern values, strip them)
- Zero/null field stripping

**v2 improvements (after v1 validated):**
- Language detection + filtering (keep English + primary language only)
- Distributor detection (source_group != frequency-winner company_name → flag)
- Multi-company domain detection (name variant count > 10 + low agreement → flag for review)
- Upstream prompt refinement for manufacture vs repair distinction

### Implementation Priority

1. **Consolidation service** — new `src/services/extraction/consolidation.py`
2. **Schema-driven merge config** — add `consolidation_strategy` to field definitions
3. **Consolidated extraction storage** — store canonical records (new table or flag)
4. **Report integration** — reports read consolidated records instead of raw extractions
5. **v1 filters** — event venues, placeholder specs, confidence thresholds
6. **Validation** — run on full 238 companies, spot-check 30+ against web
7. **v2 filters** — language detection, distributor/multi-company detection
8. **Search integration** — embed consolidated records for better retrieval
9. **Entity extraction** — run on consolidated records (not raw per-page)
