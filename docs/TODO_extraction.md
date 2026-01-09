# TODO: Extraction Module

## Overview

Extracts structured facts from scraped markdown content using LLM with configurable profiles.

## Core Tasks

### Extraction Profiles

- [ ] Profile schema definition
  ```python
  @dataclass
  class ExtractionProfile:
      name: str
      categories: list[str]
      prompt_focus: str
      depth: Literal["summary", "detailed", "comprehensive"]
      custom_instructions: str | None = None
  ```
- [ ] Built-in profiles (stored in `prompts/profiles/`)
  - [ ] `technical_specs.yaml`
  - [ ] `api_docs.yaml`
  - [ ] `security.yaml`
  - [ ] `pricing.yaml`
  - [ ] `general.yaml`
- [ ] Profile loading from YAML
- [ ] Custom profile creation via API
- [ ] Profile storage in PostgreSQL

### Prompt Generation

- [ ] Base extraction prompt template
- [ ] Dynamic prompt builder from profile
  ```python
  def build_extraction_prompt(content: str, profile: ExtractionProfile) -> str
  ```
- [ ] Depth-based prompt variations
  - summary: Extract key facts only (5-10)
  - detailed: Extract all relevant facts (10-30)
  - comprehensive: Extract everything + inferences (30+)
- [ ] JSON output schema in prompt

### LLM Client

- [ ] Reuse your existing OpenAI-compatible client pattern
- [ ] Configure for extraction model (gemma3-12b-awq default)
- [ ] Structured output parsing (JSON)
- [ ] Retry on malformed JSON
- [ ] Timeout handling (match your existing config)

### Fact Extraction

- [ ] Extract facts from single page
  ```python
  async def extract_facts(page: ScrapedPage, profile: ExtractionProfile) -> list[ExtractedFact]
  ```
- [ ] Chunking for long content (if > context limit)
- [ ] Merge facts from chunks
- [ ] Confidence scoring (from LLM or heuristic)

### Fact Validation

- [ ] Schema validation (required fields present)
- [ ] Category validation (matches profile categories)
- [ ] Deduplication within page
- [ ] Basic quality filters (min length, not empty)

### Fact Storage

- [ ] Store to PostgreSQL `facts` table
- [ ] Generate embedding via BGE-large-en
- [ ] Store embedding to Qdrant
- [ ] Link fact → page relationship

---

## Data Models

```python
@dataclass
class ExtractionProfile:
    name: str
    categories: list[str]
    prompt_focus: str
    depth: Literal["summary", "detailed", "comprehensive"]
    custom_instructions: str | None = None

@dataclass
class ExtractedFact:
    fact_text: str
    category: str
    confidence: float
    source_context: str | None = None  # snippet where fact was found

@dataclass
class StoredFact:
    id: UUID
    page_id: UUID
    fact_text: str
    category: str
    confidence: float
    profile_used: str
    extracted_at: datetime
    embedding_id: str  # Qdrant point ID
    metadata: dict
```

---

## Extraction Prompt Template

```
You are a technical fact extractor. Extract concrete, verifiable facts from the provided documentation.

Focus: {profile.prompt_focus}
Categories: {profile.categories}
Depth: {profile.depth}

Rules:
- Only extract factual, specific information
- Skip marketing language, vague claims, and opinions
- Each fact should be self-contained and understandable without context
- Assign confidence based on how explicit the fact is in the source

Output JSON array:
{
  "facts": [
    {
      "fact": "Specific technical statement",
      "category": "one of: {categories}",
      "confidence": 0.0-1.0
    }
  ]
}

Content:
---
{markdown_content}
---
```

---

## Built-in Profiles

### technical_specs.yaml
```yaml
name: technical_specs
categories:
  - specs
  - hardware
  - requirements
  - compatibility
  - performance
prompt_focus: >
  Hardware specifications, system requirements, supported platforms,
  performance metrics, compatibility information
depth: detailed
```

### api_docs.yaml
```yaml
name: api_docs
categories:
  - endpoints
  - authentication
  - rate_limits
  - sdks
  - versioning
prompt_focus: >
  API endpoints, authentication methods, rate limits, SDK availability,
  API versioning, request/response formats
depth: detailed
```

### security.yaml
```yaml
name: security
categories:
  - certifications
  - compliance
  - encryption
  - audit
  - access_control
prompt_focus: >
  Security certifications (SOC2, ISO27001, etc), compliance standards,
  encryption methods, audit capabilities, access control features
depth: comprehensive
```

### pricing.yaml
```yaml
name: pricing
categories:
  - pricing
  - tiers
  - limits
  - features
prompt_focus: >
  Pricing tiers, feature inclusions per tier, usage limits,
  enterprise options, free tier details
depth: detailed
```

### general.yaml
```yaml
name: general
categories:
  - general
  - features
  - technical
  - integration
prompt_focus: >
  General technical facts about the product, features, integrations,
  and capabilities
depth: summary
```

---

## Configuration

```yaml
extraction:
  default_profile: general
  max_content_length: 50000  # characters before chunking
  chunk_size: 10000
  chunk_overlap: 500
  min_fact_length: 20
  max_facts_per_page: 100
  confidence_threshold: 0.5  # filter low-confidence facts
```

---

## API Endpoints

```python
# POST /api/v1/extract
# Request:
{
    "page_ids": ["uuid1", "uuid2"],
    "profile": "api_docs"
}
# Response:
{
    "job_id": "uuid",
    "status": "queued"
}

# POST /api/v1/extract (custom profile)
{
    "page_ids": ["uuid1"],
    "profile": "custom",
    "custom_focus": "Extract deployment and scaling information",
    "custom_categories": ["deployment", "scaling", "regions"]
}

# GET /api/v1/profiles
# Response:
{
    "profiles": [
        {"name": "technical_specs", "categories": [...], "depth": "detailed"},
        ...
    ]
}

# POST /api/v1/profiles
# Request:
{
    "name": "infrastructure",
    "categories": ["deployment", "scaling", "regions"],
    "prompt_focus": "Cloud deployment options, scaling, availability",
    "depth": "detailed"
}
```

---

## File Structure

```
pipeline/
├── services/
│   └── extraction/
│       ├── __init__.py
│       ├── profiles.py        # Profile loading/management
│       ├── prompt_builder.py  # Dynamic prompt generation
│       ├── extractor.py       # Fact extraction logic
│       ├── validator.py       # Fact validation
│       └── service.py         # ExtractionService (orchestration)
├── models/
│   └── extraction.py          # ExtractionProfile, ExtractedFact
├── prompts/
│   ├── extraction_base.txt    # Base prompt template
│   └── profiles/
│       ├── technical_specs.yaml
│       ├── api_docs.yaml
│       ├── security.yaml
│       ├── pricing.yaml
│       └── general.yaml
└── api/
    └── routes/
        └── extraction.py      # API endpoints
```

---

## Testing Checklist

- [ ] Unit: Prompt builder generates valid prompts
- [ ] Unit: Profile loading from YAML
- [ ] Unit: JSON parsing handles malformed responses
- [ ] Unit: Fact validation filters correctly
- [ ] Integration: Extract from sample markdown
- [ ] Integration: Custom profile extraction
- [ ] Integration: Long content chunking works
