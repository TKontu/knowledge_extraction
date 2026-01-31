# TODO: Extraction Pipeline Optimization

## Executive Summary

Optimize the extraction pipeline to reduce LLM calls by ~65% through intelligent page classification and targeted field group extraction. This enables cost-effective processing at scale (300+ domains × 50 pages).

### Risk Profile

| Increment | Effort | Additive? | Breaking Changes |
|-----------|--------|-----------|------------------|
| 1. Foundation | ~2h | ✅ Yes | None |
| 2. Classifier | ~3h | ✅ Yes | None |
| 3. Integration | ~4h | ⚠️ No | Return type change, 6 test files |

**Key mitigation:** Two feature flags (`classification_enabled`, `classification_skip_enabled`) allow gradual rollout with instant rollback.

## Problem Statement

### Current State

| Metric | Value |
|--------|-------|
| Field groups per template | 7 (drivetrain_company_analysis) |
| Chunks per page (avg) | 2 |
| LLM calls per page | ~14 |
| Scale target | 300 domains × 50 pages = 15,000 pages |
| **Total LLM calls** | **~210,000** |

### Pain Points

1. **Wasted computation** - Extracting "products_motor" from "Contact Us" pages yields nothing
2. **KV cache pressure** - Large templates hit 100% cache utilization, causing request queuing
3. **Cost at scale** - Linear scaling of LLM calls makes large projects expensive
4. **Time at scale** - 210K calls × 2s avg = ~116 hours of LLM time

### Target State (Phase 1)

| Metric | Current | Target | Improvement |
|--------|---------|--------|-------------|
| LLM calls per page (avg) | 14 | 5 | -65% |
| Total LLM calls | 210,000 | 75,000 | -65% |
| Pages skipped (careers, news, etc.) | 0% | ~15% | New |

---

## Architecture Overview

### Current Pipeline

```
Source → Chunk → Extract ALL Field Groups → Merge → Store
                      ↓
              7 parallel extractions
              (regardless of page content)
```

### Proposed Pipeline (Phase 1)

```
Source → Classify → Select Relevant Groups → Chunk → Extract → Merge → Store
             ↓              ↓                                       ↓
        Page Type    1-4 field groups                    Store classification
        Detection    (not 7)                             result on Source
```

---

## Implementation Phases

### Phase 1: Page Classification System ✅ ACTIVE
**Priority: HIGH | Impact: -65% LLM calls**

Add a pre-extraction classification step that determines which field groups are relevant for each page.

### Phase 2: Combined Field Groups ⏸️ DEFERRED
**Reason:** Complex entity list merging, template maintenance burden. Evaluate after Phase 1 metrics.

### Phase 3: Content Deduplication ⏸️ DEFERRED
**Reason:** Need to verify actual duplicate rate first (~10% assumed). Implement after Phase 1.

### Phase 4: Adaptive Extraction Depth ⏸️ DEFERRED
**Reason:** Optimization of optimization. Low priority until Phase 1 proves value.

---

## Phase 1: Page Classification System

### Objective

Classify each page before extraction to determine which field groups are relevant, reducing unnecessary LLM calls by ~65%.

### Implementation Tasks

#### Task 1: Create PageClassifier Service

**File:** `src/services/extraction/page_classifier.py`

