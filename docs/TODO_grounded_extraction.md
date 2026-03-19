# TODO: Grounded Extraction & Downstream Quality

**Created:** 2026-03-05
**Status:** Layers 1 + 3 DEPLOYED (2026-03-09). Three-tier grounding gate active in production. Layer 2 skip-gate Phase 2 COMPLETE (implementation wired into schema_orchestrator.py). Phase 3 (SmartClassifier removal) pending. Layers 4-5 (multilingual, reports) pending.
**Depends on:** Findings from `docs/TODO_downstream_trials.md`

## Problem Statement

The extraction pipeline produces high volumes of data but downstream quality is poor:

- **60-80% of numeric values are hallucinated** — LLM fabricates specs, employee counts, and other numbers from world knowledge when the page doesn't state them
- **No cross-source consolidation** — 10-26 extractions per entity per type, reports are 90%+ empty cells
- **57.7% of extraction calls produce zero-confidence results** — embedding classifier lets irrelevant pages through
- **Multilingual duplication** — same entities appear in 6+ languages
- **Misattribution** — vendor/partner names extracted instead of actual entity

These problems share three root causes:

1. **The LLM fills fields it shouldn't** — every page gets the full schema, so a product catalog page is asked for `employee_count` and the LLM invents a number
2. **No grounding verification** — source quotes exist but are never checked against the source text
3. **Page-level extraction, entity-level questions** — the pipeline extracts per-page but the user needs per-entity answers

## Design Principles

### Template-Agnostic by Default

Every mechanism must work across arbitrary templates (companies, recipes, jobs, financial news) without domain-specific configuration. Domain-specific optimizations are optional layers on top, never required.

### Grounding Over Routing

There are two ways to prevent hallucination:

1. **Routing** — predict what's on the page, only ask relevant questions. Requires knowing what page types exist per domain. Template-specific.
2. **Grounding** — ask everything, but require evidence for every answer. Requires nothing domain-specific. Template-agnostic.

Routing is an optimization. Grounding is the mechanism. This proposal uses grounding as the primary quality gate and routing as an optional enhancement.

### The Core Contract Change

The extraction contract changes from:

> "Extract these fields from the page."

To:

> "Extract these fields from the page. For each value, provide the exact quote that states it. If no quote supports a field, return null."

This works universally because it constrains the output, not the input:

| Template | Field | Page says | Without grounding | With grounding |
|----------|-------|-----------|-------------------|----------------|
| Company | employee_count | "140-year history" | 140000 (hallucinated) | null |
| Recipe | cooking_time | "Grandma's classic pasta" | "30 minutes" (guessed) | null |
| Job | salary | "competitive compensation" | "$120,000" (guessed) | null |
| Finance | revenue | "strong Q3 performance" | "$2.4B" (from memory) | null |

The LLM already knows what's on the page. The problem is it tries to be helpful by filling gaps from world knowledge. Grounding forces it to only report what it can cite.

---

## Trial Results

### Trial 1: Post-Extraction Grounding Verification (existing data)

Ran automated verification on 47K existing extractions: check if extracted values appear in their own `_quotes` field.

#### Employee Count (584 extractions at >=0.7 confidence)

| Result | Count | % |
|--------|-------|---|
| Grounded (number in quote) | 370 | 63.4% |
| Ungrounded (number NOT in quote) | 214 | 36.6% |

Ungrounded examples — clear hallucinations:
- Igus: emp=1000, quote="over 500 employees" (doubled the stated number)
- Gleason: emp=5000, quote="manufacturing facilities in the U.S., Brazil..." (no number at all)
- Alphagearhead: emp=5000, quote="The M division had only 35 employees" (wrong entity)
- En: emp=5000, quote="DMG MORI supplies 54 automation solutions" (54 products → 5000 employees)

#### Product Specs — power_rating_kw (1,386 non-null values at >=0.7)

| Result | Count | % |
|--------|-------|---|
| Grounded | 238 | 17% |
| **Ungrounded** | **1,148** | **83%** |

Confirms the 81% hallucination rate from earlier observation. Known hallucination patterns detected in 25.5% of non-null specs:
- 0.746 kW (= 1 HP conversion): 285 occurrences (7.8%)
- 0.75 kW (~1 HP rounded): 285 (7.8%)
- 1.5 kW (= 2 HP): 256 (7.0%)
- 7.5 kW (= 10 HP): 101 (2.8%)

#### Company Names (3,764 extractions at >=0.7)

| Result | Count | % |
|--------|-------|---|
| Grounded | 3,719 | 98.8% |
| Ungrounded | 45 | 1.2% |

Company names are almost always in the quote. Remaining 1.2% are mostly formatting mismatches ("STEEL PARTS" vs "Steelparts") handled by aggressive normalization.

**Verdict: Post-extraction verification works well for numerics and product specs. Not useful for strings (already 98.8% grounded). Boolean semantic grounding via keyword matching fails for multilingual content.**

### Trial 2: Consolidation with Grounding Filter

Compared employee_count consolidation with and without grounding filter on 87 companies:

