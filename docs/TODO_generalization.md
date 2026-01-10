# TODO: System Generalization

## Overview

Transform from "company technical facts extractor" to a **general-purpose extraction pipeline** that supports any domain, schema, and entity types.

**Status:** Design phase - requires incremental migration from existing implementation.

**Related Documentation:**
- See `docs/TODO_project_system.md` for project management implementation
- See `docs/TODO_migrations.md` for Alembic migration strategy

---

## Problem Statement

The current system is designed specifically for extracting company technical documentation:
- Hardcoded `company` field on pages
- Fixed fact schema (fact_text, category, confidence)
- Static entity types (plan, feature, limit, certification, pricing)
- Fixed extraction profiles

This limits the system to a single use case. A generalized system could support:
- Academic paper analysis
- Legal contract review
- Product review mining
- Any structured data extraction from documents

---

## Core Concept: Projects

A **Project** defines the complete extraction configuration:

```
Project
├── name, description
├── source_config        → What to scrape (web, PDF), how to group sources
├── extraction_schema    → Custom fields to extract (JSONB validated)
├── entity_types         → What entities to recognize
├── prompt_templates     → How to instruct the LLM
└── settings             → Active, template, etc.
```

---

## Design Decisions

### 1. JSONB Hybrid Approach (Not Pure EAV)

**Rationale:** JSONB provides schema flexibility while maintaining query performance via GIN indexes.

```sql
-- Extractions store dynamic data in JSONB
CREATE TABLE extractions (
    id UUID PRIMARY KEY,
    project_id UUID REFERENCES projects(id),
    source_id UUID REFERENCES sources(id),
    data JSONB NOT NULL,  -- Schema validated at app layer
    -- Common fields denormalized for indexing
    extraction_type TEXT NOT NULL,
    source_group TEXT NOT NULL,
    confidence FLOAT
);
CREATE INDEX idx_extractions_data ON extractions USING GIN (data);
```

### 2. Incremental Migration (Not Big Bang)

**Rationale:** System has 156 passing tests and working scraper. Preserve existing functionality.

**Phase 1:** Add project layer alongside existing tables
**Phase 2:** Create migration adapters
**Phase 3:** Migrate existing data to new schema
**Phase 4:** Remove legacy tables

### 3. Application-Layer Schema Validation

**Rationale:** Database schema flexibility + application-level type safety.

```python
# Dynamic Pydantic model from project schema
validator = SchemaValidator(project.extraction_schema)
is_valid, errors = validator.validate(extraction_data)
```

### 4. Backward Compatibility via Default Project

**Rationale:** Existing API endpoints continue to work during migration.

```python
# Existing endpoints use implicit default project
POST /api/v1/scrape  →  Uses "company_analysis" project
POST /api/v1/extract →  Uses "company_analysis" project

# New endpoints are project-scoped
POST /api/v1/projects/{project_id}/sources
POST /api/v1/projects/{project_id}/extract
```

---

## Example Project Configurations

### 1. Company Technical Analysis (Current Use Case)

```yaml
name: "company_analysis"
description: "Extract technical facts from company documentation"
is_template: true

source_config:
  type: web
  group_by: company  # source_group = company name

extraction_schema:
  name: technical_fact
  fields:
    - name: fact_text
      type: text
      required: true
    - name: category
      type: enum
      values: [specs, api, security, pricing, features, integration]
      required: true
    - name: confidence
      type: float
      min: 0.0
      max: 1.0
      default: 0.8
    - name: source_quote
      type: text
      required: false

entity_types:
  - name: plan
    description: "Pricing tier or plan"
  - name: feature
    description: "Product capability"
  - name: limit
    description: "Quota or threshold"
    attributes:
      - {name: numeric_value, type: number}
      - {name: unit, type: text}
  - name: certification
    description: "Security or compliance certification"
  - name: pricing
    description: "Cost or price point"
```

### 2. Academic Research Survey

