# Robust Page Classification — Implementation Spec

Version: 3.0 (2026-03-05)
Status: **Phase 1 COMPLETE** · **Phase 2 COMPLETE** — Phase 3 (remove SmartClassifier) pending
Depends on: None (independent of global sources refactor)

## What's Implemented (Phase 1)

**File: `src/services/extraction/llm_skip_gate.py`** — fully implemented:
- `SkipGateResult` dataclass (decision, method, confidence)
- `LLMSkipGate` class with `should_extract()` async method
- `build_schema_summary()` — auto-generates schema context from any template
- `_parse_decision()` — handles thinking tags (Qwen3), markdown fences, JSON parse, keyword fallback
- Safety defaults: LLM failure → "extract", parse failure → "extract", no schema → "extract"

**File: `src/config.py`** — config fields added:
- `classification_skip_gate_enabled` (default False)
- `classification_skip_gate_model` (default empty = use LLM_MODEL)
- `classification_skip_gate_content_limit` (default 2000)

**Phase 2 is complete**: `llm_skip_gate.py` IS wired into `schema_orchestrator.py` as Level 1 classifier (lines 458–483). SmartClassifier remains as Level 2 fallback — Phase 3 (remove it) is still pending.

## Problem

**57.7% of all LLM extraction calls produce zero-confidence results — pure waste.**

The extraction pipeline runs all 7 field groups on every page. The embedding classifier that should filter fails for 42% of pages (near-zero cosine similarity between schema metadata and website copy), falling back to "extract everything."

### Measured waste on 12,069 pages × 7 groups = 47,201 extractions

| Metric | Value |
|--------|-------|
| Zero-confidence extractions | 27,225 (57.7%) |
| Sources with 0 useful extractions | 2,810 (25%) — all 7 calls wasted |
| Average useful groups per source | ~1.8 out of 7 |

### Why the current classifier fails

The SmartClassifier uses embedding cosine similarity between page content and field group schema descriptions. This fails because:

1. **Semantic gap**: Schema descriptions are dry metadata ("Gearbox product information: product_name, series_name, torque_rating_nm"). Page content is marketing copy ("Walks the walk, talks the torque").
2. **Language mismatch**: 30%+ of pages are non-English (DE, FR, ES, PT, FI).
3. **Wrong question**: "How similar is this page to this schema description?" ≠ "Does this page contain extractable data?"

---

## Architecture: LLM Skip-Gate + Confidence Gating

### Key design insight from trials

**Don't try to predict which field groups a page matches. Instead, answer a simpler binary question: "Does this page contain data matching the extraction schema at all?"**

Trials showed that asking LLMs to select specific field groups produces 34-53% recall — too low. But asking "extract or skip?" produces 92-100% recall with the right prompt design.

**The downstream extraction pipeline already handles quality filtering** via confidence scoring. If a page gets extracted and produces low-confidence results, those get weighted down in reports. The skip-gate only needs to catch the obviously irrelevant pages.

### Architecture

```
[Source: URL + Title + Content]
    │
    ▼
[Level 0: Rule-Based Skip] ← FREE, instant
    │ Matches /careers, /privacy, /login, /tag/, etc.
    │ Result: skip → DONE, no extraction
    │
    ▼
[Level 1: LLM Skip-Gate] ← CHEAP, ~1100 tokens, 0.2s
    │ Binary: "Does this page match the extraction schema?"
    │ Schema-agnostic: receives field group descriptions as context
    │ Intentionally permissive: "when uncertain, extract"
    │ Result: skip → DONE | extract → proceed to extraction
    │
    ▼
[Level 2: Full Extraction] ← EXPENSIVE, N calls × ~5500 tokens each
    │ Runs ALL field groups (no group selection)
    │ Confidence scoring filters low-quality results
    │
    ▼
[Existing: Confidence Gating in Reports/Queries]
    │ Low-confidence extractions weighted down
    │ High-confidence results surface in reports
```

### Why this beats group-selection classification

| Approach | Recall | Skip Accuracy | Complexity |
|----------|--------|---------------|------------|
| Embedding group-selection (current) | 42% | N/A | High (embeddings + reranker) |
| LLM group-selection (trial v1) | 34-53% | 14% | Medium |
| **LLM binary skip-gate (trial v2-v4)** | **92-100%** | **60-75%** | **Low** |

