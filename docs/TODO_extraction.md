# TODO: Extraction Module

## Overview

Extracts structured data from scraped content using LLM with **project-defined schemas**.

**Architecture:** Uses the generalized project-based system. See `docs/TODO_generalization.md`.

## Status

**Completed (PRs #8, #9):**
- Built-in profiles defined in `init.sql` (technical_specs, api_docs, security, pricing, general)
- LLM configuration in `config.py` (OpenAI-compatible endpoints, model names, timeouts)
- Profile ORM model exists (`pipeline/orm_models.py` - PR #4)
- **Document chunking module** (`services/llm/chunking.py`, 17 tests)
- **LLM client implementation** (`services/llm/client.py`, 9 tests)
- **Data models** (DocumentChunk, ExtractedFact, ExtractionResult)
- **Profile repository** (`services/extraction/profiles.py`, 10 tests)
- **Extraction orchestrator** (`services/extraction/extractor.py`, 9 tests)
- **Fact validator** (`services/extraction/validator.py`, 11 tests)

**Completed (Current):**
- **Extraction API endpoints** (`api/v1/extraction.py`, 25 tests)
  - POST /api/v1/projects/{project_id}/extract (async job creation)
  - GET /api/v1/projects/{project_id}/extractions (list with filtering)
  - Full integration with ProjectRepository, SourceRepository, ExtractionRepository
  - Pagination, filtering by source_id, source_group, extraction_type, min_confidence

**Pending:**
- Schema-driven extraction (use project.extraction_schema)
- Dynamic prompt generation from project schema
- Store results in `extractions` table (not `facts`)
- Integration tests with mocked LLM
- Legacy API endpoints (POST /api/v1/extract, GET /api/v1/profiles)

**Refactoring Required (Generalization):**
- Replace fixed fact schema with dynamic project schema
- Replace category validation with project-defined categories
- Update storage to use `extractions` table

---

## Core Concept: Schema-Driven Extraction

Instead of extracting a fixed fact schema, extraction uses the project's `extraction_schema`:

```python
# Current (fixed)
facts = await extractor.extract(page, profile="technical_specs")
# Returns: list[ExtractedFact] with fact_text, category, confidence, source_quote

# Generalized
extractions = await extractor.extract(source, project)
# Returns: list[Extraction] with data matching project.extraction_schema
```

---

## Implementation Tasks

### Extraction Service (Schema-Aware)

```python
# pipeline/services/extraction/service.py
from ..projects.schema import SchemaValidator
from ..llm.client import LLMClient
from ..llm.chunking import chunk_document
from .prompt_builder import DynamicPromptBuilder

class ExtractionService:
    """Orchestrates extraction using project schema."""

    def __init__(
        self,
        llm_client: LLMClient,
        prompt_builder: DynamicPromptBuilder,
    ):
        self.llm = llm_client
        self.prompt_builder = prompt_builder

    async def extract(
        self,
        source: Source,
        project: Project,
    ) -> list[dict]:
        """Extract data from source using project schema."""
        # 1. Chunk the content
        chunks = chunk_document(
            source.content,
            max_tokens=8000,
        )

        # 2. Build validator from project schema
        validator = SchemaValidator(project.extraction_schema)

        # 3. Extract from each chunk
        all_extractions = []
        for i, chunk in enumerate(chunks):
            # Build dynamic prompt from schema
            prompt = self.prompt_builder.build(
                project=project,
                content=chunk.content,
                chunk_context={
                    "source_group": source.source_group,
                    "header_path": chunk.header_path,
                    "chunk_index": i,
                    "total_chunks": len(chunks),
                },
            )

            # Extract via LLM
            raw_extractions = await self.llm.extract_json(
                system_prompt=prompt["system"],
                user_prompt=prompt["user"],
            )

            # Validate each extraction
            for raw in raw_extractions.get("extractions", []):
                is_valid, errors = validator.validate(raw)
                if is_valid:
                    raw["_chunk_index"] = i
                    raw["_chunk_context"] = chunk.header_path
                    all_extractions.append(raw)

        # 4. Deduplicate (exact match on main text field)
        text_field = self._get_text_field(project.extraction_schema)
        unique = self._deduplicate(all_extractions, text_field)

        return unique

    def _get_text_field(self, schema: dict) -> str:
        """Get the primary text field from schema."""
        for field in schema.get("fields", []):
            if field.get("type") == "text" and field.get("required"):
                return field["name"]
        return "text"  # fallback

    def _deduplicate(self, extractions: list[dict], text_field: str) -> list[dict]:
        """Remove exact duplicates based on text field."""
        seen = set()
        unique = []
        for ext in extractions:
            key = ext.get(text_field, "").lower().strip()
            if key and key not in seen:
                seen.add(key)
                unique.append(ext)
        return unique
```

### Dynamic Prompt Builder

```python
# pipeline/services/extraction/prompt_builder.py
from ...models.project import Project

class DynamicPromptBuilder:
    """Builds extraction prompts from project schema."""

    def build(
        self,
        project: Project,
        content: str,
        chunk_context: dict,
    ) -> dict[str, str]:
        """Build system and user prompts from project schema."""
        schema = project.extraction_schema

        # Build field documentation
        field_docs = self._build_field_docs(schema)

        # Build entity type documentation
        entity_docs = self._build_entity_docs(project.entity_types)

        # Check for custom prompt template
        custom_template = project.prompt_templates.get("extraction")
        if custom_template:
            return self._render_custom(custom_template, schema, content, chunk_context)

        # Default prompt
        system = f"""You are extracting structured data from documents.

Project: {project.name}
{project.description or ""}

Source: {chunk_context.get("source_group", "Unknown")}
Section: {" > ".join(chunk_context.get("header_path", []))}

## Extraction Schema: {schema["name"]}

Extract the following fields:
{field_docs}

{entity_docs}

## Output Format

Return JSON:
{{
  "extractions": [
    {{
      {self._build_example_fields(schema)}
    }}
  ]
}}

## Rules
- Only extract factual, specific information
- Skip vague claims, marketing language, and opinions
- Each extraction should be self-contained
- Assign confidence based on how explicit the source is
"""

        user = f"""Extract data from this content:

---
{content}
---"""

        return {"system": system, "user": user}

    def _build_field_docs(self, schema: dict) -> str:
        """Build documentation for schema fields."""
        lines = []
        for field in schema.get("fields", []):
            line = f"- {field['name']} ({field['type']})"
            if field.get("required"):
                line += " [required]"
            if field.get("description"):
                line += f": {field['description']}"
            if field["type"] == "enum":
                line += f" (allowed: {', '.join(field['values'])})"
            lines.append(line)
        return "\n".join(lines)

    def _build_entity_docs(self, entity_types: list) -> str:
        """Build documentation for entity types."""
        if not entity_types:
            return ""

        lines = ["## Entity Types to Identify", ""]
        for et in entity_types:
            attrs = ", ".join(a["name"] for a in et.get("attributes", []))
            desc = et.get("description", "")
            if attrs:
                lines.append(f"- {et['name']}: {desc} (attributes: {attrs})")
            else:
                lines.append(f"- {et['name']}: {desc}")

        return "\n".join(lines)

    def _build_example_fields(self, schema: dict) -> str:
        """Build example JSON field list."""
        fields = [f'"{f["name"]}": ...' for f in schema.get("fields", [])]
        return ", ".join(fields)

    def _render_custom(
        self,
        template: str,
        schema: dict,
        content: str,
        context: dict,
    ) -> dict[str, str]:
        """Render custom prompt template."""
        # Simple variable substitution
        rendered = template.format(
            schema_name=schema["name"],
            field_docs=self._build_field_docs(schema),
            source_group=context.get("source_group", ""),
            header_path=" > ".join(context.get("header_path", [])),
            content=content,
        )
        return {"system": rendered, "user": ""}
```

### Extraction Validator (Schema-Aware)

```python
# pipeline/services/extraction/validator.py
from ...services.projects.schema import SchemaValidator

class ExtractionValidator:
    """Validates extraction results against project schema."""

    def __init__(self, project: Project):
        self.project = project
        self.schema_validator = SchemaValidator(project.extraction_schema)
        self.schema = project.extraction_schema

    def validate(self, data: dict) -> tuple[bool, list[str]]:
        """Validate extraction data. Returns (is_valid, errors)."""
        errors = []

        # Schema validation
        is_valid, schema_errors = self.schema_validator.validate(data)
        if not is_valid:
            errors.extend(schema_errors)

        # Confidence threshold
        confidence = data.get("confidence", 1.0)
        if confidence < 0.5:
            errors.append(f"confidence {confidence} below threshold 0.5")

        # Minimum text length (for primary text field)
        text_field = self._get_text_field()
        text_value = data.get(text_field, "")
        if len(text_value) < 20:
            errors.append(f"{text_field} too short (min 20 chars)")

        return len(errors) == 0, errors

    def _get_text_field(self) -> str:
        """Get the primary text field from schema."""
        for field in self.schema.get("fields", []):
            if field.get("type") == "text" and field.get("required"):
                return field["name"]
        return "text"
```

---

## Legacy Support

During transition, maintain backward compatibility with existing profiles:

```python
# Adapter: Profile -> Project schema
def profile_to_project_schema(profile: Profile) -> dict:
    """Convert legacy profile to project extraction_schema."""
    return {
        "name": "technical_fact",
        "fields": [
            {"name": "fact_text", "type": "text", "required": True},
            {"name": "category", "type": "enum", "values": profile.categories, "required": True},
            {"name": "confidence", "type": "float", "min": 0.0, "max": 1.0, "default": 0.8},
            {"name": "source_quote", "type": "text", "required": False},
        ],
    }
```

---

## API Endpoints

```python
# POST /api/v1/projects/{project_id}/extract
# Request:
{
    "source_ids": ["uuid1", "uuid2"],  # Or omit for all pending sources
    "profile": "detailed"  # Optional depth override
}

# Response:
{
    "job_id": "uuid",
    "status": "queued",
    "source_count": 2
}

# GET /api/v1/projects/{project_id}/extractions
# Response:
{
    "extractions": [
        {
            "id": "uuid",
            "source_id": "uuid",
            "data": {"fact_text": "...", "category": "api", ...},
            "confidence": 0.95,
            "extracted_at": "2024-01-01T00:00:00Z"
        }
    ],
    "total": 100
}
```

### Legacy Endpoints (Backward Compatible)

```python
# POST /api/v1/extract (uses default project)
{
    "page_ids": ["uuid1", "uuid2"],
    "profile": "api_docs"
}

# GET /api/v1/profiles (returns legacy profiles)
{
    "profiles": [
        {"name": "technical_specs", "categories": [...], "depth": "detailed"}
    ]
}
```

---

## Data Flow

```
Source content
    ↓
chunk_document() → list[DocumentChunk]
    ↓
For each chunk:
    ↓
DynamicPromptBuilder.build() → {system, user}
    ↓
LLMClient.extract_json() → {"extractions": [...]}
    ↓
SchemaValidator.validate() → filter invalid
    ↓
Deduplicate by text field
    ↓
Store in extractions table (JSONB data)
    ↓
Generate embedding → Store in Qdrant
```

---

## File Structure

```
pipeline/
├── services/
│   ├── extraction/
│   │   ├── __init__.py
│   │   ├── service.py          # ExtractionService (schema-aware)
│   │   ├── prompt_builder.py   # DynamicPromptBuilder
│   │   ├── validator.py        # ExtractionValidator
│   │   ├── profiles.py         # ProfileRepository (legacy)
│   │   └── extractor.py        # ExtractionOrchestrator (legacy)
│   └── llm/
│       ├── client.py           # LLMClient
│       ├── chunking.py         # Semantic chunking
│       └── merging.py          # Chunk result merging
├── models/
│   ├── extraction.py           # ExtractedData, ExtractionResult
│   └── project.py              # ExtractionSchema, FieldDefinition
└── api/
    └── v1/
        └── extraction.py       # Extraction endpoints
```

---

## Implementation Tasks

### Phase 1: Schema-Aware Service
- [ ] Create DynamicPromptBuilder
- [ ] Create ExtractionService (uses project schema)
- [ ] Create ExtractionValidator (schema-aware)
- [ ] Update LLMClient for generic JSON extraction

### Phase 2: Storage Integration
- [ ] Store results in `extractions` table
- [ ] Include chunk context in metadata
- [ ] Generate embeddings for text field
- [ ] Store in Qdrant with project_id

### Phase 3: API Endpoints
- [x] Create POST /api/v1/projects/{project_id}/extract
- [x] Create GET /api/v1/projects/{project_id}/extractions
- [x] Add extraction router to main.py
- [x] Write comprehensive tests (25 tests)
- [ ] Maintain legacy POST /api/v1/extract (uses default project)
- [ ] Maintain legacy GET /api/v1/profiles

### Phase 4: Integration Tests
- [ ] End-to-end extraction with mocked LLM
- [ ] Multiple project schema testing
- [ ] Error handling scenarios
- [ ] Deduplication verification

---

## Configuration

```yaml
extraction:
  max_chunk_tokens: 8000
  min_text_length: 20
  max_extractions_per_source: 100
  confidence_threshold: 0.5

  # LLM settings
  temperature: 0.1  # Low for consistent extraction
  max_retries: 3
  timeout_seconds: 900
```

---

## Testing Checklist

### Completed (PRs #8, #9)
- [x] Unit: Chunking produces correct sizes
- [x] Unit: Headers preserved in chunks
- [x] Unit: Profile loading from database
- [x] Unit: Fact validation filters correctly

### Pending
- [ ] Unit: DynamicPromptBuilder generates valid prompts
- [ ] Unit: SchemaValidator validates extraction data
- [ ] Unit: Deduplication removes exact matches
- [ ] Integration: Extract from sample markdown with project schema
- [ ] Integration: Multiple chunk extraction works
- [ ] Integration: End-to-end with mocked LLM
- [ ] Integration: Storage in extractions table

---

## Example: Company Analysis Extraction

**Project Schema:**
```json
{
  "name": "technical_fact",
  "fields": [
    {"name": "fact_text", "type": "text", "required": true},
    {"name": "category", "type": "enum", "values": ["specs", "api", "security", "pricing"]},
    {"name": "confidence", "type": "float", "min": 0, "max": 1},
    {"name": "source_quote", "type": "text"}
  ]
}
```

**Input Content:**
```markdown
## Rate Limits

Our API supports 10,000 requests per minute for Pro plan users.
Enterprise customers get unlimited API access.
```

**LLM Output:**
```json
{
  "extractions": [
    {
      "fact_text": "Pro plan API rate limit is 10,000 requests per minute",
      "category": "api",
      "confidence": 0.95,
      "source_quote": "10,000 requests per minute for Pro plan"
    },
    {
      "fact_text": "Enterprise plan has unlimited API access",
      "category": "api",
      "confidence": 0.90,
      "source_quote": "Enterprise customers get unlimited API access"
    }
  ]
}
```

---

## Example: Research Survey Extraction

**Project Schema:**
```json
{
  "name": "research_finding",
  "fields": [
    {"name": "finding", "type": "text", "required": true},
    {"name": "finding_type", "type": "enum", "values": ["result", "claim", "limitation"]},
    {"name": "methodology", "type": "text"},
    {"name": "confidence", "type": "float"}
  ]
}
```

**Generated Prompt (different from company analysis):**
```
You are extracting structured data from documents.

Project: Research Survey
Extract findings from academic papers

## Extraction Schema: research_finding

Extract the following fields:
- finding (text) [required]: The research finding
- finding_type (enum) (allowed: result, claim, limitation)
- methodology (text): How the finding was established
- confidence (float): Confidence in the finding
...
```
