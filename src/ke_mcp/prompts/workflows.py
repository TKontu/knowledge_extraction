"""Workflow prompt templates."""

from mcp.server.fastmcp import FastMCP


def register_workflow_prompts(mcp: FastMCP) -> None:
    """Register workflow prompts."""

    @mcp.prompt()
    def analyze_company_docs(
        company_name: str,
        documentation_url: str,
        focus_areas: str = "all technical facts",
    ) -> str:
        """Complete workflow to extract knowledge from company documentation."""
        return f"""# Analyze {company_name} Documentation

Follow these steps to extract structured knowledge from {company_name}'s documentation.

## Step 1: Create Project
```
create_project(
    name="{company_name.lower().replace(" ", "-")}-analysis",
    template="company_analysis",
    description="Analysis of {company_name} documentation"
)
```

## Step 2: Crawl Documentation
```
crawl_website(
    url="{documentation_url}",
    project_id="<project_id from step 1>",
    company="{company_name}",
    max_depth=3,
    limit=200,
    wait_for_completion=True
)
```

## Step 3: Extract Knowledge
```
extract_knowledge(
    project_id="<project_id>"
)
```
Then wait for extraction to complete using `get_job_status()`.

## Step 4: Search and Explore
```
search_knowledge(
    project_id="<project_id>",
    query="{focus_areas}"
)

get_entity_summary(project_id="<project_id>")
```

## Step 5: Generate Report
```
create_report(
    project_id="<project_id>",
    report_type="single",
    source_groups=["{company_name}"],
    title="{company_name} Analysis Report"
)
```

## Expected Results
- Structured extractions of technical facts
- Normalized entities (features, pricing tiers, limits)
- Searchable knowledge base
- Summary report in markdown format
"""

    @mcp.prompt()
    def compare_competitors(
        company_names: str,
        focus_area: str = "features and pricing",
    ) -> str:
        """Workflow to compare multiple companies."""
        companies = [c.strip() for c in company_names.split(",")]

        return f"""# Compare: {" vs ".join(companies)}

This workflow compares {len(companies)} companies on {focus_area}.

## Prerequisites
Each company must already have extracted data in a project.
If not, run the `analyze_company_docs` workflow for each company first.

## Step 1: Search Across Companies
For each focus area, search across all companies:
```
search_knowledge(
    project_id="<project_id>",
    query="{focus_area}",
    source_groups={companies}
)
```

## Step 2: Compare Entities
```
list_entities(
    project_id="<project_id>",
    entity_type="feature"  # or "pricing", "plan", etc.
)
```

## Step 3: Generate Comparison Report
```
create_report(
    project_id="<project_id>",
    report_type="comparison",
    source_groups={companies},
    title="{" vs ".join(companies)} - {focus_area.title()}"
)
```

## Expected Results
- Side-by-side comparison of {focus_area}
- Differences and similarities highlighted
- Structured comparison table
"""
