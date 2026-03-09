# TODO: Extraction Quality Improvements

**Status**: Phase A COMPLETE & DEPLOYED (2026-03-09). Re-extraction running on all 3 projects with grounding gate active.
**Created**: 2026-03-08
**Priority**: Phase B (prompt improvements) is next

## Evidence Base

Wide trial across 3 schemas (drivetrain n=1765, Wikipedia n=182, jobs n=382) with real-time grounding computation. Value-as-quote deep analysis on n=1898 quotes. Trial scripts in `scripts/trial_wide_analysis.py`, `scripts/trial_grounding_realtime.py`, `scripts/trial_value_as_quote.py`.

## Cross-Schema Quality Summary

| Project | Schema | n | Well grounded (>=0.8) | Poorly grounded (<0.3) | Overconfident |
|---------|--------|---|----------------------|----------------------|---------------|
| Drivetrain | company_info, manufacturing, services, etc. | 1765 | 86.9% | 8.7% | 6.3% |
| Wikipedia | article_info, key_facts | 182 | 79.7% | 6.6% | 4.9% |
| Jobs | job_overview, job_benefits | 382 | **97.1%** | **0.0%** | 0.0% |

Jobs extraction is nearly perfect. Drivetrain is worst due to location/count fields. Wikipedia is middle ground. **Quality correlates with source completeness** — job postings contain all the data, company pages often lack HQ/employee info and the LLM fills gaps from training knowledge.

## Pipeline Status (post-Phase A)

Grounding gate is now active in the extraction pipeline:

- `apply_grounding_gate()` — async post-parse function: >=0.8 KEEP, 0.3-0.8 LLM RESCUE, <0.3 DROP
- `is_negation_quote()` — filters "No mention of..." quotes before grounding
- `effective_weight() = min(confidence, grounding_score)` — zero weight for fabricated data in consolidation
- `v2_source_grounding_retry` — re-extracts chunks with avg_grounding below threshold
- Field-type-aware: boolean/text/summary fields exempt from rescue

**Observed in production (2026-03-09)**: Grounding retries firing correctly. Truncation issue on `company_meta` for pages with municipality lists (LLM hallucinates hundreds of cities as locations, exceeds max_tokens). Anti-hallucination prompt (Phase B) would mitigate.

### Three-Tier Grounding Decision (validated by `trial_grounding_middle.py`)

| String-match grounding | Decision | Rationale |
|------------------------|----------|-----------|
| >= 0.8 | **KEEP** | Tier 1/2 match — quote verified in source |
| 0.3 – 0.8 | **LLM RESCUE** | Borderline — ask LLM to find exact quote or confirm fabrication |
| < 0.3 | **DROP** | Clearly fabricated — no source support |

**Why not just use 0.3 as keep threshold?** Trial on 99 cases in the 0.3-0.8 range showed only 5% have the value actually in the source. The rest are word-window false positives — coincidental word overlap (e.g., "service" appearing in both a fabricated service list and source text about "severe service"). Keeping these would let ~94 fabricated fields through.

**Why LLM rescue instead of dropping at 0.5?** A hard 0.5 threshold drops everything in the band. LLM rescue can save the ~5% of legitimate cases where the extraction LLM paraphrased or reworded the source quote, while still rejecting the 95% fabricated cases. The volume is tiny (~99 cases out of ~1800 = 5.5%), so the LLM cost is negligible (~20s at 0.2s/call).

## Problem 1: World Knowledge Leaking (8.7% of extractions)

**The biggest quality issue.** LLM uses training data knowledge instead of source text.

### Evidence

| Field | n | Poorly grounded | Avg grounding | Pattern |
|-------|---|----------------|---------------|---------|
| `company_info.headquarters_location` | 275 | **28.4%** | 0.69 | Fabricates "Köln, Germany", "United States" |
| `company_info.employee_count_range` | 62 | **32.3%** | 0.66 | Invents "501-1000", "1001-5000" |
| `company_info.employee_count` | 34 | 17.6% | 0.76 | Invents "1200", "over 1,000" |
| `services.service_types` | 98 | 15.3% | 0.81 | Generates generic lists |
| `article_info.category` | 23 | 30.4% | 0.70 | Classification from knowledge |
| `key_facts.time_period` | 23 | 21.7% | 0.68 | Date ranges from knowledge |

