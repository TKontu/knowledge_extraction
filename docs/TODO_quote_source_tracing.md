# TODO: Reliable Quote-to-Source Position Tracing

**Status**: Planned (revised after production trial)
**Created**: 2026-03-08
**Revised**: 2026-03-08 — coverage estimates corrected via trial on 1218 production quotes
**Priority**: Medium — incremental improvement, not the crisis originally assumed

## Problem

v2 extraction stores per-field `{value, confidence, quote, grounding, location}`. The grounding score works — `verify_quote_in_source()` has 3-tier matching (exact → punct-stripped → word-window). But `locate_in_source()` only has Tier 1 (exact normalized substring), so it misses positions that the grounding verifier can confirm.

**Root cause**: Two functions use different matching logic. `verify_quote_in_source()` (grounding.py) strips punctuation and does word-window sliding. `locate_in_source()` (extraction_items.py) only does basic normalized substring. They disagree on "found."

**Actual impact** (measured, not estimated):
- v2 extractions: **16.2% null char_offset** (6/37 quotes), not ~60% as initially assumed
- v1 drivetrain batch (n=1218 quotes): **79.6% already match** with Tier 1 alone
- The original "~60%" figure was from a single Wikipedia example, not a systematic measurement

**Real-world example**: LLM quotes `"Preceded by Anna Mkapa"` but source contains:
```
| Preceded by | [Anna Mkapa](https://en.wikipedia.org/wiki/Anna_Mkapa "Anna Mkapa") |
```
Markdown link syntax + table formatting makes the clean substring unfindable by `locate_in_source()`.

## Production Trial Results

### Trial 1: Coverage analysis (before prototype)

Script: `scripts/trial_quote_tracing.py` + `scripts/trial_quote_tracing_deep.py`
Dataset: 2000 v1 extractions from drivetrain batch (project `99a19141`), 1218 quotes.

| Tier | Match Type | Originally Predicted | Actual | Delta |
|------|-----------|---------------------|--------|-------|
| 1 | Normalized substring | ~40% | **79.6%** | +39.6% |
| 2 | Markdown-stripped | ~40% | **1.3%** | -38.7% |
| 3 | Block-level fuzzy | ~15% | **5.3%** | -9.7% |
| — | Unmatched | ~5% | **10.3%** | +5.3% |

Why quotes fail (breakdown of 249 Tier-1 misses):

| Category | Count | % of misses | Fixable? |
|----------|-------|-------------|----------|
| Fabricated values ("501-1000", "Pune, India") | 63 | 25.3% | No — LLM invented it |
| Reworded (all words present, different order) | 55 | 22.1% | Yes — fuzzy |
| Punct-strip (em-dash, special chars) | 44 | 17.7% | Yes |
| Hallucinated (quote not in source) | 35 | 14.1% | No — fabrication |
| Partial overlap (paraphrasing) | 30 | 12.0% | Partial |
| MD + punct combined | 13 | 5.2% | Yes |
| MD-strip only | 3 | 1.2% | Yes |

