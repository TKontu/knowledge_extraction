# Handoff: YAML Template System

## Completed

### YAML Template Refactor (PR #59 + follow-up fix)

Refactored extraction templates from Python dictionaries to YAML files:

1. **Template Loader Module** (`src/services/projects/template_loader.py`)
   - `TemplateRegistry` class with validation via `SchemaAdapter`
   - Global functions: `get_template()`, `list_template_names()`, `get_all_templates()`
   - Startup loading with fail-fast on invalid templates

2. **YAML Templates** (`src/services/projects/templates/`)
   - 7 templates converted to YAML format
   - `company_analysis.yaml`, `research_survey.yaml`, `contract_review.yaml`
   - `book_catalog.yaml`, `drivetrain_company.yaml`, `drivetrain_company_simple.yaml`
   - `default.yaml`

3. **Backward Compatibility** (`src/services/projects/templates.py`)
   - `__getattr__` lazy loading for old constant imports
   - `COMPANY_ANALYSIS_TEMPLATE`, `DEFAULT_EXTRACTION_TEMPLATE`, etc. still work

4. **API Integration** (`src/api/v1/projects.py`)
   - Fixed: Now uses `template_loader` instead of hardcoded dict
   - `GET /api/v1/projects/templates` returns all 7 templates
   - `POST /api/v1/projects/from-template` works with any template

### Tests
- `tests/test_template_loader.py` - 10 tests for loader
- `tests/test_project_templates.py` - 5 tests for backward compat
- `tests/test_template_compatibility.py` - 18 tests for schema validation
- All 33 template-related tests passing

## In Progress

- MCP Server implementation (`docs/TODO-agent-mcp-server.md` assigned)

## Next Steps

- [ ] Review and merge MCP server PR when ready
- [ ] Consider removing deprecated Python template constants after migration period
- [ ] Add more domain-specific templates as needed

## Key Files

| File | Purpose |
|------|---------|
| `src/services/projects/template_loader.py` | YAML loading + validation |
| `src/services/projects/templates.py` | Backward-compat lazy exports |
| `src/services/projects/templates/*.yaml` | 7 template definitions |
| `src/api/v1/projects.py` | API endpoints using loader |
| `tests/test_template_loader.py` | Loader tests |

## Context

### Default Template for New Projects

Projects created without a template get `default` schema applied via:
1. `ProjectCreate` model validator in `src/models.py:307-314`
2. Pipeline fallback in `src/services/extraction/pipeline.py`

### Template Names

| YAML File | Template Name | Old Constant |
|-----------|---------------|--------------|
| company_analysis.yaml | company_analysis | COMPANY_ANALYSIS_TEMPLATE |
| research_survey.yaml | research_survey | RESEARCH_SURVEY_TEMPLATE |
| contract_review.yaml | contract_review | CONTRACT_REVIEW_TEMPLATE |
| book_catalog.yaml | book_catalog | BOOK_CATALOG_TEMPLATE |
| drivetrain_company.yaml | drivetrain_company_analysis | DRIVETRAIN_COMPANY_TEMPLATE |
| drivetrain_company_simple.yaml | drivetrain_company_simple | DRIVETRAIN_COMPANY_TEMPLATE_SIMPLE |
| default.yaml | default | DEFAULT_EXTRACTION_TEMPLATE |

---

**Recommendation:** Run `/clear` to start fresh session.