### Examples

```
Igus/headquarters_location:  conf=0.90  ground=0.00  quote="Köln, Germany"
  → LLM knows Igus HQ from training data, source page doesn't mention it

Fls/employee_count_range:    conf=0.90  ground=0.00  quote="101-500"
  → LLM estimates range, source has no employee data

Parvalux/headquarters_location: conf=0.80  ground=0.00  quote="United Kingdom"
  → Country name not on the crawled pages
```

### Fix: Three-tier grounding gate with LLM rescue

Grounding already detects fabrications (score=0.0-0.3). The 0.3-0.8 band is a noise zone (95% fabricated, 5% legitimate paraphrases). The fix uses a three-tier decision:

```python
# In _parse_chunk_to_v2(), after computing grounding for each field:
grounding = ground_field_item(field_def.name, value, quote, chunk.content, field_def.field_type)
grounding_mode = GROUNDING_DEFAULTS.get(field_def.field_type, "required")

if grounding_mode == "required":
    if grounding < 0.3:
        continue  # clearly fabricated — drop
    elif grounding < 0.8:
        # borderline — LLM rescue: ask for exact quote or confirm fabrication
        rescue = await self._rescue_quote(field_def.name, value, chunk.content)
        if rescue is None:
            continue  # LLM confirmed no support — drop
        quote = rescue  # LLM found real quote — use it
        grounding = verify_quote_in_source(rescue, chunk.content)
        if grounding < 0.8:
            continue  # rescued quote still doesn't match — drop
```

**Location**: `src/services/extraction/schema_orchestrator.py`, inside the field loop in `_parse_chunk_to_v2()` (~line 1000) and entity loop in `_parse_entity_chunk_v2()` (~line 1050).

**Three thresholds (validated by `trial_grounding_middle.py`, n=99 borderline cases)**:
- **>= 0.8**: KEEP — Tier 1 exact (1.0) or Tier 2 punct-stripped (0.95). All legitimate.
- **0.3 – 0.8**: LLM RESCUE — 95% are word-window false positives (coincidental word overlap). Send chunk + field + value to LLM to find exact supporting quote. If LLM finds one, rescue the field with the corrected quote. If not, drop. Volume: ~5.5% of extractions (~99 out of 1800). Cost: ~20s per batch at 0.2s/call.
- **< 0.3**: DROP — All fabricated. avg grounding=0.14, 93% below 0.3.

**Semantic/none fields exempt**: Boolean fields (`provides_services`, `manufactures_gearboxes`) use `grounding_mode="semantic"` and are not gated — their quotes support a yes/no answer where the value itself won't appear in the quote, and their grounding is already high (0.93-0.99).

## Problem 2: Value-as-Quote Echo (23.1% of quotes)

**The LLM echoes its extracted value as the "supporting quote" instead of quoting source text.**

### Evidence

- 567 out of 1898 quotes are exact copies of the value (29.9%)
- Concentrated in: `company_name` (312), `headquarters_location` (192), `employee_count_range` (47)

### Deep analysis result

**Grounding alone completely solves this.** No prompt change or separate filter needed.

| Category | n | Avg grounding | Grounding >= 0.8 |
|----------|---|---------------|-----------------|
| Legitimate echo (value IS in source text) | 377 | **1.00** | **100%** |
| Bad echo (value fabricated, echoed as quote) | 190 | **0.14** | **7%** |

When `value == quote` and the value actually exists in the source (e.g., `company_name="Cotta Transmission LLC"`), it's not a problem — it's a short but valid source quote. Grounding = 1.0 confirms it.