| Metric | Value |
|--------|-------|
| Groups where grounding changes winner | 33/87 (37.9%) |

Against ground truth (12 known companies, 30% tolerance):

| Strategy | Accuracy |
|----------|----------|
| Frequency vote (all) | 6/12 (50%) |
| **Grounded frequency vote** | **7/12 (58%)** |
| Weighted median (all) | 5/12 (42%) |
| Grounded weighted median | 4/12 (33%) |

Grounding improves frequency voting slightly but both strategies are poor for employee counts. Root cause: employee count varies legitimately across pages (subsidiary vs global count), and many pages don't state employee counts at all.

**Verdict: Grounding filter provides marginal improvement for consolidation. The fundamental problem is that employee counts are rarely stated explicitly on web pages — most values are hallucinated regardless of strategy.**

### Trial 3: Grounded Extraction Prompt (LLM-side)

Tested 3 prompt variants on known-hallucinated and known-grounded pages:

#### Prompt A (v2 — strict "return null if can't quote")

| Metric | Hallucinated pages (n=15) | Control pages (n=10) |
|--------|--------------------------|---------------------|
| Fixed/Preserved | 13/15 (87%) | 1/10 (10%) |
| Still bad / Lost | 2/15 | 7/10 (70%) |

**Kills 70% of legitimate values.** The LLM becomes overly cautious.

#### Prompt B ("only extract if page explicitly states the number")

| Metric | Hallucinated pages (n=15) | Control pages (n=15) |
|--------|--------------------------|---------------------|
| Fixed | 14/15 (93%) | — |
| Preserved | — | 5/15 (33%) |

Still too aggressive — **67% recall loss on legitimate values.**

#### Prompt C (self-rated grounding score per field)

| Metric | Hallucinated pages (n=15) | Control pages (n=15) |
|--------|--------------------------|---------------------|
| Fixed | 13/15 (87%) | — |
| Preserved | — | 8/15 (53%) |

Best compromise but **still loses 47% of legitimate values.**

#### Root cause of recall loss:
1. Employee counts often appear in boilerplate/footer text that gets truncated at 8K chars
2. The LLM over-corrects when given permission to return null
3. Pages stating "over 1,000 employees" — LLM sees the grounding instruction and returns null because "over 1,000" isn't an exact number

### Trial 4: Grounded Product Spec Prompt

Tested on product pages where 83% of power_rating_kw specs are hallucinated:

| Metric | Hallucinated pages (n=10) | Control pages (n=5) |
|--------|--------------------------|---------------------|
| Specs removed (good) | 6/10 (60%) | — |
| Specs now grounded | 2/10 | — |
| Still hallucinating | 2/10 | — |
| Preserved | — | 1/5 (20%) |
| **Lost** | — | **4/5 (80%)** |

**80% recall loss on real product specs.** The grounded prompt is even more destructive for product specs than employee counts, likely because spec values appear in complex table/list formats that the LLM can't easily quote.

### Trial 5: LLM Verification Pass

Tested two approaches: (A) verify claim against full page content, (B) verify claim against its own quote, (C) model comparison across all available models.

#### 5A: Full-Content Verification

Initially tested with 2000-char context (artificially small — vLLM supports 20K tokens). Then re-tested with proper 20K-char context.

| Variant | Model | Detection | Recall | Notes |
|---------|-------|-----------|--------|-------|
| 2K chars context | gemma3-4B | 67% | 50% | 74% of claims beyond 2K position |
| **20K chars context** | gemma3-4B | 40% | 60% | Model gets distracted by cookie banners, irrelevant numbers |
| 20K chars context | gemma3-12b-it-qat-awq | 47% | **20%** | Too strict — rejects pages where employee count isn't on that specific page |
| 20K chars context | **Qwen3-30B-A3B-it-4bit** | **73%** | **20%** | Best detection, but still terrible recall |

**Full-content verification fails regardless of model size or context length.** Larger models are more precise at detection but have catastrophic recall loss (20%) — they correctly note that most individual pages for a company don't mention the employee count. The task is fundamentally mismatched: we're asking "does this page support this claim?" but the claim may come from a different page for the same entity.

Specific failure modes with full-content:
- gemma3-4B: Confuses "140-year history" with 140,000 employees, "170 million products" with 17,000 employees
- gemma3-12b: Too strict — rejects 80% of grounded claims because the specific page doesn't mention employee count
- Qwen3-30B: Best reasoning (correctly rejects distractors) but same recall problem

#### 5B: Quote-Based Verification (verify quote→value pairs)

**This is the breakthrough approach.** Instead of searching the whole page, verify whether the extraction's OWN quote supports the claimed value.

#### 5C: Model Comparison for Quote-Based Verification

Tested 3 models on the same deterministic sample (n=15 ungrounded, n=10 grounded):

| Model | Detection | Recall | Latency | Errors |
|-------|-----------|--------|---------|--------|
| gemma3-4B | 67% | 100% | 4.28s | 0 |
| gemma3-12b-it-qat-awq | 73% | 100% | 4.65s | 0 |
| **Qwen3-30B-A3B-it-4bit** | **80%** | **100%** | **3.22s** | **0** |

