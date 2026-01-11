# TODO: LLM Integration

## Overview

Integration with vLLM gateway for fact extraction from scraped documents.

## Status

**✅ FOUNDATION COMPLETE (PR #8):**
- ✅ LLM configuration in `config.py` (OpenAI-compatible endpoints, model names, timeouts)
- ✅ vLLM gateway available at `192.168.0.247:9003`
- ✅ Embedding endpoint at `192.168.0.136:9003` (BGE-large-en)
- ✅ **LLM client implementation** (`services/llm/client.py`, 9 tests)
- ✅ **Document chunking** (`services/llm/chunking.py`, 17 tests)
- ✅ **Structured output parsing** (JSON mode with validation)

**Pending:**
- Extraction service (orchestration)
- Fact validation module
- Chunk result merging strategy
- Integration with database

**Configuration Available:**
```python
OPENAI_BASE_URL = "http://192.168.0.247:9003/v1"  # Extraction LLM
OPENAI_EMBEDDING_BASE_URL = "http://192.168.0.136:9003/v1"  # Embeddings
LLM_MODEL = "gemma3-12b-awq"
LLM_HTTP_TIMEOUT = 900  # seconds
LLM_MAX_RETRIES = 5
```

---

## Design Decisions

### Use JSON Mode (Not Repair Logic)

**Avoid:** Complex regex-based JSON repair
**Instead:** Use `response_format={"type": "json_object"}` in OpenAI-compatible API

```python
# Simple and reliable
response = await client.chat.completions.create(
    model=settings.llm_model,
    messages=[...],
    response_format={"type": "json_object"}  # Enforces valid JSON
)
return json.loads(response.choices[0].message.content)
```

### Semantic Chunking (Not Pure Token-Based)

**Avoid:** Splitting mid-paragraph or mid-code-block
**Instead:** Split on markdown headers, then token-limit if needed

```python
def chunk_document(markdown: str, max_tokens: int = 8000) -> list[str]:
    # Split on ## headers first (semantic boundaries)
    sections = split_by_headers(markdown)

    chunks = []
    for section in sections:
        if count_tokens(section) <= max_tokens:
            chunks.append(section)
        else:
            # Only token-split if section too large
            chunks.extend(token_chunk(section, max_tokens))
    return chunks
```

### Trust LLM Confidence (Not Heuristics)

**Avoid:** Brittle heuristic scoring (`if 'may' in text: score -= 0.2`)
**Instead:** Ask LLM for confidence, use as-is

---

## Implementation Tasks

### 1. LLM Client ✅ COMPLETE (PR #8)

**Implemented in `services/llm/client.py`:**
- AsyncOpenAI client with retry logic (tenacity)
- JSON mode for structured output
- Temperature 0.1 for consistent extraction
- Error handling for malformed JSON
- 9 comprehensive tests

```python
# services/llm/client.py - IMPLEMENTED
from openai import AsyncOpenAI
from tenacity import retry, stop_after_attempt, wait_exponential

class LLMClient:
    def __init__(self, settings: Settings):
        self.client = AsyncOpenAI(
            base_url=settings.openai_base_url,
            api_key=settings.openai_api_key,
            timeout=settings.llm_http_timeout,
        )
        self.model = settings.llm_model

    @retry(
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=2, min=4, max=60),
    )
    async def extract_facts(
        self, content: str, categories: list[str], profile_name: str = "general"
    ) -> list[ExtractedFact]:
        # Full implementation in services/llm/client.py
```

### 2. Semantic Document Chunking ✅ COMPLETE (PR #8)

**Implemented in `services/llm/chunking.py`:**
- Semantic splitting on `##` headers
- Token-aware chunking with configurable limits
- Large section handling with word-level fallback
- Header path extraction for context
- 17 comprehensive tests

```python
# services/llm/chunking.py - IMPLEMENTED
import re

def split_by_headers(markdown: str) -> list[str]:
    """Split markdown on ## headers, keeping header with content."""
    pattern = r'(?=^## )'  # Split before ## headers
    sections = re.split(pattern, markdown, flags=re.MULTILINE)
    return [s.strip() for s in sections if s.strip()]


def chunk_document(
    markdown: str,
    max_tokens: int = 8000,
    overlap_tokens: int = 200,
) -> list[DocumentChunk]:
    """Chunk document semantically, respecting markdown structure."""
    sections = split_by_headers(markdown)
    chunks = []
    current_chunk = ""
    current_tokens = 0

    for section in sections:
        section_tokens = count_tokens(section)

        # Section fits in current chunk
        if current_tokens + section_tokens <= max_tokens:
            current_chunk += "\n\n" + section
            current_tokens += section_tokens

        # Section too large - need to split it
        elif section_tokens > max_tokens:
            # Save current chunk first
            if current_chunk:
                chunks.append(DocumentChunk(content=current_chunk.strip()))

            # Split large section by paragraphs
            for sub_chunk in split_large_section(section, max_tokens):
                chunks.append(DocumentChunk(content=sub_chunk))

            current_chunk = ""
            current_tokens = 0

        # Start new chunk
        else:
            if current_chunk:
                chunks.append(DocumentChunk(content=current_chunk.strip()))
            current_chunk = section
            current_tokens = section_tokens

    # Don't forget last chunk
    if current_chunk:
        chunks.append(DocumentChunk(content=current_chunk.strip()))

    return chunks


def count_tokens(text: str) -> int:
    """Approximate token count (4 chars ≈ 1 token for English)."""
    return len(text) // 4


def extract_header_path(markdown: str, position: int) -> list[str]:
    """Extract header breadcrumb path at given position."""
    lines = markdown[:position].split('\n')
    headers = []
    for line in lines:
        if line.startswith('# '):
            headers = [line[2:].strip()]
        elif line.startswith('## '):
            headers = headers[:1] + [line[3:].strip()]
        elif line.startswith('### '):
            headers = headers[:2] + [line[4:].strip()]
    return headers
```

### Chunk Context for Better Extraction

Include context in extraction prompt for attribution:

```python
def build_chunk_prompt(chunk: DocumentChunk, context: ChunkContext) -> str:
    header_context = " > ".join(context.header_path) if context.header_path else "Main content"

    return f"""
Page: {context.page_title}
Section: {header_context}
Chunk {context.chunk_index + 1} of {context.total_chunks}

---
{chunk.content}
---
"""
```

### 3. Extraction Prompt

```python
# services/llm/prompts.py
def build_extraction_prompt(content: str, profile: ExtractionProfile) -> dict:
    system = f"""You are a technical fact extractor. Extract concrete, verifiable facts from documentation.

Focus: {profile.prompt_focus}
Categories: {', '.join(profile.categories)}

Output a JSON object with this exact structure:
{{
  "facts": [
    {{
      "fact": "Specific technical statement",
      "category": "one of: {', '.join(profile.categories)}",
      "confidence": 0.0-1.0,
      "source_quote": "brief supporting quote from source"
    }}
  ]
}}

Rules:
- Only extract factual, specific information
- Skip marketing language and vague claims
- Each fact should be self-contained
- Assign confidence based on how explicit the source is
- Include source_quote for attribution"""

    user = f"""Extract facts from this documentation:

---
{content}
---"""

    return {"system": system, "user": user}
```

### 4. Chunk Result Merging

```python
# services/llm/merging.py
async def extract_from_document(
    llm: LLMClient,
    markdown: str,
    profile: ExtractionProfile,
) -> list[ExtractedFact]:
    """Extract facts from entire document, handling chunking."""
    chunks = chunk_document(markdown)

    if len(chunks) == 1:
        return await llm.extract_facts(chunks[0].content, profile)

    # Process chunks and merge results
    all_facts = []
    for chunk in chunks:
        facts = await llm.extract_facts(chunk.content, profile)
        all_facts.extend(facts)

    # Simple deduplication by exact match
    seen = set()
    unique_facts = []
    for fact in all_facts:
        key = fact.fact.lower().strip()
        if key not in seen:
            seen.add(key)
            unique_facts.append(fact)

    return unique_facts
```

### 5. Fact Validation

```python
# services/llm/validation.py
def validate_fact(fact: ExtractedFact, profile: ExtractionProfile) -> bool:
    """Validate extracted fact meets quality requirements."""
    # Check category is valid
    if fact.category not in profile.categories:
        return False

    # Check minimum length
    if len(fact.fact) < 20:
        return False

    # Check confidence threshold
    if fact.confidence < 0.5:
        return False

    return True
```

---

## Data Models

```python
@dataclass
class DocumentChunk:
    content: str
    header_path: list[str]  # ["API Reference", "Rate Limits"] - breadcrumb context
    chunk_index: int
    total_chunks: int
    start_line: int | None = None
    end_line: int | None = None

@dataclass
class ChunkContext:
    """Context passed to LLM for better extraction."""
    page_title: str
    page_url: str
    header_path: list[str]
    chunk_index: int
    total_chunks: int

@dataclass
class ExtractedFact:
    fact: str
    category: str
    confidence: float
    source_quote: str | None = None
    header_context: str | None = None  # Section where fact was found

@dataclass
class ExtractionResult:
    page_id: UUID
    facts: list[ExtractedFact]
    chunks_processed: int
    extraction_time_ms: int
```

---

## Configuration

```yaml
llm:
  extraction:
    max_chunk_tokens: 8000
    temperature: 0.1
    max_retries: 3
    timeout_seconds: 900
    confidence_threshold: 0.5
```

---

## File Structure

```
src/
├── services/
│   └── llm/
│       ├── __init__.py
│       ├── client.py          # LLMClient with retry logic
│       ├── chunking.py        # Semantic document chunking
│       ├── prompts.py         # Prompt templates
│       ├── merging.py         # Chunk result merging
│       └── validation.py      # Fact validation
```

---

## Error Handling

| Error Type | Strategy |
|------------|----------|
| HTTP 429 (rate limit) | Exponential backoff, respect Retry-After |
| HTTP 500/503 | Backoff, max 3 attempts |
| Timeout | Retry with same timeout (model may be slow) |
| Invalid JSON | Retry once (JSON mode should prevent this) |
| Context too long | Re-chunk with smaller size |

---

## Tasks

### Completed ✅ (PR #8)
- [x] **Create `LLMClient` class with async OpenAI client**
- [x] **Implement semantic chunking (`split_by_headers`, `chunk_document`)**
- [x] **Implement header path extraction for chunk context**
- [x] **Create extraction prompt templates per profile** (system + user prompts)
- [x] **Add retry logic with tenacity** (3 attempts, exponential backoff)
- [x] **Add configuration loading** (uses Settings from config.py)
- [x] **Write tests for chunking** (17 tests)
- [x] **Write tests for header path extraction**
- [x] **Write tests for fact extraction (mock LLM)** (9 tests)

### Pending
- [ ] Implement chunk result merging with basic dedup
- [ ] Add fact validation (category, length, confidence)
- [ ] Integration test with real vLLM gateway
- [ ] Extraction service (orchestration)

---

## Testing Checklist

### Completed ✅ (PR #8)
- [x] **Unit: Chunking produces correct sizes**
- [x] **Unit: Headers preserved in chunks**
- [x] **Unit: Validation filters correctly** (incomplete facts skipped)
- [x] **Integration: Retry logic triggers on failures**

### Pending
- [ ] Unit: Merging removes exact duplicates
- [ ] Integration: Extract from sample markdown (end-to-end)
- [ ] Integration: End-to-end with real model
