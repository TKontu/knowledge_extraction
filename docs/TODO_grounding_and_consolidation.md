# TODO: Grounding Verification & Consolidation — Implementation Plan

**Created:** 2026-03-05
**Status:** Ready to implement
**Depends on:** Trial results in `docs/TODO_grounded_extraction.md`
**Design doc:** `docs/TODO_grounded_extraction.md`

---

## Overview

Transform 47K raw extractions (60-80% numeric hallucination, 10-26 rows per entity) into reliable consolidated records (1 row per entity, grounding-verified, provenance-tracked).

Six increments, each independently shippable and testable:

| # | Increment | New files | Changed files | Tests | Risk |
|---|-----------|-----------|---------------|-------|------|
| 1 | Grounding pure functions + string-match | 1 | 0 | ~25 | None |
| 2 | DB schema + retroactive scoring | 2 | 2 | ~10 | Low |
| 3 | LLM quote verification | 1 | 1 | ~12 | Low |
| 4 | Consolidation pure functions | 1 | 0 | ~30 | None |
| 5 | Consolidation service + DB | 2 | 2 | ~15 | Medium |
| 6 | Pipeline integration (inline grounding) | 0 | 2 | ~8 | Medium |

---

## Increment 1: Grounding Pure Functions

**Goal:** Implement string-match grounding verification as pure functions with zero dependencies. This is the foundation everything else builds on.

**Rationale:** String-match catches 83% of product spec hallucinations and 37% of employee count hallucinations for free (no LLM calls). Trials validated these numbers on real data.

### New file: `src/services/extraction/grounding.py`

```python
# ~200 lines. All pure functions, no imports beyond stdlib.

def verify_numeric_in_quote(value: int | float, quote: str) -> float:
    """Check if numeric value appears in quote text.

    Handles format variants: 1000, 1,000, 1.000, 1 000.
    Returns 1.0 if found, 0.0 if not.
    """

def verify_string_in_quote(value: str, quote: str) -> float:
    """Check if string value appears in quote (case-insensitive, normalized).

    Normalizes: case, hyphens, extra whitespace.
    Returns 1.0 for exact match, 0.5+ for fuzzy, 0.0 for no match.
    """

def verify_list_items_in_quote(items: list, quote: str) -> float:
    """Check fraction of list items grounded in quote.

    Returns fraction: 3/5 items found → 0.6.
    """

def compute_grounding_scores(
    data: dict,
    field_types: dict[str, str],
) -> dict[str, float]:
    """Score all fields in an extraction's data dict.

    Args:
        data: Extraction data (includes _quotes).
        field_types: Map of field_name → type ("integer", "string", "boolean", etc.)

    Returns:
        Dict of field_name → grounding_score (0.0-1.0).
        Fields with grounding mode "none" or "semantic" are excluded.
        Fields without quotes get 0.0.
    """

# Grounding mode defaults by field type (used when schema doesn't specify)
GROUNDING_DEFAULTS: dict[str, str] = {
    "string": "required",
    "integer": "required",
    "float": "required",
    "boolean": "semantic",  # don't string-match booleans
    "text": "none",         # descriptions are synthesized
    "enum": "required",
}
```

**Key implementation details:**

- Number format variants: `140000`, `140,000`, `140.000` (European), `140 000` (French). Already proven in trials (`/tmp/trial_grounding_v2.py`).
- String normalization: lowercase, collapse whitespace, strip hyphens. 98.8% of company names already ground with simple matching.
- `_quotes` dict is keyed by field name. Entity list items use `_quote` per item.
- Boolean fields get `grounding_mode = "semantic"` → skip string-match entirely (trials showed 35% false rejection).
- Text fields (descriptions) get `grounding_mode = "none"` → no grounding check.

### Tests: `tests/test_grounding.py` (~25 tests)

