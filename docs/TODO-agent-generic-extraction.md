# TODO: Make Extraction System Generic

## Context

The extraction system has hardcoded "company documentation" assumptions that prevent it from being used for generic content (recipes, articles, research papers, etc.). Templates should define their own context.

**Current hardcoded patterns:**
- `"from company documentation"` in prompts
- `"Company: {name}"` label in user prompts
- `company_name` parameter throughout codebase
- `product_name` prioritized in entity ID detection
- Validation requires `product_name` or `entity_id` for entity lists

## Objective

Make the extraction system fully generic by allowing templates to define context configuration, while maintaining backward compatibility with existing templates.

## Design

### 1. Template Schema Extension

Extend the **template level** (not nested in extraction_schema) with `extraction_context`:

```python
# Template structure - context at template level for clarity
RECIPE_TEMPLATE = {
    "name": "recipe_extraction",

    # NEW: Extraction context - controls prompt generation
    "extraction_context": {
        "source_type": "recipe website",           # "extracting from {source_type}"
        "source_label": "Recipe Site",             # "{source_label}: {value}" in user prompt
        "entity_id_fields": ["recipe_id", "recipe_name"],  # For deduplication
    },

    # Existing: Schema definition - controls what fields to extract
    "extraction_schema": {
        "name": "recipes",
        "version": "1.0",
        "field_groups": [...]
    }
}
```

**Why at template level (not inside extraction_schema):**
- `extraction_schema` defines WHAT to extract (fields, types, validation)
- `extraction_context` defines HOW to prompt (source type, labels)
- Cleaner separation of concerns
- Easier to override context without touching schema

**Defaults when `extraction_context` not specified:**
```python
DEFAULT_EXTRACTION_CONTEXT = {
    "source_type": "content",                    # Generic
    "source_label": "Source",                    # Generic
    "entity_id_fields": ["entity_id", "name", "id"]  # Common ID patterns
}
```

**Existing templates get explicit context (backward compatible):**
```python
DRIVETRAIN_MFG_TEMPLATE = {
    "name": "drivetrain_mfg",

    "extraction_context": {
        "source_type": "company documentation",   # Explicit, was implicit
        "source_label": "Company",                # Explicit, was hardcoded
        "entity_id_fields": ["product_name", "entity_id", "name", "id"],
    },

    "extraction_schema": {...}  # Unchanged
}
```

### 2. Prompt Templates (After Changes)

**Regular extraction system prompt:**
```
You are extracting {field_group.description} from {context.source_type}.

Fields to extract:
{field_specs}

{prompt_hint}

Output JSON with exactly these fields. Use null for unknown values.
For boolean fields, only return true if there is clear evidence.
```

**User prompt:**
```
{context.source_label}: {source_context}

Extract {field_group.name} information from this content:

---
{content}
---
```

**Example with recipe template:**
- System: `You are extracting Recipe information from recipe blog.`
- User: `Recipe Site: AllRecipes.com\n\nExtract recipes information from this content:`

**Example with company template:**
- System: `You are extracting Manufacturing capabilities from company documentation.`
- User: `Company: Acme Corp\n\nExtract manufacturing information from this content:`

### 3. Parameter Renaming

| Old | New | Reason |
|-----|-----|--------|
| `company_name: str` | `source_context: str` | Generic - the value passed at runtime (e.g., "Acme Corp", "AllRecipes.com") |
| `company=company_name` in logs | `source=source_context` | Consistent logging |
| `"Company: {name}"` in prompt | `"{context.source_label}: {source_context}"` | Template-controlled label |

## Tasks

### Phase 1: Add ExtractionContext Dataclass

**File: `src/services/extraction/schema_adapter.py`**

1. Add `ExtractionContext` dataclass:
```python
@dataclass
class ExtractionContext:
    """Context configuration for extraction prompts."""
    source_type: str = "content"
    source_label: str = "Source"
    entity_id_fields: list[str] = field(default_factory=lambda: ["entity_id", "name", "id"])

    @classmethod
    def from_dict(cls, data: dict | None) -> "ExtractionContext":
        """Create from template's extraction_context dict."""
        if not data:
            return cls()
        return cls(
            source_type=data.get("source_type", "content"),
            source_label=data.get("source_label", "Source"),
            entity_id_fields=data.get("entity_id_fields", ["entity_id", "name", "id"]),
        )
```

