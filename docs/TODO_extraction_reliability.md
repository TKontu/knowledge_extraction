# Extraction Reliability — Implementation Spec

Version: 3.2 (2026-02-25)
**Implementation status: 2026-02-26 — Phases 0, 1 (quality), 2, 3 DONE. Phase 1A pending.**
Review: `docs/pipeline_review_extraction_reliability.md`
Data analysis: `debug/analyze_plan_impact.py`

## Problem

The small LLM (Qwen3-30B / gemma3-12b-awq) hallucinates when extracting from irrelevant pages. Classification is disabled by default, so all field groups are extracted from all pages. Non-entity-list prompts lack grounding. Empty extractions get 0.8 confidence and pass the merge filter. Additionally, `content[:8000]` in the user prompt silently drops content from 35% of pages.

**Case study** (David Brown Santasalo): "Santasalo" listed as a city, Helsinki listed as HQ (real: Jyväskylä). 630 extractions from 90 pages. Manufacturing "found" on 96% of pages (reality: ~15%). 521 location entries across 233 unique cities (reality: ~20 locations). HQ extracted as "United Kingdom" 31 times, "Finland" 20 times, "Santa Salo, Finland" 3 times, real answer "Jyväskylä, Finland" only 2 times.

**Content quality** (12,069 pages across ~290 companies, measured 2026-02-25):
- Median page: 5,183 chars. P75: 10,461. P90: 20,389.
- 35% of pages exceed 8,000 chars — the `content[:8000]` user prompt limit
- For those 35%: only 57% of content captured on average (P10 worst: 22%)
- 38% have nav-dominated embedding windows (>0.50 link density)
- 100% of pages are single-chunk with current `chunk_document(max_tokens=8000)` — the merge logic (3B) never fires on real data
- Firecrawl strips `<header>/<nav>/<footer>` but NOT `<div>`-based nav with custom CSS classes

**Confidence distribution** (84,483 existing extractions):
- 32.3% at 0.0 (empty entity lists — correct)
- 58.8% at 0.8 (the default fallback — mixes real + hallucinated)
- 1.3% between 0.1–0.7
- The 0.8 bucket is the core problem — Phase 3A targets it directly

**Root causes**:
1. Classification disabled — all field groups extracted from all pages
2. Weak prompts — no grounding, LLM fills gaps with world knowledge
3. Empty extractions stored with 0.8 confidence — passes merge filter
4. Chunk merge amplifies hallucinations — boolean `any()`, list concatenation
5. Field group embeddings missing `prompt_hint` vocabulary
6. Low-confidence fallback returns "all groups" — defeats classification
7. Nav junk in embedding window — classifier embeds navigation, not content
8. `content[:8000]` truncation — 35% of pages lose content before LLM sees it

## Architecture

```
[PHASE 0] Model Upgrade ✅ DONE
    ├── 0A: Switch bge-large-en → bge-m3 (8192 tokens, same 1024 dims) ✅
    └── 0B: Truncation safety net in EmbeddingService ✅

Source Content (Firecrawl markdown — often contaminated with nav junk)
    ↓
[PHASE 1] Classification Filter (★ highest impact) — quality ✅, enablement ⬜
    ├── 1A: Enable classification (4 config booleans → True) ⬜ PENDING
    ├── 1B: Add prompt_hint to field group embeddings ✅
    ├── 1C: Expand window 2000 → 6000 chars (requires bge-m3) ✅
    ├── 1D: Dynamic fallback (top 80% of scores, not "all groups") ✅
    └── 1E: Content cleaning before embedding (2-layer: pattern strip + line-density) ✅
    ↓
[PHASE 2] Strengthened Prompts + Extraction Window ✅ DONE
    ├── 2A: Grounding rules + remove domain-specific lines ✅
    ├── 2B: Expand extraction content window 8K → 20K chars (★ prevents knowledge loss) ✅
    └── 2C: Apply Layer 1 structural cleaning to extraction input ✅
    ↓
[PHASE 3] Post-Extraction Fixes ✅ DONE
    ├── 3A: Confidence recalibration (empty → ≤0.1) ✅
    ├── 3B: Boolean majority vote (not any()) — future-proofing, does not fire on current data ✅
    └── 3C: Fix confidence=None bypass in merge filter ✅
```