```
TestVerifyNumericInQuote:
    - test_exact_match ("140,000 employees" → 140000 → 1.0)
    - test_european_format ("30.000 colaboradores" → 30000 → 1.0)
    - test_french_format ("30 000 employés" → 30000 → 1.0)
    - test_no_match ("140-year history" → 140000 → 0.0)
    - test_partial_number ("over 500 employees" → 5000 → 0.0, not 500≠5000)
    - test_zero_value (0 → always 0.0, don't match zeros in text)
    - test_float_value ("2.9 kW" → 2.9 → 1.0)
    - test_none_value (None → 0.0)
    - test_empty_quote ("" → 0.0)

TestVerifyStringInQuote:
    - test_exact_match ("ABB" in "ABB is a leading..." → 1.0)
    - test_case_insensitive ("abb" in "ABB is a leading..." → 1.0)
    - test_hyphen_normalization ("igus" in "igus® GmbH" → 1.0)
    - test_no_match ("Siemens" in "ABB is a leading..." → 0.0)

TestVerifyListItemsInQuote:
    - test_all_found (3/3 → 1.0)
    - test_partial (2/3 → 0.67)
    - test_none_found (0/3 → 0.0)

TestComputeGroundingScores:
    - test_full_extraction_with_quotes (realistic company_info data)
    - test_missing_quotes (no _quotes → all 0.0)
    - test_boolean_skipped (manufactures_gears not scored)
    - test_text_skipped (description not scored)
    - test_entity_list_items (products with per-item _quote)
    - test_empty_data ({} → {})
```

**Verification:** Run on 12 known companies from trials. Check:
- ABB "more than 140-year history" → employee_count score 0.0
- ABB "approximately 140,000 employees" → employee_count score 1.0
- Eickhoff "267" → employee_count score 1.0
- Known hallucinated specs (0.746kW) → score 0.0

---

## Increment 2: DB Schema + Retroactive Scoring

**Goal:** Add `grounding_scores` column to extractions table. Run string-match scoring on all 47K existing extractions.

**Rationale:** Immediate value — grounding scores available for all existing data without re-extraction. Enables Increment 4 (consolidation) to use weights.

### New file: `alembic/versions/20260305_add_grounding_scores.py`

```python
def upgrade() -> None:
    op.add_column("extractions", sa.Column(
        "grounding_scores",
        postgresql.JSONB(astext_type=sa.Text()),
        nullable=True,
    ))
    # Index for filtering grounded vs ungrounded extractions
    op.create_index(
        "ix_extractions_grounding_scores",
        "extractions",
        ["grounding_scores"],
        postgresql_using="gin",
    )

def downgrade() -> None:
    op.drop_index("ix_extractions_grounding_scores")
    op.drop_column("extractions", "grounding_scores")
```

### Changed file: `src/orm_models.py`

Add to `Extraction` class:
```python
grounding_scores: Mapped[dict | None] = mapped_column(
    JSON, nullable=True
)
# e.g. {"employee_count": 1.0, "company_name": 1.0, "headquarters_location": 0.0}
```

### Changed file: `src/services/storage/repositories/extraction.py`

Add method:
```python
def update_grounding_scores(
    self, extraction_id: UUID, scores: dict[str, float]
) -> None:
    """Update grounding scores for an extraction."""

def update_grounding_scores_batch(
    self, updates: list[tuple[UUID, dict[str, float]]]
) -> int:
    """Batch update grounding scores. Returns count updated."""
```

### New file: `scripts/backfill_grounding_scores.py`

Batch job that:
1. Reads all extractions for a project (paginated, 500 at a time)
2. Loads field_types from project's extraction_schema
3. Calls `compute_grounding_scores()` for each
4. Batch-updates `grounding_scores` column
5. Prints progress + summary stats

Expected output for the 47K dataset:
```
Processed 47,201 extractions in 238 source_groups
  employee_count: 370 grounded (63%), 214 ungrounded (37%)
  power_rating_kw: 238 grounded (17%), 1148 ungrounded (83%)
  company_name: 3719 grounded (99%), 45 ungrounded (1%)
```

### Tests: `tests/test_grounding_backfill.py` (~10 tests)

