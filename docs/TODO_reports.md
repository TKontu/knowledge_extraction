# TODO: Report Generation Module

## Overview

Generates structured reports from extracted facts and entities. Supports single company deep-dives, comparisons, and topic summaries.

## Status

**Completed:**
- ✅ Reports table schema in `init.sql`
- ✅ Report ORM model (`pipeline/orm_models.py` - PR #4)
- ✅ LLM configuration available for report generation

**Pending:**
- Report type definitions
- Fact aggregation logic
- Entity-based structured comparisons
- Report generation and formatting

**Dependencies:**
- Requires Phase 3 (Extraction) - facts must exist
- Requires Phase 4 (Knowledge Layer) - entities enable structured comparisons
- Requires Phase 5 (Storage) - fact/entity retrieval

**Related Documentation:**
- See `docs/TODO_knowledge_layer.md` for entity-based comparisons
- See `docs/TODO_llm_integration.md` for LLM client

---

## Core Tasks

### Report Types

- [ ] Define report type enum and schemas
  ```python
  class ReportType(Enum):
      SINGLE = "single"           # One company, all facts
      COMPARISON = "comparison"   # Multiple companies side-by-side
      TOPIC = "topic"             # Cross-company by category
      SUMMARY = "summary"         # Executive summary
  ```

### Report Request Handling

- [ ] Report request schema
  ```python
  @dataclass
  class ReportRequest:
      type: ReportType
      companies: list[str] | None = None
      categories: list[str] | None = None
      entity_types: list[str] | None = None  # Filter by entity type
      date_range: tuple[datetime, datetime] | None = None
      format: Literal["md", "pdf"] = "md"
      max_facts_per_section: int = 20
  ```
- [ ] Request validation (e.g., comparison needs 2+ companies)

### Fact Aggregation

- [ ] Query facts for report
  ```python
  async def gather_facts(request: ReportRequest) -> dict[str, list[StoredFact]]
  ```
- [ ] Group by company and/or category
- [ ] Sort by confidence (high first)
- [ ] Deduplicate similar facts
- [ ] Limit facts per section (prevent huge reports)

### Entity-Based Structured Comparisons

> **Key Enhancement:** Use entities for accurate tables instead of LLM inference.

- [ ] Query entities for comparison
  ```python
  async def gather_entities(
      companies: list[str],
      entity_types: list[str]
  ) -> dict[str, dict[str, list[Entity]]]
  ```
- [ ] Build comparison tables from entity data
  ```python
  async def build_comparison_table(
      companies: list[str],
      entity_type: str  # e.g., "limit", "pricing", "feature"
  ) -> ComparisonTable
  ```
- [ ] Merge entity data with fact context

**Example - Entity-Based Comparison:**
```python
# Instead of asking LLM to infer rate limits from text:
# Query entities directly:
limits = await get_entities(companies=["A", "B"], type="limit")

# Returns structured data:
# {
#   "Company A": [{"value": "10,000/min", "numeric": 10000}],
#   "Company B": [{"value": "5,000/min", "numeric": 5000}]
# }

# Build accurate table (no hallucination risk)
```

### Report Generation

- [ ] Template-based prompt builder
  ```python
  def build_report_prompt(
      report_type: ReportType,
      facts: dict,
      entity_tables: dict | None = None,  # Pre-built comparison tables
      instructions: str | None = None
  ) -> str
  ```
- [ ] LLM call for report synthesis
- [ ] Inject entity tables directly (not LLM-generated)
- [ ] Markdown output formatting
- [ ] PDF conversion (via Pandoc, optional)

### Report Storage

- [ ] Store generated reports in PostgreSQL
- [ ] Link report → source facts (for attribution)
- [ ] Link report → source entities
- [ ] Cache reports (regenerate on demand)

---

## Report Templates

### Single Company Report

```markdown
# {company} - Technical Facts Report

Generated: {date}
Sources: {source_count} pages
Facts: {fact_count}

## Technical Specifications
{facts_for_category}

## API & Integration
{facts_for_category}

## Security & Compliance
{facts_for_category}

## Sources
- {source_urls}
```

### Comparison Report (Entity-Enhanced)

```markdown
# Comparison: {company_a} vs {company_b}

Generated: {date}

## Rate Limits
<!-- Table built from entities, not LLM inference -->
| Limit Type | {company_a} | {company_b} |
|------------|-------------|-------------|
| API calls/min | 10,000 | 5,000 |
| Storage | 100GB | 50GB |

## Pricing
<!-- Table built from pricing entities -->
| Plan | {company_a} | {company_b} |
|------|-------------|-------------|
| Pro | $99/month | $79/month |
| Enterprise | Custom | $299/month |

## Features
<!-- Table built from feature entities -->
| Feature | {company_a} | {company_b} |
|---------|-------------|-------------|
| SSO | ✓ | ✓ |
| Webhooks | ✓ | ✗ |

## Analysis
{llm_generated_comparison_summary}
<!-- LLM synthesizes insights from accurate data above -->
```

### Topic Report

```markdown
# {category} - Cross-Company Analysis

Generated: {date}
Companies: {company_list}

## Overview
{llm_summary}

## By Company

### {company_1}
- {fact_1}
- {fact_2}

### {company_2}
...

## Key Insights
{llm_insights}
```

### Executive Summary

```markdown
# Executive Summary: {company}

Generated: {date}

## Key Highlights
{top_5_facts}

## Notable Capabilities
{llm_synthesis}

## Potential Considerations
{llm_analysis}
```

---

## Prompt Templates

### Comparison Prompt (Entity-Enhanced)

```
You are creating a technical comparison report.

Companies: {companies}

## Structured Data (Verified)
The following tables contain verified data from documentation:

{entity_tables}

## Additional Context (Facts)
{supporting_facts}

Create a comparison report that:
1. Uses the structured tables above as-is (do not modify values)
2. Adds analysis and insights based on the data
3. Highlights key differences and similarities
4. Notes any gaps ("Not documented" where data missing)

Output format: Markdown

IMPORTANT: Do not invent or modify the data in the tables.
Only add analysis and commentary.
```

### Summary Prompt

```
You are creating an executive summary of technical facts.

Company: {company}
Total facts: {count}

Facts by category:
{structured_facts}

Create a concise executive summary that:
1. Highlights the 5 most important facts
2. Synthesizes key capabilities
3. Notes any potential limitations or gaps
4. Is suitable for quick executive review

Output format: Markdown (no more than 500 words)
```

---

## Data Models

```python
@dataclass
class ReportRequest:
    type: ReportType
    companies: list[str] | None = None
    categories: list[str] | None = None
    entity_types: list[str] | None = None
    date_range: tuple[datetime, datetime] | None = None
    format: Literal["md", "pdf"] = "md"
    max_facts_per_section: int = 20
    custom_instructions: str | None = None

@dataclass
class ComparisonTable:
    """Pre-built comparison table from entities."""
    entity_type: str  # "limit", "pricing", "feature"
    headers: list[str]  # Company names
    rows: list[dict[str, str]]  # {aspect: company_value}

@dataclass
class GeneratedReport:
    id: UUID
    type: ReportType
    title: str
    content: str  # Markdown
    companies: list[str]
    categories: list[str]
    fact_ids: list[UUID]  # Source facts
    entity_ids: list[UUID]  # Source entities
    generated_at: datetime
    format: str

@dataclass
class ReportJob:
    id: UUID
    request: ReportRequest
    status: str
    report_id: UUID | None = None
    error: str | None = None
```

---

## Configuration

```yaml
reports:
  max_facts_per_section: 20
  max_facts_total: 200
  default_format: md
  cache_ttl_hours: 24

  # Entity-based comparisons
  use_entity_tables: true  # Build tables from entities
  entity_types_for_tables:
    - limit
    - pricing
    - feature
    - certification

  # LLM settings for report generation
  llm_model: ${LLM_MODEL:-gemma3-12b-awq}
  max_tokens: 4000
  temperature: 0.3  # Lower for factual reports

  # PDF export (optional)
  enable_pdf: true
  pandoc_path: /usr/bin/pandoc
```

---

## API Endpoints

```python
# POST /api/v1/reports
# Request (single company):
{
    "type": "single",
    "companies": ["Example Inc"],
    "format": "md"
}

# Request (comparison with entity tables):
{
    "type": "comparison",
    "companies": ["Company A", "Company B", "Company C"],
    "categories": ["pricing", "api_limits", "security"],
    "entity_types": ["limit", "pricing", "feature"],
    "format": "md"
}

# Request (topic):
{
    "type": "topic",
    "categories": ["security"],
    "format": "md"
}

# Request (summary):
{
    "type": "summary",
    "companies": ["Example Inc"],
    "format": "md"
}

# Response:
{
    "job_id": "uuid",
    "status": "queued"
}

# GET /api/v1/reports/{job_id}
# Response (completed):
{
    "job_id": "uuid",
    "status": "completed",
    "report": {
        "id": "uuid",
        "title": "Comparison: Company A vs Company B",
        "format": "md",
        "content": "# Comparison...",
        "download_url": "/api/v1/reports/uuid/download"
    }
}

# GET /api/v1/reports/{report_id}/download
# Returns: Raw markdown or PDF file
```

---

## File Structure

```
pipeline/
├── services/
│   └── reports/
│       ├── __init__.py
│       ├── aggregator.py      # Fact gathering and grouping
│       ├── entity_tables.py   # Build comparison tables from entities
│       ├── generator.py       # Report generation logic
│       ├── prompts.py         # Prompt templates
│       ├── formatter.py       # Output formatting (MD, PDF)
│       └── service.py         # ReportService (orchestration)
├── models/
│   └── reports.py             # ReportRequest, GeneratedReport
└── api/
    └── routes/
        └── reports.py         # API endpoints
```

---

## Testing Checklist

- [ ] Unit: Fact aggregation grouping
- [ ] Unit: Entity table building
- [ ] Unit: Prompt building for each report type
- [ ] Unit: Markdown formatting
- [ ] Integration: Single company report generation
- [ ] Integration: Comparison report with entity tables
- [ ] Integration: Comparison report with real facts
- [ ] Integration: PDF export (if enabled)
- [ ] Integration: Report caching works