## Implementation Approach

Implemented directly (no agents) in risk-minimized incremental order:
1. Phase 0 (model switch) → 2. Phase 1 quality (1B-1E) → 3. Phase 3 (bug fixes) → 4. Phase 2 (prompts+window) → 5. Phase 1A (enable)

Original agent plan preserved below for reference:

| Agent | Phases | Files |
|-------|--------|-------|
| A | 0 + 1 | `config.py`, `embedding.py`, `smart_classifier.py`, `content_cleaner.py` (new), `client.py` |
| B | 2 + 3 | `schema_extractor.py`, `schema_orchestrator.py`, `smart_merge.py`, `content_cleaner.py` (import only) |

---

## Phase 0: Model & Infrastructure ✅

### 0A. Switch Embedding Model

bge-large-en has a hard 512-token limit — vLLM returns HTTP 400 (crashes, does NOT truncate). Safe ceiling: ~2800 chars. This blocks the 6000-char window in Phase 1C.

bge-m3: 8192 tokens (~47K chars), 1024 dimensions. Already deployed on 192.168.0.136:9003. Drop-in replacement (same dims, same API).

**`src/config.py`** (line 89):

```python
# Change default:
rag_embedding_model: str = Field(default="bge-m3", ...)
```

**Tests**: Default settings use `bge-m3`. Embedding of >3000 chars succeeds.

### 0B. Truncation Safety Net

Defensive cap before API call — prevents crashes if input is extremely long.

**`src/services/storage/embedding.py`** (line 77):

```python
MAX_EMBED_CHARS = 28000  # ~7000 tokens, within bge-m3's 8192 limit

async def embed(self, text: str) -> list[float]:
    original_length = len(text)
    if original_length > MAX_EMBED_CHARS:
        text = text[:MAX_EMBED_CHARS]
        logger.debug("embedding_text_truncated", original_length=original_length)
    # ... existing code
```

Also add to `embed_batch()` — truncate each text in the list before sending.

**Tests**: Long text truncated. Normal text unchanged.

---

## Phase 1: Enable & Improve Classification (quality ✅, enablement ⬜)

### 1A. Enable Classification

The `SmartClassifier` and `PageClassifier` already exist. They use embeddings (not LLM) — reliable and cheap. Just needs to be turned on.

**`src/config.py`** (lines 389-434) — flip 4 defaults:

| Setting | Old | New |
|---------|-----|-----|
| `classification_enabled` | `False` | `True` |
| `classification_skip_enabled` | `False` | `True` |
| `smart_classification_enabled` | `False` | `True` |
| `classification_use_default_skip_patterns` | `False` | `True` |

No other code changes — wiring in `schema_orchestrator.py:87-136` and `pipeline.py:543-548` already handles classification when enabled.

**Tests**: Default settings have all 4 flags True. Classification invoked when URL provided.

### 1B. Improve Field Group Embedding Quality

`_create_group_text()` in `smart_classifier.py:457-469` embeds only `name + description + field names`. Missing `prompt_hint`, which has the best matching vocabulary (e.g., "manufacturing plants, headquarters, sales offices").

**`src/services/extraction/smart_classifier.py`** — `_create_group_text()` (line 457):

```python
def _create_group_text(self, group: FieldGroup) -> str:
    lines = [f"{group.name}: {group.description}", "", "Fields:"]
    for field in group.fields:
        lines.append(f"- {field.name}: {field.description}")
    if group.prompt_hint:
        lines.append("")
        lines.append(group.prompt_hint)
    return "\n".join(lines)
```

Cache keys hash the group text, so cached embeddings auto-refresh (24h TTL).

**Tests**: Includes prompt_hint when available. Works when prompt_hint is None.

### 1C. Increase Classification Window

`_create_page_summary()` uses `content[:2000]`. With bge-m3 (8192 tokens), we can safely use 6000 chars (~1500 tokens).

**`src/services/extraction/smart_classifier.py`**:

`_create_page_summary()` (line 510):
```python
truncated_content = self._truncate_at_word_boundary(content, 6000)
```

`_rerank_groups()` (line 299):
```python
query = self._truncate_at_word_boundary(content, 6000)
```

