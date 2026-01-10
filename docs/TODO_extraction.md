# TODO: Extraction Module

## Overview

Extracts structured facts from scraped markdown content using LLM with configurable profiles.

## Status

**Completed (PR #8 - LLM Foundation):**
- ✅ Built-in profiles defined in `init.sql` (technical_specs, api_docs, security, pricing, general)
- ✅ LLM configuration in `config.py` (OpenAI-compatible endpoints, model names, timeouts)
- ✅ Profile ORM model exists (`pipeline/orm_models.py` - PR #4)
- ✅ **Document chunking module** (`services/llm/chunking.py`, 17 tests)
- ✅ **LLM client implementation** (`services/llm/client.py`, 9 tests)
- ✅ **Data models** (DocumentChunk, ExtractedFact, ExtractionResult)

**Pending (~75% remaining):**
- Profile schema/dataclass implementation
- Profile loading from database (repository pattern)
- Extraction service (orchestrates chunking + LLM + validation)
- Fact validation (schema, categories, confidence filtering)
- Fact storage to PostgreSQL and Qdrant

**Related Documentation:**
- See `docs/TODO_llm_integration.md` for detailed LLM client design
- See `docs/TODO_deduplication.md` for fact deduplication strategy

---

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
- [x] Built-in profiles inserted in database (`init.sql`)
  - [x] `technical_specs` (detailed)
  - [x] `api_docs` (detailed)
  - [x] `security` (comprehensive)
  - [x] `pricing` (detailed)
  - [x] `general` (summary)
- [x] Profile ORM model (PR #4)
- [ ] Profile loading from database (repository)
- [ ] Custom profile creation via API

### LLM Client ✅ COMPLETE (PR #8)

> **Detailed implementation:** See `docs/TODO_llm_integration.md`

- [x] **Create OpenAI-compatible client wrapper** (PR #8 - `services/llm/client.py`)
- [x] Configuration ready in `config.py`:
  - [x] `OPENAI_BASE_URL` (extraction LLM)
  - [x] `LLM_MODEL` (default: gemma3-12b-awq)
  - [x] `LLM_HTTP_TIMEOUT` (900s)
  - [x] `LLM_MAX_RETRIES` (5)
- [x] **Use JSON mode for structured output** (PR #8 - `response_format={"type": "json_object"}`)
- [x] **Retry logic with tenacity** (PR #8 - exponential backoff, 3 attempts)
- [x] **Timeout handling** (PR #8 - via AsyncOpenAI client)

### Document Chunking ✅ COMPLETE (PR #8)

> **Detailed implementation:** See `docs/TODO_llm_integration.md`

- [x] **Semantic chunking (split on markdown headers)** (PR #8)
- [x] **Token-based splitting for large sections** (PR #8 - word-level fallback)
- [x] **Preserve context across chunks** (PR #8 - header path tracking)
- [x] **Track chunk source positions** (PR #8 - chunk index, total chunks)

### Fact Extraction

- [ ] Extract facts from single page
  ```python
  async def extract_facts(page: ScrapedPage, profile: ExtractionProfile) -> list[ExtractedFact]
  ```
- [ ] Chunking for long content (if > context limit)
- [ ] Merge facts from chunks (with basic dedup)
- [ ] Confidence scoring (from LLM response)

### Fact Validation

- [ ] Schema validation (required fields present)
- [ ] Category validation (matches profile categories)
- [ ] Basic quality filters (min length, not empty)
- [ ] Confidence threshold filtering (default: 0.5)

### Fact Storage

> **Deduplication:** See `docs/TODO_deduplication.md`

- [ ] Store to PostgreSQL `facts` table
- [ ] Generate embedding via BGE-large-en
- [ ] Store embedding to Qdrant
- [ ] Check for duplicates before insert (same-company)
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
    fact: str
    category: str
    confidence: float
    source_quote: str | None = None  # snippet for attribution

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

Output a JSON object with this exact structure:
{
  "facts": [
    {
      "fact": "Specific technical statement",
      "category": "one of the allowed categories",
      "confidence": 0.0-1.0,
      "source_quote": "brief supporting quote"
    }
  ]
}

Rules:
- Only extract factual, specific information
- Skip marketing language, vague claims, and opinions
- Each fact should be self-contained
- Include source_quote for attribution
- Assign confidence based on how explicit the source is

Content:
---
{markdown_content}
---
```

---

## Built-in Profiles

| Profile | Categories | Depth | Use Case |
|---------|------------|-------|----------|
| `technical_specs` | specs, hardware, requirements, compatibility, performance | detailed | Product specifications |
| `api_docs` | endpoints, authentication, rate_limits, sdks, versioning | detailed | API documentation |
| `security` | certifications, compliance, encryption, audit, access_control | comprehensive | Security posture |
| `pricing` | pricing, tiers, limits, features | detailed | Competitive intel |
| `general` | general, features, technical, integration | summary | Broad extraction |

---

## Configuration

```yaml
extraction:
  default_profile: general
  max_chunk_tokens: 8000
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
│   ├── extraction/
│   │   ├── __init__.py
│   │   ├── profiles.py        # Profile loading/management
│   │   ├── extractor.py       # Fact extraction logic
│   │   ├── validator.py       # Fact validation
│   │   └── service.py         # ExtractionService (orchestration)
│   └── llm/
│       ├── __init__.py
│       ├── client.py          # LLMClient (OpenAI-compatible)
│       ├── chunking.py        # Semantic document chunking
│       ├── prompts.py         # Prompt templates
│       └── merging.py         # Chunk result merging
├── models/
│   └── extraction.py          # ExtractionProfile, ExtractedFact
└── api/
    └── routes/
        └── extraction.py      # API endpoints
```

---

## Testing Checklist

- [ ] Unit: Profile loading from database
- [ ] Unit: Prompt builder generates valid prompts
- [ ] Unit: Chunking produces correct sizes
- [ ] Unit: Fact validation filters correctly
- [ ] Integration: Extract from sample markdown
- [ ] Integration: Custom profile extraction
- [ ] Integration: Long content chunking works
- [ ] Integration: End-to-end with real vLLM model