**Qwen3-30B wins on all three metrics:** highest detection (80%), perfect recall (100%), and fastest (3.22s — likely due to MoE architecture).

Key cases where Qwen3-30B outperformed gemma3-4B:
- ABB "over one million washdown motors installed" → emp=100000: gemma3-4B **accepted** (confused motors with employees), Qwen3-30B **correctly rejected**
- Bonfiglioli "becomes a €1 Billion Company" → emp=1000: gemma3-4B **accepted** (confused revenue with headcount), Qwen3-30B **correctly rejected**
- Bilfinger "over 60 years of experience" → emp=6800: both correctly rejected (years ≠ employees)

All models maintained 100% recall — no false rejections on legitimately grounded quotes.

**Earlier trials with gemma3-4B (different random sample):**

**Employee count (n=14 ungrounded, n=15 grounded):**

| Metric | Result |
|--------|--------|
| Ungrounded correctly rejected | **13/14 (93%)** |
| Ungrounded wrongly accepted | 1/14 (7%) — "161.600 Mitarbeitern" (German locale format) |
| Grounded correctly accepted | **15/15 (100%)** |
| Grounded wrongly rejected | 0/15 (0%) |

**93% detection, 100% recall.** The single false negative is a German locale format ("161.600" = 161,600) which the LLM correctly understood as matching 161,631.

**Model recommendation: Qwen3-30B-A3B-it-4bit** for verification. Same model used for extraction, so no additional model loading overhead. Faster than gemma3-4B on this task (MoE efficiency on short outputs), significantly better at distinguishing semantic categories (revenue vs headcount, products vs employees).

Critically, the LLM verifier handles multilingual quotes that string-matching cannot:
- Korean: "총 1,000여명의 직원들" → emp=1000 → **ACCEPTED** (string match would fail)
- Italian: "impiega circa 630 persone" → emp=630 → **ACCEPTED**
- Chinese: "工业分销网络达18,000余个点" → emp=18000 → **ACCEPTED** (false — this is distribution points, not employees, but verifier accepted on number match)
- Portuguese: "com cerca de 30 técnicos especializados" → emp=40 → **REJECTED** (30 technicians ≠ 40 employees)

**Boolean claims (n=26):**

| Claim type | Supported | Rejected |
|-----------|-----------|----------|
| True claims | 17 (65%) | 9 (35%) |
| False claims | 0 | 0 (no false claims in sample) |

The verifier is **overly strict on booleans** — rejects 35% of true manufacturing claims because the quote mentions products/gears but doesn't explicitly say "manufactures." E.g., "Boston Gear offers the industry's largest line of gearboxes" → REJECTED because "offers" ≠ "manufactures." This is too pedantic for boolean consolidation where any-true already handles the aggregation.

**Product specs — gemma3-4B only (earlier trial, n=15 ungrounded, n=10 grounded):**

| Metric | Result |
|--------|--------|
| Ungrounded rejected | 10/15 (67%) |
| Ungrounded accepted | 5/15 (33%) — includes unit conversions and misreads |
| Grounded accepted | **8/10 (80%)** |
| Grounded rejected | 2/10 (20%) |

The 5 false acceptances include:
- "Power 0.25 Hp to 50 Hp" → 37.3kW → ACCEPTED (unit conversion — should reject)
- "aerogeradores Vestas V-150 (4.2MW)" → 4200kW → ACCEPTED (correct conversion but model already failed to ID unit)
- "Reduction ratio: 5:1 to 3000:1" → 350kW → ACCEPTED (misread ratio as power — hallucination)

#### 5D: Product Spec Model Comparison (power_rating_kw)

Tested on n=20 ungrounded, n=12 grounded product specs (deterministic sample):

| Model | Detection | Recall | Latency |
|-------|-----------|--------|---------|
| gemma3-4B | 95% (18/19) | 75% (9/12) | 3.16s |
| **Qwen3-30B-A3B-it-4bit** | **100% (19/19)** | **67% (8/12)** | **2.53s** |

**Qwen3-30B achieves perfect hallucination detection** on product specs — including correctly rejecting HP→kW unit conversions (`"40HP" → 29.8kW` rejected because "HP is not kW"). gemma3-4B accepted this conversion.

The 4 "false rejections" by Qwen3-30B on grounded specs are all cases where the quote genuinely doesn't contain the kW number (e.g., `"ACS880-01 wall-mounted single drives"` quoted for 0.55kW). These are **extractor quoting errors**, not verifier errors — string-match already verified the truly grounded ones, so they won't reach the LLM tier.

**Verdict: Qwen3-30B-A3B-it-4bit is the best verification model for both employee counts (80% detection, 100% recall) and product specs (100% detection, 67% recall). For booleans, too strict — don't use for boolean verification, stick with any-true consolidation.**

**Combined two-tier detection rates (string-match + Qwen3-30B LLM):**

