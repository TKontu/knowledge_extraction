# TODO: Knowledge Layer (Entities & Relationships)

## Overview

Extends extraction with a lightweight knowledge graph layer - entities and relations extracted from extractions to enable structured queries and comparisons.

**Architecture:** Uses the generalized project-based system. Entity types are defined per-project, not hardcoded.

## Status

**Complete** - EntityExtractor fully implemented + Entity API endpoints

**Completed:**
- [x] EntityExtractor class skeleton (`services/knowledge/extractor.py`)
- [x] Entity extraction prompt builder (`_build_prompt()` method)
- [x] Entity normalization (`_normalize()` method - plan, feature, limit, pricing types)
- [x] LLM entity extraction call (`_call_llm()` method with JSON mode)
- [x] Entity storage with deduplication (`_store_entities()` method)
- [x] Entity-extraction linking (via `entity_repo.link_to_extraction()`)
- [x] Main `extract()` method (full pipeline orchestration)
- [x] Full test suite (27 tests in `test_entity_extractor.py`)
- [x] **Entity API endpoints** (`api/v1/entities.py` - 15 tests)
  - GET /api/v1/projects/{project_id}/entities (list with filtering)
  - GET /api/v1/projects/{project_id}/entities/types (type counts)
  - GET /api/v1/projects/{project_id}/entities/{entity_id} (single entity)
  - GET /api/v1/projects/{project_id}/entities/by-value (find source_groups)

**Pending:**
- [ ] Integrate EntityExtractor into extraction pipeline

**Related Documentation:**
- See `docs/TODO_extraction.md` for extraction module
- See `docs/TODO_generalization.md` for project-based architecture
- See `docs/TODO_llm_integration.md` for LLM details

---

## Problem Statement

Current plan extracts facts as isolated text blobs. This limits:
- **Structured queries:** "Which companies support SSO?" requires text search
- **Comparisons:** "Compare rate limits across companies" requires LLM inference
- **Navigation:** "What features does the Pro plan include?" hard to answer precisely

A lightweight entity/relation layer enables:
- Direct SQL queries for structured comparisons
- Hybrid search (vector + entity filtering)
- Accurate comparison tables (not LLM guessing)

---

## Architecture

```
Current: Facts Only
┌────────────────────────────────────────────────────────┐
│ Fact: "Pro plan supports 10,000 API calls per minute" │
│ → Only searchable via embedding similarity             │
└────────────────────────────────────────────────────────┘

Enhanced: Facts + Entities
┌────────────────────────────────────────────────────────┐
│ Fact: "Pro plan supports 10,000 API calls per minute" │
│ Entities:                                              │
│   - {type: "plan", value: "Pro"}                       │
│   - {type: "limit", value: "10,000/min", numeric: 10000}│
│ Relation:                                              │
│   - Pro --[has_limit]--> 10,000 calls/min              │
└────────────────────────────────────────────────────────┘
```

---

## Design Decisions

### Phased Approach (Not All At Once)

**Avoid:** Building complete knowledge graph upfront
**Instead:** Extract entities first, add relations when needed

**Phase 1 (MVP):** Entity extraction only
- Simple entity types: plan, feature, limit, certification
- Link entities to facts
- Enable entity-filtered search

**Phase 2 (Post-MVP):** Relation extraction
- Relations between entities
- Graph queries

**Phase 3 (Future):** Page links
- Store outbound links from Firecrawl
- Navigation hierarchy inference

### Separate Extraction Pass (Not Combined)

**Avoid:** Single complex prompt for facts + entities + relations
**Instead:** Two-pass extraction

```python
# Pass 1: Extract facts (existing)
facts = await extract_facts(page, profile)

# Pass 2: Extract entities from facts
for fact in facts:
    entities = await extract_entities(fact)
```

Rationale:
- Simpler prompts, better results
- Can improve entity extraction independently
- Facts work without entities (graceful degradation)

### Minimal Entity Taxonomy (Start Small)

**Avoid:** 15+ entity types from day one
**Instead:** Start with 5 core types, add more as needed

---

## Entity Taxonomy

Entity types are **defined per-project** in `project.entity_types`, not hardcoded.

### Default Types (Company Analysis Template)

