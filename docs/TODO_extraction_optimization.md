# TODO: Extraction Pipeline Optimization

## Executive Summary

Optimize the extraction pipeline to reduce LLM calls by ~80% through intelligent page classification, targeted field group extraction, and content deduplication. This enables cost-effective processing at scale (300+ domains × 50 pages).

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

### Target State

| Metric | Current | Target | Improvement |
|--------|---------|--------|-------------|
| LLM calls per page (avg) | 14 | 3 | -78% |
| Total LLM calls | 210,000 | 45,000 | -78% |
| Estimated processing time | ~116 hours | ~25 hours | -78% |

---

## Architecture Overview

### Current Pipeline

```
Source → Chunk → Extract ALL Field Groups → Merge → Store
                      ↓
              7 parallel extractions
              (regardless of page content)
```

### Proposed Pipeline

```
Source → Classify → Select Relevant Groups → Chunk → Extract → Merge → Store
             ↓              ↓
        Page Type    1-3 field groups
        Detection    (not 7)
```

---

## Implementation Phases

### Phase 1: Page Classification System
**Priority: HIGH | Impact: -65% LLM calls**

Add a pre-extraction classification step that determines which field groups are relevant for each page.

### Phase 2: Combined Field Groups
**Priority: MEDIUM | Impact: -30% additional**

Merge related simple field groups to reduce extraction calls.

### Phase 3: Content Deduplication
**Priority: MEDIUM | Impact: -15% additional**

Skip extraction for duplicate/similar content across pages.

### Phase 4: Adaptive Extraction Depth
**Priority: LOW | Impact: -10% additional**

Vary extraction thoroughness based on page importance.

---

## Phase 1: Page Classification System

### Objective

Classify each page before extraction to determine which field groups are relevant, reducing unnecessary LLM calls by ~65%.

### Classification Strategies

#### Strategy A: Rule-Based Classification (Recommended First)

**Pros:** Zero LLM calls, instant, predictable
**Cons:** Less accurate for edge cases

```python
class PageClassifier:
    """Rule-based page classification using URL and title patterns."""

    # URL pattern → relevant field groups mapping
    URL_PATTERNS = {
        r'/products?/': ['products_gearbox', 'products_motor', 'products_accessory'],
        r'/gearbox|/reducer|/gear': ['products_gearbox', 'manufacturing'],
        r'/motor|/drive': ['products_motor', 'manufacturing'],
        r'/service|/repair|/maintenance': ['services'],
        r'/about|/company|/who-we-are': ['company_info', 'company_meta'],
        r'/contact|/location': ['company_info'],
        r'/career|/jobs': [],  # Skip - not relevant
        r'/news|/blog|/press': [],  # Skip - usually not useful
        r'/certific|/quality|/iso': ['company_meta'],
    }

    # Title keywords → field groups mapping
    TITLE_KEYWORDS = {
        'gearbox': ['products_gearbox'],
        'reducer': ['products_gearbox'],
        'motor': ['products_motor'],
        'service': ['services'],
        'repair': ['services'],
        # ... etc
    }

    def classify(self, url: str, title: str, headers: list[str]) -> ClassificationResult:
        """Classify page and return relevant field groups."""
        ...
```

#### Strategy B: LLM-Assisted Classification (Future Enhancement)

**Pros:** More accurate, handles edge cases
**Cons:** 1 additional LLM call per page (but much smaller/faster)

```python
CLASSIFICATION_PROMPT = """
Classify this page for a drivetrain company analysis.

URL: {url}
Title: {title}
First 500 chars: {content_preview}

Which categories apply? (comma-separated)
- products_gearbox: Gearbox/reducer product pages
- products_motor: Motor product pages
- products_accessory: Coupling, shaft, bearing pages
- manufacturing: Manufacturing capabilities
- services: Repair/maintenance services
- company_info: About, history, leadership
- company_meta: Certifications, locations
- skip: Not relevant (careers, news, legal)

Categories:
"""
```

### Data Model Changes

```python
# New: Page classification result stored with source
class Source:
    # ... existing fields ...

    # New fields for classification
    page_type: str | None  # e.g., "product", "about", "service"
    relevant_field_groups: list[str] | None  # Groups to extract
    classification_method: str | None  # "rule" or "llm"
    classification_confidence: float | None
```

