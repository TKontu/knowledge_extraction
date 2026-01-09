# TODO: Storage Module

## Overview

Handles PostgreSQL storage for metadata and Qdrant for vector embeddings. Provides search functionality.

## Core Tasks

### PostgreSQL Repository

- [ ] Database connection pool (SQLAlchemy async)
- [ ] Page repository
  ```python
  class PageRepository:
      async def create(self, page: ScrapedPage) -> UUID
      async def get(self, id: UUID) -> ScrapedPage | None
      async def get_by_url(self, url: str) -> ScrapedPage | None
      async def list(self, filters: PageFilters) -> list[ScrapedPage]
      async def update_status(self, id: UUID, status: str)
  ```
- [ ] Fact repository
  ```python
  class FactRepository:
      async def create(self, fact: StoredFact) -> UUID
      async def create_batch(self, facts: list[StoredFact]) -> list[UUID]
      async def get(self, id: UUID) -> StoredFact | None
      async def list(self, filters: FactFilters) -> list[StoredFact]
      async def get_by_page(self, page_id: UUID) -> list[StoredFact]
  ```
- [ ] Job repository
  ```python
  class JobRepository:
      async def create(self, job: Job) -> UUID
      async def get(self, id: UUID) -> Job | None
      async def update_status(self, id: UUID, status: str, result: dict | None)
      async def list_pending(self, job_type: str) -> list[Job]
  ```
- [ ] Profile repository (for custom profiles)

### Qdrant Repository

- [ ] Qdrant client setup
- [ ] Collection initialization (dim=1024 for BGE-large-en)
  ```python
  async def init_collection(self):
      # Create collection if not exists
      # Configure HNSW index
  ```
- [ ] Upsert embeddings
  ```python
  async def upsert(self, fact_id: UUID, embedding: list[float], payload: dict) -> str
  async def upsert_batch(self, items: list[EmbeddingItem]) -> list[str]
  ```
- [ ] Search
  ```python
  async def search(
      self, 
      query_embedding: list[float], 
      limit: int = 10,
      filters: dict | None = None
  ) -> list[SearchResult]
  ```
- [ ] Delete (for re-extraction)

### Embedding Generation

- [ ] BGE-large-en client via vLLM
  ```python
  class EmbeddingService:
      async def embed(self, text: str) -> list[float]
      async def embed_batch(self, texts: list[str]) -> list[list[float]]
  ```
- [ ] Use existing embedding config: `OPENAI_EMBEDDING_BASE_URL`
- [ ] Batch processing for efficiency
- [ ] Caching (optional, via Redis)

### Search Service

- [ ] Semantic search
  ```python
  async def search(
      self,
      query: str,
      filters: SearchFilters,
      limit: int = 10
  ) -> list[FactSearchResult]
  ```
- [ ] Filter support
  - company (exact match)
  - category (in list)
  - date_range (scraped_at)
  - confidence_min
- [ ] Result enrichment (join with PostgreSQL for full fact data)
- [ ] Pagination (offset-based for MVP)

---

## Data Models

```python
@dataclass
class PageFilters:
    company: str | None = None
    domain: str | None = None
    status: str | None = None
    scraped_after: datetime | None = None
    scraped_before: datetime | None = None

@dataclass
class FactFilters:
    company: str | None = None
    category: str | None = None
    profile: str | None = None
    confidence_min: float | None = None
    extracted_after: datetime | None = None

@dataclass
class SearchFilters:
    company: str | None = None
    companies: list[str] | None = None  # for comparison
    category: str | None = None
    categories: list[str] | None = None
    confidence_min: float = 0.5
    date_range: tuple[datetime, datetime] | None = None

@dataclass
class SearchResult:
    fact_id: UUID
    score: float  # similarity score
    fact_text: str
    category: str
    company: str
    source_url: str
    confidence: float

@dataclass
class EmbeddingItem:
    fact_id: UUID
    text: str
    payload: dict
```

---

## Database Schema