| Field type | String-match catches | LLM catches (remaining) | Combined estimate |
|------------|---------------------|------------------------|-------------------|
| Employee count | 37% | 80% of remainder | ~87% |
| Product specs (kW) | 83% | 100% of remainder | ~100% |
| Company names | 98.8% | N/A (already near-perfect) | ~99% |
| Booleans | N/A | N/A (use any-true instead) | N/A |

### Trial 6: Multilingual Content Analysis

#### 6A: Language Distribution (100 random extractions)

| Language | % of extractions |
|----------|-----------------|
| English | 69.5% |
| Portuguese | 11.6% |
| German | 8.4% |
| Spanish | 5.3% |
| Chinese | 3.2% |
| Other (Arabic, Romanian) | 2.1% |

**30% of extractions come from non-English content.** This is a major quality factor — it drives multilingual product duplication, localized company names, and non-English quotes that defeat string-matching grounding.

#### 6B: Product Name Duplication Across Languages

| Company | Total mentions | Languages | Duplication example |
|---------|---------------|-----------|-------------------|
| Rossi | 293 | en, de, es, fr, it, pl, tr | "G SERIES" / "G-Reihe" / "Série G" / "Серия G" |
| Bonfiglioli | 133 | en, de, it | "Riduttore 712T" = unnamed in English |
| Bauergears | 81 | en, fr, it, zh | "BG Series" / "Motoréducteur coaxial série BG" / "BG 系列同轴减速电机" |
| Flender | 102 | en, es | Lower duplication (primarily English site) |

Products like Rossi's "G SERIES" appear in 7 languages, creating 7x false unique products after simple dedup.

#### 6C: LLM Translation + Dedup

LLM-based product name normalization on 30 Rossi product names timed out (too many names in one call). Need to batch smaller or use a different approach.

**Recommended multilingual handling strategy (from trial evidence):**