| Type | Examples | Normalization |
|------|----------|---------------|
| `plan` | "Pro", "Enterprise", "Free tier" | lowercase |
| `feature` | "SSO", "API access", "Webhooks" | lowercase canonical |
| `limit` | "10,000 req/min", "100GB" | numeric + unit |
| `certification` | "SOC 2 Type II", "ISO 27001" | canonical cert name |
| `pricing` | "$99/month", "$0.01/request" | numeric + period |

### Example: Research Survey Template

| Type | Examples | Normalization |
|------|----------|---------------|
| `model` | "GPT-4", "BERT", "ResNet-50" | canonical name |
| `dataset` | "ImageNet", "COCO" | canonical name |
| `metric` | "accuracy: 94.5%" | numeric + name |
| `author` | "Smith et al." | normalized name |

**Adding Custom Types:**
Projects can define any entity types via the `entity_types` JSONB field.

---

## Database Schema

```sql
-- Entities table (project-scoped)
CREATE TABLE entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    source_group TEXT NOT NULL,  -- Replaces hardcoded "company"
    entity_type TEXT NOT NULL,  -- From project.entity_types
    value TEXT NOT NULL,  -- Original text
    normalized_value TEXT NOT NULL,  -- For matching
    attributes JSONB DEFAULT '{}',  -- Type-specific (numeric_value, unit, etc.)
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    UNIQUE(project_id, source_group, entity_type, normalized_value)
);

CREATE INDEX idx_entities_project ON entities(project_id);
CREATE INDEX idx_entities_group ON entities(source_group);
CREATE INDEX idx_entities_type ON entities(entity_type);
CREATE INDEX idx_entities_normalized ON entities(normalized_value);

-- Extraction-Entity junction (replaces fact_entities)
CREATE TABLE extraction_entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    extraction_id UUID REFERENCES extractions(id) ON DELETE CASCADE,
    entity_id UUID REFERENCES entities(id) ON DELETE CASCADE,
    role TEXT DEFAULT 'mention',  -- mention, subject, object
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    UNIQUE(extraction_id, entity_id, role)
);

CREATE INDEX idx_extraction_entities_extraction ON extraction_entities(extraction_id);
CREATE INDEX idx_extraction_entities_entity ON extraction_entities(entity_id);
```

**Relations table (Phase 2 - not MVP):**
```sql
-- Add later when needed
CREATE TABLE relations (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    company TEXT NOT NULL,
    relation_type TEXT NOT NULL,  -- has_feature, has_limit
    subject_entity_id UUID REFERENCES entities(id),
    object_entity_id UUID REFERENCES entities(id),
    fact_id UUID REFERENCES facts(id),
    confidence FLOAT DEFAULT 1.0,
    UNIQUE(company, relation_type, subject_entity_id, object_entity_id)
);
```

---

## Entity Extraction Prompt

```
Extract entities from this fact. Return JSON with entities found.

Fact: "{fact_text}"
Company: "{company}"

Entity types:
- plan: Pricing tiers (Pro, Enterprise, Free)
- feature: Product capabilities (SSO, API, Webhooks)
- limit: Quotas, thresholds (requests/min, storage, users)
- certification: Security certs (SOC2, ISO27001)
- pricing: Costs, prices ($99/month)

Output format:
{
  "entities": [
    {
      "type": "plan",
      "value": "Pro",
      "normalized": "pro",
      "metadata": {}
    },
    {
      "type": "limit",
      "value": "10,000/min",
      "normalized": "10000_per_minute",
      "metadata": {"numeric_value": 10000, "unit": "requests", "period": "minute"}
    }
  ]
}

Only extract entities explicitly mentioned. Do not infer.
```

---

## Implementation Tasks

### Phase 1: Entity Extraction (MVP)