The skip-gate is intentionally simple. It doesn't try to be smart about which groups to extract — it just prevents obviously wasted work. The extraction model's confidence scoring is the real quality gate.

---

## Trial Results

### Trial v2: Binary skip-gate with few-shot (31 hand-curated pages)

| Model | Accuracy | Precision | Recall | F1 | FN |
|-------|----------|-----------|--------|----|----|
| Qwen3-30B-A3B-it-4bit | 87.1% | 91.3% | 91.3% | 91.3% | 2 |
| gemma3-4B | 83.9% | 82.1% | 100% | 90.2% | 0 |

### Trial v4: Schema-agnostic, real DB data (80 random pages)

| Model | Tok/pg | Lat/pg | Precision | Recall | F1 | FN |
|-------|--------|--------|-----------|--------|----|----|
| Qwen3-30B-A3B-it-4bit | 1048 | 0.14s | 84.8% | 51.9% | 64.4% | 26 |
| gemma3-4B | 1132 | 0.18s | 71.4% | 92.6% | 80.6% | 4 |

**Note on v4 GT noise**: Many "false positives" are GT errors — the extraction model failed on pages that clearly contain relevant data (e.g., ABB motor product pages, Hansen Motor /company, Maxgear gear catalog). After human audit of GT corrections, gemma3-4B achieves ~85% accuracy.

**gemma3-4B FN analysis**: All 4 false negatives were pages where `cleaned_content` was just cookie consent banners — the actual content was stripped. This is a boilerplate removal issue, not a classification issue.

### Model recommendation

**gemma3-4B** for the skip-gate:
- **92.6% recall** — almost never loses useful data
- **0.18s/page** — negligible latency
- **~1100 tokens/page** — 5x cheaper than one extraction call
- When it says "skip", it's right 60-100% of the time (varies by trial)
- Can run on the same vLLM instance as the extraction model

---

## Schema-Agnostic Design

The skip-gate must work for ANY extraction template — drivetrain companies, recipes, job listings, real estate. This requires:

### 1. Schema is context, not hardcoded

The system prompt contains no domain-specific knowledge. The extraction schema is passed as a formatted context block in the user prompt:

```python
SYSTEM_PROMPT = """You classify web pages for a structured data extraction pipeline.

You receive an extraction schema describing target data types, plus a web page.

Decision rules:
- "extract" = page contains data matching ANY field group in the schema
- "skip" = page has NO matching data (wrong industry, empty, navigation-only,
  login, job listings, holiday notices, legal/privacy, forum index)

When genuinely uncertain, prefer "extract" — missing data costs more than
a wasted extraction call.

Output JSON only: {"decision": "extract" or "skip"}"""
```

### 2. Schema summary is auto-generated from template

```python
def build_schema_summary(schema: dict) -> str:
    """Auto-generate schema context from any extraction_schema.

    This is the ONLY place where the LLM learns what data we're looking for.
    Works for any template — drivetrain, recipes, jobs, etc.
    """
    ctx = schema.get("extraction_context", {})
    lines = [
        f"Data domain: {ctx.get('source_type', 'documents')}",
        f"Entity type: {ctx.get('source_label', 'entities')}",
        "",
        "Target field groups:",
    ]
    for fg in schema.get("field_groups", []):
        lines.append(f"  {fg['name']}: {fg['description']}")
        # Include prompt_hint first line if available (most informative)
        hint = fg.get("prompt_hint", "").split("\n")[0].strip()
        if hint:
            lines.append(f"    Hint: {hint}")
        # Include key field names for grounding
        fields = [f["name"] for f in fg.get("fields", [])[:4]]
        lines.append(f"    Key fields: {', '.join(fields)}")
    return "\n".join(lines)
```

**Why this works**: The LLM reads the schema summary and understands what data we're looking for. For a recipe template, it would see "Data domain: recipe websites, Entity type: Recipe, Field groups: ingredients, instructions, nutrition..." and correctly skip a page about car insurance.

### 3. No domain-specific skip rules

