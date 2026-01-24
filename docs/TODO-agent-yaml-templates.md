# TODO: Refactor Templates from Python to YAML

**Agent:** agent-yaml-templates
**Branch:** `feat/yaml-templates`
**Priority:** medium

## Context

Templates are currently defined as Python dictionaries in `src/services/projects/templates.py`. There are 7 templates:
- `COMPANY_ANALYSIS_TEMPLATE`
- `RESEARCH_SURVEY_TEMPLATE`
- `CONTRACT_REVIEW_TEMPLATE`
- `BOOK_CATALOG_TEMPLATE`
- `DRIVETRAIN_COMPANY_TEMPLATE`
- `DRIVETRAIN_COMPANY_TEMPLATE_SIMPLE`
- `DEFAULT_EXTRACTION_TEMPLATE`

Templates are consumed by:
- `src/api/v1/projects.py` - `TEMPLATES` dict for API lookups
- `src/models.py` - `DEFAULT_EXTRACTION_TEMPLATE` in model validator
- `src/services/projects/repository.py` - `COMPANY_ANALYSIS_TEMPLATE` for default project

Validation exists in `src/services/extraction/schema_adapter.py` via `SchemaAdapter.validate_extraction_schema()`.

## Objective

Move templates from Python dictionaries to YAML files with a loader module, enabling non-developers to create/edit templates.

## Tasks

### 1. Add PyYAML dependency

**File:** `requirements.txt`

**Requirements:**
- Add `pyyaml>=6.0` to requirements.txt

### 2. Create template loader module

**File:** `src/services/projects/template_loader.py` (NEW)

**Requirements:**
- Create `TemplateLoadError` exception with `template_name` and `errors` attributes
- Create `TemplateRegistry` class with:
  - `__init__()` - Initialize empty `_templates` dict, `_loaded` flag, `_adapter` (SchemaAdapter)
  - `load_templates(templates_dir: Path | None = None)` - Load all YAML files from directory
  - `_load_and_validate(yaml_file: Path) -> dict` - Load single file, validate with SchemaAdapter
  - `get(name: str) -> dict | None` - Get template by name (lazy-loads if needed)
  - `get_all() -> dict[str, dict]` - Get all templates (returns copy)
  - `list_names() -> list[str]` - List all template names
- Create global `_registry` instance
- Create module-level functions that delegate to global registry:
  - `get_template(name: str) -> dict | None`
  - `get_all_templates() -> dict[str, dict]`
  - `list_template_names() -> list[str]`
  - `load_templates(templates_dir: Path | None = None) -> None`