```python
"""Page classification for targeted field group extraction."""

import re
from dataclasses import dataclass
from enum import Enum

import structlog

logger = structlog.get_logger(__name__)


class ClassificationMethod(str, Enum):
    RULE_BASED = "rule"
    LLM_ASSISTED = "llm"  # Future
    HYBRID = "hybrid"  # Future


@dataclass
class ClassificationResult:
    """Result of page classification."""

    page_type: str  # product, service, about, contact, skip, general
    relevant_groups: list[str]  # Field group names to extract
    skip_extraction: bool  # True if page should be skipped entirely
    confidence: float  # 0.0 - 1.0
    method: ClassificationMethod
    reasoning: str | None = None


class PageClassifier:
    """Classifies pages to determine relevant extraction field groups.

    Uses URL patterns and title keywords to identify page type and select
    only the field groups likely to contain relevant information.
    """

    # URL pattern → relevant field groups mapping
    URL_PATTERNS: dict[str, list[str]] = {
        # Product pages
        r"/products?($|/)": ["products_gearbox", "products_motor", "products_accessory"],
        r"/gearbox|/gear-?box|/reducer|/gear-?reducer": ["products_gearbox", "manufacturing"],
        r"/motor|/electric-?motor|/servo|/drive": ["products_motor", "manufacturing"],
        r"/coupling|/shaft|/bearing|/brake|/clutch": ["products_accessory"],
        # Service pages
        r"/service|/repair|/maintenance|/refurbish": ["services"],
        r"/field-?service|/on-?site": ["services"],
        # Company pages
        r"/about|/company|/who-?we-?are|/history": ["company_info", "company_meta"],
        r"/contact|/location|/office|/address": ["company_info"],
        r"/certific|/quality|/iso|/standard": ["company_meta"],
        r"/facilit|/plant|/factory|/manufactur": ["company_meta", "manufacturing"],
    }

    # Patterns that indicate pages to skip entirely
    SKIP_PATTERNS: list[str] = [
        r"/career|/job|/employ|/vacanc",
        r"/news|/blog|/press|/media|/event",
        r"/privacy|/terms|/legal|/cookie|/gdpr",
        r"/login|/account|/cart|/checkout",
        r"/sitemap|/search|/404|/error",
    ]

    # Title keywords → field groups mapping
    TITLE_KEYWORDS: dict[str, list[str]] = {
        "gearbox": ["products_gearbox"],
        "gear box": ["products_gearbox"],
        "reducer": ["products_gearbox"],
        "planetary": ["products_gearbox"],
        "helical": ["products_gearbox"],
        "motor": ["products_motor"],
        "servo": ["products_motor"],
        "coupling": ["products_accessory"],
        "service": ["services"],
        "repair": ["services"],
        "maintenance": ["services"],
        "about": ["company_info"],
        "contact": ["company_info"],
        "certification": ["company_meta"],
        "iso": ["company_meta"],
    }

    def __init__(
        self,
        method: ClassificationMethod = ClassificationMethod.RULE_BASED,
        available_groups: list[str] | None = None,
    ):
        """Initialize classifier.

        Args:
            method: Classification method to use.
            available_groups: List of valid field group names. If provided,
                classification results are filtered to only include these.
        """
        self._method = method
        self._available_groups = set(available_groups) if available_groups else None

    def classify(
        self,
        url: str,
        title: str | None = None,
    ) -> ClassificationResult:
        """Classify a page and determine relevant field groups.

        Args:
            url: Page URL.
            title: Page title (optional but improves accuracy).

        Returns:
            ClassificationResult with page type and relevant groups.
        """
        if self._method == ClassificationMethod.RULE_BASED:
            result = self._classify_rule_based(url, title)
        else:
            # Future: LLM-assisted classification
            result = self._classify_rule_based(url, title)

        # Filter to available groups if specified
        if self._available_groups and result.relevant_groups:
            result.relevant_groups = [
                g for g in result.relevant_groups if g in self._available_groups
            ]

        return result

    def _classify_rule_based(
        self,
        url: str,
        title: str | None,
    ) -> ClassificationResult:
        """Rule-based classification using URL and title patterns."""
        url_lower = url.lower()

        # Check skip patterns first
        for pattern in self.SKIP_PATTERNS:
            if re.search(pattern, url_lower):
                return ClassificationResult(
                    page_type="skip",
                    relevant_groups=[],
                    skip_extraction=True,
                    confidence=0.9,
                    method=ClassificationMethod.RULE_BASED,
                    reasoning=f"URL matches skip pattern: {pattern}",
                )

        matched_groups: set[str] = set()
        confidence = 0.0
        page_type = "general"
        reasoning_parts: list[str] = []

        # URL pattern matching
        for pattern, groups in self.URL_PATTERNS.items():
            if re.search(pattern, url_lower):
                matched_groups.update(groups)
                confidence = max(confidence, 0.8)
                page_type = self._infer_page_type(groups)
                reasoning_parts.append(f"URL matches: {pattern}")

        # Title keyword matching
        if title:
            title_lower = title.lower()
            for keyword, groups in self.TITLE_KEYWORDS.items():
                if keyword in title_lower:
                    matched_groups.update(groups)
                    confidence = max(confidence, 0.7)
                    reasoning_parts.append(f"Title contains: {keyword}")

        # Default: use ALL groups with low confidence (conservative)
        # This ensures we don't miss important content on unclassified pages
        if not matched_groups:
            return ClassificationResult(
                page_type="general",
                relevant_groups=[],  # Empty means "use all groups"
                skip_extraction=False,
                confidence=0.3,
                method=ClassificationMethod.RULE_BASED,
                reasoning="No patterns matched, using all groups",
            )

        return ClassificationResult(
            page_type=page_type,
            relevant_groups=list(matched_groups),
            skip_extraction=False,
            confidence=confidence,
            method=ClassificationMethod.RULE_BASED,
            reasoning="; ".join(reasoning_parts) if reasoning_parts else None,
        )

    def _infer_page_type(self, groups: list[str]) -> str:
        """Infer page type from matched groups."""
        if any("product" in g for g in groups):
            return "product"
        if "services" in groups:
            return "service"
        if "company_info" in groups:
            return "about"
        return "general"
```