The `SYSTEM_PROMPT` skip criteria are universal:
- "wrong industry" — the LLM infers this from the schema context
- "empty, navigation-only, login, job listings, holiday notices, legal/privacy" — universal patterns
- No hardcoded keywords like "drivetrain", "gearbox", etc.

### 4. User prompt template

```python
USER_TEMPLATE = """EXTRACTION SCHEMA:
{schema_summary}

PAGE:
URL: {url}
Title: {title}

Content:
{content}

Should this page be extracted or skipped? JSON only:"""
```

### 5. extraction_context is required in templates

For the skip-gate to work well, templates MUST include meaningful `extraction_context`:

```json
{
  "extraction_context": {
    "source_type": "company documentation",  // What kind of sources?
    "source_label": "Company"                // What entity are we extracting?
  }
}
```

If `extraction_context` is missing, the skip-gate falls back to extracting everything (conservative).

---

## Phase 1: LLM Skip-Gate Implementation ✅ COMPLETE

**Risk: LOW** — Additive. Doesn't change existing extraction path.

### 1a. New file: `src/services/extraction/llm_skip_gate.py`

```python
@dataclass(frozen=True)
class SkipGateResult:
    """Result of the LLM skip-gate classification."""
    decision: str  # "extract" or "skip"
    method: ClassificationMethod
    confidence: float  # 1.0 for clear decisions, 0.5 for uncertain


class LLMSkipGate:
    """Binary LLM-based page classifier: extract or skip.

    Schema-agnostic: receives the extraction schema as context.
    Intentionally permissive: defaults to "extract" on uncertainty
    or parse failure. Quality filtering is the extraction pipeline's job.

    Uses gemma3-4B (or configured model) — small, fast, high recall.
    """

    def __init__(
        self,
        llm_client: LLMClient,
        content_limit: int = 2000,
        model: str | None = None,  # None = use default LLM_MODEL
    ):
        self._llm = llm_client
        self._content_limit = content_limit
        self._model = model

    async def should_extract(
        self,
        url: str,
        title: str | None,
        content: str,
        schema: dict,
    ) -> SkipGateResult:
        """Decide if a page should be extracted.

        Args:
            url: Page URL.
            title: Page title (may be None).
            content: Page content (cleaned_content preferred).
            schema: The project's extraction_schema dict.

        Returns:
            SkipGateResult with decision and confidence.
        """
        # Skip the gate entirely if no schema context (conservative)
        if not schema or not schema.get("field_groups"):
            return SkipGateResult("extract", ClassificationMethod.RULE_BASED, 0.5)

        schema_summary = build_schema_summary(schema)
        user_msg = USER_TEMPLATE.format(
            schema_summary=schema_summary,
            url=url,
            title=title or "(no title)",
            content=content[:self._content_limit],
        )

        try:
            response = await self._llm.generate(
                system=SYSTEM_PROMPT,
                user=user_msg,
                temperature=0.0,
                max_tokens=60,
                model=self._model,
            )
            decision = self._parse_decision(response)
        except Exception:
            # On any LLM failure, default to extract (never lose data)
            logger.warning("skip_gate_llm_error", url=url, exc_info=True)
            decision = "extract"

        return SkipGateResult(
            decision=decision,
            method=ClassificationMethod.LLM,
            confidence=1.0 if decision == "skip" else 0.8,
        )

    def _parse_decision(self, text: str) -> str:
        """Parse LLM response. Defaults to 'extract' on any ambiguity."""
        text = text.strip()
        # Handle thinking tags (Qwen3)
        if "<think>" in text:
            idx = text.rfind("</think>")
            if idx != -1:
                text = text[idx + 8:].strip()
        # Handle markdown fences
        if text.startswith("```"):
            text = text.split("\n", 1)[1] if "\n" in text else text[3:]
            text = text.rsplit("```", 1)[0].strip()
        # Try JSON parse
        try:
            d = json.loads(text)
            if d.get("decision", "").lower().strip() == "skip":
                return "skip"
            return "extract"
        except (json.JSONDecodeError, AttributeError):
            pass
        # Fallback: look for "skip" keyword
        if '"skip"' in text.lower() or "'skip'" in text.lower():
            return "skip"
        # Default: extract (never lose data on parse failure)
        return "extract"