bge-reranker-v2-m3 also supports 8192 tokens — 6000 chars is safe.

**Tests**: Both methods use 6000-char window.

### 1D. Fix Low-Confidence Fallback

When max embedding score < 0.4, classifier returns `relevant_groups=[]` which means "use all groups" in the orchestrator. This defeats classification.

Replace with dynamic threshold: include all groups within 80% of the top score. Adapts to score distribution — clustered scores keep most groups, standout scores keep few.

**`src/services/extraction/smart_classifier.py`** — `_classify_with_embeddings()` (lines 250-266):

```python
if max_score < low_threshold:
    sorted_groups = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    top_score = sorted_groups[0][1]
    cutoff = top_score * 0.8
    top_groups = [name for name, score in sorted_groups if score >= cutoff]
    if len(top_groups) < 2:
        top_groups = [name for name, _ in sorted_groups[:2]]

    return SmartClassificationResult(
        page_type="general",
        relevant_groups=top_groups,
        skip_extraction=False,
        confidence=max_score,
        method=ClassificationMethod.HYBRID,
        reasoning=f"Low embedding similarity (<{low_threshold}), using {len(top_groups)} groups within 80% of top score",
        embedding_scores=scores,
        reranker_scores=None,
    )
```

Apply same logic in `_rerank_groups()` (lines 348-359) when no groups pass reranker threshold:

```python
if not confirmed_groups:
    sorted_reranker = sorted(reranker_scores.items(), key=lambda x: x[1], reverse=True)
    top_score = sorted_reranker[0][1]
    cutoff = top_score * 0.8
    top_groups = [name for name, score in sorted_reranker if score >= cutoff]
    if len(top_groups) < 2:
        top_groups = [name for name, _ in sorted_reranker[:2]]

    return SmartClassificationResult(
        page_type="general",
        relevant_groups=top_groups,
        ...
    )
```

**Tests**:
- Uniform scores (0.30-0.38) → most groups included
- One standout (0.38 vs 0.20-0.25) → only top groups
- Minimum 2 groups always returned
- Same logic in both embedding and reranker fallback paths

### 1E. Content Cleaning Before Embedding

**The highest-impact change.** 45% of pages have nav junk dominating the embedding window. Without cleaning, the classifier compares navigation text against field groups — garbage in, garbage out.

Full cleaning (Layer 1 + Layer 2) applied in the classification path (before embedding/reranking). Layer 1 patterns only (structural junk removal) also applied to extraction LLM input (Phase 2C) — these patterns remove content that is never extractable (tracking pixels, bare nav links, skip-to-content).

**Two-layer approach** (validated on 11,582 real pages):
1. **Layer 1**: Strip 4 universal structural patterns (language-agnostic, no keywords)
2. **Layer 2**: Skip nav preamble using line-density windowing — navigation has high `[text](url)` ratio, content has low ratio. Works identically on any language.

**Measured effectiveness** (6000-char embedding window):

| Category | Before | After | Notes |
|----------|--------|-------|-------|
| Clean (<0.15 density) | 17.4% | 41.3% | Excellent signal |
| Mixed (0.15-0.30) | 23.2% | 25.2% | Good signal |
| Link Heavy (0.30-0.50) | 31.5% | 17.0% | Acceptable — links are part of content |
| Nav Dominated (>0.50) | 27.9% | 16.4% | Poor — but 15.2% genuinely link-only |
| **Usable (Clean + Mixed)** | **40.6%** | **66.5%** | |

True gap: 1.0% of pages have content that cleaning misses (buried >6000 chars deep). These still get extracted via dynamic fallback (1D) + full content to LLM (Phase 2).

False positive rate: 0.07% (8 pages out of 11,582).

#### Changes

**NEW FILE: `src/services/extraction/content_cleaner.py`**:

```python
"""Content cleaning for embedding and extraction.

Two-layer approach: universal safe patterns + line-density windowing.
- Full cleaning (Layer 1 + 2): classification path (before embedding/reranking)
- Layer 1 only: extraction LLM input (Phase 2C) — safe structural removal

Design: language-agnostic, template-agnostic, conservative (<1% false positives).
"""

import re


# Layer 1: Universal safe patterns (structural, never real content)
UNIVERSAL_PATTERNS: list[re.Pattern] = [
    # Empty-alt images: ![](url) — logos, tracking pixels, spacers
    re.compile(r"!\[\]\(https?://[^)]+\)\s*", re.IGNORECASE),

    # Skip-to-content accessibility links
    re.compile(r"^\[Skip to [^\]]*\]\([^)]*\)\s*\n?", re.MULTILINE | re.IGNORECASE),

    # Bare link list items: "* [Link](url)" with nothing after
    # Preserves: "* [Link](url) — Description" (has text after)
    re.compile(
        r"^(?:[\*\-]\s+)\[([^\]]{1,80})\]\([^)]*(?:\([^)]*\)[^)]*)*\)\s*$",
        re.MULTILINE,
    ),

    # Bare image lines: "![alt](url)" alone on a line
    re.compile(r"^!\[[^\]]*\]\([^)]+\)\s*$", re.MULTILINE),
]


# Layer 2: Line-density content windowing

def compute_line_link_density(line: str) -> float:
    """Ratio of markdown link syntax chars to total chars. 0.0-1.0."""
    if not line:
        return 0.0
    total_len = len(line)
    link_chars = 0
    for match in re.finditer(r"\[([^\]]*)\]\([^)]*\)", line):
        link_chars += len(match.group(0))
    for match in re.finditer(r"(?<!\()https?://\S+", line):
        link_chars += len(match.group(0))
    return link_chars / total_len


def find_content_by_line_density(
    content: str,
    min_content_lines: int = 3,
    density_threshold: float = 0.4,
    min_line_length: int = 20,
    max_scan_lines: int = 200,
) -> int:
    """Find char offset where real content begins using link density.

    Scans from top. Content = low density (<0.4) + meaningful length (>20 chars).
    Returns offset of first run of min_content_lines consecutive content lines.
    Returns 0 if content starts immediately or no clear region found (conservative).
    """
    if not content:
        return 0

    lines = content.split("\n")
    consecutive_content = 0
    content_start_line = 0

    for i, line in enumerate(lines[:max_scan_lines]):
        stripped = line.strip()
        if not stripped or len(stripped) < min_line_length:
            continue

        density = compute_line_link_density(stripped)
        if density < density_threshold:
            if consecutive_content == 0:
                content_start_line = i
            consecutive_content += 1
            if consecutive_content >= min_content_lines:
                return sum(len(lines[j]) + 1 for j in range(content_start_line))
        else:
            consecutive_content = 0

    return 0


def strip_structural_junk(content: str) -> str:
    """Layer 1 only: strip universal structural patterns.

    Safe for extraction input — removes only content that is never extractable
    (tracking pixels, bare nav links, skip-to-content, bare images).
    Does NOT apply line-density windowing (Layer 2) to preserve all real content.
    """
    if not content:
        return content
    cleaned = content
    for pattern in UNIVERSAL_PATTERNS:
        cleaned = pattern.sub("", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def clean_markdown_for_embedding(content: str) -> str:
    """Full clean: Layer 1 (patterns) + Layer 2 (density). For classification only."""
    if not content:
        return content

    cleaned = strip_structural_junk(content)

    content_offset = find_content_by_line_density(cleaned)
    if content_offset > 0:
        cleaned = cleaned[content_offset:]

    return cleaned.strip()
```

**`src/services/extraction/smart_classifier.py`** — use cleaning in both embedding paths:

```python
from services.extraction.content_cleaner import clean_markdown_for_embedding

# _create_page_summary() (line ~510):
def _create_page_summary(self, url, title, content):
    cleaned = clean_markdown_for_embedding(content)
    truncated_content = self._truncate_at_word_boundary(cleaned, 6000)
    ...

# _rerank_groups() (line ~299):
async def _rerank_groups(self, url, content, ...):
    cleaned = clean_markdown_for_embedding(content)
    query = self._truncate_at_word_boundary(cleaned, 6000)
    ...
```

**`src/services/scraper/client.py`** — pass `onlyMainContent` for new crawls:

```python
# scrape() — add to request:
json={"url": url, "onlyMainContent": True}

# start_batch_scrape() — add to request:
batch_request = {
    "urls": urls,
    "formats": formats,
    "onlyMainContent": True,
}
```