### New Components

#### 1. PageClassifier Service

**Location:** `src/services/extraction/page_classifier.py`

```python
from dataclasses import dataclass
from enum import Enum

class ClassificationMethod(str, Enum):
    RULE_BASED = "rule"
    LLM_ASSISTED = "llm"
    HYBRID = "hybrid"

@dataclass
class ClassificationResult:
    """Result of page classification."""
    page_type: str  # product, service, about, contact, other
    relevant_groups: list[str]  # Field groups to extract
    skip_extraction: bool  # True if page should be skipped entirely
    confidence: float  # 0.0 - 1.0
    method: ClassificationMethod
    reasoning: str | None = None  # For debugging

class PageClassifier:
    """Classifies pages to determine relevant extraction field groups."""

    def __init__(
        self,
        method: ClassificationMethod = ClassificationMethod.RULE_BASED,
        llm_client: LLMClient | None = None,
    ):
        self._method = method
        self._llm_client = llm_client
        self._url_patterns = self._load_url_patterns()
        self._title_keywords = self._load_title_keywords()

    def classify(
        self,
        url: str,
        title: str | None,
        content_preview: str | None = None,
        headers: list[str] | None = None,
    ) -> ClassificationResult:
        """Classify a page and determine relevant field groups."""

        if self._method == ClassificationMethod.RULE_BASED:
            return self._classify_rule_based(url, title, headers)
        elif self._method == ClassificationMethod.LLM_ASSISTED:
            return self._classify_llm(url, title, content_preview)
        else:  # HYBRID
            rule_result = self._classify_rule_based(url, title, headers)
            if rule_result.confidence < 0.7:
                return self._classify_llm(url, title, content_preview)
            return rule_result

    def _classify_rule_based(
        self, url: str, title: str | None, headers: list[str] | None
    ) -> ClassificationResult:
        """Rule-based classification using URL and title patterns."""
        matched_groups = set()
        confidence = 0.0
        page_type = "other"

        # URL pattern matching
        url_lower = url.lower()
        for pattern, groups in self._url_patterns.items():
            if re.search(pattern, url_lower):
                matched_groups.update(groups)
                confidence = max(confidence, 0.8)
                page_type = self._infer_page_type(groups)

        # Title keyword matching
        if title:
            title_lower = title.lower()
            for keyword, groups in self._title_keywords.items():
                if keyword in title_lower:
                    matched_groups.update(groups)
                    confidence = max(confidence, 0.7)

        # Skip patterns (careers, news, legal)
        skip_patterns = [r'/career', r'/job', r'/news', r'/blog', r'/press',
                        r'/privacy', r'/terms', r'/legal', r'/cookie']
        if any(re.search(p, url_lower) for p in skip_patterns):
            return ClassificationResult(
                page_type="skip",
                relevant_groups=[],
                skip_extraction=True,
                confidence=0.9,
                method=ClassificationMethod.RULE_BASED,
            )

        # Default: extract core groups if no specific match
        if not matched_groups:
            matched_groups = {'company_info', 'manufacturing', 'services'}
            confidence = 0.5
            page_type = "general"

        return ClassificationResult(
            page_type=page_type,
            relevant_groups=list(matched_groups),
            skip_extraction=False,
            confidence=confidence,
            method=ClassificationMethod.RULE_BASED,
        )
```

#### 2. Classification Configuration

**Location:** `src/services/extraction/classification_config.py`