```

### 1b. Config additions

**File: `src/config.py`**:

```python
# Skip-gate classification
# NOTE: Implementation uses default=False (not True as shown here).
# Disabled by default to avoid enabling prematurely in production.
classification_skip_gate_enabled: bool = Field(
    default=True,
    description="Enable LLM skip-gate before extraction",
)
classification_skip_gate_model: str = Field(
    default="",
    description="Model for skip-gate (empty = use default LLM_MODEL). "
                "Recommend a small fast model like gemma3-4B.",
)
classification_skip_gate_content_limit: int = Field(
    default=2000,
    ge=500,
    le=5000,
    description="Max chars of content sent to skip-gate LLM",
)
```

### 1c. Tests

```
TestLLMSkipGate:
    - test_product_page_returns_extract
    - test_holiday_page_returns_skip
    - test_unrelated_industry_returns_skip
    - test_non_english_product_page_returns_extract
    - test_empty_content_returns_extract (conservative)
    - test_llm_failure_returns_extract (never lose data)
    - test_parse_failure_returns_extract
    - test_no_schema_returns_extract (conservative)
    - test_schema_summary_generated_from_template
    - test_different_template_produces_different_summary
```

---

## Phase 2: Pipeline Integration ✅ COMPLETE

**Implemented**: `skip_gate` param in `SchemaExtractionOrchestrator.__init__()` (line 372). `self._skip_gate` and `self._extraction_schema` stored. Full Level 1 path in `extract_all_groups()` (lines 458–483). Config default is `False` (disabled unless explicitly enabled).

**Risk: MEDIUM** — Changes classification flow. Must not regress.

### 2a. Integrate into schema_orchestrator.py

**File: `src/services/extraction/schema_orchestrator.py`** — Replace SmartClassifier with skip-gate:

```python
# CURRENT (lines 106-131):
if source_url and self._classification.enabled:
    if self._smart_classifier and self._classification.smart_enabled:
        classification = await self._smart_classifier.classify(...)
    else:
        classification = classifier.classify(url=source_url, title=source_title)

# NEW:
if source_url and self._classification.enabled:
    # Level 0: Rule-based skip (existing PageClassifier)
    available_group_names = [g.name for g in field_groups]
    rule_classifier = PageClassifier(available_groups=available_group_names)
    rule_result = rule_classifier.classify(url=source_url, title=source_title)

    if rule_result.skip_extraction and self._classification.skip_enabled:
        # Rule-based skip — clear pattern match (/careers, /privacy, etc.)
        classification = rule_result
    elif self._skip_gate and self._classification.skip_gate_enabled:
        # Level 1: LLM skip-gate
        gate_result = await self._skip_gate.should_extract(
            url=source_url,
            title=source_title,
            content=markdown,
            schema=self._extraction_schema,
        )
        if gate_result.decision == "skip":
            classification = ClassificationResult(
                page_type="skip",
                relevant_groups=[],
                skip_extraction=True,
                confidence=gate_result.confidence,
                method=gate_result.method,
                reasoning="LLM skip-gate: page does not match extraction schema",
            )
        else:
            # Extract all groups — confidence gating handles quality
            classification = ClassificationResult(
                page_type=rule_result.page_type,
                relevant_groups=available_group_names,
                skip_extraction=False,
                confidence=gate_result.confidence,
                method=gate_result.method,
            )
    else:
        # No skip-gate: extract all groups (current fallback behavior)
        classification = ClassificationResult(
            page_type=rule_result.page_type,
            relevant_groups=available_group_names,
            skip_extraction=False,
            confidence=0.5,
            method=ClassificationMethod.RULE_BASED,
        )
```

**Key point**: When the skip-gate says "extract", we extract ALL field groups, not a subset. Group selection by the classifier was unreliable (34-53% recall in trials). The extraction model's confidence scoring is the quality gate.

### 2b. Wire skip-gate into orchestrator constructor

```python
# In SchemaExtractionOrchestrator.__init__:
self._skip_gate: LLMSkipGate | None = None
if extraction_config.skip_gate_enabled:
    self._skip_gate = LLMSkipGate(
        llm_client=llm_client,
        content_limit=extraction_config.skip_gate_content_limit,
        model=extraction_config.skip_gate_model or None,
    )
