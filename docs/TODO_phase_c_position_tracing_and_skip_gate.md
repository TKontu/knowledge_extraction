# TODO: Phase C — Position Tracing & LLM Skip-Gate

**Created:** 2026-03-09
**Status:** Planned
**Depends on:** Phase A & B (grounding gate + prompt improvements) — DEPLOYED
**Blocked by:** Nothing — both features are independent of each other and of deployment

---

## Overview

Two independent features that improve extraction quality and efficiency:

| Feature | What | Impact | Risk |
|---------|------|--------|------|
| **Position tracing** | Unified `ground_and_locate()` replaces buggy `locate_in_source()` | Fixes broken offset positions + improves match rate 79.6% → 87.3% | LOW — pure functions, additive |
| **LLM skip-gate** | Binary page classifier before extraction | Eliminates ~15-25% wasted extraction calls | MEDIUM — changes classification flow |

Both are **independently deployable**. Recommended order: position tracing first (lower risk, no config changes), then skip-gate.

---

## Code Review Findings (pre-implementation audit)

### Critical: `locate_in_source()` has a position mapping bug

The current implementation (`extraction_items.py` lines 93-133) stores **normalized-space indices** as if they were original-content indices:

```python
norm_quote = _normalize(quote)        # collapses whitespace, lowercases
norm_content = _normalize(full_content)
pos = norm_content.find(norm_quote)
if pos >= 0:
    char_offset = pos                 # ← index into NORMALIZED text, NOT original
    char_end = pos + len(norm_quote)  # ← length of NORMALIZED quote, NOT original
```

When consumers use `full_content[char_offset:char_end]`, they get the wrong substring because normalization shifts all positions. Any content with extra whitespace, newlines, or multi-byte characters produces incorrect offsets. This is not just a coverage gap — the positions that ARE returned are wrong.

### Pipeline flow understanding (verified against code)

```
Source (with content)
  → Classification (Level 0: rule-based, Level 1: SmartClassifier)
  → chunk_document(content) → [Chunk1, Chunk2, ...]
  → For each chunk × field_group:
      → LLM extraction → raw JSON
      → _parse_chunk_to_v2() [schema_orchestrator.py:1149-1208]:
          For each field:
            grounding = ground_field_item(value, quote, chunk.content, field_type)
                        → min(verify_quote_in_source(quote, CHUNK), score_field(value, quote))
            location = locate_in_source(quote, FULL_CONTENT, chunk)  ← BUGGY
      → apply_grounding_gate() [outside LLM semaphore]:
          ≥ 0.8: KEEP
          < 0.3: DROP
          0.3-0.8 + required: rescue_quote() → re-verify ≥ 0.8 or DROP
  → merge chunks → to_v2_data() → store Extraction
```