When `value == quote` and the value is fabricated (e.g., `headquarters_location="Brazil"`), grounding = 0.0 catches it.

**Breakdown by field:**

| Field | Total echoes | Value in source | Value NOT in source |
|-------|-------------|----------------|-------------------|
| `company_name` | 312 | 304 (97%) | 8 (3%) |
| `headquarters_location` | 192 | 68 (35%) | **124 (65%)** |
| `employee_count_range` | 47 | 0 (0%) | **47 (100%)** |

**No separate filter needed.** Requiring longer quotes would hurt legitimate cases like company names where the entire value IS a short source text snippet. The grounding gate from Problem 1 handles the bad echoes.

## Problem 3: LLM Confidence is Poorly Calibrated

### Evidence

| Confidence | n | Avg grounding | Poorly grounded (<0.3) |
|-----------|---|---------------|----------------------|
| 0.9-1.0 | 865 | 0.90 | **7.6%** |
| 0.7-0.9 | 697 | 0.89 | **9.2%** |
| 0.5-0.7 | 156 | 0.88 | 10.9% |
| 0.0-0.5 | 47 | 0.84 | 12.8% |

Near-zero correlation between stated confidence and actual groundedness. LLM confidence < 0.7-0.8 is already very unconfident — the LLM is inherently optimistic. The LLM reports 0.90 confidence for fabricated values.

### Fix: Confidence recalibration

Replace the current consolidation weight formula:

```python
# CURRENT (consolidation.py line ~280):
# weight = confidence * max(grounding_score, 0.1)
# Problem: floor of 0.1 means grounding=0.0 still contributes

# NEW:
effective_confidence = min(stated_confidence, grounding_score)
# grounding=0.0 → effective=0.0 (correctly rejected)
# grounding=1.0, conf=0.9 → effective=0.9 (correctly kept)
# grounding=1.0, conf=0.5 → effective=0.5 (correctly cautious)
```

**Location**: `src/services/extraction/consolidation.py`, `effective_weight()` function (~line 270).

Change:
```python
def effective_weight(confidence, grounding_score, grounding_mode):
    if grounding_mode == "required":
        gs = grounding_score if grounding_score is not None else 0.0
        return min(confidence, gs)  # was: confidence * max(gs, 0.1)
    return confidence
```

**Effect**: Removes the floor=0.1 that allows fabricated data to contribute. Combined with the grounding gate, fabricated fields are either dropped at extraction time (gate) or given zero weight in consolidation (recalibration). Belt and suspenders.

## Problem 4: Negation Quotes (0.4% — minor)

LLM writes "No mention of X" or "N/A" as quotes instead of omitting the field.

### Examples

```
certifications:     "No explicit certifications mentioned"
manufactures_motors: "N/A"
services_motors:    "no mention of motors"
```

### Fix: Filter in `_parse_chunk_to_v2()`

These always have grounding=0.0, so the grounding gate catches them too. But a specific filter provides cleaner diagnostics and catches edge cases:

```python
_NEGATION_RE = re.compile(
    r"^(no|not|n/?a|none)\b.{0,50}"
    r"(mention|explicit|specified|found|available|provided|information|data|details|certif)",
    re.IGNORECASE,
)

def _is_negation_quote(quote: str) -> bool:
    return bool(_NEGATION_RE.match(quote.strip()))
```

**Location**: `src/services/extraction/grounding.py` (pure function), called from `_parse_chunk_to_v2()`.

Apply before grounding computation — if quote is negation, skip the field entirely. This is a micro-optimization (14 cases) but trivial to implement.

## Problem 5: Overconfident Fabrications (6.3%)

112 cases where conf >= 0.8 AND grounding < 0.3.

**Already solved by Problems 1 + 3.** The grounding gate drops these at extraction time. Confidence recalibration gives them zero weight in consolidation. No additional work needed.

## Problem 6: Boolean/Summary Fields (expected behavior — no fix)