2. Add helper to parse full template (not just schema):
```python
def parse_template(self, template: dict) -> tuple[list[FieldGroup], ExtractionContext]:
    """Parse template into FieldGroups and ExtractionContext.

    Args:
        template: Full template dict with extraction_context and extraction_schema.

    Returns:
        Tuple of (field_groups, context).
    """
    schema = template.get("extraction_schema", template)  # Backward compat
    field_groups = self.convert_to_field_groups(schema)
    context = ExtractionContext.from_dict(template.get("extraction_context"))
    return field_groups, context
```

3. Update validation to accept custom `entity_id_fields` (warning not error):
```python
# Rule 7: is_entity_list groups should have at least one identifiable field
# (warning, not error - some lists may not need dedup)
if fg.get("is_entity_list", False):
    field_names = [f.get("name") for f in fg["fields"] if isinstance(f, dict)]
    # Check against common ID patterns - validation doesn't know template context
    common_id_fields = ["entity_id", "name", "id", "product_name"]
    has_id_field = any(name in field_names for name in common_id_fields)
    if not has_id_field:
        warnings.append(
            f"field_groups[{i}] is_entity_list=true but has no common ID field "
            f"for deduplication. Consider adding one of: {common_id_fields}"
        )
```

**Tests:**
- `test_extraction_context_defaults()` - missing context uses defaults
- `test_extraction_context_from_template()` - custom context is parsed
- `test_parse_template_returns_both()` - returns tuple of (field_groups, context)
- `test_validation_warns_on_missing_id_field()` - warning, not error

### Phase 2: Extractor Updates

**File: `src/services/extraction/schema_extractor.py`**

1. Update `__init__` to accept context:
```python
def __init__(self, settings: Settings, llm_queue=None, context: ContextConfig = None):
    self.context = context or ContextConfig()
```

2. Update `_build_system_prompt()`:
```python
def _build_system_prompt(self, field_group: FieldGroup) -> str:
    # ... field_specs building ...

    return f"""You are extracting {field_group.description} from {self.context.source_type}.

Fields to extract:
{fields_str}

{field_group.prompt_hint}

Output JSON with exactly these fields. Use null for unknown values.
For boolean fields, only return true if there is clear evidence.
"""
```

3. Update `_build_entity_list_system_prompt()`:
```python
def _build_entity_list_system_prompt(self, field_group: FieldGroup) -> str:
    # ... existing logic ...

    return f"""You are extracting {field_group.description} from {self.context.source_type}.
    # ... rest unchanged ...
    """
```

4. Rename `company_name` parameter to `source_context`:
```python
async def extract_field_group(
    self,
    content: str,
    field_group: FieldGroup,
    source_context: str | None = None,  # RENAMED
) -> dict[str, Any]:
```

5. Update `_build_user_prompt()`:
```python
def _build_user_prompt(
    self,
    content: str,
    field_group: FieldGroup,
    source_context: str | None,  # RENAMED
) -> str:
    context_line = f"{self.context.context_label}: {source_context}\n\n" if source_context else ""

    return f"""{context_line}Extract {field_group.name} information from this content:

---
{content[:8000]}
---"""
```

**Tests:**
- `test_system_prompt_uses_context_source_type()`
- `test_user_prompt_uses_context_label()`
- `test_default_context_is_generic()`

### Phase 3: Orchestrator Updates

**File: `src/services/extraction/schema_orchestrator.py`**

1. Update `__init__` to accept context:
```python
def __init__(self, schema_extractor: SchemaExtractor, context: ContextConfig = None):
    self._extractor = schema_extractor
    self._context = context or ContextConfig()
```

2. Rename `company_name` to `source_context` in `extract_all_groups()`:
```python
async def extract_all_groups(
    self,
    source_id: UUID,
    markdown: str,
    source_context: str,  # RENAMED from company_name
    field_groups: list[FieldGroup],
) -> list[dict]:
```

3. Update logging to use generic terms:
```python
logger.info(
    "schema_extraction_started",
    source_id=str(source_id),
    source=source_context,  # was: company=company_name
    groups=len(groups),
    chunks=len(chunks),
)
```

4. Update `_merge_entity_lists()` to use context's entity_id_fields:
```python
def _merge_entity_lists(self, chunk_results: list[dict]) -> dict:
    # ... existing key detection ...

    for entity in entities:
        # Use context-defined ID fields instead of hardcoded list
        entity_id = None
        for id_field in self._context.entity_id_fields:
            if entity.get(id_field):
                entity_id = entity.get(id_field)
                break

        if entity_id and entity_id not in seen_ids:
            seen_ids.add(entity_id)
            all_entities.append(entity)
        elif not entity_id:
            all_entities.append(entity)
```

**Tests:**
- `test_orchestrator_uses_custom_entity_id_fields()`
- `test_logging_uses_generic_source_term()`

