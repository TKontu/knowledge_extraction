# TODO: Extraction Quality Improvements

**Status**: Analysis complete, implementation planned
**Created**: 2026-03-08
**Priority**: High — 8.7% of extractions are poorly grounded, 23% have useless quotes

## Evidence Base

Wide trial across 3 schemas (drivetrain n=1765, Wikipedia n=182, jobs n=382) with real-time grounding computation. Value-as-quote deep analysis on n=1898 quotes. Trial scripts in `scripts/trial_wide_analysis.py`, `scripts/trial_grounding_realtime.py`, `scripts/trial_value_as_quote.py`.

## Cross-Schema Quality Summary

| Project | Schema | n | Well grounded (>=0.8) | Poorly grounded (<0.3) | Overconfident |
|---------|--------|---|----------------------|----------------------|---------------|
| Drivetrain | company_info, manufacturing, services, etc. | 1765 | 86.9% | 8.7% | 6.3% |
| Wikipedia | article_info, key_facts | 182 | 79.7% | 6.6% | 4.9% |
| Jobs | job_overview, job_benefits | 382 | **97.1%** | **0.0%** | 0.0% |

Jobs extraction is nearly perfect. Drivetrain is worst due to location/count fields. Wikipedia is middle ground. **Quality correlates with source completeness** — job postings contain all the data, company pages often lack HQ/employee info and the LLM fills gaps from training knowledge.

## Current Pipeline Gap

Grounding scores ARE computed inline during v2 extraction, but **nothing acts on them**:

- `_parse_chunk_to_v2()` — computes grounding per field, stores it, **never drops fields**
- Consolidation — uses grounding as a weight with **floor=0.1** (`conf * max(grounding, 0.1)`), so even grounding=0.0 fields contribute
- Reports — **no filtering at all**, uses raw extractions
- v1 data — grounding backfill never ran, all scores are 0.0

**Grounding is the single mechanism that solves problems 1, 2, and 5 simultaneously.** The trial proved the separation is clean: legitimate values avg grounding=1.00 vs fabricated values avg grounding=0.14.

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

## Implementation Plan

### Phase A: Quality gates (no re-extraction needed)

Three-tier grounding gate + LLM rescue for borderline cases + confidence recalibration. Solves problems 1, 2, and 5.

#### Step 1: Negation quote filter (trivial, do first)

**File**: `src/services/extraction/grounding.py` — add `is_negation_quote()` function.
**File**: `src/services/extraction/schema_orchestrator.py` — call before grounding in `_parse_chunk_to_v2()`.

```python
_NEGATION_RE = re.compile(
    r"^(no|not|n/?a|none)\b.{0,50}"
    r"(mention|explicit|specified|found|available|provided|information|data|details|certif)",
    re.IGNORECASE,
)

def is_negation_quote(quote: str) -> bool:
    return bool(_NEGATION_RE.match(quote.strip()))
```

```python
# In _parse_chunk_to_v2(), before grounding computation:
if quote and is_negation_quote(quote):
    continue  # LLM said "no mention of X" — skip field
```

**Impact**: 14 negation quotes eliminated. Trivial but clean.

#### Step 2: LLM quote rescue function (new)

**File**: `src/services/extraction/llm_grounding.py` — extend `LLMGroundingVerifier` with `rescue_quote()` method.

The existing `verify_quote()` method asks "does this quote support this value?" which is not what we need. We need a new method that asks the LLM to **find the exact supporting quote in the source text**:

```python
_RESCUE_SYSTEM_PROMPT = """You are a fact verification assistant. You will be given:
1. A field name and its claimed value (from a data extraction)
2. The original source text

Your task: find the EXACT verbatim text from the source that supports the claimed value.

Rules:
- Search the source text for a passage that directly supports the claimed value
- The quote must be a VERBATIM excerpt — copied exactly from the source, not paraphrased
- If no passage in the source supports this value, the value was likely fabricated
- Respond with JSON: {"quote": "exact verbatim text from source" | null, "supported": true/false}
"""

async def rescue_quote(
    self,
    field_name: str,
    value: Any,
    source_text: str,
) -> str | None:
    """Ask LLM to find the exact supporting quote in source text.

    Called for borderline grounding scores (0.3-0.8) where string-match
    is inconclusive. Returns the exact quote if found, None if fabricated.
    """
    # Truncate source to avoid excessive token usage
    truncated = source_text[:4000] if len(source_text) > 4000 else source_text

    user_prompt = (
        f"Field: {field_name}\n"
        f"Claimed value: {value}\n\n"
        f"Source text:\n{truncated}\n\n"
        f"Find the exact verbatim quote from the source that supports this value."
    )

    start = time.monotonic()
    try:
        response = await self._llm.complete(
            system_prompt=_RESCUE_SYSTEM_PROMPT,
            user_prompt=user_prompt,
            response_format={"type": "json_object"},
            temperature=0.0,
        )
        latency = time.monotonic() - start

        if not response.get("supported"):
            logger.info("llm_rescue_rejected", field=field_name, value=value, latency=latency)
            return None

        rescued_quote = response.get("quote")
        if not rescued_quote or not isinstance(rescued_quote, str):
            return None

        logger.info(
            "llm_rescue_found",
            field=field_name,
            value=value,
            quote=rescued_quote[:80],
            latency=latency,
        )
        return rescued_quote

    except Exception as e:
        logger.warning("llm_rescue_error", field=field_name, error=str(e))
        return None  # on error, treat as unrescuable → field dropped
```