Of the 249 unmatched: 98 had grounding score >= 0.8 (verifier confirms but locator can't find).

### Trial 2: Prototype validation

Script: `scripts/trial_ground_and_locate.py`
Dataset: same 2000 extractions, 1240 quotes (more captured due to ellipsis preprocessing).

| Tier | Match Type | Coverage | Quotes |
|------|-----------|----------|--------|
| 1 | Normalized substring | **72.5%** | 899 |
| 2 | Punct-stripped with offset map | **3.9%** | 48 |
| 3 | MD+punct stripped with offset map | **1.9%** | 24 |
| 4 | Block-level fuzzy | **9.0%** | 111 |
| — | Unmatched | **12.7%** | 158 |
| | **TOTAL MATCHED** | **87.3%** | **1082** |

**Key results:**
- **+183 quotes recovered** over baseline (Tier 1 only)
- **Only 2 of 158 unmatched have grounding >= 0.8** — algorithm now finds virtually everything findable
- 156 remaining unmatched are truly fabricated/hallucinated (grounding < 0.8)
- 6 position mapping errors (offset map composition bugs in Tier 3 spans) — fixable
- Tier 1 drop (79.6% → 72.5%) is from ellipsis preprocessing — quotes like "products..." now become "products" which may not substring-match but get caught by later tiers

**Pre-processing wins:**
- Trailing ellipsis strip ("products..." → "products") enables fuzzy/punct tiers to catch more
- Unicode dash normalization (– → -) fixes em-dash mismatches

**Position error patterns (6 cases):**
- Tier 3 offset map composition yields spans that bleed into markdown URLs
- Fix: clamp `source_end` to next `\n` boundary when span contains unstripped markdown

## Content Structure Reality

Firecrawl converts HTML to markdown. Content is **NOT clean sentences** — no `re.split(r'[.!?\n]')`:

- **Markdown tables**: `| Key | [Value](url "title") |` — URLs embedded inline
- **Bullet lists**: `* Item text` or `• Item text`
- **Numbered feature blocks**: `01\n--\n\nDescription text.`
- **Headers**: `## Section` (or `===` / `---` underline style)
- **Prose paragraphs**: Standard text separated by `\n\n`
- **Inline markdown**: `**bold**`, `[text](url)`, `![alt](url)`

**Key insight**: `\n\n` (double newline) is the universal block boundary — maps to HTML div/section/block elements. Same boundary domain dedup uses for block hashing. Within blocks, `\n` separates table rows, list items, etc.

## Solution: Unified `ground_and_locate()` (validated by prototype)

Replace separate `verify_quote_in_source()` + `locate_in_source()` with one function that returns BOTH grounding score AND position. Prototype validated in `scripts/trial_ground_and_locate.py`.

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
├── Tier 1: norm_quote in norm_content → str.find() with norm_map
├── Tier 2: _punct_strip_with_map(norm_content) → find + compose_maps
├── Tier 3: _strip_markdown_with_map(content) → normalize → punct_strip → find + compose_maps
└── Tier 4: split on \n\n → per-block word overlap → best block/line position
```

### Key design: Offset map composition

Each transformation produces `(transformed_text, offset_map)` where `offset_map[i]` = position in input for output char `i`. Maps compose: if `map_a` tracks A→B and `map_b` tracks B→C, then `compose(map_a, map_b)` gives A→C.

```python
def _compose_maps(map_a: list[int], map_b: list[int]) -> list[int]:
    """result[i] = map_a[map_b[i]] — maps positions in C back to A."""
    return [map_a[j] for j in map_b]
```

This means Tier 3 can chain: `original ← md_strip ← normalize ← punct_strip` — each step has its own simple offset map, composed to get original positions.

### Pre-processing (applied to quote before matching)

```python
def _preprocess_quote(quote: str) -> str:
    """Clean LLM quoting artifacts."""
    q = quote.strip()
    q = re.sub(r"\.{2,}$|…$", "", q).strip()  # trailing ellipsis
    q = q.translate(UNICODE_DASHES)              # – → -
    return q
```

Trial showed this converts 22 previously-unfindable quotes into Tier 2/4 matches.

### Offset-mapped transformations

```python
def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Lowercase + collapse whitespace. offset_map[i] = original position."""

def _punct_strip_with_map(text: str) -> tuple[str, list[int]]:
    """Strip [^\w\s]. Input should be normalized. offset_map[i] = input position."""

def _strip_markdown_with_map(text: str) -> tuple[str, list[int]]:
    """Strip markdown syntax via keep-mask. offset_map[i] = original position.
    Strips: [text](url)→text, ![alt](url)→alt, **bold**→bold,
    table separators, |→space, `code`→code."""
```

### Return Type

```python
@dataclass
class GroundingResult:
    score: float              # 0.0-1.0 grounding score
    source_offset: int | None # char position in source content
    source_end: int | None    # end char position
    matched_span: str | None  # actual source text at [offset:end]
    match_tier: int           # 1-4, or 0 for unmatched
```

### 4-Tier Matching (all validated with production data)

**Tier 1: Normalized substring (score=1.0)** — 72.5% of quotes
- `_normalize_with_map()` on content, `_normalize()` on quote
- `str.find()` for position, `norm_map` to map back to original
- Handles: exact quotes, case differences, whitespace variations

**Tier 2: Punct-stripped with offset map (score=0.95)** — 3.9% of quotes
- `_punct_strip_with_map()` on normalized content
- `_compose_maps(norm_map, punct_map)` for original positions
- Handles: em-dashes, commas in names ("Rochester,"), colons ("Provider:"), smart quotes

**Tier 3: MD+punct stripped with offset map (score=0.9)** — 1.9% of quotes
- `_strip_markdown_with_map(content)` → `_normalize_with_map()` → `_punct_strip_with_map()`
- Triple map composition for original positions
- Handles: `[text](url)` links, `**bold**`, table pipes, combined with punct
- **Span boundary fix needed**: clamp `source_end` to `\n` boundary to avoid bleeding into URLs

**Tier 4: Block-level fuzzy (score=best_overlap)** — 9.0% of quotes
- Split on `\n\n`, per-block word-overlap with `threshold >= 0.6`
- Line-level refinement within winning block
- Handles: reworded quotes, word reordering, partial paraphrasing, tense changes

### Coverage (validated)

| Tier | Match Type | Coverage | Latency |
|------|-----------|----------|---------|
| 1 | Normalized substring | 72.5% | microseconds |
| 2 | Punct-stripped | 3.9% | microseconds |
| 3 | MD+punct stripped | 1.9% | microseconds |
| 4 | Block fuzzy | 9.0% | microseconds |
| — | Unmatched (fabrication) | 12.7% | — |
| | **TOTAL MATCHED** | **87.3%** | |

87% of quotes get a source position with validated offsets. The 12.7% unmatched are LLM fabrications — only 2 have grounding >= 0.8.

## Chunk-to-Source Position Mapping

### Add `source_char_offset` to `DocumentChunk`

```python
@dataclass
class DocumentChunk:
    content: str
    chunk_index: int
    total_chunks: int
    header_path: list[str] | None = None
    start_line: int | None = None
    end_line: int | None = None
    source_char_offset: int = 0  # NEW: where this chunk starts in full source
```

`chunk_document()` already builds chunks sequentially from sections — add an accumulator to track character position.

**Benefit**: Search the chunk (~5K tokens) instead of full source (~20K chars). Smaller search space = fewer false matches, then add `source_char_offset` to get source-level position.

### Update `SourceLocation`

```python
@dataclass(frozen=True)
class SourceLocation:
    heading_path: list[str]
    char_offset: int | None
    char_end: int | None
    chunk_index: int
    match_quality: float = 1.0      # NEW: tier score (1.0, 0.95, or overlap)
    matched_span: str | None = None  # NEW: actual source text that matched
```

## Implementation Steps

### Step 1: Core matching functions in `grounding.py` (Low risk)

Add offset-mapped transformation functions. These are pure functions, no side effects.
Reference implementation: `scripts/trial_ground_and_locate.py` lines 45-150.

- `_preprocess_quote(quote)` — strip trailing ellipsis, normalize unicode dashes
- `_normalize_with_map(text)` — lowercase + collapse whitespace, return offset map
- `_punct_strip_with_map(text)` — strip `[^\w\s]`, return offset map
- `_strip_markdown_with_map(text)` — keep-mask approach, return offset map
- `_compose_maps(map_a, map_b)` — compose two offset maps

### Step 2: `ground_and_locate()` with 4 tiers in `grounding.py` (Medium risk)

Single entry point replacing both `verify_quote_in_source()` + `locate_in_source()`.
Reference implementation: `scripts/trial_ground_and_locate.py` lines 153-270.

- Returns `GroundingResult(score, source_offset, source_end, matched_span, match_tier)`
- Tier 3 span boundary fix: clamp `source_end` to next `\n` to avoid URL bleeding
- Keep existing `verify_quote_in_source()` as-is for backward compat

### Step 3: Update `SourceLocation` in `extraction_items.py` (Low risk)

- Add `match_quality: float = 1.0` and `matched_span: str | None = None`
- Update `_location_to_dict()` serialization
- Remove `locate_in_source()` usage (callers switch to `ground_and_locate()`)

### Step 4: Wire into v2 extraction path in `schema_orchestrator.py` (Medium risk)

- `_parse_chunk_to_v2()`: replace `ground_field_item()` + `locate_in_source()` with `ground_and_locate()`
- `_parse_entity_chunk_v2()`: replace `ground_entity_item()` + `locate_in_source()` with `ground_and_locate()`
- Pass `GroundingResult` fields into `FieldItem`/`EntityItem`/`ListValueItem`

### Step 5: Tests (—)

- Unit tests for each offset-mapped function with representative content
- Unit tests for each tier with production-derived examples
- Integration test: full v2 extraction → verify non-null positions
- Run `scripts/trial_ground_and_locate.py` to confirm numbers match

### Step 6 (optional): Chunk-level positions

- Add `source_char_offset: int = 0` to `DocumentChunk` in `models.py`
- Track accumulator in `chunk_document()` in `chunking.py`
- Benefit: search chunk instead of full source, fewer false matches

## Files to Modify

| File | Changes | Step |
|------|---------|------|
| `src/services/extraction/grounding.py` | All offset-mapped functions + `ground_and_locate()` | 1, 2 |
| `src/services/extraction/extraction_items.py` | `match_quality`, `matched_span` on `SourceLocation`; `_location_to_dict()` | 3 |
| `src/services/extraction/schema_orchestrator.py` | Replace `ground_*()` + `locate_in_source()` with `ground_and_locate()` | 4 |
| `tests/` | Per-tier unit tests, integration test | 5 |
| `src/models.py` | `source_char_offset: int = 0` on `DocumentChunk` (optional) | 6 |
| `src/services/llm/chunking.py` | Char offset accumulator (optional) | 6 |

## Constraints

- No embedding-based matching (Tier 4 block-fuzzy handles paraphrasing)
- No sentence splitting (`re.split(r'[.!?\n]')` — content is not sentences)
- No changes to v1 extraction path
- No prompt changes (don't constrain LLM quoting behavior)
- No DB migration (SourceLocation is stored inside JSON `data`)
- Keep `verify_quote_in_source()` for backward compat — new code uses `ground_and_locate()`

## Separate concern: LLM quote fabrication

~10% of quotes are fabricated by the LLM (grounding score = 0.0). No matching algorithm fixes this. Potential mitigations (separate work):
- Confidence gating: reject fields with grounding < 0.3
- Prompt engineering: stricter quoting instructions (v2 `strict_quoting` already helps)
- Post-extraction filtering: drop fields where grounding confirms fabrication

## Verification

1. **Unit tests** per tier with production-derived examples:
   - Tier 1: exact quotes, case/whitespace variations
   - Tier 2: em-dash quotes ("Sizes 25 – 100"), colon-prefixed ("Provider: Gleason"), commas in names ("Ludington, Michigan")
   - Tier 3: markdown links ("[FLENDER](url) gearbox"), bold ("**Repair & Rebuild**"), table pipes
   - Tier 4: reworded ("cleaing" vs "cleaning"), tense change ("relocates" vs "relocated"), word reordering
   - Pre-processing: trailing ellipsis ("products..."), unicode dashes
2. **Position validation**: each tier's offset map produces spans that contain >50% of quote words
3. **Re-run trial**: `scripts/trial_ground_and_locate.py` — confirm 87%+ match rate, 0 position errors
4. **Regression**: existing grounding tests pass, scores same or improved
5. **Production trial**: re-extract Wikipedia project, compare null offset count before/after
6. `pytest -q` — all tests pass

## Trial Scripts

| Script | Purpose | Status |
|--------|---------|--------|
| `scripts/trial_quote_tracing.py` | 3-tier coverage analysis (initial assessment) | Done — established baseline |
| `scripts/trial_quote_tracing_deep.py` | Failure categorization of unmatched quotes | Done — identified root causes |
| `scripts/trial_quote_tracing_v2_check.py` | Check actual null offset rate in stored v2 data | Done — debunked 60% claim |
| `scripts/trial_quote_tracing_examples.py` | Detailed examples per failure category | Done — informed tier design |
| `scripts/trial_ground_and_locate.py` | **Prototype validation** — full algorithm with position tracking | Done — 87.3% matched, 6 minor position errors |