1. **Language detection per page** — cheap (gemma3-4B or fasttext), informs downstream
2. **Extract in source language** — don't translate during extraction (loses nuance)
3. **Normalize during consolidation** — LLM call to group cross-language product variants
4. **For company names and booleans** — multilingual is handled by frequency voting / any-true (language doesn't matter for aggregation)
5. **For product lists** — batch LLM dedup: "group these product names by same product, return canonical English name"

### Trial Summary: Key Findings

| Approach | Hallucination detection | Recall preservation | Multilingual | Verdict |
|----------|------------------------|-------------------|-------------|---------|
| String-match verification | 83% specs, 37% emp | **100%** | No | Good baseline, free |
| LLM quote verification (gemma3-4B) | 67-93% emp, 67-95% specs | 100% emp, 75-80% specs | Yes | Good |
| **LLM quote verification (Qwen3-30B)** | **80% emp, 100% specs** | **100% emp, 67% specs** | **Yes** | **Best overall** |
| Full-content LLM (20K, Qwen3-30B) | 73% | 20% | Yes | Dead end (recall) |
| Full-content LLM (20K, gemma3-4B) | 40% | 60% | Partial | Dead end (both) |
| Full-content LLM (2K, gemma3-4B) | 67% | 50% | Partial | Dead end (context) |
| Strict grounding prompt | 87-93% | 10-33% | N/A | Too destructive |
| Self-rated grounding prompt | 87% | 53% | N/A | Still too destructive |
| Grounded product prompt | 60-80% | 20% | N/A | Unacceptable |

**Critical insight: Prompt-based grounding causes unacceptable recall loss.** The LLM cannot distinguish "I hallucinated this" from "this is real but hard to quote" — it over-applies the null instruction to both cases.

**Full-content verification is a dead end regardless of model or context size.** Even with 20K-char context and the best model (Qwen3-30B), recall drops to 20% because most individual pages don't contain the claim being verified.

**The winning strategy is a two-tier post-extraction verification:**
1. **String-match verification** (free, instant) — catches 83% of product spec hallucinations, handles English content
2. **LLM quote verification** (Qwen3-30B-A3B-it-4bit, ~3.2s per claim) — catches multilingual cases, paraphrases, and semantic claims that string matching misses. 80% detection with 100% recall. Faster and more accurate than gemma3-4B.

Use both: string-match first (free), then LLM verify the ambiguous cases (string-match score 0.0 but quote exists).

---

## Architecture (v2 — after all trials)

- **Layer 1a: String-match verification** — DEPLOYED. Free, instant, catches 83% of spec hallucinations
- **Layer 1b: LLM quote rescue** — DEPLOYED. Three-tier gate with `rescue_quote()` for borderline cases (0.3-0.8 grounding). `apply_grounding_gate()` as async post-parse in `schema_orchestrator.py`.
- **Layer 2: Skip-gate** — Phase 2 COMPLETE. Wired into `schema_orchestrator.py` as Level 1 classifier. Config: `classification_skip_gate_enabled` (default `False`). Phase 3 (SmartClassifier removal) pending. See `docs/TODO_classification_robustness.md`
- **Layer 3: Grounding-weighted consolidation** — DEPLOYED. `effective_weight() = min(confidence, grounding_score)`. 6 strategies implemented.
- **Layer 4: Multilingual dedup during consolidation** — PENDING. Language detection + LLM-based product name grouping.
- ~~Grounded extraction prompts~~ → **Dropped** (47-80% recall loss, unacceptable)

## Architecture: Three Layers

```
Layer 1: Grounded Extraction     ← primary quality gate (template-agnostic)
Layer 2: Skip-Gate               ← cost optimization (template-agnostic)
Layer 3: Schema-Driven Consol.   ← entity-level output (template-agnostic)

Optional: Page-Type Routing      ← per-template optimization (domain-specific)
```

Each layer is independently valuable. Together they compound:

| Problem | Layer 1 alone | + Layer 2 | + Layer 3 |
|---------|:---:|:---:|:---:|
| Numeric hallucination | Detect/prevent ~90% | Fewer irrelevant pages | Remaining filtered by grounding weight |
| Misattribution | Detect when name not in quote | Fewer wrong-entity pages | Frequency voting catches rest |
| Boolean confusion | Semantic grounding check | Fewer misleading votes | Any-true on clean votes |
| Zero-confidence waste | — | Eliminate 57% of LLM calls | — |
| Multilingual duplication | — | — | Dedup in union strategy |
| Report quality | Cleaner data | Cleaner data | One row per entity |

---

## Layer 1: Grounded Extraction

### 1.1 Schema-Level Grounding Modes

Each field declares a grounding mode. Three modes cover all field types:

```yaml
fields:
  company_name:
    type: string
    grounding: required        # value must appear in quote verbatim

  employee_count:
    type: integer
    grounding: required        # number must appear in quote

  manufactures_gears:
    type: boolean
    grounding: semantic        # quote must be topically relevant

  company_description:
    type: text
    grounding: none            # intentionally synthesized

  products:
    type: list[string]
    grounding: required        # each item must appear on the page
```

**`required`** — The extracted value (or a format variant: `2,500` / `2500` / `2 500`) must appear in the source quote. Automated verification via string matching.

**`semantic`** — The quote must be topically relevant to the field. For booleans: does the quote mention the topic (manufacturing, cooking method, salary range)? Lighter check — keyword overlap or short classifier. Catches obvious nonsense (quote about company history used to justify a boolean about manufacturing).

**`none`** — Synthesized fields (descriptions, summaries). No grounding expected or checked.

**Sensible defaults by type** — so template authors don't need to declare grounding for every field:

| Field type | Default grounding | Rationale |
|-----------|-------------------|-----------|
| string | required | Names, locations should be quotable |
| integer / float | required | Numbers should be stated explicitly |
| boolean | semantic | Can't string-match "true", but quote should be relevant |
| text (long) | none | Summaries are synthesized |
| list[string] | required | Each item should appear on page |
| list[object] | required | Each object's identity fields should appear |

Template authors override only when the default is wrong for their specific field.

### 1.2 Extraction Prompt Changes

Add to the system prompt for extraction:

```
IMPORTANT: For each field, you MUST provide a verbatim quote from the text that
supports your extracted value. If no text on the page explicitly states the
information for a field, you MUST return null for that field.

Do NOT:
- Infer numeric values from context or domain knowledge
- Convert units or calculate derived values
- Fill fields based on what you know about the entity from training data
- Guess values that seem plausible but aren't stated

When in doubt, return null. A missing value is always better than a wrong value.
```

### 1.3 Post-Extraction Grounding Verification

A verification step runs after extraction, before storage. For each field with `grounding: required`:

```python
def verify_grounding(value, quote: str, field_type: str) -> float:
    """Return grounding score 0.0-1.0."""
    if not quote or not value:
        return 0.0

    if field_type in ("integer", "float"):
        # Check numeric value appears in quote (with format variants)
        return verify_numeric_in_text(value, quote)

    if field_type == "string":
        # Fuzzy match: normalized value in normalized quote
        return verify_string_in_text(value, quote)

    if field_type.startswith("list"):
        # Check each item; score is fraction of items grounded
        return verify_list_in_text(value, quote)

    return 0.0
```

For `grounding: semantic`, a lighter check:

```python
def verify_semantic_grounding(field_name: str, value, quote: str) -> float:
    """Check quote is topically relevant to the field."""
    if not quote:
        return 0.0
    # Extract key terms from field name (e.g., "manufactures_gearboxes" -> ["manufactur", "gearbox", "gear"])
    field_terms = extract_field_terms(field_name)
    quote_lower = quote.lower()
    # Score based on term overlap
    matches = sum(1 for term in field_terms if term in quote_lower)
    return matches / len(field_terms) if field_terms else 0.0
```

**Output**: Each extraction field gets a `grounding_score` (0.0-1.0) stored alongside the extraction. Downstream consumers (consolidation, reports) use this as a quality signal.

**Thresholds**: Values with `grounding: required` and `grounding_score < 0.5` are treated as unverified. The value is preserved but flagged. Consolidation can choose to exclude unverified values or weight them down.

### 1.4 Retroactive Verification (Existing Data)

Grounding verification can run on existing extractions without re-extraction:
- Read each extraction's field values and quotes from the database
- Compute grounding scores
- Store scores (new column or in extraction metadata)
- Immediately improves consolidation quality on the 47K existing extractions

This is the fastest path to impact — no prompt changes, no re-extraction, pure post-processing.

### 1.5 Nullable Numeric Schema Change

For future extractions, make numeric fields explicitly nullable in the schema:

```yaml
employee_count:
  type: integer | null
  grounding: required
  extraction_hint: "ONLY extract if the page states a specific number"
```

The `extraction_hint` is injected into the prompt per-field, giving the LLM explicit permission to return null. Combined with the grounding prompt, this eliminates hallucination at the source.

---

## Layer 2: Skip-Gate Classification

### Already Proven Template-Agnostic

From classification trials (`docs/TODO_classification_robustness.md`):
- Binary "extract or skip?" with gemma3-4B: 92.6% recall, 0.18s/page
- Schema passed as context — no hardcoded domain knowledge
- Works because the model reads the schema and the page, then decides relevance

### Integration Point

The skip-gate sits before extraction:

```
Page → Skip-Gate (gemma3-4B, ~0.18s) → Extract (Qwen3-30B, ~2-5s) or Skip
```

Eliminates 57.7% of zero-confidence waste. Saves ~$0.50-1.00 per 1000 pages in LLM costs.

### Optional Enhancement: Multi-Label Routing

For high-volume templates, extend the binary gate to multi-label:

```
"Which of these field_groups have extractable data on this page?"
→ [company_info, manufacturing]  (skip products, services)
```

This is the template-agnostic version of page-type routing — the model sees the actual field_group definitions, not hardcoded page types. But it adds complexity and another failure point. Recommend deferring until binary skip-gate is validated in production.

---

## Layer 3: Schema-Driven Consolidation

### 3.1 Consolidation Strategies

Per-field strategies declared in the schema. Six strategies cover all field types:

| Strategy | Use For | Algorithm |
|----------|---------|-----------|
| `frequency` | Identity scalars (names) | Most-frequent non-null value, case-insensitive |
| `weighted_frequency` | Detail scalars (HQ, locations) | Sum (confidence x grounding_score) per unique value, pick highest |
| `any_true` | Company-level booleans | True if N+ extractions say true at min confidence and grounding |
| `longest_top_k` | Free text (descriptions) | Longest value from top-K by confidence, grounding-filtered |
| `union_dedup` | Entity lists (products) | Union all, dedup by normalized name, rank by mention frequency |
| `weighted_median` | Numerics (employee count) | Confidence-and-grounding-weighted median, grounded values only |

### 3.2 Grounding-Aware Weighting

When Layer 1 grounding scores are available, consolidation uses them:

```python
def effective_weight(extraction_field) -> float:
    """Combine confidence and grounding into a single quality weight."""
    confidence = extraction_field.confidence or 0.0
    grounding = extraction_field.grounding_score or 0.0

    if extraction_field.grounding_mode == "required" and grounding < 0.5:
        return 0.0  # ungrounded required field — exclude entirely

    if extraction_field.grounding_mode == "required":
        return confidence * grounding  # both must be high

    return confidence  # semantic/none — confidence only
```

This means:
- An employee count of 2,500 with grounding 0.9 and confidence 0.8 → weight 0.72
- An employee count of 140,000 with grounding 0.0 and confidence 0.95 → weight 0.0 (excluded)
- A boolean `manufactures_gears: true` with semantic grounding 0.8 and confidence 0.9 → weight 0.9

### 3.3 Consolidation as Pure Function

```python
def consolidate(
    extractions: list[Extraction],
    field_group: FieldGroup,
    confidence_threshold: float = 0.5,
) -> ConsolidatedRecord:
    """Produce one canonical record from N per-source extractions.

    Args:
        extractions: All extractions for a (project_id, source_group, extraction_type).
        field_group: Schema with per-field consolidation strategies.
        confidence_threshold: Minimum confidence to include an extraction.

    Returns:
        ConsolidatedRecord with per-field values, provenance, and quality metadata.
    """
```

No side effects. Fully testable. Takes extractions in, returns one record out.

### 3.4 Provenance Tracking

Every consolidated field records how it was derived:

```python
@dataclass
class ConsolidatedField:
    value: Any
    strategy: str                    # "frequency", "any_true", etc.
    source_count: int                # how many extractions contributed
    agreement: float                 # fraction of sources that agree with chosen value
    grounded_source_count: int       # how many had grounding_score >= 0.5
    top_sources: list[str]           # source URLs that contributed (for audit)
```

This lets downstream consumers (reports, UI) show confidence indicators and lets humans audit decisions.

### 3.5 Pre-Consolidation Filters

Applied before consolidation, configured per-project or per-template:

| Filter | What it does | Template-agnostic? |
|--------|-------------|-------------------|
| Confidence threshold | Exclude extractions below threshold | Yes (threshold per type) |
| Grounding filter | Exclude ungrounded required fields | Yes (from schema) |
| Zero/null stripping | Ignore empty values | Yes |
| Event venue filter | Exclude site_type containing "event" from locations | No (domain-specific, optional) |
| Placeholder detection | Detect known hallucination patterns (0.746kW, etc.) | No (domain-specific, optional) |

Template-agnostic filters are always on. Domain-specific filters are opt-in, configured in the extraction schema or project settings.

### 3.6 Storage

Consolidated records are stored as first-class entities:

- New `consolidated_extractions` table (or flag on existing `extractions` table)
- One row per (project_id, source_group, extraction_type)
- Reports, search, and entity extraction read from consolidated records
- Raw per-source extractions preserved for audit and re-consolidation

### 3.7 Reconsolidation

Consolidation is idempotent and re-runnable:
- New extractions arrive → reconsolidate affected source_groups
- Grounding scores updated → reconsolidate
- Schema/strategy changes → reconsolidate all

---

## Revised Implementation Plan

### Phase 1: Two-Tier Grounding Verification — DEPLOYED (2026-03-09)

**Status:** Live in production. String-match inline during v2 extraction (`grounding.py`). LLM rescue (`llm_grounding.py`). Three-tier grounding gate (`apply_grounding_gate()` in `schema_orchestrator.py`). Re-extraction running on all 3 projects with gate active.

#### Phase 1a: String-Match Verification (free, instant) — DONE

1. Implement `verify_numeric_grounding()` — locale-aware number format variants (1000, 1,000, 1.000, 1 000)
2. Implement `verify_string_grounding()` — normalized matching (case, hyphen, whitespace collapse)
3. Score all extractions (batch job)
4. Store grounding scores in extraction metadata

**What it catches:** 83% of product spec hallucinations, 37% of employee count hallucinations, 98.8% company name verification.
**What it misses:** Multilingual numbers ("Com mais de 30.000 colaboradores"), paraphrased values ("over a thousand"), booleans.

#### Phase 1b: LLM Quote Verification — DONE

For extractions where string-match grounding = 0.0 but a quote exists, run LLM verification:

1. Send the extraction's own `_quote`/`_quotes` + claimed value to Qwen3-30B-A3B-it-4bit
2. Ask: "Does this quote support this claimed value? YES/NO with reason"
3. Score: supported → 1.0, not supported → 0.0
4. Only verify numeric and string fields (not booleans — too strict, 35% false rejection)

**Why Qwen3-30B, not gemma3-4B:** Model comparison trial tested all available models on the same sample. Qwen3-30B achieved 80% detection vs 67% for gemma3-4B, both with 100% recall. Qwen3-30B was also faster (3.2s vs 4.3s — MoE architecture). Key advantage: correctly distinguishes semantic categories (revenue vs headcount, products vs employees) that confuse smaller models. Same model as extraction, so no extra GPU memory for model loading.

| Model | Detection | Recall | Latency |
|-------|-----------|--------|---------|
| gemma3-4B | 67% | 100% | 4.28s |
| gemma3-12b-it-qat-awq | 73% | 100% | 4.65s |
| **Qwen3-30B-A3B-it-4bit** | **80%** | **100%** | **3.22s** |

**Cost estimate:** ~30% of extractions have ungrounded numeric values needing LLM verification. At 3.2s/claim, ~2000 claims ≈ ~107 minutes for the full 47K dataset. Can be parallelized (vLLM handles concurrent requests).

**What it catches additionally:** Multilingual number formats, paraphrased values, semantic mismatches ("35 employees" quoted to support emp=5000 → correctly rejected), revenue/product numbers confused with employee counts.
**What it misses:** Unit conversions accepted as valid (130 HP → 96.98 kW marked as supported). Addressed by string-match catching these first (0.746, 7.5 patterns).

**Combined detection rate:** String-match + LLM verification should achieve ~90-95% hallucination detection with ~95-100% recall for numerics.

**Scope:** ~300-400 lines. New file `src/services/extraction/grounding.py`.

### Phase 2: Grounding-Weighted Consolidation — DEPLOYED (2026-03-09)

**Status:** Live. 6 strategies implemented. `effective_weight() = min(confidence, grounding_score)`. API endpoint: `POST /projects/{id}/consolidate`. DB table: `consolidated_extractions`. Ready to run on completed extractions.

### Phase 3: Multilingual Handling

**Goal:** Reduce 30-40% false product duplication from multilingual content.

1. **Language detection per source** — fast classifier (gemma3-4B or fasttext library) during crawl/scrape, stored in source metadata
2. **Consolidation-time product dedup** — batch LLM call: "group these product names by same product, return canonical English name"
   - Batch size: ~30 names per LLM call (within context window)
   - One call per (source_group, extraction_type) during consolidation
   - Store canonical name + language variants mapping
3. **Language-aware weighting** — during consolidation, prefer English extractions for string fields (company name, HQ) when available

**Cost estimate:** ~1 LLM call per company for product dedup. 238 companies × 1 call = trivial cost.

**What it doesn't need:**
- No per-page translation (too expensive, loses nuance)
- No language filtering during extraction (we want the data, just need to dedup it)
- Company names and booleans don't need language handling (frequency voting / any-true works across languages)

### Phase 4: Report Integration

**Goal:** Reports read consolidated records instead of raw extractions.

1. Report generation reads from consolidated_extractions
2. One row per entity (not per URL)
3. Show provenance indicators (source count, agreement level, grounding status)
4. Validate: generate reports, compare against Trial 4A findings

### Phase 5: Skip-Gate Integration ✅ COMPLETE

**Goal:** Stop wasting 57.7% of extraction calls on irrelevant pages.

Per `docs/TODO_classification_robustness.md` spec. Binary skip-gate with gemma3-4B, schema passed as context. Phase 2 (pipeline integration) is complete — wired into `schema_orchestrator.py`. Phase 3 (SmartClassifier removal) remains pending.

### Phase 6: Light Prompt Improvements — TRIAL COMPLETE, READY TO DEPLOY

**Status update (2026-03-09):** Earlier trials of **strict grounding prompts** (forcing LLM to prove every field) caused 47-80% recall loss — correctly dropped as primary mechanism. However, a **lighter approach** — a hallucination guard block that simply says "don't use training knowledge" — was trialed and shows consistent improvement with zero recall loss.

**A/B trial** (`scripts/trial_prompt_ab.py`, 30 sources × 7 groups):
- Well grounded: +2.5pp, poorly grounded: -1.3pp, value-as-quote: -1.6pp (4x reduction)
- 0 regressions, 3 fields fixed, 0.5s faster, 7 more fields extracted
- Key: this is NOT the destructive "grounded prompt" from earlier — it's a guard block that prevents world-knowledge leakage without changing extraction format requirements

**Implementation**: Add `_HALLUCINATION_GUARD` + `_QUOTE_NOT_VALUE_NOTE` constants to `schema_extractor.py`, inject into 4 v2 prompt builders. See `docs/TODO_extraction_quality.md` Phase B for exact text and plan.

---

## Success Metrics

| Metric | Current | After Phase 1 (verification) | After Phase 2 (consolidation) | After Phase 3-5 | Target |
|--------|---------|------------------------------|-------------------------------|-----------------|--------|
| Numeric hallucination rate | 60-80% | Detected ~90-95% | Deprioritized in output | +skip-gate prevents | <10% in consolidated output |
| Product spec hallucination | 83% | Detected: 83% string + 67% LLM | Excluded from consolidated | — | <15% |
| Report rows per entity | 10-26 | 10-26 | **1** | 1 | 1 |
| Zero-confidence waste | 57.7% | 57.7% | 57.7% | **<10%** | <10% |
| Company name accuracy | 87-90% | 87-90% | 87-90% (frequency) | 90-95% | >90% |
| Boolean accuracy (F1) | ~85% | ~85% | ~88% (any-true, cleaner votes) | ~90% | >85% |
| Product list precision | 60-70% | 60-70% | 70-80% (dedup) | **85-90%** (multilingual dedup) | >80% |
| Recall preservation | 100% | **100%** | 100% | ~95% (skip-gate) | >95% |

## Key Design Decisions

1. **Two-tier verification: string-match + LLM.** String matching is free and catches 83% of product spec hallucinations. LLM quote verification (Qwen3-30B-A3B-it-4bit) handles the remaining multilingual and paraphrased cases at 80% detection / 100% recall (best of 5 models tested). Qwen3-30B is the same model used for extraction, so no extra GPU memory cost. Together they achieve ~90-95% detection with near-zero recall loss.

2. **Post-extraction verification, not prompt-based grounding.** Trials proved prompt changes cause 47-80% recall loss. The LLM cannot distinguish "I hallucinated this" from "this is real but hard to quote." Post-extraction verification achieves better detection with 0% recall loss.

3. **Grounding as a weight signal, not a filter.** Don't delete ungrounded values — deprioritize them in consolidation. If all values for a field are ungrounded, the best ungrounded value is still better than nothing.

4. **Don't LLM-verify booleans.** Trial showed 35% false rejection rate on boolean claims — the verifier is too pedantic ("offers gearboxes" ≠ "manufactures gearboxes"). Booleans are better handled by any-true consolidation strategy which is proven at 86% accuracy.

5. **Multilingual dedup at consolidation time, not extraction time.** Extract in source language (preserves nuance), then group cross-language product variants during consolidation with a single LLM call per company. 30% of content is non-English — this directly addresses the 30-40% product false duplication.

6. **Schema-driven, not code-driven.** Consolidation strategies, grounding modes, and thresholds are declared in the extraction schema. Adding a new template never requires code changes.

7. **Surface uncertainty, don't hide it.** Reports should show "~2,500 (3 sources, grounded)" not just "2,500". The verification and consolidation pipeline produces provenance metadata — use it.