**Key design decisions**:
- Source text truncated to 4000 chars (the chunk is typically ~5000 chars, but we don't need all of it)
- On error, returns None → field is dropped (fail-safe, not fail-open)
- The rescued quote is re-verified with `verify_quote_in_source()` — if the LLM hallucinates a quote that isn't actually in the source, it still gets caught
- Uses same `LLMClient` already injected into the orchestrator

#### Step 3: Three-tier grounding gate in extraction (highest impact)

**File**: `src/services/extraction/schema_orchestrator.py`

Requires making `_parse_chunk_to_v2()` async (the caller `_extract_group_v2` is already async).

In the field loop, after grounding computation:

```python
grounding = ground_field_item(field_def.name, value, quote, chunk.content, field_def.field_type)
grounding_mode = GROUNDING_DEFAULTS.get(field_def.field_type, "required")

if grounding_mode == "required":
    if grounding < 0.3:
        continue  # clearly fabricated — drop

    if grounding < 0.8:
        # borderline — LLM rescue
        rescued = await self._llm_grounding.rescue_quote(
            field_def.name, value, chunk.content
        )
        if rescued is None:
            continue  # LLM confirmed fabrication — drop
        # Re-verify the rescued quote against source
        rescued_grounding = verify_quote_in_source(rescued, chunk.content)
        if rescued_grounding < 0.8:
            continue  # rescued quote doesn't actually match source — drop
        quote = rescued
        grounding = rescued_grounding

location = locate_in_source(quote, full_content, chunk)
# ... rest of field storage
```

Same pattern in `_parse_entity_chunk_v2()` for entity-level grounding.

**Async change**: `_parse_chunk_to_v2()` becomes `async def _parse_chunk_to_v2()`. The only caller is `_extract_group_v2()` which is already async, so this is a minimal change. Add `await` at the call site.

**LLM verifier injection**: `SchemaExtractionOrchestrator.__init__()` already receives `LLMClient`. Create `LLMGroundingVerifier` instance during init:

```python
# In SchemaExtractionOrchestrator.__init__():
from services.extraction.llm_grounding import LLMGroundingVerifier
self._llm_grounding = LLMGroundingVerifier(self._llm_client)
```

**Impact**: Eliminates 8.7% poorly grounded fields + rescues ~5% of borderline cases that are actually legitimate paraphrases. Catches 93% of fabrications at the < 0.3 gate, remaining ~5.5% go through LLM rescue which catches 95% of those.

#### Step 4: Confidence recalibration in consolidation

**File**: `src/services/extraction/consolidation.py`

Change `effective_weight()`:

```python
def effective_weight(confidence, grounding_score, grounding_mode):
    if grounding_mode == "required":
        gs = grounding_score if grounding_score is not None else 0.0
        return min(confidence, gs)  # was: confidence * max(gs, 0.1)
    return confidence
```

**Impact**: Fabricated data gets zero weight. Removes the floor=0.1 safety net that was allowing bad data through. Belt-and-suspenders with the grounding gate — even if a field somehow passes the gate with low grounding, it gets minimal weight in consolidation.

#### Step 5: Run grounding backfill for v1 data

```bash
.venv/bin/python scripts/backfill_grounding_scores.py
```

v1 data currently has no grounding scores (all 0.0). The backfill computes them from stored quotes + source content. Required for the consolidation recalibration to work on v1 data.

### Phase B: Prompt improvements (requires re-extraction)

Lower priority — Phase A eliminates the bad data post-hoc. Phase B prevents it at the source.

#### Step 6: Anti-hallucination prompt instruction

**File**: `src/services/extraction/schema_extractor.py`

Add to system prompt (both v1 and v2):

```
IMPORTANT: ONLY extract information explicitly stated in the provided text.
Do NOT use your background knowledge to fill in missing information.
If a field's value cannot be found in the text, set it to null.
```

#### Step 7: Quote ≠ value prompt instruction

Already partially addressed by v2 `strict_quoting` mode. For normal mode, strengthen:

```
The "quote" field must contain the EXACT TEXT from the source document that
supports your answer. It must be a verbatim excerpt from the source, NOT a
restatement of your extracted value.
```

**Note**: Trial showed this is less urgent than originally thought — the grounding gate + LLM rescue catches bad echoes without prompt changes. Only pursue if re-extracting anyway.

### Verification

After implementing Phase A:

1. Re-run `scripts/trial_grounding_realtime.py` — confirm poorly grounded drops from 8.7% to <1%
2. Re-run `scripts/trial_value_as_quote.py` — confirm bad echoes eliminated
3. Re-run `scripts/trial_wide_analysis.py` — confirm overconfident count drops to ~0
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