self._extraction_schema = extraction_schema  # Store for skip-gate context
```

### 2c. Pass extraction_schema through the pipeline

The `extraction_schema` dict must reach the orchestrator. Currently `pipeline.py` → `worker.py` → `schema_orchestrator.py`. The schema is loaded in `pipeline.py` from the project. Ensure it's passed as a parameter (not fetched again).

**File: `src/services/extraction/pipeline.py`** — pass `extraction_schema` to worker.
**File: `src/services/extraction/worker.py`** — pass to orchestrator.

### 2d. Tests

```
TestSkipGateIntegration:
    - test_rule_skip_still_works (Level 0 unchanged)
    - test_llm_skip_gate_prevents_extraction
    - test_llm_extract_runs_all_groups (not subset)
    - test_skip_gate_disabled_extracts_everything
    - test_skip_gate_failure_extracts_everything
    - test_schema_passed_to_skip_gate
```

---

## Phase 3: Remove Embedding Classifier ⬜

**Risk: LOW** — Removal of unused code path after Phase 2 is validated.

### 3a. Remove SmartClassifier

- Remove `SmartClassifier` from `schema_orchestrator.py`
- Delete `src/services/extraction/smart_classifier.py`
- Remove embedding classification from `EmbeddingService` (keep embedding for search/dedup)
- Remove classification-related Redis cache keys

### 3b. Simplify config

Remove from `src/config.py`:
- `classification_embedding_high_threshold`
- `classification_embedding_low_threshold`
- `classification_reranker_threshold`
- `classification_reranker_model`
- `classification_cache_ttl`
- `classification_smart_enabled`

### 3c. Update ClassificationMethod enum

```python
class ClassificationMethod(str, Enum):
    RULE_BASED = "rule"  # Level 0: URL pattern skip
    LLM = "llm"         # Level 1: LLM skip-gate
```

Remove `HYBRID`.

### 3d. Tests

```
TestCleanup:
    - test_smart_classifier_not_imported
    - test_old_config_keys_removed
    - test_classification_method_values