```yaml
name: "research_survey"
description: "Extract findings from academic papers"
is_template: true

source_config:
  type: pdf
  group_by: paper

extraction_schema:
  name: research_finding
  fields:
    - name: finding
      type: text
      required: true
    - name: finding_type
      type: enum
      values: [result, claim, limitation, future_work]
    - name: methodology
      type: text
    - name: metrics
      type: json

entity_types:
  - name: model
    attributes: [{name: architecture, type: text}]
  - name: dataset
    attributes: [{name: domain, type: text}]
  - name: author
    attributes: [{name: affiliation, type: text}]
```

### 3. Legal Contract Review

```yaml
name: "contract_review"
description: "Extract clauses and risks from legal contracts"
is_template: true

source_config:
  type: pdf
  group_by: contract

extraction_schema:
  name: clause
  fields:
    - name: clause_text
      type: text
      required: true
    - name: clause_type
      type: enum
      values: [liability, termination, payment, confidentiality, ip, warranty]
    - name: risk_level
      type: enum
      values: [low, medium, high, critical]
    - name: party_obligations
      type: list

entity_types:
  - name: party
    attributes: [{name: role, type: enum, values: [vendor, client]}]
  - name: monetary_amount
    attributes: [{name: currency, type: text}]
```

---

## Database Schema Changes

### New Tables

```sql
-- Projects define extraction configurations
CREATE TABLE projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT UNIQUE NOT NULL,
    description TEXT,

    -- Configuration stored as JSONB
    source_config JSONB NOT NULL DEFAULT '{"type": "web", "group_by": "company"}',
    extraction_schema JSONB NOT NULL,
    entity_types JSONB NOT NULL DEFAULT '[]',
    prompt_templates JSONB NOT NULL DEFAULT '{}',

    -- Settings
    is_template BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Sources (generalized from pages)
CREATE TABLE sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,

    source_type TEXT NOT NULL DEFAULT 'web',  -- web, pdf, api, text
    uri TEXT NOT NULL,
    source_group TEXT NOT NULL,  -- Replaces hardcoded "company"

    title TEXT,
    content TEXT,  -- Processed content (markdown)
    raw_content TEXT,  -- Original content

    metadata JSONB DEFAULT '{}',
    outbound_links JSONB DEFAULT '[]',

    status TEXT DEFAULT 'pending',
    fetched_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    UNIQUE(project_id, uri)
);

-- Extractions (generalized from facts)
CREATE TABLE extractions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    source_id UUID REFERENCES sources(id) ON DELETE CASCADE,

    -- Dynamic data validated against project schema
    data JSONB NOT NULL,

    -- Denormalized for indexing/queries
    extraction_type TEXT NOT NULL,
    source_group TEXT NOT NULL,
    confidence FLOAT,

    -- Provenance
    profile_used TEXT,
    chunk_index INT,
    chunk_context JSONB,

    -- Vector reference
    embedding_id TEXT,

    extracted_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for JSONB queries
CREATE INDEX idx_extractions_data ON extractions USING GIN (data);
CREATE INDEX idx_extractions_project ON extractions(project_id);
CREATE INDEX idx_extractions_group ON extractions(source_group);
```

### Migration from Legacy Tables

```sql
-- Create default project for existing data
INSERT INTO projects (name, description, extraction_schema, entity_types)
VALUES (
    'company_analysis',
    'Default project for company technical facts (migrated)',
    '{"name": "technical_fact", "fields": [...]}',
    '[{"name": "plan"}, {"name": "feature"}, ...]'
);

-- Migrate pages → sources
INSERT INTO sources (project_id, source_type, uri, source_group, title, content, ...)
SELECT
    (SELECT id FROM projects WHERE name = 'company_analysis'),
    'web',
    url,
    company,  -- becomes source_group
    title,
    markdown_content,
    ...
FROM pages;

-- Migrate facts → extractions
INSERT INTO extractions (project_id, source_id, data, extraction_type, source_group, confidence, ...)
SELECT
    (SELECT id FROM projects WHERE name = 'company_analysis'),
    (SELECT id FROM sources WHERE uri = p.url),
    jsonb_build_object('fact_text', f.fact_text, 'category', f.category, ...),
    'technical_fact',
    p.company,
    f.confidence,
    ...
FROM facts f
JOIN pages p ON f.page_id = p.id;
```