#### Task 2: Update Pipeline Call Chain

**File:** `src/services/extraction/pipeline.py`

Update `extract_source()` to pass URL and title:

```python
# In extract_source(), around line 529, change:

# BEFORE:
results = await self._orchestrator.extract_all_groups(
    source_id=source.id,
    markdown=source.content,
    source_context=context_value,
    field_groups=field_groups,
)

# AFTER:
results = await self._orchestrator.extract_all_groups(
    source_id=source.id,
    markdown=source.content,
    source_context=context_value,
    field_groups=field_groups,
    source_url=source.uri,
    source_title=source.title,
)
```

#### Task 3: Update SchemaExtractionOrchestrator

**File:** `src/services/extraction/schema_orchestrator.py`

```python
# Update extract_all_groups signature and add classification logic:

async def extract_all_groups(
    self,
    source_id: UUID,
    markdown: str,
    source_context: str,
    field_groups: list[FieldGroup],
    source_url: str | None = None,
    source_title: str | None = None,
    company_name: str | None = None,  # Deprecated
) -> tuple[list[dict], ClassificationResult | None]:
    """Extract field groups with classification-based filtering.

    Args:
        source_id: Source UUID for tracking.
        markdown: Markdown content.
        source_context: Source context (e.g., company name).
        field_groups: Field groups to extract.
        source_url: Source URL for classification.
        source_title: Source title for classification.
        company_name: DEPRECATED. Use source_context.

    Returns:
        Tuple of (extraction results, classification result or None).
    """
    context_value = source_context if source_context is not None else company_name
    classification: ClassificationResult | None = None

    if not field_groups:
        logger.error(
            "extract_all_groups_no_field_groups",
            source_id=str(source_id),
        )
        return [], None

    # Classify page if URL is available
    if source_url and settings.classification_enabled:
        available_group_names = [g.name for g in field_groups]
        classifier = PageClassifier(available_groups=available_group_names)
        classification = classifier.classify(url=source_url, title=source_title)

        logger.info(
            "page_classified",
            source_id=str(source_id),
            url=source_url,
            page_type=classification.page_type,
            relevant_groups=classification.relevant_groups,
            skip=classification.skip_extraction,
            confidence=classification.confidence,
        )

        # Only skip if skip is enabled (two-stage rollout)
        if classification.skip_extraction and settings.classification_skip_enabled:
            logger.info(
                "skipping_extraction",
                source_id=str(source_id),
                reason=classification.reasoning,
            )
            return [], classification

        # Filter field groups if classification found specific matches
        if classification.relevant_groups:
            relevant_names = set(classification.relevant_groups)
            field_groups = [g for g in field_groups if g.name in relevant_names]

            if not field_groups:
                logger.warning(
                    "no_matching_field_groups",
                    source_id=str(source_id),
                    classified_groups=classification.relevant_groups,
                )
                return [], classification

    # Continue with existing extraction logic...
    groups = field_groups
    # ... rest of method unchanged ...

    return results, classification
```

#### Task 4: Store Classification Result

**File:** `src/services/extraction/pipeline.py`

After extraction, store classification on source:

```python
# In extract_source(), after getting results:

results, classification = await self._orchestrator.extract_all_groups(...)

# Store classification result on source
if classification:
    source.page_type = classification.page_type
    source.relevant_field_groups = classification.relevant_groups
    source.classification_method = classification.method.value
    source.classification_confidence = classification.confidence
```

#### Task 5: Alembic Migration

**File:** `alembic/versions/YYYYMMDD_HHMM_add_source_classification_columns.py`