```python
"""Classification rules configuration.

This module defines the URL patterns and keywords used for rule-based
page classification. Rules can be customized per template.
"""

# Default rules for drivetrain_company templates
DRIVETRAIN_URL_PATTERNS = {
    # Product pages
    r'/products?($|/)': ['products_gearbox', 'products_motor', 'products_accessory'],
    r'/gearbox|/gear-?box|/reducer|/gear-?reducer': ['products_gearbox', 'manufacturing'],
    r'/motor|/electric-?motor|/servo|/drive': ['products_motor', 'manufacturing'],
    r'/coupling|/shaft|/bearing|/brake|/clutch': ['products_accessory'],

    # Service pages
    r'/service|/repair|/maintenance|/refurbish': ['services'],
    r'/field-?service|/on-?site': ['services'],

    # Company pages
    r'/about|/company|/who-?we-?are|/history': ['company_info', 'company_meta'],
    r'/contact|/location|/office|/address': ['company_info'],
    r'/certific|/quality|/iso|/standard': ['company_meta'],
    r'/facilit|/plant|/factory|/manufactur': ['company_meta', 'manufacturing'],

    # Skip pages (low value for extraction)
    r'/career|/job|/employ|/vacanc': [],
    r'/news|/blog|/press|/media|/event': [],
    r'/privacy|/terms|/legal|/cookie|/gdpr': [],
    r'/login|/account|/cart|/checkout': [],
    r'/sitemap|/search|/404|/error': [],
}

DRIVETRAIN_TITLE_KEYWORDS = {
    'gearbox': ['products_gearbox'],
    'gear box': ['products_gearbox'],
    'reducer': ['products_gearbox'],
    'gear reducer': ['products_gearbox'],
    'planetary': ['products_gearbox'],
    'helical': ['products_gearbox'],
    'worm gear': ['products_gearbox'],
    'motor': ['products_motor'],
    'servo': ['products_motor'],
    'drive': ['products_motor'],
    'coupling': ['products_accessory'],
    'service': ['services'],
    'repair': ['services'],
    'maintenance': ['services'],
    'about': ['company_info'],
    'contact': ['company_info'],
    'certification': ['company_meta'],
    'iso': ['company_meta'],
}

# Template-specific rule sets
TEMPLATE_RULES = {
    'drivetrain_company_analysis': {
        'url_patterns': DRIVETRAIN_URL_PATTERNS,
        'title_keywords': DRIVETRAIN_TITLE_KEYWORDS,
    },
    'drivetrain_company_simple': {
        'url_patterns': DRIVETRAIN_URL_PATTERNS,
        'title_keywords': DRIVETRAIN_TITLE_KEYWORDS,
    },
    # Add more templates as needed
}
```

### Pipeline Integration

#### Modified SchemaExtractionOrchestrator

```python
# src/services/extraction/schema_orchestrator.py

class SchemaExtractionOrchestrator:
    def __init__(
        self,
        schema_extractor: SchemaExtractor,
        page_classifier: PageClassifier | None = None,  # NEW
        context: ExtractionContext | None = None,
    ):
        self._extractor = schema_extractor
        self._classifier = page_classifier or PageClassifier()  # NEW
        self._context = context or ExtractionContext()

    async def extract_all_groups(
        self,
        source_id: UUID,
        markdown: str,
        source_context: str,
        field_groups: list[FieldGroup],
        source_url: str | None = None,  # NEW
        source_title: str | None = None,  # NEW
        use_classification: bool = True,  # NEW
    ) -> list[dict]:
        """Extract field groups with optional classification-based filtering."""

        # NEW: Classify page to determine relevant groups
        if use_classification and source_url:
            classification = self._classifier.classify(
                url=source_url,
                title=source_title,
                content_preview=markdown[:1000] if markdown else None,
            )

            logger.info(
                "page_classified",
                source_id=str(source_id),
                page_type=classification.page_type,
                relevant_groups=classification.relevant_groups,
                skip=classification.skip_extraction,
                confidence=classification.confidence,
            )

            if classification.skip_extraction:
                logger.info(
                    "skipping_extraction",
                    source_id=str(source_id),
                    reason="page_classified_as_skip",
                )
                return []

            # Filter field groups to only relevant ones
            relevant_names = set(classification.relevant_groups)
            field_groups = [g for g in field_groups if g.name in relevant_names]

            if not field_groups:
                logger.warning(
                    "no_relevant_field_groups",
                    source_id=str(source_id),
                    classification=classification.relevant_groups,
                )
                return []

        # ... rest of existing extraction logic ...
```

### Database Migration

```sql
-- Migration: Add classification columns to sources table

ALTER TABLE sources
ADD COLUMN page_type VARCHAR(50),
ADD COLUMN relevant_field_groups JSONB,
ADD COLUMN classification_method VARCHAR(20),
ADD COLUMN classification_confidence FLOAT;

-- Index for filtering by page type
CREATE INDEX ix_sources_page_type ON sources(project_id, page_type);
```

### Configuration