---

## Implementation Tasks

### Phase 1: Project Layer Foundation

- [ ] Add `projects` table via Alembic migration
- [ ] Create Project ORM model
- [ ] Create ProjectRepository
- [ ] Create default "company_analysis" project with current schema
- [ ] Create SchemaValidator class (dynamic Pydantic models)
- [ ] Add `project_id` to existing jobs table

### Phase 2: Source Abstraction

- [ ] Add `sources` table via migration
- [ ] Create Source ORM model (mirrors existing Page)
- [ ] Create SourceRepository
- [ ] Update scraper worker to create Sources (with project context)
- [ ] Create backward-compatible adapter (page API → source creation)

### Phase 3: Extraction Abstraction

- [ ] Add `extractions` table via migration
- [ ] Create Extraction ORM model
- [ ] Create ExtractionRepository with JSONB query support
- [ ] Update extraction pipeline to use project schema
- [ ] Update LLM prompts to use dynamic field definitions
- [ ] Migrate validation to use project schema

### Phase 4: Entity Generalization

- [ ] Update entities table with project_id
- [ ] Update entity extraction to use project entity_types
- [ ] Create EntityTypeValidator

### Phase 5: API Layer

- [ ] Add project CRUD endpoints
- [ ] Add project-scoped source endpoints
- [ ] Add project-scoped extraction endpoints
- [ ] Maintain backward-compatible endpoints (use default project)

### Phase 6: Data Migration

- [ ] Create data migration script
- [ ] Migrate existing pages → sources
- [ ] Migrate existing facts → extractions
- [ ] Migrate existing entities with project_id
- [ ] Verify data integrity

### Phase 7: Cleanup

- [ ] Mark legacy endpoints as deprecated
- [ ] Update documentation
- [ ] Remove legacy tables (after verification period)

---

## Prompt Template System

Templates use variables from project schema:

```python
class PromptTemplateEngine:
    def render_extraction_prompt(
        self,
        project: Project,
        content: str,
        chunk_context: dict,
    ) -> str:
        schema = project.extraction_schema

        # Build field descriptions dynamically
        field_docs = []
        for field in schema['fields']:
            doc = f"- {field['name']} ({field['type']})"
            if 'description' in field:
                doc += f": {field['description']}"
            if field['type'] == 'enum':
                doc += f" [allowed: {', '.join(field['values'])}]"
            field_docs.append(doc)

        return f"""
You are extracting structured data for: {project.name}

Source: {chunk_context.get('source_group', 'Unknown')}
Section: {' > '.join(chunk_context.get('header_path', []))}

## Fields to Extract
{chr(10).join(field_docs)}

## Output Format
Return JSON array of extractions matching the schema above.

---
{content}
---
"""
```

---

## Testing Checklist

- [ ] Unit: Create project with custom schema
- [ ] Unit: SchemaValidator validates extraction data
- [ ] Unit: JSONB queries filter correctly
- [ ] Unit: Prompt template renders with schema
- [ ] Integration: Create source via project
- [ ] Integration: Extract with project schema
- [ ] Integration: Migration preserves existing data
- [ ] Integration: Legacy endpoints work with default project

---

## Risk Assessment

| Risk | Mitigation |
|------|------------|
| Data migration failures | Incremental migration with rollback capability |
| JSONB query performance | GIN indexes + denormalized common fields |
| Schema validation overhead | Cache compiled Pydantic models |
| API backward compatibility | Default project + adapter layer |
| Increased complexity | Phased rollout, comprehensive tests |

---

## File Structure

```
pipeline/
├── models/
│   ├── project.py          # Project, ExtractionSchema
│   ├── source.py           # Source (generalized Page)
│   └── extraction.py       # Extraction (generalized Fact)
├── services/
│   ├── projects/
│   │   ├── repository.py   # Project CRUD
│   │   ├── templates.py    # Built-in templates
│   │   └── schema.py       # SchemaValidator
│   ├── extraction/
│   │   └── prompt_builder.py  # Dynamic prompts
│   └── ...
├── api/
│   └── routes/
│       ├── projects.py     # Project management
│       └── ...
```