#### Tests

**Layer 1 (patterns)**:
- Strips `![](https://example.com/logo.png)` (empty-alt)
- Strips `[Skip to content](#main)`
- Strips `* [Products](/products)` (bare link list item)
- Strips `![Logo](https://example.com/logo.png)` (bare image line)
- Preserves `* [Products](/products) — Our full catalog` (has description)
- Preserves images within paragraph text
- Handles empty/whitespace input

**Layer 2 (line density)**:
- `compute_line_link_density()`: ~0.0 for text, >0.7 for nav links, ~0.3-0.5 for mixed
- Returns 0 for content that starts immediately
- Skips nav preamble of high-density lines
- Returns 0 (conservative) when no clear content region found
- Works on Portuguese, German, Japanese content

**Integration**:
- Combines Layer 1 + Layer 2
- `_create_page_summary()` and `_rerank_groups()` use cleaned content
- Cleaning 10K chars completes in <10ms

---

## Phase 2: Strengthen Prompts + Extraction Window ✅

### 2A. Strengthen Extraction Prompts

Non-entity-list prompt says only "use null for unknown values." No grounding. Small model fills gaps with world knowledge. The entity-list prompt already has grounding and works better — extend the same pattern.

**`src/services/extraction/schema_extractor.py`**

**`_build_system_prompt()` (line 336)** — replace non-entity-list prompt (lines 353-362):

```python
return f"""You are extracting {field_group.description} from {self.context.source_type}.

Fields to extract:
{fields_str}

{field_group.prompt_hint}

RULES:
- Extract ONLY from the content provided below. Do NOT use outside knowledge.
- If the content does not contain information for a field, return null.
- If the content is not relevant to {field_group.description}, return null for ALL fields.
- For boolean fields, return true ONLY if there is explicit evidence in the content.
- For list fields, return empty list [] if no items are found in the content.

Output JSON with exactly these fields. Include a "confidence" field (0.0-1.0):
- 0.0 if the content has no relevant information
- 0.5-0.7 if only partial information found
- 0.8-1.0 if the content is clearly relevant with good data
"""
```

**`_build_entity_list_system_prompt()` (line 365)** — two changes:

1. **Remove lines 407 AND 408** (domain-specific, violate template-agnostic principle):
```python
# DELETE: "For locations: focus on headquarters, manufacturing sites..."
# DELETE: "For products: focus on main product lines, not every variant..."
# The generic "Extract ONLY the most relevant/significant items (max 20)" covers this.
```

2. **Add grounding** after the `IMPORTANT LIMITS` block:
```python
CRITICAL: Extract ONLY from the provided content. Do NOT use outside knowledge.
If this content does not contain any {entity_singular} information, return an empty list.
```

**Tests** (2A):
- Non-entity prompt contains "Do NOT use outside knowledge" and "confidence" instruction
- Entity-list prompt contains "Do NOT use outside knowledge"
- Entity-list prompt does NOT contain "For locations:" or "For products:"

### 2B. Expand Extraction Content Window (★ prevents knowledge loss)

`_build_user_prompt()` truncates content to `content[:8000]` — 8,000 chars ≈ 2,000 tokens. This silently drops content from **35% of pages**, with average capture at 57% for those pages (P10 worst: 22%).

Qwen3-30B has ~32K token context. System prompt ≈ 500-1500 tokens, response max = 8192 tokens. Available input budget: **~24,000 tokens**. Current utilization: **8% (2,000/24,000)**.

With classification (Phase 1) filtering to relevant field groups, content is already on-topic — longer input is safe.

**Measured impact** (12,069 pages):

| Window | Pages fully captured | Notes |
|--------|---------------------|-------|
| content[:8000] | 65% | Current — 35% lose content |
| content[:16000] | 86% | |
| content[:20000] | **90%** | Recommended — uses 21% of context budget |

**NOTE**: `chunk_document(max_tokens=8000)` uses `count_tokens = len//4`, meaning max 32K chars per chunk. On real data, **100% of pages are single-chunk** — the chunker never splits. The content[:N] limit is the only content gate. Increasing it captures proportionally more content.

**`src/services/extraction/schema_extractor.py`** — `_build_user_prompt()` (line 424):