```python
# config.py additions

# Page Classification
CLASSIFICATION_ENABLED: bool = True
CLASSIFICATION_METHOD: str = "rule"  # "rule", "llm", "hybrid"
CLASSIFICATION_MIN_CONFIDENCE: float = 0.5  # Below this, use default groups
```

### Testing Strategy

```python
# tests/test_page_classifier.py

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

    def test_service_page_classification(self, classifier):
        """Service URLs should map to services field group."""
        result = classifier.classify(
            url="https://example.com/services/repair",
            title="Repair Services",
        )
        assert "services" in result.relevant_groups

    def test_skip_page_classification(self, classifier):
        """Career/news pages should be skipped."""
        result = classifier.classify(
            url="https://example.com/careers/engineer",
            title="Join Our Team",
        )
        assert result.skip_extraction
        assert result.relevant_groups == []

    def test_fallback_classification(self, classifier):
        """Unknown pages should get default groups."""
        result = classifier.classify(
            url="https://example.com/xyz123",
            title="Some Page",
        )
        assert result.confidence < 0.7
        assert len(result.relevant_groups) > 0  # Has defaults
```

### Metrics & Monitoring

```python
# New metrics to track classification effectiveness

classification_total = Counter(
    "extraction_classification_total",
    "Total pages classified",
    ["method", "page_type"]
)

classification_groups_selected = Histogram(
    "extraction_classification_groups_selected",
    "Number of field groups selected per page",
    buckets=[0, 1, 2, 3, 4, 5, 6, 7]
)

extraction_skipped_total = Counter(
    "extraction_skipped_total",
    "Pages skipped due to classification",
    ["reason"]
)
```

---

## Phase 2: Combined Field Groups

### Objective

Reduce the number of separate LLM calls by combining related field groups into unified extraction calls.

### Combination Strategy

#### Before: 7 Field Groups

```
1. manufacturing (4 fields, mostly boolean)
2. services (6 fields, mostly boolean)
3. company_info (5 fields, mixed)
4. products_gearbox (8 fields, entity list)
5. products_motor (7 fields, entity list)
6. products_accessory (4 fields, entity list)
7. company_meta (2 fields, lists)
```

#### After: 4 Combined Groups

```
1. capabilities (manufacturing + services) - 10 boolean fields
2. company_info (unchanged) - 5 mixed fields
3. products (gearbox + motor + accessory) - 19 fields, unified entity list
4. company_meta (unchanged) - 2 list fields
```

### Implementation

#### Template Modification

Create optimized versions of templates with combined groups:

```yaml
# templates/drivetrain_company_optimized.yaml

name: drivetrain_company_optimized
description: Optimized extraction with combined field groups

field_groups:
  - name: capabilities
    description: Manufacturing and service capabilities
    fields:
      # From manufacturing
      - name: manufactures_gearboxes
        type: boolean
        required: true
      - name: manufactures_motors
        type: boolean
        required: true
      - name: manufactures_drivetrain_accessories
        type: boolean
        required: true
      - name: manufacturing_details
        type: text
      # From services
      - name: provides_services
        type: boolean
        required: true
      - name: services_gearboxes
        type: boolean
        required: true
      - name: services_motors
        type: boolean
        required: true
      - name: services_drivetrain_accessories
        type: boolean
        required: true
      - name: provides_field_service
        type: boolean
        required: true
      - name: service_types
        type: list

  - name: company_info
    # ... unchanged ...

  - name: products
    description: All product types (gearboxes, motors, accessories)
    is_entity_list: true
    fields:
      - name: product_name
        type: text
        required: true
      - name: product_category
        type: enum
        enum_values: [gearbox, motor, accessory]
        required: true
      - name: series_name
        type: text
      - name: model_number
        type: text
      - name: subcategory
        type: text
      # Gearbox-specific
      - name: gear_ratio
        type: text
      - name: gear_efficiency_percent
        type: float
      # Motor-specific
      - name: speed_rating_rpm
        type: float
      - name: voltage
        type: text
      # Common specs
      - name: power_rating_kw
        type: float
      - name: torque_rating_nm
        type: float

  - name: company_meta
    # ... unchanged ...
```

#### Migration Path

1. Create optimized templates alongside existing ones
2. Add `optimized: true` flag to project config
3. Gradually migrate projects to optimized templates
4. Keep original templates for backward compatibility