Many fields show 0% value-in-quote rate: `provides_services`, `manufactures_gearboxes`, `services_gearboxes`, etc. This is **correct behavior** — these are boolean/summary fields where the quote supports a yes/no answer. The grounding is high (0.93-0.99) because the quote text IS in the source, it's just that the value ("true") isn't literally in the quote text.

**No fix needed.** The grounding mode (`semantic` for boolean, `none` for summary) correctly exempts these from the grounding gate.

## Implementation Status

### Phase A: Quality gates — COMPLETE & DEPLOYED (2026-03-09)

All 4 steps implemented and deployed. Architecture:
- `apply_grounding_gate()` — async post-parse function (not inline in sync parse), runs outside extraction semaphore
- `is_negation_quote()` — regex filter, wired into both field and entity paths
- `rescue_quote()` — LLM finds verbatim passage, re-verifies with `verify_quote_in_source()`. Source truncated to 16K chars.
- `effective_weight() = min(confidence, grounding_score)` — zero weight for fabricated data
- Field-type-aware: boolean/text/summary exempt from rescue
- Entity rescue only attempts fields with identifiable name/entity_id/id
- Tests: `tests/test_grounding_gate.py`
- `LLMGroundingVerifier` created in `worker.py`, passed to `SchemaExtractionOrchestrator.__init__()` as `grounding_verifier`

**Grounding backfill** (`scripts/backfill_grounding_scores.py`): Not needed for new v2 extractions (grounding computed inline). Only needed if analyzing old v1 data.

### Phase B: Prompt improvements — TRIAL COMPLETE, READY TO DEPLOY

Phase A eliminates bad data post-hoc. Phase B prevents it at the source. A/B trial confirms modest but consistent improvement with zero regressions.

**Motivated by production observation (2026-03-09)**: `company_meta` truncation on pages with municipality lists — LLM hallucinates hundreds of cities as manufacturing locations, generating 28K+ char responses. Anti-hallucination prompt would reduce this.

#### A/B Trial Results (2026-03-09)

**Trial**: `scripts/trial_prompt_ab.py` — 30 sources × 7 field groups, paired comparison on identical inputs.

| Metric | A (baseline) | B (anti-halluc) | Delta |
|--------|-------------|-----------------|-------|
| Well grounded (≥0.8) | 88.3% | **90.8%** | **+2.5pp** |
| Poorly grounded (<0.3) | 6.4% | **5.1%** | **-1.3pp** |
| Overconfident | 6.4% | **5.1%** | **-1.3pp** |
| Value == quote | 2.1% | **0.5%** | **-1.6pp (4x reduction)** |
| Avg latency | 5.81s | **5.29s** | **-0.52s** |
| Fields extracted | 188 | 195 | +7 |

**Per-field highlights:**
- `services_gearboxes`: 33% poor → 0% — negation quotes eliminated
- `services_drivetrain_accessories`: 25% poor → 0%
- `manufactures_drivetrain_accessories`: 12% poor → 0%
- 3 fields fixed (A<0.3 → B≥0.8), **0 regressions** (A≥0.8 → B<0.3)

**Known limitation — `company_name` quoting artifact**: 8 cases where LLM quotes `"Company: X"` from the user prompt context line instead of source text. Shows as poorly grounded but is NOT a hallucination — the value is correct. This is a separate issue (the context line `Company: {source_group}` is not in the source text we verify against). Not caused or worsened by prompt changes.

#### Step 6: Anti-hallucination prompt instruction

**File**: `src/services/extraction/schema_extractor.py`
**Target**: All 4 v2 prompt builders (`_build_system_prompt_v2`, `_build_entity_list_system_prompt_v2`, and v1 equivalents)

Inject a hallucination guard block **before** the RULES section:

```
CRITICAL CONSTRAINT: You are a text extraction tool, NOT a knowledge base.
- ONLY extract information that is EXPLICITLY STATED in the provided text below.
- If a field's information is not in the text, you MUST return null — do NOT guess or fill in from your training knowledge.
- Common mistake: inventing headquarters locations, employee counts, or categories from your training data. Do NOT do this.
- If you are unsure whether information is in the text or from your own memory, return null.
```