```
TestExtractionRepository:
    - test_update_grounding_scores_single
    - test_update_grounding_scores_batch
    - test_grounding_scores_nullable (old extractions have None)
    - test_grounding_scores_queryable (WHERE grounding_scores->>'field' ...)

TestBackfillScript:
    - test_backfill_with_mock_data (5 extractions, verify scores computed)
    - test_backfill_pagination (>500 extractions, verify all processed)
    - test_backfill_idempotent (run twice, same result)
    - test_backfill_handles_missing_schema (project without schema → skip)
```

**Verification:** After running backfill, validate with SQL:
```sql
-- Should match trial findings: ~63% grounded for employee_count
SELECT
    CASE WHEN (grounding_scores->>'employee_count')::float > 0.5 THEN 'grounded' ELSE 'ungrounded' END as status,
    count(*)
FROM extractions
WHERE project_id = '99a19141...'
  AND extraction_type = 'company_info'
  AND data->>'employee_count' IS NOT NULL
  AND grounding_scores IS NOT NULL
GROUP BY 1;
```

---

## Increment 3: LLM Quote Verification

**Goal:** For extractions where string-match score = 0.0 but a quote exists, run LLM verification with Qwen3-30B.

**Rationale:** String-match misses multilingual quotes ("30.000 colaboradores"), paraphrases ("over a thousand"), and semantic mismatches ("35 employees" supporting emp=5000). Qwen3-30B catches these at 80% detection / 100% recall (trial-validated).

### New file: `src/services/extraction/llm_grounding.py`

```python
# ~150 lines

@dataclass(frozen=True)
class LLMGroundingResult:
    supported: bool | None   # True/False/None (error)
    reason: str
    latency: float

class LLMGroundingVerifier:
    """Verifies extraction quotes against claimed values using LLM.

    Only called for fields where:
    - grounding_mode == "required"
    - string-match score == 0.0
    - quote exists and is non-empty
    - field_type is NOT boolean (too strict, 35% false rejection)

    Uses Qwen3-30B-A3B-it-4bit (same as extraction model).
    Trial-validated: 80% detection, 100% recall for employee counts.
    100% detection for product specs (power_rating_kw).
    """

    def __init__(self, llm_client: LLMClient, model: str | None = None):
        self._llm = llm_client
        self._model = model  # None = use default

    async def verify_quote(
        self, field_name: str, value: Any, quote: str
    ) -> LLMGroundingResult:
        """Ask LLM: does this quote support this claimed value?"""

    async def verify_extraction(
        self,
        data: dict,
        grounding_scores: dict[str, float],
        field_types: dict[str, str],
    ) -> dict[str, float]:
        """Verify all unresolved fields in an extraction.

        Only verifies fields where grounding_scores[field] == 0.0
        and a non-empty quote exists. Updates and returns scores.
        """
```

### Changed file: `scripts/backfill_grounding_scores.py`

Add `--llm` flag:
```
python scripts/backfill_grounding_scores.py --project-id <id>           # string-match only
python scripts/backfill_grounding_scores.py --project-id <id> --llm     # + LLM verification
```

LLM pass only processes extractions where `grounding_scores` has 0.0 entries with quotes. Estimated: ~2000 claims × 3.2s = ~107 minutes (parallelizable via vLLM concurrent requests).

### Config addition: `src/config.py`

```python
grounding_llm_verify_enabled: bool = Field(default=True)
grounding_llm_verify_model: str = Field(default="")  # empty = use LLM_MODEL
```

### Tests: `tests/test_llm_grounding.py` (~12 tests)

```
TestLLMGroundingVerifier:
    - test_supported_numeric ("140,000 employees" → emp=140000 → True)
    - test_rejected_wrong_number ("35 employees" → emp=5000 → False)
    - test_rejected_unit_conversion ("40HP" → 29.8kW → False)
    - test_rejected_wrong_category ("€1 Billion" → emp=1000 → False)
    - test_multilingual_supported ("30 mil colaboradores" → emp=30000 → True)
    - test_llm_error_returns_none (timeout → supported=None, score unchanged)
    - test_boolean_skipped (boolean fields not sent to LLM)
    - test_only_verifies_score_zero (score=1.0 fields not re-verified)
    - test_only_verifies_with_quote (no quote → skip)

TestVerifyExtraction:
    - test_mixed_scores (some 0.0, some 1.0 → only 0.0 verified)
    - test_all_grounded (all 1.0 → no LLM calls)
    - test_updates_scores_in_place
```