---

## Phase 3: Content Deduplication

### Objective

Skip extraction for pages with content identical or highly similar to already-processed pages.

### Implementation

#### Content Hashing

```python
# src/services/extraction/content_dedup.py

import hashlib
from dataclasses import dataclass

@dataclass
class ContentHash:
    """Hash of page content for deduplication."""
    full_hash: str  # SHA-256 of full content
    structure_hash: str  # Hash of headers/structure only

class ContentDeduplicator:
    """Deduplicates pages based on content similarity."""

    def __init__(self, extraction_repo: ExtractionRepository):
        self._extraction_repo = extraction_repo
        self._seen_hashes: dict[str, UUID] = {}  # hash -> first source_id

    def compute_hash(self, content: str) -> ContentHash:
        """Compute content hashes for deduplication."""
        # Full content hash
        normalized = self._normalize(content)
        full_hash = hashlib.sha256(normalized.encode()).hexdigest()

        # Structure hash (headers only)
        headers = self._extract_headers(content)
        structure_hash = hashlib.sha256(headers.encode()).hexdigest()

        return ContentHash(full_hash=full_hash, structure_hash=structure_hash)

    def check_duplicate(
        self,
        project_id: UUID,
        content_hash: ContentHash
    ) -> tuple[bool, UUID | None]:
        """Check if content has been seen before.

        Returns:
            (is_duplicate, original_source_id)
        """
        cache_key = f"{project_id}:{content_hash.full_hash}"

        if cache_key in self._seen_hashes:
            return True, self._seen_hashes[cache_key]

        # Check database for existing extraction with same hash
        existing = self._extraction_repo.find_by_content_hash(
            project_id, content_hash.full_hash
        )

        if existing:
            self._seen_hashes[cache_key] = existing.source_id
            return True, existing.source_id

        return False, None

    def register(self, project_id: UUID, content_hash: ContentHash, source_id: UUID):
        """Register a processed content hash."""
        cache_key = f"{project_id}:{content_hash.full_hash}"
        self._seen_hashes[cache_key] = source_id

    def _normalize(self, content: str) -> str:
        """Normalize content for consistent hashing."""
        # Remove extra whitespace
        content = ' '.join(content.split())
        # Remove common boilerplate patterns
        content = re.sub(r'©.*?\d{4}', '', content)  # Copyright
        content = re.sub(r'All rights reserved\.?', '', content, flags=re.I)
        return content.strip()

    def _extract_headers(self, content: str) -> str:
        """Extract headers for structure comparison."""
        headers = re.findall(r'^#+\s+(.+)$', content, re.MULTILINE)
        return '\n'.join(headers)
```

#### Database Changes

```sql
-- Add content hash columns
ALTER TABLE sources ADD COLUMN content_hash VARCHAR(64);
ALTER TABLE sources ADD COLUMN structure_hash VARCHAR(64);

-- Index for fast lookup
CREATE INDEX ix_sources_content_hash ON sources(project_id, content_hash);
```

---

## Phase 4: Adaptive Extraction Depth

### Objective

Vary extraction thoroughness based on page importance signals.

### Implementation

```python
class ExtractionDepth(str, Enum):
    MINIMAL = "minimal"  # Quick scan, high confidence only
    STANDARD = "standard"  # Normal extraction
    THOROUGH = "thorough"  # Deep extraction, multiple passes

def determine_depth(
    page_type: str,
    url_signals: dict,
    content_length: int,
) -> ExtractionDepth:
    """Determine extraction depth based on page signals."""

    # Product pages get thorough extraction
    if page_type == "product":
        return ExtractionDepth.THOROUGH

    # Short pages get minimal extraction
    if content_length < 500:
        return ExtractionDepth.MINIMAL

    # Homepage gets thorough (often has company overview)
    if url_signals.get("is_homepage"):
        return ExtractionDepth.THOROUGH

    return ExtractionDepth.STANDARD
```

---

## Implementation Checklist

### Phase 1: Page Classification (Week 1-2)