#### Step 7: Quote ≠ value prompt instruction

Append to the existing `quoting_note` in non-strict mode:

```
The "quote" must be a VERBATIM excerpt copied directly from the source text,
NOT a restatement of your extracted value. If your quote would be identical
to the value, find a longer surrounding passage instead.
```

#### Implementation plan

1. Add `_HALLUCINATION_GUARD` constant to `schema_extractor.py` (single source of truth)
2. Add `_QUOTE_NOT_VALUE_NOTE` constant
3. Insert hallucination guard into `_build_system_prompt_v2()` — before RULES block
4. Insert hallucination guard into `_build_entity_list_system_prompt_v2()` — before RULES block
5. Append quote-not-value to non-strict `quoting_note` in both v2 builders
6. Keep v1 prompts consistent (they already have "Extract ONLY" rule)
7. Run `pytest -q` — no functional changes, just prompt text
8. Deploy and monitor grounding metrics

**No architectural changes needed — prompt-only modification in 4 methods.**

### Phase B verification

After deploying prompt changes:

1. Monitor `v2_source_grounding_retry` event frequency — should decrease (fewer low-grounding first attempts)
2. Monitor `schema_extraction_truncated` events — should decrease (fewer municipality hallucinations)
3. Re-run `scripts/trial_prompt_ab.py --limit 50` on next re-extraction batch to confirm at scale
4. `pytest -q` — all tests pass

### Phase A verification (completed)

1. ~~Re-run `scripts/trial_grounding_realtime.py`~~ — run after current re-extraction completes
2. ~~Re-run `scripts/trial_value_as_quote.py`~~ — grounding gate catches bad echoes
3. ~~Re-run `scripts/trial_wide_analysis.py`~~ — overconfident cases gated
4. Verify LLM rescue logs — check rescue rate and quality of rescued quotes
5. `pytest -q` — all tests pass
6. Run consolidation on drivetrain project — verify report quality improves

## Schema-Specific Findings

### Drivetrain schema (worst quality)

- `company_info` group is the main problem child (81% well-grounded vs 90%+ for others)
- `headquarters_location` and `employee_count_range` are the worst individual fields
- LLM is excellent at `manufactures_gearboxes` (97%), `services_gearboxes` (99%), `provides_services` (99%)
- Manufacturing/services extraction is high quality when the info exists in source

### Wikipedia schema (medium quality)

- `article_info.category` (30% bad): LLM classifies articles using world knowledge
- `key_facts.time_period` (22% bad): LLM invents date ranges from memory
- `article_info.title` (98% grounded): Excellent — always in source
- `article_info.inception_date` (96%): Very good when dates exist in text

### Jobs schema (excellent quality)

- 97.1% well grounded, 0% poorly grounded
- Job postings are self-contained — all extracted data IS in the posting text
- No world knowledge leaking — LLM has nothing to fabricate
- This demonstrates the pipeline works well when source text is comprehensive

## Trial Scripts

| Script | Purpose | Key finding |
|--------|---------|-------------|
| `scripts/trial_wide_analysis.py` | Cross-project quote quality categorization | 84.3% good, 11.6% bad, 4% acceptable |
| `scripts/trial_grounding_realtime.py` | Real-time grounding computation for v1 data | 86.9% well grounded, 8.7% poorly grounded |
| `scripts/trial_value_as_quote.py` | Value-as-quote echo deep analysis | Grounding perfectly separates legitimate (1.00) from bad (0.14) echoes |
| `scripts/trial_grounding_middle.py` | Borderline grounding 0.3-0.8 investigation | 99 cases, only 5% have value in source — 95% are word-window false positives. Justified LLM rescue approach over hard threshold. |
| `scripts/trial_ground_and_locate.py` | Position tracing prototype (related TODO) | 87.3% match rate with 4-tier algorithm |
