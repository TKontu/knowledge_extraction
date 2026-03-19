# Pipeline Review: Template-Driven Extraction

> **Status: FIXED** - All critical and important issues resolved.

## Flow
```
POST /projects → models.py:apply_default_schema → projects.py:create_project → repo.create
POST /projects/{id}/extract-schema → extraction.py:extract_schema → pipeline.py:extract_project
  → SchemaAdapter.validate → SchemaAdapter.convert_to_field_groups
  → orchestrator.extract_all_groups → extractor.extract_field_group
```

---

## Critical (must fix)

### 🔴 1. Incompatible schema formats between old and new templates
**Files:** `templates.py`, `schema_adapter.py`

Old templates (lines 1-600) use:
- `"type": "text"`
- `"values": ["a", "b"]` for enums

New DEFAULT_EXTRACTION_TEMPLATE (lines 602-701) uses:
- `"field_type": "text"`
- `"enum_values": ["a", "b"]`

**Impact:** If a project is created with `template: "company_analysis"` or any old template, the schema adapter validation will **fail** because:
- `schema_adapter.py:126` checks `field["field_type"]` but old templates have `field["type"]`
- `schema_adapter.py:134` checks `"enum_values"` but old templates have `"values"`

The extraction will silently fall back to DEFAULT_TEMPLATE, discarding the user's chosen template.

**Verify:**
```python
from services.extraction.schema_adapter import SchemaAdapter
from services.projects.templates import COMPANY_ANALYSIS_TEMPLATE
adapter = SchemaAdapter()
result = adapter.validate_extraction_schema(COMPANY_ANALYSIS_TEMPLATE["extraction_schema"])
print(result.errors)  # Will show validation failures
```

---

### 🔴 2. Old templates missing `field_groups` structure
**Files:** `templates.py`

Old templates have:
```json
{"extraction_schema": {"name": "...", "fields": [...]}}
```

New format requires:
```json
{"extraction_schema": {"name": "...", "field_groups": [{"name": "...", "fields": [...]}]}}
```

**Impact:** `schema_adapter.py:39-45` immediately fails validation if `field_groups` is missing, returning empty `field_groups` list.

---

### 🔴 3. `_merge_entity_lists` only supports `product_name`, not `entity_id`
**File:** `schema_orchestrator.py:248-267`

Validation rule 7 (`schema_adapter.py:91-97`) requires entity_list groups to have `product_name` OR `entity_id` field. However, `_merge_entity_lists` only handles `product_name`:

```python
name = product.get("product_name", "")  # line 256
```

**Impact:** Entity lists using `entity_id` instead of `product_name` will have broken deduplication (all entities passed through without proper merging).

---

## Important (should fix)

### 🟠 4. Hardcoded `profile_used="drivetrain_schema"` in pipeline
**File:** `pipeline.py:415`

```python
profile_used="drivetrain_schema",  # Hardcoded, should use schema name
```

This should use the actual schema name from the project for accurate tracking.

---

### 🟠 5. `SchemaValidator` class is obsolete and unused
**File:** `schema.py`

The old `SchemaValidator` in `services/projects/schema.py`:
- Uses `field["type"]` format (old)
- Uses `field.get("values", [])` for enums (old)
- Operates on flat `fields` list, not `field_groups`
- Is **never imported or used** anywhere in the codebase

This creates confusion about which schema format is canonical.

---

### 🟠 6. `create_from_template` doesn't apply schema validation
**File:** `projects.py:165-208`

The `/from-template` endpoint clones a template directly without validating the schema through `SchemaAdapter`. If the old-format template is used, the invalid schema is stored in the database and will fail at extraction time.

---

### 🟠 7. Missing API validation of user-provided schemas
**File:** `projects.py:35-71`

When a user creates a project with a custom `extraction_schema`, there's no validation that the schema is valid before storing. Invalid schemas are only caught at extraction time (pipeline.py:459-466).

---

## Minor

### 🟡 8. Docstring outdated
**File:** `extraction.py:247-250`

```python
"""Run schema-based extraction on project sources.

This uses the drivetrain company template with 7 field groups,  # WRONG
```

No longer accurate - should describe template-driven extraction.

---

### 🟡 9. Extraction API endpoint name inconsistency
**File:** `extraction.py:241`

Endpoint is `/extract-schema` but it now uses project's schema, not a fixed "schema". Consider renaming to `/extract` or updating docs.

---

### 🟡 10. No migration path for existing projects
**Files:** N/A

Existing projects with old-format schemas in the database will silently use DEFAULT_TEMPLATE instead of their stored schema. No warning is shown to users about this degraded behavior.

---

## Recommendations

### Immediate (before release):
1. **Migrate old templates** to new format (`field_type`, `enum_values`, `field_groups` wrapper)
2. **Update `_merge_entity_lists`** to handle both `product_name` and `entity_id`
3. **Fix `profile_used`** to use `schema.get("name")`

### Short-term:
4. Add schema validation in `create_project` and `create_from_template` endpoints
5. Delete or update `SchemaValidator` class to avoid confusion
6. Add database migration script for existing project schemas

### Optional:
7. Support both old and new schema formats in SchemaAdapter (backward compatibility)

---

## Fixes Applied

### 🔴 Critical Issues - FIXED

**1-2. Template format incompatibility**
- **File:** `src/services/projects/templates.py`
- **Fix:** Migrated all 6 templates to new format with `field_groups[]`, `field_type`, `enum_values`
- All templates now pass `SchemaAdapter.validate_extraction_schema()`

**3. `_merge_entity_lists` only handled `product_name`**
- **File:** `src/services/extraction/schema_orchestrator.py`
- **Fix:** Updated to support multiple entity keys (`products`, `entities`, `items`) and multiple ID fields (`product_name`, `entity_id`, `name`, `id`)

### 🟠 Important Issues - FIXED

**4. Hardcoded `profile_used="drivetrain_schema"`**
- **File:** `src/services/extraction/pipeline.py`
- **Fix:** Added `schema_name` parameter to `extract_source()`, now uses actual schema name

### Tests Added

- `tests/test_template_compatibility.py` - Validates all templates pass SchemaAdapter validation