- Default templates_dir: `Path(__file__).parent / "templates"`
- On validation failure, raise `TemplateLoadError` with all errors
- Log warnings for schema validation warnings (don't fail)

### 3. Create templates directory with YAML files

**Directory:** `src/services/projects/templates/` (NEW)

**Files to create:**

1. `company_analysis.yaml` - Convert from `COMPANY_ANALYSIS_TEMPLATE`
2. `research_survey.yaml` - Convert from `RESEARCH_SURVEY_TEMPLATE`
3. `contract_review.yaml` - Convert from `CONTRACT_REVIEW_TEMPLATE`
4. `book_catalog.yaml` - Convert from `BOOK_CATALOG_TEMPLATE`
5. `drivetrain_company.yaml` - Convert from `DRIVETRAIN_COMPANY_TEMPLATE`
6. `drivetrain_company_simple.yaml` - Convert from `DRIVETRAIN_COMPANY_TEMPLATE_SIMPLE`
7. `default.yaml` - Convert from `DEFAULT_EXTRACTION_TEMPLATE`

**YAML format:**
```yaml
name: template_name
description: Human readable description
source_config:
  type: web
  group_by: category
extraction_context:
  source_type: source description
  source_label: Label
  entity_id_fields:
    - entity_id
    - name
extraction_schema:
  name: schema_name
  version: "1.0"
  field_groups:
    - name: group_name
      description: Group description
      is_entity_list: false
      fields:
        - name: field_name
          field_type: text
          description: Field description
          required: true
          default: ""
entity_types:
  - name: entity_name
    description: Entity description
prompt_templates: {}
is_template: true
```

**Important:** Preserve all fields exactly from the Python dicts, including:
- Multi-line `prompt_hint` strings (use YAML `|` block scalar)
- All `enum_values` lists
- All `entity_types` with their `attributes`
- The `prompt_templates` dict (even if empty)

### 4. Update templates.py for backward compatibility

**File:** `src/services/projects/templates.py` (MODIFY)

**Requirements:**
- Delete all template dict definitions (COMPANY_ANALYSIS_TEMPLATE, etc.)
- Keep the module docstring
- Add `__getattr__` function for lazy loading:
  ```python
  def __getattr__(name: str):
      template_mapping = {
          "COMPANY_ANALYSIS_TEMPLATE": "company_analysis",
          "RESEARCH_SURVEY_TEMPLATE": "research_survey",
          "CONTRACT_REVIEW_TEMPLATE": "contract_review",
          "BOOK_CATALOG_TEMPLATE": "book_catalog",
          "DRIVETRAIN_COMPANY_TEMPLATE": "drivetrain_company",
          "DRIVETRAIN_COMPANY_TEMPLATE_SIMPLE": "drivetrain_company_simple",
          "DEFAULT_EXTRACTION_TEMPLATE": "default",
      }
      if name in template_mapping:
          from services.projects.template_loader import get_template
          template = get_template(template_mapping[name])
          if template is None:
              raise AttributeError(f"Template not found: {name}")
          return template
      raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
  ```
- Keep `__all__` list with the constant names

### 5. Add startup loading to main.py

**File:** `src/main.py` (MODIFY)

**Requirements:**
- Import `load_templates` and `TemplateLoadError` from `services.projects.template_loader`
- In the lifespan startup section (look for `@asynccontextmanager` and `async def lifespan`):
  - Call `load_templates()`
  - Wrap in try/except to catch `TemplateLoadError`
  - Log error and re-raise on failure (fail fast)
- Add log message on successful load

### 6. Write tests for template loader

**File:** `tests/test_template_loader.py` (NEW)

**Test cases:**

```python
class TestTemplateRegistry:
    def test_load_valid_templates(self, tmp_path):
        """Create valid YAML, load, verify it's in registry."""

    def test_load_invalid_schema_fails(self, tmp_path):
        """Create YAML with missing field_groups, verify TemplateLoadError raised."""

    def test_get_nonexistent_returns_none(self):
        """Verify get() returns None for unknown template name."""

    def test_list_names_returns_all_templates(self, tmp_path):
        """Load multiple templates, verify list_names() returns all."""


class TestBackwardCompatibility:
    def test_import_company_analysis_template(self):
        """from services.projects.templates import COMPANY_ANALYSIS_TEMPLATE works."""

    def test_import_default_template(self):
        """from services.projects.templates import DEFAULT_EXTRACTION_TEMPLATE works."""

    def test_all_templates_importable(self):
        """All 7 template constants can be imported."""


class TestProductionTemplates:
    def test_all_yaml_files_load(self):
        """All 7 YAML files in templates/ directory load successfully."""

    def test_all_yaml_files_validate(self):
        """All loaded templates pass SchemaAdapter validation."""

    def test_template_names_match_filenames(self):
        """Template 'name' field matches the YAML filename (without .yaml)."""
```

**Use fixtures:**
- `tmp_path` (pytest built-in) for creating test YAML files
- Create helper to write test YAML files

## Constraints

- Do NOT modify `SchemaAdapter` - use it as-is for validation
- Do NOT add hot-reload - startup-only loading is sufficient
- Do NOT change the template structure/fields - only the storage format
- Do NOT run full test suite - only run tests in Test Scope below
- Do NOT lint entire codebase - only lint files in Lint Scope below
- PRESERVE all template data exactly when converting to YAML

## Test Scope

**ONLY run these tests - do NOT run `pytest` without arguments:**

```bash
# New loader tests
pytest tests/test_template_loader.py -v

# Existing template tests (must still pass)
pytest tests/test_project_templates.py tests/test_template_compatibility.py -v
```

## Lint Scope

**ONLY lint these files - do NOT run `ruff check src/`:**

```bash
ruff check src/services/projects/template_loader.py src/services/projects/templates.py src/main.py tests/test_template_loader.py
```

## Verification

Before creating PR, run ONLY the scoped commands above:

1. `pytest tests/test_template_loader.py -v` - Must pass
2. `pytest tests/test_project_templates.py tests/test_template_compatibility.py -v` - Must pass
3. `ruff check {your files}` - Must be clean
4. Manual check: `python -c "from services.projects.templates import COMPANY_ANALYSIS_TEMPLATE; print(COMPANY_ANALYSIS_TEMPLATE['name'])"` - Should print `company_analysis`

## Definition of Done

- [ ] `requirements.txt` has `pyyaml>=6.0`
- [ ] `src/services/projects/template_loader.py` created with TemplateRegistry
- [ ] `src/services/projects/templates/` directory with 7 YAML files
- [ ] `src/services/projects/templates.py` updated with `__getattr__` lazy loading
- [ ] `src/main.py` loads templates at startup
- [ ] `tests/test_template_loader.py` with all test cases passing
- [ ] Existing template tests still pass
- [ ] Lint clean (scoped)
- [ ] PR created with title: `feat: refactor templates from Python to YAML`