- [x] Add `entities` and `extraction_entities` tables (completed in PR #11)
- [x] Create ORM models for Entity, ExtractionEntity (completed in PR #11)
- [x] Create `EntityExtractor` class (complete - all methods implemented)
- [x] Create entity extraction prompt (completed - `_build_prompt()` method)
- [x] Implement value normalization per type (`_normalize()` method)
- [x] **Entity API endpoints** (list, get, types, by-value queries)
- [ ] Integrate into extraction pipeline (run after fact extraction)

### Phase 2: Relations (Post-MVP)

- [ ] Add `relations` table
- [ ] Define relation types (has_feature, has_limit, has_price)
- [ ] Create relation extraction prompt
- [ ] Implement relation extraction
- [ ] Graph queries

### Phase 3: Page Links (Future)

- [ ] Store outbound links from Firecrawl response
- [ ] Create `page_links` table
- [ ] Resolve internal links to scraped pages
- [ ] Use for crawl expansion

---

## Query Patterns

### MVP Queries (Entity-Only, Project-Scoped)

```python
# "Which source_groups have SSO?" (e.g., which companies support SSO)
async def groups_with_feature(project_id: UUID, feature: str) -> list[str]:
    return await db.fetch("""
        SELECT DISTINCT source_group
        FROM entities
        WHERE project_id = $1
        AND entity_type = 'feature'
        AND normalized_value = $2
    """, project_id, normalize(feature))

# "What are the rate limits for source_group X?"
async def group_limits(project_id: UUID, source_group: str) -> list[dict]:
    return await db.fetch("""
        SELECT value, attributes
        FROM entities
        WHERE project_id = $1
        AND source_group = $2
        AND entity_type = 'limit'
    """, project_id, source_group)

# "Compare pricing across source_groups"
async def compare_pricing(project_id: UUID) -> list[dict]:
    return await db.fetch("""
        SELECT source_group, value, attributes
        FROM entities
        WHERE project_id = $1
        AND entity_type = 'pricing'
        ORDER BY source_group
    """, project_id)
```

### Hybrid Search (Vector + Entity)

```python
async def search_with_entity_filter(
    project_id: UUID,
    query: str,
    entity_type: str | None = None,
    entity_value: str | None = None,
) -> list[Extraction]:
    # Vector search for relevant extractions
    extraction_ids = await qdrant_search(
        query,
        limit=50,
        filters={"project_id": str(project_id)}
    )

    # Filter by entity if specified
    if entity_type and entity_value:
        extraction_ids = await db.fetch("""
            SELECT ee.extraction_id
            FROM extraction_entities ee
            JOIN entities e ON e.id = ee.entity_id
            WHERE ee.extraction_id = ANY($1)
            AND e.entity_type = $2
            AND e.normalized_value = $3
        """, extraction_ids, entity_type, normalize(entity_value))

    return await get_extractions(extraction_ids)
```

---

## Integration with Reports

Entity data enables accurate comparison tables:

```python
async def generate_comparison_table(companies: list[str], entity_type: str):
    """Generate comparison table from structured entity data."""
    data = {}
    for company in companies:
        entities = await get_entities(company, entity_type)
        data[company] = {e.normalized_value: e.value for e in entities}

    # Build table (no LLM inference needed)
    return format_comparison_table(data)
```

---

## File Structure

```
src/
├── services/
│   └── knowledge/
│       ├── __init__.py
│       └── extractor.py          # EntityExtractor (✅ implemented)
├── services/
│   └── storage/
│       └── repositories/
│           └── entity.py         # EntityRepository (✅ implemented in PR #11)
├── tests/
│   └── test_entity_extractor.py  # EntityExtractor tests (✅ implemented)
```

**Note:** Simplified structure from original design - no separate entities/ subdirectory needed.

---

## Configuration

```yaml
knowledge_layer:
  enabled: true
  entity_extraction:
    enabled: true
    model: ${LLM_MODEL}
    batch_size: 10  # Facts per LLM call
  entity_types:
    - plan
    - feature
    - limit
    - certification
    - pricing
```

---

## Testing Checklist

- [x] Unit: EntityExtractor initialization (✅ test_init_requires_llm_client_and_entity_repo)
- [x] Unit: Prompt building with entity types (✅ TestBuildPrompt - 2 tests)
- [x] Unit: Value normalization (limits, pricing) - TestNormalize class
- [x] Unit: Entity extraction from sample extractions with mocked LLM - TestCallLlm class
- [x] Unit: Entity deduplication (same project + source_group + type + normalized) - TestStoreEntities class
- [x] Unit: Main extract() method - TestExtract class
- [x] Integration: Entity API endpoints - test_entity_endpoint.py (15 tests)
- [ ] Integration: Entity-filtered search
- [ ] Integration: Comparison query

---

## Critical Assessment vs Candidate

**Accepted from candidate:**
- Entity taxonomy concept (simplified)
- Fact-entity junction pattern
- Structured query examples

**Rejected/Deferred:**
- Combined extraction (too complex for MVP)
- 11+ entity types (start with 5)
- Relations (Phase 2, not MVP)
- Page links (Phase 3)

**Key simplification:**
The candidate proposes a full knowledge graph from day one. This plan takes a pragmatic approach: entities first (high value, simpler), relations and links later when needed.