- [ ] Create `PageClassifier` service
- [ ] Define URL patterns and title keywords for drivetrain template
- [ ] Add classification columns to sources table (migration)
- [ ] Integrate classifier into `SchemaExtractionOrchestrator`
- [ ] Add configuration flags (`CLASSIFICATION_ENABLED`, `CLASSIFICATION_METHOD`)
- [ ] Write unit tests for classifier
- [ ] Write integration tests for pipeline with classification
- [ ] Add classification metrics/logging
- [ ] Test with drivetrain template on sample project
- [ ] Measure LLM call reduction

### Phase 2: Combined Field Groups (Week 2-3)

- [ ] Create `drivetrain_company_optimized.yaml` template
- [ ] Validate combined template produces equivalent data
- [ ] Add template selection logic (original vs optimized)
- [ ] Update report generation to handle combined groups
- [ ] Test backward compatibility
- [ ] Measure additional LLM call reduction

### Phase 3: Content Deduplication (Week 3-4)

- [ ] Create `ContentDeduplicator` service
- [ ] Add content hash columns (migration)
- [ ] Integrate deduplication into pipeline
- [ ] Handle cross-source-group deduplication
- [ ] Test with known duplicate content
- [ ] Measure deduplication rate

### Phase 4: Adaptive Depth (Week 4+)

- [ ] Define depth levels and their behaviors
- [ ] Implement depth determination logic
- [ ] Adjust prompts per depth level
- [ ] Test quality at each depth
- [ ] Measure impact on extraction quality

---

## Success Metrics

### Primary Metrics

| Metric | Baseline | Target | Measurement |
|--------|----------|--------|-------------|
| LLM calls per page | 14 | 3 | Count API calls |
| Pages skipped | 0% | 15% | Classification skip rate |
| Field groups per page | 7 | 2.5 avg | Logging/metrics |
| Content dedup rate | 0% | 10% | Hash collision rate |

### Quality Metrics

| Metric | Threshold | Measurement |
|--------|-----------|-------------|
| Extraction recall | >95% | Manual review sample |
| False skip rate | <2% | Review skipped pages |
| Duplicate miss rate | <5% | Compare extractions |

### Performance Metrics

| Metric | Baseline | Target |
|--------|----------|--------|
| Time per 1000 pages | 8 hours | 2 hours |
| KV cache utilization | 98-100% | 60-80% |
| Queue wait time | Variable | Consistent |

---

## Rollout Plan

### Stage 1: Shadow Mode (1 week)
- Enable classification but don't skip extractions
- Log what would have been skipped
- Validate classification accuracy

### Stage 2: Gradual Rollout (2 weeks)
- Enable skipping for new projects only
- Monitor quality metrics closely
- Adjust classification rules as needed

### Stage 3: Full Deployment (1 week)
- Enable for all projects
- Provide opt-out flag for projects needing full extraction
- Document any known limitations

---

## Risks and Mitigations

| Risk | Impact | Likelihood | Mitigation |
|------|--------|------------|------------|
| Classification misses important pages | Data loss | Medium | Conservative default rules, manual review |
| Combined groups reduce accuracy | Quality degradation | Low | A/B test before migration |
| Content dedup misses variations | Incomplete data | Medium | Use structure hash fallback |
| Performance regression | Slower pipeline | Low | Benchmark before/after |

---

## Related Documents

- [Architecture Documentation](./architectureV1_1.md)
- [High Concurrency Tuning](./TODO_high_concurrency_tuning.md)
- [Extraction Pipeline Analysis](./extraction_pipeline_analysis.md)

---

## Appendix: Classification Rules Reference

### URL Patterns (Drivetrain Template)

| Pattern | Field Groups | Skip |
|---------|-------------|------|
| `/product` | products_* | No |
| `/gearbox`, `/reducer` | products_gearbox, manufacturing | No |
| `/motor`, `/drive` | products_motor, manufacturing | No |
| `/service`, `/repair` | services | No |
| `/about`, `/company` | company_info, company_meta | No |
| `/contact` | company_info | No |
| `/career`, `/job` | - | Yes |
| `/news`, `/blog` | - | Yes |
| `/privacy`, `/terms` | - | Yes |

### Title Keywords

| Keyword | Field Groups |
|---------|-------------|
| gearbox, reducer, planetary | products_gearbox |
| motor, servo, drive | products_motor |
| service, repair, maintenance | services |
| about, company, history | company_info |
| certification, ISO, quality | company_meta |