```python
EXTRACTION_CONTENT_LIMIT = 20000  # chars, ~5000 tokens, 21% of Qwen3-30B context budget

def _build_user_prompt(
    self,
    content: str,
    field_group: FieldGroup,
    source_context: str | None,
) -> str:
    """Build user prompt with content."""
    context_line = (
        f"{self.context.source_label}: {source_context}\n\n"
        if source_context
        else ""
    )

    return f"""{context_line}Extract {field_group.name} information from ONLY the content below. If this content does not contain {field_group.name} information, return null/empty values.

---
{content[:EXTRACTION_CONTENT_LIMIT]}
---"""
```

Also update the queue-mode worker fallback at `src/services/llm/worker.py:405,464-466` to use the same limit.

**Tests** (2B):
- User prompt truncates at EXTRACTION_CONTENT_LIMIT (20000), not 8000
- Content shorter than limit is unchanged
- Content longer than limit is truncated (no crash)

### 2C. Apply Layer 1 Cleaning to Extraction Input

Phase 1E's Layer 1 patterns (empty-alt images, skip-to-content links, bare nav links, bare images) are safe to remove from extraction input — they are never extractable content. This reclaims ~800 chars (median) of the content window for real content.

Only Layer 1 (pattern stripping) — NOT Layer 2 (line-density windowing). Layer 2 could remove content sections the LLM should see.

**`src/services/extraction/schema_extractor.py`** — `_build_user_prompt()`:

```python
from services.extraction.content_cleaner import strip_structural_junk

def _build_user_prompt(self, content, field_group, source_context):
    # ... context_line ...
    cleaned_content = strip_structural_junk(content)
    return f"""{context_line}Extract {field_group.name} information from ONLY the content below. If this content does not contain {field_group.name} information, return null/empty values.

---
{cleaned_content[:EXTRACTION_CONTENT_LIMIT]}
---"""
```

**Tests** (2C):
- Extraction input has bare nav links removed
- Extraction input retains all paragraph text, headings, described links
- strip_structural_junk does NOT apply line-density windowing

---

## Phase 3: Post-Extraction Fixes ✅

### 3A. Confidence Recalibration

Empty/default extractions get LLM's self-reported confidence (often 0.8). The merge filter (min 0.3) can't exclude them. Fix: count populated fields — if <20% populated, cap confidence at 0.1.

**`src/services/extraction/schema_orchestrator.py`** — add `_is_empty_result()`:

```python
def _is_empty_result(self, data: dict, group: FieldGroup) -> tuple[bool, float]:
    """Returns (is_empty, populated_ratio)."""
    if group.is_entity_list:
        for key, value in data.items():
            if key == "confidence":
                continue
            if isinstance(value, list) and len(value) > 0:
                return False, 1.0
        return True, 0.0

    total = 0
    populated = 0
    for field_def in group.fields:
        if field_def.name == "confidence":
            continue
        total += 1
        value = data.get(field_def.name)
        if value is None:
            continue
        if field_def.default is not None and value == field_def.default:
            continue
        if isinstance(value, str) and not value.strip():
            continue
        if isinstance(value, list) and len(value) == 0:
            continue
        populated += 1

    if total == 0:
        return True, 0.0
    ratio = populated / total
    return ratio < 0.2, ratio
```

Modify `extract_group()` (lines 169-175) — recalibrate after chunk merge:

```python
if chunk_results:
    merged = self._merge_chunk_results(chunk_results, group)
    group_result["data"] = merged

    # Explicit None handling — current system avoids None via chain of 0.8 fallbacks,
    # but that chain is fragile (schema_orchestrator:339 → merged.pop("confidence", 0.8))
    raw_confidence = merged.pop("confidence", None)
    is_empty, populated_ratio = self._is_empty_result(merged, group)

    if raw_confidence is None:
        raw_confidence = 0.0 if is_empty else 0.5 * populated_ratio

    if is_empty:
        group_result["confidence"] = min(raw_confidence, 0.1)
    else:
        group_result["confidence"] = raw_confidence * (0.5 + 0.5 * populated_ratio)
```