```sql
-- pages table
CREATE TABLE pages (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    url TEXT UNIQUE NOT NULL,
    domain TEXT NOT NULL,
    company TEXT NOT NULL,
    title TEXT,
    markdown_content TEXT,
    scraped_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    status TEXT DEFAULT 'completed',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_pages_company ON pages(company);
CREATE INDEX idx_pages_domain ON pages(domain);
CREATE INDEX idx_pages_status ON pages(status);

-- facts table
CREATE TABLE facts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    page_id UUID REFERENCES pages(id) ON DELETE CASCADE,
    fact_text TEXT NOT NULL,
    category TEXT NOT NULL,
    confidence FLOAT NOT NULL,
    profile_used TEXT NOT NULL,
    embedding_id TEXT,  -- Qdrant point ID
    extracted_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_facts_page_id ON facts(page_id);
CREATE INDEX idx_facts_category ON facts(category);
CREATE INDEX idx_facts_profile ON facts(profile_used);

-- jobs table
CREATE TABLE jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    type TEXT NOT NULL,  -- scrape, extract, report
    status TEXT DEFAULT 'queued',  -- queued, running, completed, failed
    priority INT DEFAULT 0,
    payload JSONB NOT NULL,
    result JSONB,
    error TEXT,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    started_at TIMESTAMP WITH TIME ZONE,
    completed_at TIMESTAMP WITH TIME ZONE
);

CREATE INDEX idx_jobs_status ON jobs(status);
CREATE INDEX idx_jobs_type ON jobs(type);

-- profiles table (for custom profiles)
CREATE TABLE profiles (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT UNIQUE NOT NULL,
    categories TEXT[] NOT NULL,
    prompt_focus TEXT NOT NULL,
    depth TEXT NOT NULL,
    custom_instructions TEXT,
    is_builtin BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);
```

---

## Qdrant Collection Config

```python
collection_config = {
    "name": "techfacts",
    "vectors": {
        "size": 1024,  # BGE-large-en dimension
        "distance": "Cosine"
    },
    "hnsw_config": {
        "m": 16,
        "ef_construct": 100
    },
    "payload_schema": {
        "fact_id": "keyword",
        "company": "keyword",
        "category": "keyword",
        "confidence": "float"
    }
}
```

---

## Configuration

```yaml
storage:
  postgres:
    url: ${DATABASE_URL}
    pool_size: ${STORAGE_POOL_SIZE:-10}
    max_overflow: ${STORAGE_MAX_OVERFLOW:-20}
  
  qdrant:
    url: ${QDRANT_URL:-http://qdrant:6333}
    collection: techfacts
    
  embedding:
    base_url: ${OPENAI_EMBEDDING_BASE_URL}
    model: ${RAG_EMBEDDING_MODEL:-bge-large-en}
    batch_size: 25
    dimension: 1024
```

---

## API Endpoints

```python
# POST /api/v1/search
# Request:
{
    "query": "API rate limits",
    "filters": {
        "company": "Example Inc",
        "categories": ["rate_limits", "api"],
        "confidence_min": 0.7
    },
    "limit": 20
}
# Response:
{
    "results": [
        {
            "fact_id": "uuid",
            "fact_text": "API supports 10,000 requests per minute",
            "category": "rate_limits",
            "company": "Example Inc",
            "source_url": "https://...",
            "confidence": 0.95,
            "score": 0.87
        }
    ],
    "total": 1
}
```

---

## File Structure

```
pipeline/
├── services/
│   └── storage/
│       ├── __init__.py
│       ├── postgres/
│       │   ├── __init__.py
│       │   ├── connection.py    # Connection pool
│       │   ├── pages.py         # PageRepository
│       │   ├── facts.py         # FactRepository
│       │   ├── jobs.py          # JobRepository
│       │   └── profiles.py      # ProfileRepository
│       ├── qdrant/
│       │   ├── __init__.py
│       │   ├── client.py        # Qdrant client
│       │   └── repository.py    # QdrantRepository
│       ├── embedding.py         # EmbeddingService
│       └── search.py            # SearchService
├── models/
│   └── storage.py               # Filters, SearchResult
└── api/
    └── routes/
        └── search.py            # Search endpoint
```

---

## Testing Checklist

- [ ] Unit: Repository CRUD operations
- [ ] Unit: Filter building
- [ ] Unit: Embedding batching
- [ ] Integration: PostgreSQL migrations run cleanly
- [ ] Integration: Qdrant collection creation
- [ ] Integration: End-to-end search (embed query → search → return results)
- [ ] Integration: Filters work correctly
