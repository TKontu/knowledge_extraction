# TODO: Agent Templates

**Agent ID**: `agent-templates`
**Branch**: `feat/project-templates`
**Priority**: 1

## Objective

Add two new project templates (`research_survey` and `contract_review`) to enable extraction for academic papers and legal documents.

## Context

- The system supports project-based extraction with configurable schemas
- One template exists: `COMPANY_ANALYSIS_TEMPLATE` in `src/services/projects/templates.py`
- Templates define: extraction_schema, entity_types, source_config, prompt_templates
- Templates are retrieved via `GET /api/v1/projects/templates` and cloned via `POST /api/v1/projects/from-template`

## Tasks

### 1. Add RESEARCH_SURVEY_TEMPLATE

**File**: `src/services/projects/templates.py`

Add template for academic paper extraction:

```python
RESEARCH_SURVEY_TEMPLATE = {
    "name": "research_survey",
    "description": "Extract key information from academic papers and research documents",
    "source_config": {"type": "web", "group_by": "paper"},
    "extraction_schema": {
        "name": "research_finding",
        "fields": [
            {"name": "finding_text", "type": "text", "required": True, "description": "Key finding or claim"},
            {"name": "category", "type": "enum", "required": True,
             "values": ["methodology", "result", "conclusion", "limitation", "future_work"],
             "description": "Category of the finding"},
            {"name": "confidence", "type": "float", "min": 0.0, "max": 1.0, "default": 0.8},
            {"name": "source_quote", "type": "text", "required": False},
        ],
    },
    "entity_types": [
        {"name": "author", "description": "Paper author or researcher"},
        {"name": "institution", "description": "University or research organization"},
        {"name": "method", "description": "Research methodology or technique"},
        {"name": "metric", "description": "Quantitative result or measurement",
         "attributes": [{"name": "value", "type": "number"}, {"name": "unit", "type": "text"}]},
        {"name": "dataset", "description": "Dataset used in research"},
        {"name": "citation", "description": "Referenced work"},
    ],
    "prompt_templates": {},
    "is_template": True,
}
```

### 2. Add CONTRACT_REVIEW_TEMPLATE

**File**: `src/services/projects/templates.py`

Add template for legal document extraction:

```python
CONTRACT_REVIEW_TEMPLATE = {
    "name": "contract_review",
    "description": "Extract key terms and obligations from legal contracts",
    "source_config": {"type": "document", "group_by": "contract"},
    "extraction_schema": {
        "name": "contract_term",
        "fields": [
            {"name": "term_text", "type": "text", "required": True, "description": "Contract term or clause"},
            {"name": "category", "type": "enum", "required": True,
             "values": ["obligation", "right", "condition", "definition", "termination", "liability"],
             "description": "Type of contract term"},
            {"name": "confidence", "type": "float", "min": 0.0, "max": 1.0, "default": 0.8},
            {"name": "section_ref", "type": "text", "required": False, "description": "Section or clause number"},
        ],
    },
    "entity_types": [
        {"name": "party", "description": "Contract party (person or organization)"},
        {"name": "date", "description": "Significant date (effective, expiration, deadline)",
         "attributes": [{"name": "date_type", "type": "text"}]},
        {"name": "amount", "description": "Monetary amount or payment term",
         "attributes": [{"name": "value", "type": "number"}, {"name": "currency", "type": "text"}]},
        {"name": "duration", "description": "Time period or term length"},
        {"name": "jurisdiction", "description": "Governing law or venue"},
    ],
    "prompt_templates": {},
    "is_template": True,
}
```

### 3. Export templates in __all__

**File**: `src/services/projects/templates.py`

Add to module exports:
```python
__all__ = [
    "COMPANY_ANALYSIS_TEMPLATE",
    "RESEARCH_SURVEY_TEMPLATE",
    "CONTRACT_REVIEW_TEMPLATE",
]
```

### 4. Update get_all_templates in repository

**File**: `src/services/projects/repository.py`

Check if `get_all_templates()` method dynamically loads templates or needs updating. If hardcoded, add the new templates.

### 5. Write tests

**File**: `tests/test_project_templates.py`

```python
import pytest
from src.services.projects.templates import (
    COMPANY_ANALYSIS_TEMPLATE,
    RESEARCH_SURVEY_TEMPLATE,
    CONTRACT_REVIEW_TEMPLATE,
)

class TestProjectTemplates:
    def test_research_survey_template_has_required_fields(self):
        assert RESEARCH_SURVEY_TEMPLATE["name"] == "research_survey"
        assert RESEARCH_SURVEY_TEMPLATE["is_template"] is True
        assert "extraction_schema" in RESEARCH_SURVEY_TEMPLATE
        assert "entity_types" in RESEARCH_SURVEY_TEMPLATE

    def test_research_survey_entity_types(self):
        entity_names = [e["name"] for e in RESEARCH_SURVEY_TEMPLATE["entity_types"]]
        assert "author" in entity_names
        assert "method" in entity_names
        assert "dataset" in entity_names

    def test_contract_review_template_has_required_fields(self):
        assert CONTRACT_REVIEW_TEMPLATE["name"] == "contract_review"
        assert CONTRACT_REVIEW_TEMPLATE["is_template"] is True
        assert "extraction_schema" in CONTRACT_REVIEW_TEMPLATE
        assert "entity_types" in CONTRACT_REVIEW_TEMPLATE

    def test_contract_review_entity_types(self):
        entity_names = [e["name"] for e in CONTRACT_REVIEW_TEMPLATE["entity_types"]]
        assert "party" in entity_names
        assert "date" in entity_names
        assert "amount" in entity_names

    def test_all_templates_have_consistent_structure(self):
        templates = [
            COMPANY_ANALYSIS_TEMPLATE,
            RESEARCH_SURVEY_TEMPLATE,
            CONTRACT_REVIEW_TEMPLATE,
        ]
        required_keys = ["name", "description", "source_config", "extraction_schema", "entity_types", "is_template"]
        for template in templates:
            for key in required_keys:
                assert key in template, f"Template {template['name']} missing {key}"
```

## Constraints

- Do NOT modify existing COMPANY_ANALYSIS_TEMPLATE
- Do NOT add new API endpoints (templates are already exposed via existing endpoints)
- Do NOT add dependencies
- Follow existing template structure exactly

## Verification

1. `pytest tests/test_project_templates.py -v` passes
2. `pytest tests/ -v` - all existing tests still pass
3. `ruff check src/services/projects/templates.py` - no lint errors

## Definition of Done

- [ ] RESEARCH_SURVEY_TEMPLATE added with 6 entity types
- [ ] CONTRACT_REVIEW_TEMPLATE added with 5 entity types
- [ ] Both templates exported in __all__
- [ ] Tests written and passing
- [ ] PR created with title: `feat: add research_survey and contract_review templates`