**Verification:** After LLM pass, check known cases:
- ABB "more than 140-year history" → employee_count still 0.0 (correctly ungrounded)
- Atagears "yli 140 voimansiirr..." → employee_count 147 now 1.0 (Finnish, LLM understood)
- Autservice "30 mil colaboradores" → employee_count 30000 now 1.0 (Portuguese, LLM understood)

---

## Increment 4: Consolidation Pure Functions

**Goal:** Implement all 6 consolidation strategies as pure functions. Zero DB dependencies.

**Rationale:** Consolidation is the core value — turning 10-26 extractions per entity into 1 reliable record. Pure functions are fully testable without infrastructure.

### New file: `src/services/extraction/consolidation.py`

```python
# ~350 lines. All pure functions.

@dataclass(frozen=True)
class WeightedValue:
    """A value with its quality weight."""
    value: Any
    weight: float           # effective_weight = confidence * grounding_score
    source_id: str = ""     # for provenance tracking

@dataclass(frozen=True)
class ConsolidatedField:
    """Result of consolidating one field across multiple extractions."""
    value: Any
    strategy: str
    source_count: int           # how many extractions had this field
    grounded_count: int         # how many had grounding_score >= 0.5
    agreement: float            # fraction agreeing with chosen value
    top_sources: list[str]      # source IDs that contributed

@dataclass
class ConsolidatedRecord:
    """One consolidated record per (source_group, extraction_type)."""
    source_group: str
    extraction_type: str
    fields: dict[str, ConsolidatedField]


# ── Strategy functions ──
# Each takes list[WeightedValue], returns single value.

def frequency(values: list[WeightedValue]) -> Any:
    """Most frequent non-null value, case-insensitive for strings.
    Ties broken by total weight.
    For: company_name (100% accuracy in trials)."""

def weighted_frequency(values: list[WeightedValue]) -> Any:
    """Sum weights per unique value, pick highest.
    For: headquarters_location, string detail fields."""

def weighted_median(values: list[WeightedValue]) -> float | int | None:
    """Weighted median of numeric values. Excludes weight=0.
    Falls back to unweighted median if all weights are 0.
    For: employee_count, numeric fields."""

def any_true(values: list[WeightedValue], min_count: int = 2) -> bool | None:
    """True if min_count+ values are True with weight > 0.
    For: boolean fields (86% accuracy with min_count=2 in trials)."""

def longest_top_k(values: list[WeightedValue], k: int = 3) -> str | None:
    """Longest value from top-K by weight.
    For: descriptions, free text."""

def union_dedup(values: list[WeightedValue]) -> list:
    """Union all list values, deduplicate by normalized name.
    For: product lists, service lists."""


# ── Orchestrator ──

def effective_weight(
    confidence: float,
    grounding_score: float | None,
    grounding_mode: str,
) -> float:
    """Compute effective weight from confidence and grounding.

    required + score < 0.5 → 0.0 (exclude ungrounded)
    required + score >= 0.5 → confidence * grounding_score
    semantic/none → confidence only
    """

def consolidate_field(
    values: list[WeightedValue],
    strategy: str,
    **kwargs,
) -> ConsolidatedField:
    """Apply a named strategy to a list of weighted values."""

def consolidate_extractions(
    extractions: list[dict],         # list of extraction dicts (data + confidence + grounding_scores)
    field_definitions: list[dict],   # from schema: name, type, consolidation_strategy, grounding_mode
) -> ConsolidatedRecord:
    """Produce one consolidated record from N extractions.

    Pure function. No DB. No side effects.
    """
```

**Key implementation details:**