```

---

## Implementation Order

| Phase | What | Risk | Impact | Effort |
|-------|------|------|--------|--------|
| 1 | LLM skip-gate implementation | LOW | New module, additive | ~150 lines |
| 2 | Pipeline integration | MEDIUM | Replaces SmartClassifier flow | ~100 lines changed |
| 3 | Remove embedding classifier | LOW | Cleanup | ~-500 lines |

**Total**: ~250 net lines removed. The system gets simpler.

---

## Expected Outcomes

### Skip-gate impact (conservative estimate based on trials)

| Metric | Current | Expected |
|--------|---------|----------|
| Pages skipped by rule (Level 0) | ~700 (6%) | ~700 (unchanged) |
| Pages skipped by LLM gate (Level 1) | 0 | ~1,500-2,500 (15-25%) |
| Pages sent to extraction | 11,340 | ~8,500-9,500 |
| Extraction LLM calls | 47,201 | ~35,000-40,000 |
| Zero-confidence extractions | 27,225 (57.7%) | ~18,000-22,000 (~50%) |

**The skip-gate is not designed to eliminate all waste.** It's a cheap pre-filter that catches the obviously irrelevant 15-25% of pages. The remaining waste (pages that match the schema but produce low-confidence extractions) is handled by the existing confidence gating in reports.

### Cost per classification

| Component | Tokens | Cost |
|-----------|--------|------|
| Skip-gate call | ~1100/page | 0.18s latency |
| One extraction call | ~5500/page | ~1.5s latency |
| Break-even | Skip-gate saves if it prevents >1 wasted extraction per 5 calls | Always saves on avg 5.2 wasted calls/page |

### Time estimate

At ~19 sources/min, reducing from 11,340 to ~9,000 extracted pages saves ~2 hours. Plus reduced extraction calls per page (fewer zero-conf results = less queue pressure).

---

## Files Summary

**New (1):**
- `src/services/extraction/llm_skip_gate.py` — Binary LLM page classifier

**Modified (~5):**
- `src/services/extraction/schema_orchestrator.py` — Replace SmartClassifier with skip-gate
- `src/services/extraction/pipeline.py` — Pass extraction_schema to worker
- `src/services/extraction/worker.py` — Pass schema to orchestrator
- `src/config.py` — Add skip-gate config settings
- `src/constants.py` — Update ClassificationMethod enum

**Removed (Phase 3):**
- `src/services/extraction/smart_classifier.py` → deleted

**Tests:**
- `tests/test_llm_skip_gate.py` — Skip-gate unit tests
- `tests/test_skip_gate_integration.py` — Pipeline integration tests

---

## Key Design Decisions

### Why binary skip-gate, not group-selection

Trials showed group-selection by small LLMs achieves only 34-53% recall — half of valid data groups get missed. Binary "extract or skip?" achieves 92-100% recall. The extraction pipeline's confidence scoring already handles group-level filtering effectively.

### Why intentionally permissive

The cost function is asymmetric:
- **False negative** (skip a useful page): Permanent data loss. Can only recover by re-scraping + re-extracting.
- **False positive** (extract a useless page): Wasted ~38K tokens. Confidence gating prevents bad data from reaching reports.

The skip-gate's job is to catch the **obvious** waste (holiday hours, job listings, wrong industry) while letting everything borderline through. A 70% skip precision with 93% recall is better than 90% skip precision with 70% recall.

### Why schema-agnostic

The system prompt contains zero domain-specific knowledge. All domain context comes from the `extraction_schema.field_groups` descriptions and `extraction_context`, which are auto-formatted into the user prompt. This means:

- Same code works for drivetrain companies, recipes, job listings, etc.
- No maintenance when templates change
- Template authors control classification quality via good `description` and `prompt_hint` fields

### Why a separate small model (gemma3-4B) is recommended

- Skip-gate runs **before** extraction on every page. Using the extraction model (Qwen3-30B) would serialize classification and extraction.
- gemma3-4B is small enough to stay loaded alongside the extraction model.
- 0.18s/page latency is negligible compared to extraction time.
- If only one model is available, the skip-gate can use the same model — just slower due to queue contention.

### Why no structural classification layer (Level 1 from v2)

The v2 plan included URL/title/keyword structural signals as a free classification layer. After trials, this was dropped because:

1. URL patterns are unreliable (many sites use opaque paths)
2. Title keywords are language-dependent and template-specific
3. Content keyword density requires domain-specific keyword lists (not schema-agnostic)
4. The LLM skip-gate at 0.18s/page and ~1100 tokens is cheap enough to run on all pages
5. Maintaining structural pattern lists adds complexity for marginal benefit

The existing Level 0 rule-based skip (careers, privacy, login pages) remains — these are universal patterns that work across all templates.

### Why no batch classification

Batching multiple pages per LLM call saves ~10% tokens but:
- Adds complexity (parsing multi-page responses, error handling)
- One bad page in a batch can corrupt results
- 0.18s/page is fast enough without batching
- Can be added later if queue pressure is an issue

### Why no caching of skip-gate results

Classification results are already stored on the Source model (`page_type`, `classification_confidence`, `classification_method`). The skip-gate updates these fields. On re-extraction:
- If content hasn't changed AND same template → reuse existing classification
- If content changed (re-scrape) → re-classify
- This is handled by the existing `classification_method` field — just check if it's "llm"

---

## Key Risks

| Risk | Mitigation |
|---|---|
| LLM skip-gate misses useful pages (FN) | Intentionally permissive prompt. Default to extract on any uncertainty or error. Monitor FN rate. |
| Skip-gate adds latency to every page | 0.18s/page is negligible. Can parallelize with other pre-extraction work. |
| gemma3-4B not available on all deployments | Config option: `classification_skip_gate_model`. Falls back to default LLM_MODEL. Skip-gate can be disabled entirely. |
| Different templates need different skip logic | Schema-agnostic by design. Skip logic derives from the schema, not hardcoded rules. |
| Token cost of running gate on all pages | ~1100 tokens × 12K pages = 13.2M tokens. Extraction saves: ~2500 pages × 7 groups × 5500 tokens = ~96M tokens. Net savings: ~83M tokens. |
| Schema without extraction_context | Falls back to extracting everything (conservative). Log a warning. |
| Boilerplate-stripped content is too short | Use `cleaned_content` when available, fall back to raw `content`. If both are too short (<100 chars), skip the gate and extract. |