**Tests**:
- All-null data → `(True, 0.0)`
- All-default data → `(True, 0.0)`
- Data with real values → `(False, >0.5)`
- Entity list: empty → True, non-empty → False
- Empty extraction → confidence ≤ 0.1
- Full extraction → confidence preserved
- `confidence=None` → no TypeError

### 3B. Boolean Majority Vote

`_merge_chunk_results()` uses `any()` for booleans (line 304) — one hallucinating chunk poisons the result. Fix: majority vote.

**Data note**: On current data, 100% of pages are single-chunk, so this merge logic never fires. This fix is correct and future-proof — it matters when templates produce longer content, or if `chunk_document` parameters change. The primary anti-hallucination defense for booleans is classification (Phase 1) + grounding (Phase 2A), which prevent the LLM from seeing irrelevant content in the first place.

Note: `_apply_defaults()` converts null booleans → False per-chunk BEFORE merge. This helps: creates `[True, False, False]` instead of `[True]`, so majority vote works correctly.

**`src/services/extraction/schema_orchestrator.py`** — line 303-304:

```python
if field.field_type == "boolean":
    true_count = sum(1 for v in values if v is True)
    merged[field.name] = true_count > len(values) / 2
```

**Tests**: 1T+2F→F, 2T+1F→T, all T→T, all F→F, tie→F (conservative).

### 3C. Fix confidence=None Bypass

`smart_merge.py:83` allows `confidence=None` through the filter. Current system never sends None (chain of 0.8 fallbacks), but this is fragile.

**`src/services/reports/smart_merge.py`** (line 82-83):

```python
# Old: if c.confidence is None or c.confidence >= self._min_confidence
# New:
filtered = [
    c for c in candidates
    if c.confidence is not None and c.confidence >= self._min_confidence
]
```

**Tests**: None → excluded, 0.5 → passes, 0.2 → excluded, all None → null result.

---

## Key Files

| File | What Changes | Agent |
|------|-------------|-------|
| `src/config.py:89` | Embedding model → `bge-m3` | A |
| `src/config.py:389-434` | 4 classification booleans → True | A |
| `src/services/storage/embedding.py:77` | Truncation safety net | A |
| `src/services/extraction/content_cleaner.py` | **NEW** — `strip_structural_junk()` + `clean_markdown_for_embedding()` | A |
| `src/services/extraction/smart_classifier.py:457` | Add prompt_hint to group text | A |
| `src/services/extraction/smart_classifier.py:510,299` | Window 2000→6000 + use cleaned content | A |
| `src/services/extraction/smart_classifier.py:250-266,348-359` | Dynamic fallback threshold | A |
| `src/services/scraper/client.py:174-176,730-733` | `onlyMainContent: True` | A |
| `src/services/extraction/schema_extractor.py:336-441` | 2A: Grounding rules + remove lines 407-408 | B |
| `src/services/extraction/schema_extractor.py:424-441` | 2B: Content window 8K→20K + 2C: Layer 1 cleaning | B |
| `src/services/llm/worker.py:405,464-466` | 2B: Align worker content limit to 20K | B |
| `src/services/extraction/schema_orchestrator.py:152-175` | `_is_empty_result()` + recalibration | B |
| `src/services/extraction/schema_orchestrator.py:303-304` | Boolean majority vote | B |
| `src/services/reports/smart_merge.py:82-83` | Fix None bypass | B |

## Verification

Re-extract David Brown Santasalo after all phases (after Phase 1A is enabled):

1. ✅ EmbeddingService uses `bge-m3`. Embedding of 6000 chars succeeds.
2. ✅ `_create_page_summary()` uses cleaned content.
3. ⬜ Sources have `page_type` and `relevant_field_groups` populated. Product pages don't get `company_meta`. (Requires 1A)
4. ✅ Field group embeddings include prompt_hint vocabulary.
5. ✅ Empty extractions have confidence ≤ 0.1, filtered by merge.
6. ✅ Boolean fields reflect majority vote (if multi-chunk content exists).
7. ⬜ No "Santasalo" as city. HQ consistently "Jyväskylä, Finland." (Requires re-extraction after 1A)
8. ✅ Pages >8K chars have full content captured (up to 20K) — no silent truncation loss.
9. ✅ Extraction input has bare nav links stripped (Layer 1) — more useful content in the window.