```python
"""Add classification columns to sources table.

Revision ID: <auto-generated>
Create Date: <auto-generated>
"""

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers
revision = "<auto-generated>"
down_revision = "<previous-revision>"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "sources",
        sa.Column("page_type", sa.String(50), nullable=True),
    )
    op.add_column(
        "sources",
        sa.Column(
            "relevant_field_groups",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
    )
    op.add_column(
        "sources",
        sa.Column("classification_method", sa.String(20), nullable=True),
    )
    op.add_column(
        "sources",
        sa.Column("classification_confidence", sa.Float(), nullable=True),
    )

    # Index for filtering by page type
    op.create_index(
        "ix_sources_page_type",
        "sources",
        ["project_id", "page_type"],
    )


def downgrade() -> None:
    op.drop_index("ix_sources_page_type", table_name="sources")
    op.drop_column("sources", "classification_confidence")
    op.drop_column("sources", "classification_method")
    op.drop_column("sources", "relevant_field_groups")
    op.drop_column("sources", "page_type")
```

#### Task 6: Update ORM Model

**File:** `src/orm_models.py`

Add columns to Source class:

```python
class Source(Base):
    # ... existing columns ...

    # Classification columns
    page_type: Mapped[str | None] = mapped_column(String(50), nullable=True)
    relevant_field_groups: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    classification_method: Mapped[str | None] = mapped_column(String(20), nullable=True)
    classification_confidence: Mapped[float | None] = mapped_column(Float, nullable=True)
```

#### Task 7: Add Configuration

**File:** `src/config.py`

```python
# Page Classification
classification_enabled: bool = Field(
    default=False,  # Safe default - enable after validation
    description="Enable page classification to filter field groups",
)
classification_skip_enabled: bool = Field(
    default=False,  # Two-stage rollout - enable filtering first, then skipping
    description="Enable skipping pages classified as irrelevant (careers, news, etc.)",
)
```

Update orchestrator to respect `classification_skip_enabled`:

```python
if classification.skip_extraction and settings.classification_skip_enabled:
    # Only skip if both classification says skip AND skip is enabled
    return [], classification
```

---

## Development Increments

### Increment 1: Foundation (Small) ✅ Additive
**Effort:** ~2 hours | **Risk:** None

| Task | File |
|------|------|
| Task 5 | Alembic migration |
| Task 6 | ORM model update |
| Task 7 | Config setting |

**Why grouped:** No logic changes, just schema/config. Can be merged and deployed independently. Existing code continues to work (columns are nullable).

**Validation:** `alembic upgrade head`, app starts normally.

---

### Increment 2: Classifier Service (Small) ✅ Additive
**Effort:** ~3 hours | **Risk:** None

| Task | File |
|------|------|
| Task 1 | `src/services/extraction/page_classifier.py` |
| Tests | `tests/services/extraction/test_page_classifier.py` |

**Why separate:** Self-contained module with no integration. Can be thoroughly tested in isolation before wiring into pipeline.

**Validation:** `pytest tests/services/extraction/test_page_classifier.py` passes.

---

### Increment 3: Pipeline Integration (Medium) ⚠️ Breaking Changes
**Effort:** ~4 hours | **Risk:** Medium

| Task | File |
|------|------|
| Task 2 | `src/services/extraction/pipeline.py` - pass URL/title |
| Task 3 | `src/services/extraction/schema_orchestrator.py` - signature + logic |
| Task 4 | `src/services/extraction/pipeline.py` - store classification |
| Tests | Update existing tests + new integration tests |

**Why grouped:** Tightly coupled - changing the return type requires updating the caller and handling the result together.

**Breaking change:** Return type changes from `list[dict]` to `tuple[list[dict], ClassificationResult | None]`.

**Validation:** All tests pass, manual test with `classification_enabled=False` then `=True`.

---

### Increment Dependency Graph

```
[Increment 1: Foundation] ──┐
                            ├──→ [Increment 3: Integration] ──→ Review/Merge
[Increment 2: Classifier] ──┘
```

- **Increments 1 & 2** can run in parallel (separate PRs, no conflicts)
- **Increment 3** depends on both being merged first

---

## Risk Analysis

### Additive Changes (Low Risk)

| Change | Risk | Notes |
|--------|------|-------|
| New DB columns | None | Nullable, existing code ignores them |
| New config setting | None | Default `False` for safe rollout |
| New `PageClassifier` class | None | Nothing calls it until Increment 3 |

### Breaking Changes (Medium Risk)

| Change | What Breaks | Files Affected |
|--------|-------------|----------------|
| Return type change | Callers expect `list[dict]` | 1 caller: `pipeline.py:529` |
| Mocked return values | Tests mock old return type | 6 test files (see below) |

### Test Files Requiring Updates

These files mock or assert on `extract_all_groups()` and must be updated in Increment 3:

```
tests/test_pipeline_context.py        # Mocks extract_all_groups
tests/test_schema_orchestrator.py     # Calls extract_all_groups directly
tests/test_parallel_extraction.py     # Mocks extract_all_groups
tests/test_schema_orchestrator_concurrency.py
tests/test_extraction_pipeline.py     # Mocks orchestrator
tests/test_template_compatibility.py  # Creates orchestrator
```

### Behavioral Risks

| Change | Risk | Mitigation |
|--------|------|------------|
| Skip patterns too aggressive | Valid pages skipped | Start with obvious patterns only (careers, privacy, news) |
| Group filtering too narrow | Missing extractions | Empty `relevant_groups` = use all groups (conservative) |
| Classification bugs | Silent data loss | Structured logging on every classification decision |

---

## Implementation Checklist

### Increment 1: Foundation
- [ ] Create Alembic migration for classification columns
- [ ] Update Source ORM model with new columns
- [ ] Add `classification_enabled` config setting (default: `False`)
- [ ] Add `classification_skip_enabled` config setting (default: `False`)
- [ ] Run migration, verify app starts

### Increment 2: Classifier Service
- [ ] Create `PageClassifier` service (`src/services/extraction/page_classifier.py`)
- [ ] Write unit tests for PageClassifier
- [ ] Verify tests pass in isolation

### Increment 3: Pipeline Integration
- [ ] Update `extract_source()` to pass `source.uri` and `source.title`
- [ ] Update `extract_all_groups()` signature to accept URL/title
- [ ] Add classification filtering before `asyncio.gather()` call
- [ ] Respect `classification_skip_enabled` for skip behavior
- [ ] Update return type to `tuple[list[dict], ClassificationResult | None]`
- [ ] Store classification result on Source after extraction
- [ ] Update 6 existing test files for new return type:
  - `tests/test_pipeline_context.py`
  - `tests/test_schema_orchestrator.py`
  - `tests/test_parallel_extraction.py`
  - `tests/test_schema_orchestrator_concurrency.py`
  - `tests/test_extraction_pipeline.py`
  - `tests/test_template_compatibility.py`
- [ ] Write integration tests for classification behavior
- [ ] Add structured logging for classification decisions

### Validation & Rollout
- [ ] Test with `classification_enabled=False` (no behavior change)
- [ ] Test with `classification_enabled=True` on sample project
- [ ] Measure LLM call reduction
- [ ] Review skipped pages for false positives

---

## Testing Strategy

### Unit Tests

**File:** `tests/services/extraction/test_page_classifier.py`

```python
import pytest
from services.extraction.page_classifier import (
    ClassificationMethod,
    ClassificationResult,
    PageClassifier,
)


class TestPageClassifier:
    """Tests for page classification system."""

    @pytest.fixture
    def classifier(self):
        return PageClassifier(method=ClassificationMethod.RULE_BASED)

    def test_product_page_classification(self, classifier):
        """Product URLs should map to product field groups."""
        result = classifier.classify(
            url="https://example.com/products/gearboxes/planetary",
            title="Planetary Gearboxes - Example Corp",
        )
        assert result.page_type == "product"
        assert "products_gearbox" in result.relevant_groups
        assert not result.skip_extraction
        assert result.confidence >= 0.7

    def test_service_page_classification(self, classifier):
        """Service URLs should map to services field group."""
        result = classifier.classify(
            url="https://example.com/services/repair",
            title="Repair Services",
        )
        assert "services" in result.relevant_groups
        assert not result.skip_extraction

    def test_skip_career_page(self, classifier):
        """Career pages should be skipped."""
        result = classifier.classify(
            url="https://example.com/careers/engineer",
            title="Join Our Team",
        )
        assert result.skip_extraction
        assert result.relevant_groups == []
        assert result.page_type == "skip"

    def test_skip_news_page(self, classifier):
        """News/blog pages should be skipped."""
        result = classifier.classify(
            url="https://example.com/news/2024/announcement",
            title="Company News",
        )
        assert result.skip_extraction

    def test_fallback_uses_all_groups(self, classifier):
        """Unknown pages should return empty groups (meaning use all)."""
        result = classifier.classify(
            url="https://example.com/xyz123",
            title="Some Page",
        )
        assert result.relevant_groups == []  # Empty = use all
        assert not result.skip_extraction
        assert result.confidence < 0.5

    def test_title_keyword_matching(self, classifier):
        """Title keywords should influence classification."""
        result = classifier.classify(
            url="https://example.com/page",  # Generic URL
            title="Industrial Gearbox Solutions",
        )
        assert "products_gearbox" in result.relevant_groups

    def test_available_groups_filtering(self):
        """Classification should filter to available groups."""
        classifier = PageClassifier(
            available_groups=["company_info", "services"]
        )
        result = classifier.classify(
            url="https://example.com/products/gearboxes",
            title="Gearboxes",
        )
        # products_gearbox not in available_groups, so filtered out
        assert "products_gearbox" not in result.relevant_groups

    def test_multiple_patterns_match(self, classifier):
        """Multiple matching patterns should accumulate groups."""
        result = classifier.classify(
            url="https://example.com/about/manufacturing",
            title="About Our Manufacturing",
        )
        assert "company_info" in result.relevant_groups
        assert "manufacturing" in result.relevant_groups
```