**Key architectural details confirmed:**
- `ground_field_item()` runs on **chunk content** (Layer A: quote-in-source + Layer B: value-in-quote)
- `locate_in_source()` runs on **full content** (for absolute positions)
- Grounding gate runs **outside** the LLM semaphore (doesn't block extraction queue)
- Rescue uses 3-concurrency semaphore
- Entity grounding is Layer A only (quote-in-source), no value-in-quote
- `verify_quote_in_source()` is used by `rescue_quote()` for re-verification — must not be changed
- `ground_field_item()` and position tracking serve different purposes and must stay separate

### Skip-gate model selection: trial data review

**Critical finding: Qwen3-30B and gemma3-4B serve different roles.**

| Task | gemma3-4B | Qwen3-30B | Winner |
|------|-----------|-----------|--------|
| **Skip-gate classification** (v4, 80 pages) | 92.6% recall, 4 FN | 51.9% recall, 26 FN | **gemma3-4B** |
| **Quote verification** (grounding) | 67% detection, 100% recall | 80% detection, 100% recall | **Qwen3-30B** |
| **Product spec verification** | 95% detection, 75% recall | 100% detection, 67% recall | **Qwen3-30B** |

Qwen3-30B is the better *reasoner* (verification, grounding rescue) but is **too strict as a gatekeeper** — it skips pages that clearly contain extractable data (51.9% recall = loses half the data). This directly violates the "don't apply overly strict filtrations" requirement.

gemma3-4B's 4 false negatives were all pages where `cleaned_content` was just cookie banners (boilerplate removal bug, not classification failure). After GT correction, gemma3-4B achieves ~85% accuracy with near-perfect recall.

**No Qwen3-30B skip-gate trial exists at scale.** The v2 trial (31 hand-curated pages) showed 91.3% recall but the v4 trial (80 random production pages) showed 51.9%. The small trial was not representative.

**Decision needed:** The skip-gate model choice must prioritize recall over precision. The plan uses a configurable model field (`skip_gate_model`) so this can be tuned without code changes. Default should be gemma3-4B (or whichever model achieves ≥90% recall on a representative sample). A production trial on 100+ pages should validate the model choice before enabling the gate.

---

## Feature A: Position Tracing

### Problem

Two separate problems:

1. **Bug: Broken position mapping.** `locate_in_source()` returns normalized-text indices as if they were original-content indices. Every position stored today is potentially wrong.

2. **Coverage gap.** `locate_in_source()` only implements Tier 1 (normalized substring), while `verify_quote_in_source()` has 3-tier matching. The verifier confirms a quote exists but the locator can't find its position.

**Measured impact** (from `scripts/trial_quote_tracing.py`, 1218 production quotes):
- 79.6% match with Tier 1 alone
- 16.2% null `char_offset` in v2 extractions (6/37 quotes in sample)
- Of 249 Tier-1 misses: 98 had grounding ≥ 0.8 (verifier says "found" but locator can't position)

**Breakdown of why quotes miss Tier 1** (249 misses analyzed in `scripts/trial_quote_tracing_deep.py`):

| Category | Count | % | Fixed by new tiers? |
|----------|-------|---|---------------------|
| Fabricated values | 63 | 25.3% | No — LLM invented it |
| Reworded (words present, different order) | 55 | 22.1% | Yes — Tier 4 fuzzy |
| Punct-strip (em-dash, special chars) | 44 | 17.7% | Yes — Tier 2 |
| Hallucinated (quote not in source) | 35 | 14.1% | No — fabrication |
| Partial overlap (paraphrasing) | 30 | 12.0% | Partial — Tier 4 |
| MD + punct combined | 13 | 5.2% | Yes — Tier 3 |
| MD-strip only | 3 | 1.2% | Yes — Tier 3 |

### Solution: Unified `ground_and_locate()`

Single function returns BOTH a position score AND correct source positions via offset maps. Validated prototype: `scripts/trial_ground_and_locate.py`.

**Trial results — confirmed 2026-03-09** (1094 quotes, 2000 extractions from drivetrain batch):

| Tier | Match Type | Coverage | Quotes |
|------|-----------|----------|--------|
| 1 | Normalized substring | 78.4% | 858 |
| 2 | Punct-stripped with offset map | 2.9% | 32 |
| 3 | MD+punct stripped with offset map | 1.1% | 12 |
| 4 | Block-level fuzzy | 6.0% | 66 |
| — | Unmatched (fabrications) | 11.5% | 126 |
| | **TOTAL MATCHED** | **88.5%** | **968** |

- **+110 quotes recovered** over Tier 1 alone
- Only 3 of 126 unmatched have grounding ≥ 0.8 — algorithm finds virtually everything findable
- **Zero position errors** — all spans verified (>50% word overlap)
- 11.5% unmatched are genuinely fabricated/hallucinated (grounding < 0.8) — no algorithm fixes these

Earlier trial (1240 quotes) showed 87.3%. Current run on fresh v2 data shows 88.5% — improvement is consistent.

**This does NOT change grounding scores or filtering.** It only fixes position tracking. No extraction will be dropped or changed by this feature. The grounding gate continues using `ground_field_item()` → `verify_quote_in_source()` on chunk content, unchanged.

### Architecture

```
ground_and_locate(quote, content) -> GroundingResult
│
├── Pre-process quote:
│   ├── Strip trailing ellipsis ("products..." → "products")
│   └── Normalize unicode dashes (– → -)
│
├── Pre-compute: _normalize_with_map(content) → (norm_content, norm_map)
│
├── Tier 1: Normalized substring (score=1.0)        — 72.5% of quotes
├── Tier 2: Punct-stripped + offset map (score=0.95) — 3.9%
├── Tier 3: MD+punct stripped + offset map (score=0.9) — 1.9%
└── Tier 4: Block-level fuzzy (score=best_overlap)   — 9.0%
                                            Unmatched: 12.7% (fabrications)
```

**Key design: Offset map composition.** Each transformation produces `(text, offset_map)` where `offset_map[i]` = position in input for output char `i`. Maps compose via `result[i] = map_a[map_b[i]]`, chaining Tier 3's triple transformation back to original positions.

### Return type

```python
@dataclass
class GroundingResult:
    score: float              # 0.0-1.0 position match quality (NOT grounding score)
    source_offset: int | None # char position in ORIGINAL source content
    source_end: int | None    # end char position in ORIGINAL source content
    matched_span: str | None  # actual source text at content[offset:end]
    match_tier: int           # 1-4, or 0 for unmatched
```

**Note:** `GroundingResult.score` is the *position match quality* (1.0=exact, 0.95=punct-stripped, 0.9=md-stripped, 0.6-1.0=fuzzy overlap). This is NOT the grounding score stored on fields — that comes from `ground_field_item()` which combines Layer A (quote-in-source) + Layer B (value-in-quote).

### SourceLocation update

```python
@dataclass(frozen=True)
class SourceLocation:
    heading_path: list[str]
    char_offset: int | None
    char_end: int | None
    chunk_index: int
    match_tier: int = 0             # NEW: 1-4 tier that matched, 0=unmatched
    match_quality: float = 1.0      # NEW: tier score (1.0, 0.95, 0.9, or overlap ratio)
```

No DB migration needed — SourceLocation is serialized inside JSON `data`.

### Implementation Steps

#### A1. Core offset-mapped functions in `grounding.py` (LOW risk)

Pure functions, no side effects. Port from `scripts/trial_ground_and_locate.py` lines 37-216.

**Add to `grounding.py`:**

```python
# Pre-processing
_TRAILING_ELLIPSIS_RE = re.compile(r"\.{2,}$|…$")
_UNICODE_DASHES = str.maketrans({"\u2013": "-", "\u2014": "-", "\u2015": "-", "\u2212": "-"})

def _preprocess_quote(quote: str) -> str: ...
def _normalize_with_map(text: str) -> tuple[str, list[int]]: ...
def _punct_strip_with_map(text: str) -> tuple[str, list[int]]: ...
def _strip_markdown_with_map(text: str) -> tuple[str, list[int]]: ...
def _compose_maps(map_a: list[int], map_b: list[int]) -> list[int]: ...
```

**Tests (unit, per-function):**
- `_normalize_with_map`: preserves positions through case/whitespace collapse
- `_punct_strip_with_map`: strips `[^\w\s]`, offset map valid
- `_strip_markdown_with_map`: `[text](url)` → `text`, `**bold**` → `bold`, table pipes → space
- `_compose_maps`: simple 2-map and 3-map composition cases
- `_preprocess_quote`: trailing ellipsis, unicode dashes

#### A2. 4-tier `ground_and_locate()` in `grounding.py` (LOW risk)

Port from `scripts/trial_ground_and_locate.py` lines 221-416. Single entry point.

```python
def ground_and_locate(quote: str, content: str) -> GroundingResult: ...
```

Also provide a pre-computed variant to avoid redundant normalization:

```python
def ground_and_locate_precomputed(
    quote: str,
    content: str,
    norm_content: str,
    norm_map: list[int],
) -> GroundingResult:
    """Same as ground_and_locate but accepts pre-computed normalized content.

    Use when calling for multiple quotes against the same source content
    (e.g., 30+ field items per source). Avoids O(n) re-normalization per call.
    """
```

Internal tier functions (private):
```python
def _tier1_locate(norm_quote, norm_content, norm_map) -> GroundingResult | None: ...
def _tier2_locate(norm_quote_stripped, norm_content, norm_map) -> GroundingResult | None: ...
def _tier3_locate(norm_quote_stripped, content) -> GroundingResult | None: ...
def _tier4_locate(norm_quote, content, threshold=0.6) -> GroundingResult | None: ...
```

**Tier 3 span boundary fix** (from trial findings): Clamp `source_end` to next `\n` boundary when span contains unstripped markdown, preventing URL bleed into matched spans.

**Keep `verify_quote_in_source()` unchanged** — it's used by `llm_grounding.py:rescue_quote()` for re-verification and by the grounding gate logic. `ground_and_locate()` is additive — it serves position tracking only, not grounding scoring.

**Tests (per-tier with production-derived examples):**
- Tier 1: exact quotes, case/whitespace variations
- Tier 2: em-dash ("Sizes 25 – 100"), colon ("Provider: Gleason"), commas ("Ludington, Michigan")
- Tier 3: markdown links ("[FLENDER](url) gearbox"), bold ("**Repair & Rebuild**"), table pipes
- Tier 4: reworded ("cleaing" vs "cleaning"), word reordering, tense changes
- Tier 3 boundary fix: span doesn't bleed into `](url)` syntax
- Empty/null inputs: returns `GroundingResult(0.0, None, None, None, 0)`
- Pre-processing: trailing ellipsis ("products..."), unicode dashes
- **Position correctness**: for each tier, verify `content[result.source_offset:result.source_end]` contains >50% of quote words

#### A3. Update `SourceLocation` in `extraction_items.py` (LOW risk)

Add two optional fields with backward-compatible defaults:

```python
@dataclass(frozen=True)
class SourceLocation:
    heading_path: list[str]
    char_offset: int | None
    char_end: int | None
    chunk_index: int
    match_tier: int = 0          # NEW
    match_quality: float = 1.0   # NEW
```

Update `_location_to_dict()`:
```python
def _location_to_dict(loc: SourceLocation) -> dict:
    d = {
        "heading_path": loc.heading_path,
        "char_offset": loc.char_offset,
        "char_end": loc.char_end,
        "chunk_index": loc.chunk_index,
    }
    if loc.match_tier > 0:
        d["match_tier"] = loc.match_tier
        d["match_quality"] = loc.match_quality
    return d
```

**Deprecate `locate_in_source()`** — keep for backward compat but callers migrate to `ground_and_locate()`.

#### A4. Wire into v2 extraction in `schema_orchestrator.py` (MEDIUM risk)

Replace the separate `locate_in_source()` calls in `_parse_chunk_to_v2()` (lines 1149-1208) and `_extract_entity_chunk_v2()` (lines 1104-1147).

**Current pattern:**
```python
grounding = ground_field_item(name, value, quote, chunk.content, field_type)
location = locate_in_source(quote, full_content, chunk)
```

**New pattern — pre-compute once per chunk, reuse for all fields:**
```python
# Pre-compute normalized full_content once per extraction call (NOT per field)
norm_full, norm_full_map = _normalize_with_map(full_content)

# Per field:
grounding = ground_field_item(name, value, quote, chunk.content, field_type)  # UNCHANGED
location = _build_location(quote, full_content, norm_full, norm_full_map, chunk)
```

**Why keep `ground_field_item()` separate from `ground_and_locate()`:**
- `ground_field_item()` computes the *grounding score* — Layer A (quote-in-source on CHUNK) + Layer B (value-in-quote). Answers "is this value trustworthy?"
- `ground_and_locate()` computes the *position* in FULL content. Answers "where did this come from?"
- They operate on **different content** (chunk vs full) for good reasons: grounding on the chunk is more precise (fewer false matches in 5K tokens vs 20K chars), position needs the full source for absolute offsets.
- Merging them would force grounding to run on full content, reducing precision.

**Helper function:**
```python
def _build_location(
    quote: str | None,
    full_content: str,
    norm_content: str,
    norm_map: list[int],
    chunk: Any,
) -> SourceLocation | None:
    """Build SourceLocation using ground_and_locate for correct positions."""
    if not quote:
        return None
    gl = ground_and_locate_precomputed(quote, full_content, norm_content, norm_map)
    return SourceLocation(
        heading_path=list(getattr(chunk, "header_path", None) or []),
        char_offset=gl.source_offset,
        char_end=gl.source_end,
        chunk_index=getattr(chunk, "chunk_index", 0),
        match_tier=gl.match_tier,
        match_quality=gl.score,
    )
```

**Performance note:** For a source with 20K chars and 30+ field items, the pre-computed variant avoids 30+ redundant `_normalize_with_map(full_content)` calls. The normalization is O(n) on content length, so pre-computing once is important.

**Tests:**
- Integration: mock chunk + full_content, verify FieldItem.location has correct offsets
- Position correctness: `full_content[loc.char_offset:loc.char_end]` matches expected span
- Existing grounding gate tests still pass (grounding scores unchanged)
- Existing `_parse_chunk_to_v2` tests still pass

#### A5. Validation

1. **Unit tests** — per function and per tier (see above)
2. **Regression** — all existing grounding tests pass, grounding scores unchanged
3. **Trial script** — `scripts/trial_ground_and_locate.py` confirms 87%+ match rate
4. **Production trial** — after deployment, re-extract Wikipedia project (20 sources), compare null offset count before/after
5. `pytest -q` — all ~2055 tests pass

### Recall Safety Analysis

**This feature cannot reduce recall.** It only changes position tracking, not grounding or filtering:

| What changes | What stays the same |
|---|---|
| `SourceLocation.char_offset` / `char_end` values (now correct) | `ground_field_item()` return values |
| `SourceLocation` gains `match_tier` / `match_quality` fields | `apply_grounding_gate()` thresholds and logic |
| `locate_in_source()` replaced by `_build_location()` | `verify_quote_in_source()` (used by rescue) |
| Fewer null offsets (87.3% vs 79.6% match rate) | No field is dropped or modified based on position |

### Files Modified

| File | Changes | Step |
|------|---------|------|
| `src/services/extraction/grounding.py` | +`GroundingResult`, +offset-mapped functions, +`ground_and_locate()`, +`ground_and_locate_precomputed()`, +4 tier functions | A1, A2 |
| `src/services/extraction/extraction_items.py` | +`match_tier`, +`match_quality` on `SourceLocation`, update `_location_to_dict()` | A3 |
| `src/services/extraction/schema_orchestrator.py` | Pre-compute norm once per source, replace `locate_in_source()` calls with `_build_location()` | A4 |
| `tests/test_ground_and_locate.py` | NEW — per-tier unit tests, offset validation, edge cases | A1-A4 |

### Constraints

- No changes to v1 extraction path
- No DB migration (SourceLocation is JSON inside `data`)
- No changes to the grounding gate logic (`apply_grounding_gate` unchanged)
- No changes to grounding scores (only position tracking changes)
- Keep `verify_quote_in_source()` as-is (used by LLM rescue re-verification)
- Keep `locate_in_source()` as-is (deprecated but not deleted, for backward compat)
- No embedding-based matching (Tier 4 fuzzy handles paraphrasing)
- No sentence splitting (`re.split(r'[.!?\n]')` — content is markdown, not sentences)

---

## Feature B: LLM Skip-Gate

### Problem

**57.7% of extraction LLM calls produce zero-confidence results — pure waste.**

The embedding-based SmartClassifier fails for 42% of pages (semantic gap between schema metadata and website copy). The binary "extract or skip?" approach can catch the obviously irrelevant 15-25% of pages cheaply.

### Skip-Gate Model: Trial Evidence (confirmed 2026-03-09)

**Three trials now validate the same conclusion: gemma3-4B is the correct skip-gate model.**

#### Trial 2026-03-09 — 120 random production pages, stratified (100 extract / 20 skip GT)

Script: `scripts/trial_skip_gate_model_comparison.py`

| Metric | gemma3-4B | Qwen3-30B |
|--------|-----------|-----------|
| **Recall** | **94.0%** (6 FN) | 82.0% (18 FN) |
| Precision | 85.5% | 91.1% |
| F1 | 89.5% | 86.3% |
| Accuracy | 81.7% | 78.3% |
| Skip precision | 40.0% | 40.0% |
| Latency | 0.20s/page | 0.14s/page |
| Tokens | 908/page | 839/page |
| **Disagreements** | | **26/120 pages** |

**Qwen3-30B loses 3x more pages** (18 FN vs 6 FN). Its false negatives include high-confidence pages:
- Water pump repair service (conf=0.95) — clearly relevant
- Zero-Max product catalog (conf=0.80) — product data on page
- Delga lighting product (conf=0.90) — extractable product page
- Non-English product/service pages (sew-eurodrive.pe, lentax, croftsgears)

**gemma3-4B's 6 FN** are borderline: a blog post, non-English product pages (content truncated), a circular economy article, and low-confidence extractions (0.32-0.50).

**Both have identical skip precision (40%)** — when either says "skip", only 40% are true negatives. The other 60% are likely GT noise (extractions exist but with very low confidence).

#### Earlier trials (for reference)

| Trial | Pages | gemma3-4B Recall | Qwen3-30B Recall |
|-------|-------|------------------|------------------|
| v2 (31 hand-curated) | 31 | 100% (0 FN) | 91.3% (2 FN) |
| v4 (80 random, real DB) | 80 | 92.6% (4 FN) | 51.9% (26 FN) |
| **2026-03-09 (120 stratified)** | **120** | **94.0% (6 FN)** | **82.0% (18 FN)** |

Across all three trials, gemma3-4B consistently achieves ≥92% recall. Qwen3-30B ranges 52-91% — too variable and too strict for a gatekeeper.

#### Different tasks, different models

| Task | Best model | Why |
|------|-----------|-----|
| **Skip-gate classification** | **gemma3-4B** | Permissive, high recall (94%), avoids data loss |
| **Quote verification** (grounding) | **Qwen3-30B** | Better reasoning (80% detection, 100% recall) |
| **Quote rescue** (LLM grounding) | **Qwen3-30B** | Already deployed in `rescue_quote()` |

The skip-gate is a *gatekeeper* — it must err on the side of "extract". Qwen3-30B is a better *reasoner* but too strict as a gatekeeper.

**Decision: gemma3-4B for skip-gate, Qwen3-30B for verification.** The `skip_gate_model` config field allows overriding without code changes. Default: gemma3-4B.

### Architecture

```
[Source: URL + Content]
    │
    ▼
[Level 0: Rule-Based Skip] ← FREE, instant (existing PageClassifier)
    │ /careers, /privacy, /login, /tag/ etc.
    │ skip → DONE
    │
    ▼
[Level 1: LLM Skip-Gate] ← CHEAP, ~1100 tokens, ~0.15-0.2s
    │ Binary: "Does this page match the extraction schema?"
    │ Schema-agnostic: field_group descriptions as context
    │ Intentionally permissive: "when uncertain, extract"
    │ skip → DONE | extract → proceed
    │
    ▼
[Level 2: Full Extraction] ← EXPENSIVE, N calls × ~5500 tokens
    │ ALL field groups (no group selection — unreliable at 34-53% recall)
    │ Confidence + grounding scoring filters quality
```

### Key Design Decisions

1. **Binary, not group-selection.** LLM group-selection achieves only 34-53% recall. Binary "extract or skip?" achieves 92-100% recall with the right model. The extraction pipeline's confidence scoring already handles group-level filtering.

2. **Intentionally permissive.** False negative (skip useful page) = permanent data loss. False positive (extract useless page) = ~38K wasted tokens, caught by confidence gating. Default to "extract" on any uncertainty, parse failure, or LLM error.

3. **Schema-agnostic.** System prompt has zero domain knowledge. Schema context is auto-generated from `extraction_schema.field_groups` descriptions. Works for any template.

4. **Configurable model.** `skip_gate_model` setting allows using any available model. The task requires high recall (permissive), not high precision. Different from grounding verification which requires high precision.

5. **Off by default.** `skip_gate_enabled=False` until validated in production on a representative sample.

### Recall Safety Analysis

The skip-gate has multiple safety layers to prevent data loss:

| Safety Layer | How it works |
|---|---|
| **Off by default** | `skip_gate_enabled=False` — must be explicitly enabled |
| **Default to extract** | Any LLM error, timeout, or parse failure → "extract" (never lose data) |
| **Permissive prompt** | "When genuinely uncertain, prefer extract" |
| **No schema = extract** | Missing `extraction_context` or `field_groups` → extract everything |
| **Short content = extract** | Content < 100 chars → skip the gate, extract |
| **Rule-based skip preserved** | Level 0 patterns (/careers, /privacy) still work independently |
| **Classification stored** | `method="llm_skip_gate"` stored on Source — auditable |

**What the skip-gate does NOT do:**
- Does not select which field groups to extract (all or nothing)
- Does not modify extraction results or grounding scores
- Does not affect the grounding gate thresholds
- Does not replace confidence-based quality filtering

### Implementation Steps

#### B1. New file: `src/services/extraction/llm_skip_gate.py` (LOW risk)

Additive — new module, no existing code changes.

```python
"""LLM-based binary page classifier: extract or skip.

Schema-agnostic: receives extraction schema as context.
Intentionally permissive: defaults to "extract" on uncertainty or failure.
Model is configurable — must achieve ≥90% recall to be safe as a gatekeeper.
"""

SYSTEM_PROMPT = """You classify web pages for a structured data extraction pipeline.

You receive an extraction schema describing target data types, plus a web page.

Decision rules:
- "extract" = page contains data matching ANY field group in the schema
- "skip" = page has NO matching data (wrong industry, empty, navigation-only,
  login, job listings, holiday notices, legal/privacy, forum index)

When genuinely uncertain, prefer "extract" — missing data costs more than
a wasted extraction call.

Output JSON only: {"decision": "extract" or "skip"}"""

USER_TEMPLATE = """EXTRACTION SCHEMA:
{schema_summary}

PAGE:
URL: {url}
Title: {title}

Content:
{content}

Should this page be extracted or skipped? JSON only:"""


@dataclass(frozen=True)
class SkipGateResult:
    decision: str          # "extract" or "skip"
    confidence: float      # 1.0 for clear skip, 0.8 for extract
    method: str            # "llm_skip_gate"


class LLMSkipGate:
    def __init__(self, llm_client, content_limit: int = 2000, model: str | None = None): ...

    async def should_extract(self, url, title, content, schema) -> SkipGateResult:
        """Decide if a page should be extracted.

        Safety defaults:
        - No schema or no field_groups → extract (conservative)
        - Content too short (<100 chars) → extract (can't classify reliably)
        - LLM error/timeout → extract (never lose data)
        - Parse failure → extract (never lose data)
        """

    def _parse_decision(self, text: str) -> str:
        """Parse LLM response. Defaults to 'extract' on any ambiguity.

        Handles: thinking tags (Qwen3), markdown fences, JSON parse, keyword fallback.
        """


def build_schema_summary(schema: dict) -> str:
    """Auto-generate schema context from extraction_schema.

    Extracts: source_type, source_label, field group names/descriptions/hints/key fields.
    Works for any template — drivetrain, recipes, jobs, etc.
    """
```

**Tests (`tests/test_llm_skip_gate.py`):**
- `test_product_page_returns_extract` — mock LLM returns `{"decision": "extract"}`
- `test_holiday_page_returns_skip` — mock LLM returns `{"decision": "skip"}`
- `test_no_schema_returns_extract` — conservative fallback
- `test_empty_content_returns_extract` — conservative fallback
- `test_short_content_returns_extract` — content < 100 chars
- `test_llm_failure_returns_extract` — exception → never lose data
- `test_llm_timeout_returns_extract` — timeout → never lose data
- `test_parse_json_with_thinking_tags` — Qwen3 `<think>...</think>` prefix
- `test_parse_json_with_markdown_fences` — ` ```json...``` `
- `test_parse_ambiguous_returns_extract` — garbled output → default extract
- `test_schema_summary_generation` — verify field groups are formatted
- `test_different_templates_different_summaries` — recipe vs drivetrain schema
- `test_content_truncated_to_limit` — content > limit is cut

#### B2. Config additions in `src/config.py` (LOW risk)

Add to Settings class:
```python
# Skip-gate classification
classification_skip_gate_enabled: bool = Field(default=False, ...)  # Off by default
classification_skip_gate_model: str = Field(default="", ...)        # Empty = use default LLM_MODEL
classification_skip_gate_content_limit: int = Field(default=2000, ge=500, le=5000, ...)
```

Add to `ClassificationConfig` facade:
```python
@dataclass(frozen=True, slots=True)
class ClassificationConfig:
    enabled: bool
    skip_enabled: bool
    smart_enabled: bool
    skip_gate_enabled: bool          # NEW
    skip_gate_model: str             # NEW
    skip_gate_content_limit: int     # NEW
    reranker_model: str
    embedding_high_threshold: float
    embedding_low_threshold: float
    reranker_threshold: float
    cache_ttl: int
    use_default_skip_patterns: bool
    classifier_content_limit: int
```

Update the `classification` property lambda in `Settings._get_facade()` to include the 3 new fields.

**Tests:**
- Verify new config fields have correct defaults
- Verify facade includes new fields

#### B3. Wire into `schema_orchestrator.py` (MEDIUM risk)

**Constructor change:**
```python
class SchemaExtractionOrchestrator:
    def __init__(
        self,
        schema_extractor,
        *,
        extraction_config=None,
        classification_config=None,
        context=None,
        smart_classifier=None,
        grounding_verifier=None,
        skip_gate=None,              # NEW: LLMSkipGate | None
        extraction_schema=None,      # NEW: dict | None (for skip-gate context)
    ):
        ...
        self._skip_gate = skip_gate
        self._extraction_schema = extraction_schema
```

**Classification flow change in `extract_all_groups()` (lines 365-399):**

```python
if source_url and self._classification.enabled:
    # Level 0: Rule-based skip (always runs first — free, instant)
    available_group_names = [g.name for g in field_groups]
    rule_classifier = PageClassifier(available_groups=available_group_names)
    rule_result = rule_classifier.classify(url=source_url, title=source_title)

    if rule_result.skip_extraction and self._classification.skip_enabled:
        # Rule-based skip (/careers, /privacy, etc.)
        classification = rule_result

    elif self._skip_gate and self._classification.skip_gate_enabled:
        # Level 1: LLM skip-gate
        gate_result = await self._skip_gate.should_extract(
            url=source_url,
            title=source_title,
            content=markdown,
            schema=self._extraction_schema or {},
        )
        if gate_result.decision == "skip":
            classification = ClassificationResult(
                page_type="skip",
                relevant_groups=[],
                skip_extraction=True,
                confidence=gate_result.confidence,
                method=ClassificationMethod.LLM,
                reasoning="LLM skip-gate: page does not match extraction schema",
            )
        else:
            # Extract ALL groups — confidence gating handles quality
            classification = ClassificationResult(
                page_type=rule_result.page_type,
                relevant_groups=available_group_names,
                skip_extraction=False,
                confidence=gate_result.confidence,
                method=ClassificationMethod.LLM,
            )

    elif self._smart_classifier and self._classification.smart_enabled:
        # Fallback: SmartClassifier (if skip-gate disabled but smart enabled)
        classification = await self._smart_classifier.classify(
            url=source_url, title=source_title,
            content=markdown, field_groups=field_groups,
        )

    else:
        # No classifier: extract everything
        classification = ClassificationResult(
            page_type=rule_result.page_type,
            relevant_groups=available_group_names,
            skip_extraction=False,
            confidence=0.5,
            method=ClassificationMethod.RULE_BASED,
        )
```

**Note on SmartClassifier:** Kept as fallback for backward compat during rollout. SmartClassifier removal is a separate cleanup task (B6) to be done after skip-gate is validated in production. This avoids coupling two changes.

#### B4. Update `ClassificationMethod` enum

Add `LLM = "llm"` to the enum (if not already present). Keep `HYBRID` for SmartClassifier compat until B6.

#### B5. Wire into worker → pipeline → orchestrator

**`worker.py` `_create_schema_pipeline()` (lines 149-244):**

```python
# After creating grounding_verifier, before creating orchestrator:
skip_gate = None
if self._classification and self._classification.skip_gate_enabled and self._llm:
    from services.extraction.llm_skip_gate import LLMSkipGate
    from services.llm.client import LLMClient

    gate_client = LLMClient(
        self._llm,
        llm_queue=self.llm_queue,
        request_timeout=self._request_timeout,
    )
    skip_gate = LLMSkipGate(
        llm_client=gate_client,
        content_limit=self._classification.skip_gate_content_limit,
        model=self._classification.skip_gate_model or None,
    )

# Pass to orchestrator:
orchestrator = SchemaExtractionOrchestrator(
    extractor,
    extraction_config=self._extraction,
    classification_config=self._classification,
    smart_classifier=smart_classifier,
    grounding_verifier=grounding_verifier,
    skip_gate=skip_gate,                          # NEW
    extraction_schema=project.extraction_schema if project else None,  # NEW
)
```

The `extraction_schema` flows through `worker._create_schema_pipeline(project)` which already receives the project. No pipeline.py changes needed.

**Tests (`tests/test_skip_gate_integration.py`):**
- `test_rule_skip_still_works` — /careers URL still skipped (Level 0 unchanged)
- `test_skip_gate_skips_irrelevant_page` — mock gate returns "skip", extraction not called
- `test_skip_gate_extract_runs_all_groups` — gate returns "extract", ALL groups processed (not a subset)
- `test_skip_gate_disabled_extracts_everything` — `skip_gate_enabled=False`, normal flow
- `test_skip_gate_failure_extracts_everything` — gate raises exception, extraction proceeds
- `test_smart_classifier_used_when_skip_gate_disabled` — backward compat
- `test_schema_passed_to_skip_gate` — verify `extraction_schema` reaches gate

#### B6. Later cleanup: Remove SmartClassifier

**NOT part of this implementation.** Only after skip-gate is validated in production.

When ready:
- Delete `src/services/extraction/smart_classifier.py`
- Remove `smart_classifier` param from orchestrator
- Remove embedding classification config fields
- Remove `HYBRID` from ClassificationMethod
- Net: ~-500 lines

---

## Implementation Order

```
Phase C-1: Position Tracing (Feature A)
├── A1. Core offset-mapped functions in grounding.py        ~150 lines
├── A2. ground_and_locate() with 4 tiers + precomputed      ~220 lines
├── A3. Update SourceLocation + serialization               ~15 lines changed
├── A4. Wire into _parse_chunk_to_v2 / _extract_entity_chunk_v2
│       (pre-compute norm once per source, not per field)    ~50 lines changed
└── A5. Tests + validation                                  ~300 lines

Phase C-2: LLM Skip-Gate (Feature B)
├── B1. New llm_skip_gate.py module                         ~180 lines
├── B2. Config additions                                    ~15 lines
├── B3. Wire into schema_orchestrator.py classification flow ~60 lines changed
├── B4. Update ClassificationMethod enum                    ~2 lines
├── B5. Wire into worker.py                                 ~25 lines changed
└── B6. Tests                                               ~250 lines

Total: ~550 new lines, ~165 lines changed
```

### Why this order

1. **Position tracing first** — pure functions, zero config, zero risk to existing behavior. Fixes a real bug (broken offsets). Can deploy and validate independently.
2. **Skip-gate second** — config-gated (`skip_gate_enabled=False` by default), so even after merge it's opt-in. Requires production validation trial before enabling.
3. **SmartClassifier removal** — separate PR after skip-gate is production-validated.

---

## Risk Analysis

| Risk | Mitigation |
|------|------------|
| Position tracing offset map bugs | Per-tier unit tests with production-derived examples. Trial script validates 87%+ match rate. Position correctness verified: `content[offset:end]` checked per tier. |
| Tier 3 span boundary bleed | Clamp `source_end` to next `\n` boundary. Tested explicitly. |
| `ground_and_locate()` performance | All tiers are microsecond-level string ops. Pre-computed variant avoids redundant normalization. |
| Position tracing changes grounding scores | **It doesn't.** Grounding scores come from `ground_field_item()` which is unchanged. Position tracking is metadata only. |
| Skip-gate false negatives (loses data) | Off by default. Default to "extract" on any error/ambiguity. Validation trial required before enabling. Model must achieve ≥90% recall. |
| Skip-gate model too strict | Configurable model. Trial data shows model choice is critical: gemma3-4B (92.6% recall) vs Qwen3-30B (51.9% recall) for this task. |
| Skip-gate adds latency | ~0.15-0.2s/page is negligible vs 2-5s extraction. Sequential, not parallel (avoids wasted work). |
| SmartClassifier regression | Preserved as fallback. Skip-gate takes priority only when enabled. |
| Config facade breaking change | New fields have defaults. Existing configs work without changes. |

---

## Verification Checklist

### Position Tracing
- [ ] Unit tests per offset-mapped function (position correctness)
- [ ] Unit tests per tier with production-derived examples
- [ ] Tier 3 boundary fix tested (no URL bleed)
- [ ] Empty/null input edge cases
- [ ] `SourceLocation` serialization includes new fields
- [ ] Pre-computed variant produces same results as non-pre-computed
- [ ] **Grounding scores unchanged** — same scores before and after
- [ ] Existing grounding tests pass unchanged
- [ ] Existing grounding gate tests pass unchanged
- [ ] `pytest -q` — all tests pass

### Skip-Gate
- [ ] `LLMSkipGate` unit tests (all decision paths)
- [ ] Parse robustness: thinking tags, markdown fences, garbled output
- [ ] All error/edge cases default to "extract" (never lose data)
- [ ] `build_schema_summary` produces correct output for different templates
- [ ] Config facade includes new fields with correct defaults
- [ ] Integration: skip-gate disabled → normal flow (no regression)
- [ ] Integration: skip-gate enabled + "skip" → extraction skipped
- [ ] Integration: skip-gate enabled + "extract" → all groups extracted
- [ ] Integration: skip-gate error → extraction proceeds
- [ ] Rule-based skip still works (Level 0 unchanged)
- [ ] SmartClassifier still works when skip-gate disabled
- [ ] **Pre-production validation trial**: 100+ random pages, confirm recall ≥ 90%
- [ ] `pytest -q` — all tests pass

---

## Files Summary

### Position Tracing (Feature A)

| File | Action | Changes |
|------|--------|---------|
| `src/services/extraction/grounding.py` | MODIFY | +`GroundingResult`, +offset-mapped functions, +`ground_and_locate()`, +`ground_and_locate_precomputed()`, +4 tier functions |
| `src/services/extraction/extraction_items.py` | MODIFY | +`match_tier`, +`match_quality` on `SourceLocation`, update `_location_to_dict()` |
| `src/services/extraction/schema_orchestrator.py` | MODIFY | Pre-compute norm once per source, replace `locate_in_source()` calls with `_build_location()` |
| `tests/test_ground_and_locate.py` | NEW | Per-tier unit tests, offset validation, position correctness, edge cases |

### Skip-Gate (Feature B)

| File | Action | Changes |
|------|--------|---------|
| `src/services/extraction/llm_skip_gate.py` | NEW | `LLMSkipGate`, `SkipGateResult`, `build_schema_summary`, prompts |
| `src/config.py` | MODIFY | +3 settings fields, +3 facade fields, update facade builder |
| `src/services/extraction/schema_orchestrator.py` | MODIFY | +`skip_gate`/`extraction_schema` params, new classification flow |
| `src/services/extraction/worker.py` | MODIFY | Create `LLMSkipGate`, pass to orchestrator with extraction_schema |
| `src/constants.py` | MODIFY | +`LLM` to `ClassificationMethod` enum (if missing) |
| `tests/test_llm_skip_gate.py` | NEW | Skip-gate unit tests |
| `tests/test_skip_gate_integration.py` | NEW | Pipeline integration tests |

---

## Reference Materials

| Resource | Purpose |
|----------|---------|
| `scripts/trial_ground_and_locate.py` | Reference implementation for position tracing (validated 87.3%) |
| `scripts/trial_quote_tracing.py` | Initial coverage analysis (established 79.6% baseline) |
| `scripts/trial_quote_tracing_deep.py` | Failure categorization of unmatched quotes |
| `docs/TODO_quote_source_tracing.md` | Original spec with trial data and failure analysis |
| `docs/TODO_classification_robustness.md` | Original spec with trial data, model comparison, skip-gate design |
| `docs/TODO_grounded_extraction.md` | Master architecture doc (layers, phases, trial results) |