- `weighted_median`: Sort values by value, walk through cumulative weights to find the median point. Exclude weight=0 values. If all weights are 0, fall back to confidence-only weights (some data > no data).
- `frequency`: Normalize strings (lowercase, strip whitespace) for grouping, but return the original-case most-common form.
- `union_dedup`: Normalize product names (lowercase, strip series/model suffixes) for dedup. Keep the longest variant as canonical name.
- `any_true(min_count=2)`: Requires at least 2 grounded True values to return True. Single True from one page could be noise. Trial-validated at 86% accuracy.
- Strategy defaults by field type (when schema doesn't specify):

| Field type | Default strategy |
|-----------|-----------------|
| string | frequency |
| integer / float | weighted_median |
| boolean | any_true |
| text (long) | longest_top_k |
| list | union_dedup |
| enum | frequency |

### Tests: `tests/test_consolidation.py` (~30 tests)

```
TestFrequency:
    - test_clear_winner (["ABB", "ABB", "Abb Ltd"] → "ABB")
    - test_tie_broken_by_weight
    - test_case_insensitive_grouping ("ABB" == "abb" == "Abb")
    - test_single_value
    - test_all_none → None
    - test_empty_list → None

TestWeightedMedian:
    - test_single_grounded_value
    - test_multiple_with_outlier ([140000@0.9, 5000@0.0, 140000@0.85] → 140000)
    - test_excludes_zero_weight
    - test_fallback_when_all_zero_weight (uses unweighted)
    - test_integer_output (int in → int out, not float)
    - test_even_count (average of two middle values)

TestAnyTrue:
    - test_multiple_true ([True@0.9, True@0.8, False@0.7] min=2 → True)
    - test_single_true_below_min ([True@0.9, False@0.7] min=2 → None)
    - test_all_false → False
    - test_ignores_zero_weight ([True@0.0, True@0.0, False@0.9] min=2 → False)

TestLongestTopK:
    - test_picks_longest_from_top_3
    - test_single_value
    - test_respects_weight_ranking

TestUnionDedup:
    - test_merges_lists ([["A","B"], ["B","C"]] → ["A","B","C"])
    - test_normalized_dedup (["G Series", "G SERIES", "g series"] → ["G Series"])
    - test_empty_lists
    - test_preserves_order (by mention frequency)

TestEffectiveWeight:
    - test_required_grounded (conf=0.9, score=1.0 → 0.9)
    - test_required_ungrounded (conf=0.9, score=0.0 → 0.0)
    - test_semantic_no_grounding (conf=0.9, score=None → 0.9)
    - test_none_mode (conf=0.9 → 0.9)

TestConsolidateExtractions:
    - test_realistic_company_info (ABB-like data, 5 extractions)
    - test_all_ungrounded_fallback (uses best ungrounded value)
    - test_provenance_tracking (source_count, agreement populated)
    - test_empty_extractions → empty record
    - test_mixed_field_types (string + int + boolean in one record)
```

**Verification:** Run consolidation on ABB, Bonfiglioli, Flender, Igus with real extraction data (loaded as fixtures). Compare against known ground truth:

```python
GOLDEN_TRUTH = {
    "Abb": {"company_name": "ABB", "employee_count": 105000},  # within 30%: 73.5K-136.5K
    "Bonfiglioli": {"company_name": "Bonfiglioli", "employee_count": 3800},
    "Flender": {"company_name": "Flender GmbH", "employee_count": 9000},
    "Igus": {"company_name": "igus", "employee_count": 4900},
}
```

---

## Increment 5: Consolidation Service + DB

**Goal:** Wire consolidation to the database. Store consolidated records. API endpoint to trigger.

### New file: `alembic/versions/20260306_add_consolidated_extractions.py`

```python
def upgrade() -> None:
    op.create_table(
        "consolidated_extractions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True),
        sa.Column("project_id", postgresql.UUID(as_uuid=True),
                  sa.ForeignKey("projects.id", ondelete="CASCADE"), nullable=False),
        sa.Column("source_group", sa.Text(), nullable=False),
        sa.Column("extraction_type", sa.Text(), nullable=False),
        sa.Column("data", postgresql.JSONB(), nullable=False),
        sa.Column("provenance", postgresql.JSONB(), nullable=False),
        sa.Column("source_count", sa.Integer(), nullable=False),
        sa.Column("grounded_count", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now()),
        sa.UniqueConstraint("project_id", "source_group", "extraction_type",
                           name="uq_consolidated_project_sg_type"),
    )
    op.create_index("ix_consolidated_project_sg",
                    "consolidated_extractions", ["project_id", "source_group"])
```

### New file: `src/services/extraction/consolidation_service.py`

```python
# ~200 lines. DB integration layer.

class ConsolidationService:
    """Orchestrates consolidation from raw extractions to consolidated records.

    Reads extractions + grounding_scores from DB.
    Delegates to pure consolidation functions.
    Writes results to consolidated_extractions table.
    """

    def __init__(self, session: Session, project_repo: ProjectRepository):
        self._session = session
        self._project_repo = project_repo

    def consolidate_source_group(
        self,
        project_id: UUID,
        source_group: str,
    ) -> list[ConsolidatedRecord]:
        """Consolidate all extraction types for one source group.

        1. Load all extractions for (project_id, source_group)
        2. Group by extraction_type
        3. Load field definitions from project schema
        4. Call consolidate_extractions() for each type
        5. Upsert into consolidated_extractions table
        """

    def consolidate_project(
        self,
        project_id: UUID,
    ) -> dict[str, int]:
        """Consolidate all source groups in a project.

        Returns: {"source_groups": N, "records_created": M, "errors": E}
        """

    def reconsolidate(
        self,
        project_id: UUID,
        source_groups: list[str] | None = None,
    ) -> dict[str, int]:
        """Re-run consolidation (idempotent). Upserts replace old records."""
```

### Changed file: `src/orm_models.py`

Add `ConsolidatedExtraction` model:
```python
class ConsolidatedExtraction(Base):
    __tablename__ = "consolidated_extractions"
    # ... columns matching migration
```

### Changed file: `src/api/v1/projects.py`

Add endpoint:
```python
@router.post("/{project_id}/consolidate")
async def consolidate_project(project_id: UUID, source_group: str | None = None):
    """Trigger consolidation for a project (or single source_group)."""
```

### Tests: `tests/test_consolidation_service.py` (~15 tests)

```
TestConsolidationService:
    - test_consolidate_source_group (creates records in DB)
    - test_consolidate_project (all source_groups processed)
    - test_reconsolidate_idempotent (run twice → same records, updated_at changes)
    - test_upsert_replaces_old (unique constraint on project+sg+type)
    - test_handles_no_extractions (source_group with 0 extractions → skip)
    - test_handles_missing_schema (project without schema → error)
    - test_provenance_stored (check provenance JSON has expected fields)
    - test_source_count_correct
    - test_grounded_count_correct

TestConsolidateEndpoint:
    - test_trigger_consolidation_200
    - test_trigger_single_source_group
    - test_project_not_found_404
    - test_returns_summary_stats
```

**Verification:** Run consolidation on main batch project. Check invariants:
```sql
-- One record per (source_group, extraction_type)
SELECT source_group, extraction_type, count(*)
FROM consolidated_extractions
WHERE project_id = '99a19141...'
GROUP BY 1, 2
HAVING count(*) > 1;  -- should return 0 rows

-- No absurd employee counts
SELECT source_group, data->>'employee_count'
FROM consolidated_extractions
WHERE project_id = '99a19141...'
  AND extraction_type = 'company_info'
  AND (data->>'employee_count')::int > 500000;
  -- Only ABB-scale companies should appear

-- Grounded counts make sense
SELECT avg(grounded_count), avg(source_count)
FROM consolidated_extractions
WHERE project_id = '99a19141...';
```

---

## Increment 6: Pipeline Integration (Inline Grounding)

**Goal:** Score grounding inline during extraction (string-match only). New extractions get `grounding_scores` automatically.

**Rationale:** After Increments 1-5, grounding only exists as a retroactive batch job. This makes it part of the normal pipeline so new extractions are scored immediately.

### Changed file: `src/services/extraction/schema_orchestrator.py`

After extraction result is validated (existing `_validate_and_merge` flow), before storing:

```python
# After line ~440 (merged_quotes assignment), before return:
from services.extraction.grounding import compute_grounding_scores

field_types = {f.name: f.field_type for f in group.fields}
grounding_scores = compute_grounding_scores(merged, field_types)
# Store scores alongside the extraction
```

### Changed file: `src/services/storage/repositories/extraction.py`

`create()` and `create_batch()` accept optional `grounding_scores` parameter.

### Tests: `tests/test_inline_grounding.py` (~8 tests)

```
TestInlineGrounding:
    - test_extraction_gets_grounding_scores (end-to-end mock extraction)
    - test_grounded_extraction_has_score_1 (quote contains number)
    - test_ungrounded_extraction_has_score_0 (quote doesn't contain number)
    - test_no_quotes_all_zero
    - test_boolean_fields_excluded
    - test_description_fields_excluded
    - test_existing_pipeline_unchanged (grounding is additive, doesn't break anything)
    - test_scores_persisted_to_db
```

**Verification:** Run extraction on a small batch (10 pages). Check that every new extraction has `grounding_scores` populated.

---

## Implementation Order & Dependencies

```
Increment 1 ──→ Increment 2 ──→ Increment 3
(pure funcs)    (DB + backfill)  (LLM verify)
                      │
                      ▼
               Increment 4 ──→ Increment 5
               (consol funcs)   (consol service)
                                      │
                                      ▼
                               Increment 6
                               (inline)
```

- **1 → 2 → 3**: Sequential. Each builds on the previous.
- **4**: Can start in parallel with 2-3 (no DB dependency).
- **5**: Needs 2 (DB schema) + 4 (pure functions).
- **6**: Needs 1 (grounding functions) + 5 (repository changes). Ship last.

## What's NOT in This Plan

- **LLM skip-gate**: Separate TODO (`docs/TODO_classification_robustness.md`). Independent work.
- **Multilingual product dedup**: Deferred to after consolidation is live. Will be an enhancement to `union_dedup` strategy — batch LLM call during consolidation.
- **Report integration**: After Increment 5 ships, reports read from `consolidated_extractions`. Separate small change.
- **Prompt changes**: Rejected by trials (47-80% recall loss). Not implementing.
- **Schema `grounding` field declarations**: Can add later. Defaults by type cover all current templates correctly.

## Files Summary

**New (6):**
- `src/services/extraction/grounding.py` — String-match grounding (pure functions)
- `src/services/extraction/llm_grounding.py` — LLM quote verification
- `src/services/extraction/consolidation.py` — Consolidation strategies (pure functions)
- `src/services/extraction/consolidation_service.py` — DB integration layer
- `alembic/versions/20260305_add_grounding_scores.py` — Migration
- `alembic/versions/20260306_add_consolidated_extractions.py` — Migration

**Changed (~5):**
- `src/orm_models.py` — Add `grounding_scores` column, `ConsolidatedExtraction` model
- `src/services/storage/repositories/extraction.py` — Add grounding score methods
- `src/services/extraction/schema_orchestrator.py` — Inline grounding scoring
- `src/api/v1/projects.py` — Consolidation endpoint
- `src/config.py` — Grounding LLM config

**Scripts (1):**
- `scripts/backfill_grounding_scores.py` — Retroactive scoring batch job

**Tests (6):**
- `tests/test_grounding.py` — ~25 tests
- `tests/test_grounding_backfill.py` — ~10 tests
- `tests/test_llm_grounding.py` — ~12 tests
- `tests/test_consolidation.py` — ~30 tests
- `tests/test_consolidation_service.py` — ~15 tests
- `tests/test_inline_grounding.py` — ~8 tests

**Total: ~100 tests, ~1100 new lines, ~50 changed lines.**