### Integration Tests

**File:** `tests/services/extraction/test_pipeline_classification.py`

```python
import pytest
from unittest.mock import AsyncMock, MagicMock

class TestPipelineClassification:
    """Integration tests for extraction pipeline with classification."""

    @pytest.fixture
    def mock_source(self):
        source = MagicMock()
        source.id = uuid4()
        source.uri = "https://example.com/careers/apply"
        source.title = "Join Our Team"
        source.content = "# Careers\n\nWe're hiring!"
        source.source_group = "Example Corp"
        return source

    async def test_skip_page_no_extraction(self, mock_source, pipeline):
        """Skipped pages should not trigger LLM extraction."""
        # Career page should be skipped
        results = await pipeline.extract_source(
            source=mock_source,
            source_context="Example Corp",
            field_groups=[...],
        )
        assert results == []
        assert mock_source.page_type == "skip"

    async def test_classification_stored_on_source(self, mock_source, pipeline):
        """Classification result should be stored on source."""
        mock_source.uri = "https://example.com/products/gearboxes"
        await pipeline.extract_source(...)
        assert mock_source.page_type == "product"
        assert mock_source.classification_confidence >= 0.7
```

---

## Success Metrics

### Primary Metrics

| Metric | Baseline | Target | Measurement |
|--------|----------|--------|-------------|
| LLM calls per page | 14 | 5 | Count API calls |
| Pages skipped | 0% | 15% | Classification skip rate |
| Field groups per page | 7 | 2.5 avg | Logging/metrics |

### Quality Metrics

| Metric | Threshold | Measurement |
|--------|-----------|-------------|
| Extraction recall | >95% | Manual review sample |
| False skip rate | <2% | Review skipped pages |

---

## Rollout Plan

### Stage 1: Deploy with Feature Flag Off
- Merge all increments with `classification_enabled=False` (default)
- Zero behavior change in production
- Verify no regressions

### Stage 2: Shadow Mode
- Set `classification_enabled=True` but add `classification_skip_enabled=False`
- Classification runs and logs decisions but does NOT skip pages
- Field group filtering active (reduced LLM calls)
- Review logs to validate classification accuracy

### Stage 3: Full Enable
- Set `classification_skip_enabled=True`
- Monitor for false skips via `page_type="skip"` in logs
- Escape hatch: `classification_enabled=False` reverts to old behavior

---

## Deferred Phases

### Phase 2: Combined Field Groups ⏸️

**Original idea:** Merge 7 groups → 4 (capabilities, company_info, products, company_meta).

**Why deferred:**
- Entity list merging (`is_entity_list: true`) is complex
- Different `entity_id_fields` per original group
- Requires new template maintenance
- Phase 1 provides most of the benefit

**Revisit when:** Phase 1 metrics show <50% reduction.

### Phase 3: Content Deduplication ⏸️

**Original idea:** Skip extraction for duplicate/similar content.

**Why deferred:**
- Need to verify actual duplicate rate first
- Assumed 10% duplicates - may be lower
- Content hashing adds complexity

**Revisit when:** Data shows >10% content duplicates across sources.

### Phase 4: Adaptive Extraction Depth ⏸️

**Original idea:** MINIMAL/STANDARD/THOROUGH modes based on page importance.

**Why deferred:**
- Optimization of an optimization
- Unclear quality impact
- Low incremental benefit (~10%)

**Revisit when:** Phase 1-3 complete and further optimization needed.

---

## Related Documents

- [Architecture Documentation](./architectureV1_1.md)
- [High Concurrency Tuning](./TODO_high_concurrency_tuning.md)
