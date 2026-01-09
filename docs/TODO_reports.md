# TODO: Report Generation Module

## Overview

Generates structured reports from extracted facts. Supports single company deep-dives, comparisons, and topic summaries.

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
- [ ] Deduplicate similar facts (optional)
- [ ] Limit facts per section (prevent huge reports)

### Report Generation

- [ ] Template-based prompt builder
  ```python
  def build_report_prompt(
      report_type: ReportType,
      facts: dict,
      instructions: str | None = None
  ) -> str
  ```
- [ ] LLM call for report synthesis
- [ ] Markdown output formatting
- [ ] PDF conversion (via Pandoc, optional)

### Report Storage

- [ ] Store generated reports in PostgreSQL
- [ ] Link report → source facts (for attribution)
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

### Comparison Report

```markdown
# Comparison: {company_a} vs {company_b}

Generated: {date}

## {category_1}

| Aspect | {company_a} | {company_b} |
|--------|-------------|-------------|
| {aspect} | {fact} | {fact} |

## {category_2}
...

## Summary
{llm_generated_comparison_summary}
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

### Comparison Prompt

```
You are creating a technical comparison report.

Companies: {companies}
Categories: {categories}

Facts by company:
{structured_facts}

Create a comparison report that:
1. Highlights key differences
2. Notes similarities
3. Provides objective analysis
4. Uses tables where appropriate for side-by-side comparison

Output format: Markdown

Do not invent facts. Only use information provided above.
If a company lacks information for a category, note "Not documented".
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
    date_range: tuple[datetime, datetime] | None = None
    format: Literal["md", "pdf"] = "md"
    max_facts_per_section: int = 20
    custom_instructions: str | None = None

@dataclass
class GeneratedReport:
    id: UUID
    type: ReportType
    title: str
    content: str  # Markdown
    companies: list[str]
    categories: list[str]
    fact_ids: list[UUID]  # Source facts
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

# Request (comparison):
{
    "type": "comparison",
    "companies": ["Company A", "Company B", "Company C"],
    "categories": ["pricing", "api_limits", "security"],
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
│       ├── generator.py       # Report generation logic
│       ├── prompts.py         # Prompt templates
│       ├── formatter.py       # Output formatting (MD, PDF)
│       └── service.py         # ReportService (orchestration)
├── models/
│   └── reports.py             # ReportRequest, GeneratedReport
├── prompts/
│   └── reports/
│       ├── single.txt
│       ├── comparison.txt
│       ├── topic.txt
│       └── summary.txt
└── api/
    └── routes/
        └── reports.py         # API endpoints
```

---

## Testing Checklist

- [ ] Unit: Fact aggregation grouping
- [ ] Unit: Prompt building for each report type
- [ ] Unit: Markdown formatting
- [ ] Integration: Single company report generation
- [ ] Integration: Comparison report with real facts
- [ ] Integration: PDF export (if enabled)
- [ ] Integration: Report caching works
