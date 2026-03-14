# Extraction Templates

Templates define what data the pipeline extracts from source content. Each YAML file here is a reusable schema that can be applied to a project.

## Template Structure

```yaml
name: my_template
description: What this template extracts
source_config:
  type: web
  group_by: company          # How sources are grouped for consolidation
extraction_context:
  source_type: company website  # Appears in LLM prompt ("extracting from {source_type}")
  source_label: Company         # Label for source groups
  entity_id_fields:             # Fields used to identify/dedup entities
    - entity_id
    - name
    - id
extraction_schema:
  name: schema_name
  version: "1.0"
  field_groups:
    - name: group_name
      description: ...
      is_entity_list: false
      prompt_hint: |
        ...
      fields:
        - name: field_name
          field_type: text
          required: false
          description: "..."
entity_types: []
prompt_templates: {}
is_template: true
```

## Field Types

| Type | Grounding | Use For | LLM Returns |
|------|-----------|---------|-------------|
| `text` | required | Short factual values (names, IDs, locations) | `"string"` |
| `integer` | required | Counts, quantities | `123` |
| `float` | required | Measurements, scores | `1.23` |
| `boolean` | semantic | Yes/no facts, capability flags | `true`/`false` |
| `enum` | required | Categorical values from a fixed set | `"option_a"` |
| `list` | required | Simple string lists (tags, certifications) | `["a", "b"]` |
| `summary` | none | Descriptive text, narratives, details | `"long text..."` |

### Grounding Modes

- **required** — Value must be traceable to a verbatim quote in the source. Low-grounding values are dropped or sent to LLM rescue.
- **semantic** — Quote must appear in source, but value can be derived (used for booleans).
- **none** — No grounding check. Value passes through at confidence 1.0 (used for summaries where the LLM synthesizes text).

## Field Groups: Scalar vs Entity List

There are two kinds of field groups. Choosing the right one is critical for extraction quality.

### Scalar Groups (`is_entity_list: false`)

One set of values per source page. Use for properties that describe the source itself.

```yaml
- name: company_info
  is_entity_list: false
  fields:
    - name: company_name
      field_type: text
    - name: employee_count
      field_type: integer
    - name: headquarters_location
      field_type: text
```

The LLM returns one JSON object with one value per field:
```json
{"fields": {"company_name": {"value": "Acme Corp", "confidence": 0.9, "quote": "..."}}}
```

### Entity List Groups (`is_entity_list: true`)

Multiple entities per source page. Use for items, products, locations, people — anything where a page may list several.

```yaml
- name: company_locations
  is_entity_list: true
  fields:
    - name: city
      field_type: text
    - name: country
      field_type: text
      required: true
    - name: site_type
      field_type: text
```

The LLM returns an array of entities, each with its own quote:
```json
{"company_locations": [
  {"city": "Detroit", "country": "USA", "site_type": "manufacturing", "_quote": "..."},
  {"city": "Shanghai", "country": "China", "site_type": "sales", "_quote": "..."}
]}
```

Entity lists support **pagination** — if a page has more entities than the LLM can extract in one pass, the pipeline requests additional pages automatically.

### When to Use Which

| Data Shape | Use | Example |
|------------|-----|---------|
| One value per source | Scalar group | Company name, HQ location, employee count |
| Multiple items per source | Entity list | Products, locations, certifications list, job postings |
| Structured objects in a list | **Entity list** | Locations with city/country/type fields |
| Simple string tags | `list` field in scalar group | `["ISO 9001", "CE"]` |

**Key rule: If your list items have internal structure (multiple sub-fields), use an entity list group — not a `list` field type.** The `list` field type works well for simple string arrays but the v2 per-field format cannot represent complex objects reliably. This was discovered when `locations` defined as `field_type: list` with description `"List of {city, country, site_type} objects"` produced zero extractions across 6,966 attempts, while the same data extracted successfully as an entity list.

## Prompt Hints

Each field group can have a `prompt_hint` that guides the LLM. These appear directly in the system prompt.

**Good prompt hints:**
- Tell the LLM *where* to look: "Look in About Us, footer, contact pages"
- Give positive examples: "ISO 9001, ISO 14001, ATEX, UL, CE"
- Clarify ambiguity: "Do NOT confuse manufacturing with distribution or reselling"
- Set extraction boundaries: "Extract GEARBOX products only"

```yaml
prompt_hint: |
  Extract each company LOCATION as a separate entity:
  - Headquarters, manufacturing plants, factories, production sites
  - Sales offices, service centers, branch offices, R&D centers
  - Look in "About Us", "Contact", "Locations", footer sections
  - If only a country is mentioned without a city, still extract it
```

**Avoid in prompt hints:**
- Output format instructions (the pipeline handles this)
- Confidence scoring rules (built into the system prompt)
- Quoting instructions (handled by the extraction pipeline)

## Field Descriptions

Field descriptions appear in the LLM prompt as `"field_name" (field_type): description`. Keep them short and specific.

**Good:** `"Torque rating in Nm"` — tells the LLM the unit to use.

**Good:** `"headquarters, manufacturing, sales, service, R&D, warehouse, office"` — for semi-constrained text fields, listing expected values in the description guides the LLM without the rigidity of an enum.

**Bad:** `"List of {city, country, site_type} objects"` — describes a complex structure that doesn't fit the extraction format. Use an entity list group instead.

## Consolidation Strategies

Fields can specify how values from multiple source pages are merged during consolidation.

| Strategy | Use For | Behavior |
|----------|---------|----------|
| (default) | Most fields | `frequency` for text, `any_true` for booleans, `weighted_median` for numerics |
| `union_dedup` | List fields | Union of all values, deduplicated |
| `llm_summarize` | Summary fields | LLM synthesizes a summary from all extracted values |
| `longest_top_k` | Long text | Keeps the longest/highest-quality values |

```yaml
- name: manufacturing_details
  field_type: summary
  consolidation_strategy: llm_summarize
```

## Existing Templates

| Template | Domain | Key Groups |
|----------|--------|------------|
| `default.yaml` | Generic | entity_info, key_facts, contact_info, entity_locations |
| `drivetrain_company.yaml` | Industrial drivetrain | manufacturing, services, company_info, products_*, company_meta, company_locations |
| `drivetrain_company_simple.yaml` | Drivetrain (simplified) | company_summary, products_list, company_locations |
| `company_analysis.yaml` | General company | technical_facts |
| `job_listings.yaml` | Job postings | Entity list of jobs |
| `wikipedia_articles.yaml` | Wikipedia | Article facts |
| `book_catalog.yaml` | Books | Book metadata |
| `contract_review.yaml` | Legal contracts | Contract terms |
| `research_survey.yaml` | Academic papers | Research findings |

## Checklist for New Templates

1. Choose `group_by` to match how sources should be consolidated (e.g., `company`, `source`)
2. Use entity list groups for multi-item data with structured sub-fields
3. Use `list` field type only for simple string arrays (tags, certifications)
4. Add `prompt_hint` to every group — the LLM has no domain knowledge by default
5. Set `required: true` with a `default` for fields that should always have a value
6. Use `summary` type for descriptive text that shouldn't be grounding-gated
7. Use `enum` with `enum_values` for constrained categorical fields
8. Set `entity_id_fields` in `extraction_context` to match the primary identifier field in your entity lists