### Phase 4: Pipeline Updates

**File: `src/services/extraction/pipeline.py`**

1. Update signature to accept full template or context:
```python
async def extract_source(
    self,
    source,
    source_context: str,  # RENAMED from company_name
    field_groups: list[FieldGroup] | None = None,
    extraction_context: ExtractionContext | None = None,  # NEW
    schema_name: str = "unknown",
) -> list:
```

2. Update `run()` to use `parse_template()`:
```python
async def run(self, project, companies, template: dict):
    """Run extraction pipeline for project."""
    adapter = SchemaAdapter()
    field_groups, extraction_context = adapter.parse_template(template)

    # Create orchestrator with context
    orchestrator = SchemaExtractionOrchestrator(
        self._extractor,
        context=extraction_context,
    )

    for company in companies:
        await self.extract_source(
            source=company.source,
            source_context=company.name,  # RENAMED
            field_groups=field_groups,
            extraction_context=extraction_context,
            schema_name=template.get("name", "unknown"),
        )
```

**Tests:**
- `test_pipeline_parses_template_context()`
- `test_pipeline_passes_context_to_orchestrator()`

### Phase 5: Update Existing Templates

**File: `src/services/projects/templates.py`**

Add explicit `extraction_context` to all existing templates:

```python
DRIVETRAIN_MFG_TEMPLATE = {
    "name": "drivetrain_mfg",
    "extraction_context": {
        "source_type": "company documentation",
        "source_label": "Company",
        "entity_id_fields": ["product_name", "entity_id", "name", "id"]
    },
    "extraction_schema": {
        "name": "drivetrain",
        "version": "1.0",
        "field_groups": [...]  # Unchanged
    }
}

COMPANY_ANALYSIS_TEMPLATE = {
    "name": "company_analysis",
    "extraction_context": {
        "source_type": "company website",
        "source_label": "Company",
        "entity_id_fields": ["entity_id", "name", "id"]
    },
    "extraction_schema": {...}
}

# Similar updates for all 6 templates
```

This makes existing behavior explicit while allowing new templates to use different contexts.

### Phase 6: Update Callers

Search for all callers passing `company_name` and update to `source_context`:
```bash
grep -r "company_name" src/ --include="*.py"
```

## Constraints

- **Backward Compatibility**: Existing templates without `context` field must work with generic defaults
- **No Breaking Changes**: Old API signatures deprecated but still functional (use `source_context` with fallback to `company_name`)
- **Generic Defaults**: When context not specified, use "content"/"Source" not "company documentation"/"Company"

## Verification

1. **Existing templates work**: Run all tests in `tests/test_template_compatibility.py`
2. **Generic defaults**: Create test template without `context` field, verify prompts use "content"/"Source"
3. **Custom context**: Create test template with custom context, verify prompts use custom values
4. **Entity dedup**: Test entity list with custom `entity_id_fields` deduplicates correctly

```bash
pytest tests/test_template_compatibility.py tests/test_template_prompting.py tests/test_generic_extraction.py -v
```

## Example: Non-Company Template

After implementation, this template should work:

```python
RECIPE_TEMPLATE = {
    "name": "recipe_extraction",

    # Context at template level - controls prompting
    "extraction_context": {
        "source_type": "recipe blog",
        "source_label": "Recipe Site",
        "entity_id_fields": ["recipe_name", "recipe_id"]
    },

    # Schema defines what to extract
    "extraction_schema": {
        "name": "recipes",
        "version": "1.0",
        "field_groups": [
            {
                "name": "recipes",
                "description": "Recipe information",
                "is_entity_list": True,
                "fields": [
                    {"name": "recipe_name", "field_type": "text", "description": "Name of recipe"},
                    {"name": "ingredients", "field_type": "list", "description": "List of ingredients"},
                    {"name": "prep_time_minutes", "field_type": "integer", "description": "Prep time"},
                ]
            }
        ]
    }
}
```

Generated prompt would be:
```
You are extracting Recipe information from recipe blog.

For each recipe found, extract:
- "recipe_name" (text): Name of recipe
- "ingredients" (list): List of ingredients
- "prep_time_minutes" (integer): Prep time

Look for all recipes mentioned in the content. Each recipe should be a separate item in the list. For list fields, collect all mentioned values.

Output JSON with structure:
{
  "recipes": [
    {"recipe_name": "...", ...},
    ...
  ],
  "confidence": 0.0-1.0
}
```

User prompt:
```
Recipe Site: AllRecipes.com

Extract recipes information from this content:

---
{scraped content}
---
```
