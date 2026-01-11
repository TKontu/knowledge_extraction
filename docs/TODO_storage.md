# TODO: Storage Module

## Overview

Handles PostgreSQL storage for metadata and Qdrant for vector embeddings. Provides search functionality.

**Architecture:** Uses the generalized project-based schema. See `docs/TODO_generalization.md`.

## Status

**Completed:**
- Database connection module (`src/database.py` with SQLAlchemy)
- Redis connection module (`src/redis_client.py`)
- Health checks for DB, Redis, and Qdrant
- **SQLAlchemy ORM models** (PR #4 - legacy tables)
- **Qdrant client initialization** (PR #6)
- **Job persistence integrated** (PR #6)
- **Page storage** (PR #7 - via scraper worker)
- **Generalized ORM models** (Project, Source, Extraction, Entity, ExtractionEntity) - in orm_models.py
- **ProjectRepository** (9 methods, 19 tests) - CRUD, templates, default project
- **SourceRepository** (6 methods, 23 tests) - CRUD, filtering, content updates
- **ExtractionRepository** (8 methods, 26 tests) - CRUD, batch ops, JSONB queries
- **EntityRepository** (8 methods, 28 tests) - Deduplication, entity-extraction links
- **JSONB query support** (query_jsonb, filter_by_data with PostgreSQL/SQLite compatibility)
- **QdrantRepository** (5 methods, 12 tests) - Collection init, upsert, batch upsert, search, delete
- **EmbeddingService** (2 methods, 7 tests) - Single embed, batch embed with retry logic
- **SearchService** (1 method, 14 tests) - Hybrid semantic + JSONB search with over-fetching strategy

**Pending:**
- Pagination support
- Search API endpoint (POST /api/v1/projects/{project_id}/search)

---

## Database Schema (Generalized)

### Core Tables

```sql
-- Projects define extraction configurations
CREATE TABLE projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT UNIQUE NOT NULL,
    description TEXT,

    source_config JSONB NOT NULL DEFAULT '{"type": "web", "group_by": "company"}',
    extraction_schema JSONB NOT NULL,
    entity_types JSONB NOT NULL DEFAULT '[]',
    prompt_templates JSONB NOT NULL DEFAULT '{}',

    is_template BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Sources (generalized from pages)
CREATE TABLE sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,

    source_type TEXT NOT NULL DEFAULT 'web',
    uri TEXT NOT NULL,
    source_group TEXT NOT NULL,  -- Replaces "company"

    title TEXT,
    content TEXT,  -- Processed content (markdown)
    raw_content TEXT,

    metadata JSONB DEFAULT '{}',
    outbound_links JSONB DEFAULT '[]',

    status TEXT DEFAULT 'pending',
    fetched_at TIMESTAMP WITH TIME ZONE,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    UNIQUE(project_id, uri)
);

CREATE INDEX idx_sources_project ON sources(project_id);
CREATE INDEX idx_sources_group ON sources(source_group);
CREATE INDEX idx_sources_status ON sources(status);

-- Extractions (generalized from facts)
CREATE TABLE extractions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    source_id UUID REFERENCES sources(id) ON DELETE CASCADE,

    data JSONB NOT NULL,  -- Schema validated at app layer

    extraction_type TEXT NOT NULL,
    source_group TEXT NOT NULL,  -- Denormalized
    confidence FLOAT,

    profile_used TEXT,
    chunk_index INT,
    chunk_context JSONB,

    embedding_id TEXT,

    extracted_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX idx_extractions_project ON extractions(project_id);
CREATE INDEX idx_extractions_source ON extractions(source_id);
CREATE INDEX idx_extractions_group ON extractions(source_group);
CREATE INDEX idx_extractions_type ON extractions(extraction_type);
CREATE INDEX idx_extractions_confidence ON extractions(confidence);
CREATE INDEX idx_extractions_data ON extractions USING GIN (data);

-- Entities (project-scoped)
CREATE TABLE entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    source_group TEXT NOT NULL,

    entity_type TEXT NOT NULL,
    value TEXT NOT NULL,
    normalized_value TEXT NOT NULL,
    attributes JSONB DEFAULT '{}',

    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    UNIQUE(project_id, source_group, entity_type, normalized_value)
);

CREATE INDEX idx_entities_project ON entities(project_id);
CREATE INDEX idx_entities_group ON entities(source_group);
CREATE INDEX idx_entities_type ON entities(entity_type);

-- Extraction-Entity junction
CREATE TABLE extraction_entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    extraction_id UUID REFERENCES extractions(id) ON DELETE CASCADE,
    entity_id UUID REFERENCES entities(id) ON DELETE CASCADE,
    role TEXT DEFAULT 'mention',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),

    UNIQUE(extraction_id, entity_id, role)
);

-- Jobs (unchanged, but add project_id)
CREATE TABLE jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE SET NULL,
    type TEXT NOT NULL,
    status TEXT DEFAULT 'queued',
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
CREATE INDEX idx_jobs_project ON jobs(project_id);

-- Reports
CREATE TABLE reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    type TEXT NOT NULL,
    title TEXT,
    content TEXT,
    source_groups JSONB DEFAULT '[]',
    categories JSONB DEFAULT '[]',
    extraction_ids JSONB DEFAULT '[]',
    format TEXT DEFAULT 'md',
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Rate limits (unchanged)
CREATE TABLE rate_limits (
    domain TEXT PRIMARY KEY,
    request_count INT DEFAULT 0,
    daily_count INT DEFAULT 0,
    reset_at TIMESTAMP WITH TIME ZONE
);
```

### Legacy Tables (Keep During Transition)

The existing `pages`, `facts`, `profiles` tables remain until migration is complete.

---

## Core Tasks

### PostgreSQL Repositories

#### ProjectRepository
See: `docs/TODO_project_system.md`

#### SourceRepository (replaces PageRepository)

```python
class SourceRepository:
    async def create(self, source: SourceCreate) -> Source
    async def get(self, id: UUID) -> Source | None
    async def get_by_uri(self, project_id: UUID, uri: str) -> Source | None
    async def list(self, filters: SourceFilters) -> list[Source]
    async def update_status(self, id: UUID, status: str) -> None
    async def update_content(self, id: UUID, content: str, title: str) -> None
```

#### ExtractionRepository (replaces FactRepository)

```python
class ExtractionRepository:
    async def create(self, extraction: ExtractionCreate) -> Extraction
    async def create_batch(self, extractions: list[ExtractionCreate]) -> list[UUID]
    async def get(self, id: UUID) -> Extraction | None
    async def list(self, filters: ExtractionFilters) -> list[Extraction]
    async def get_by_source(self, source_id: UUID) -> list[Extraction]

    # JSONB queries
    async def query_jsonb(
        self,
        project_id: UUID,
        path: str,
        value: Any,
    ) -> list[Extraction]

    async def filter_by_data(
        self,
        project_id: UUID,
        filters: dict[str, Any],  # {"category": "pricing", "confidence": {">": 0.8}}
    ) -> list[Extraction]
```

#### EntityRepository

```python
class EntityRepository:
    async def create(self, entity: EntityCreate) -> Entity
    async def get_or_create(
        self,
        project_id: UUID,
        source_group: str,
        entity_type: str,
        value: str,
        normalized_value: str,
    ) -> Entity
    async def list_by_type(
        self,
        project_id: UUID,
        entity_type: str,
        source_group: str | None = None,
    ) -> list[Entity]
    async def link_to_extraction(
        self,
        extraction_id: UUID,
        entity_id: UUID,
        role: str = "mention",
    ) -> None
```

### Qdrant Repository

```python
class QdrantRepository:
    collection_name: str = "extractions"

    async def init_collection(self) -> None:
        """Create collection if not exists."""
        # Vector size: 1024 (BGE-large-en)
        # Distance: Cosine

    async def upsert(
        self,
        extraction_id: UUID,
        embedding: list[float],
        payload: dict,
    ) -> str:
        """Insert or update embedding."""

    async def upsert_batch(
        self,
        items: list[EmbeddingItem],
    ) -> list[str]:
        """Batch upsert for efficiency."""

    async def search(
        self,
        query_embedding: list[float],
        limit: int = 10,
        filters: dict | None = None,
    ) -> list[SearchResult]:
        """Semantic search with optional filters."""

    async def delete(self, extraction_id: UUID) -> bool:
        """Delete embedding (for re-extraction)."""
```

### Embedding Service

```python
class EmbeddingService:
    """Generate embeddings via BGE-large-en."""

    def __init__(self, settings: Settings):
        self.client = AsyncOpenAI(
            base_url=settings.openai_embedding_base_url,
            api_key=settings.openai_api_key,
        )
        self.model = settings.rag_embedding_model  # bge-large-en
        self.dimension = 1024

    async def embed(self, text: str) -> list[float]:
        """Generate embedding for single text."""
        response = await self.client.embeddings.create(
            model=self.model,
            input=text,
        )
        return response.data[0].embedding

    async def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Generate embeddings for multiple texts."""
        response = await self.client.embeddings.create(
            model=self.model,
            input=texts,
        )
        return [item.embedding for item in response.data]
```

### Search Service

```python
class SearchService:
    """Combined semantic + structured search."""

    def __init__(
        self,
        embedding_service: EmbeddingService,
        qdrant_repo: QdrantRepository,
        extraction_repo: ExtractionRepository,
    ):
        self.embedding = embedding_service
        self.qdrant = qdrant_repo
        self.extractions = extraction_repo

    async def search(
        self,
        project_id: UUID,
        query: str,
        filters: SearchFilters,
        limit: int = 10,
    ) -> list[ExtractionSearchResult]:
        """Semantic search with JSONB filters."""
        # 1. Generate query embedding
        query_embedding = await self.embedding.embed(query)

        # 2. Search Qdrant with project filter
        qdrant_filters = {"project_id": str(project_id)}
        if filters.source_groups:
            qdrant_filters["source_group"] = {"$in": filters.source_groups}

        vector_results = await self.qdrant.search(
            query_embedding=query_embedding,
            limit=limit * 2,  # Over-fetch for post-filtering
            filters=qdrant_filters,
        )

        # 3. Apply JSONB filters in PostgreSQL
        extraction_ids = [r.extraction_id for r in vector_results]
        if filters.jsonb_filters:
            extractions = await self.extractions.filter_by_data(
                project_id=project_id,
                filters=filters.jsonb_filters,
            )
            valid_ids = {e.id for e in extractions}
            vector_results = [r for r in vector_results if r.extraction_id in valid_ids]

        # 4. Enrich and return
        return vector_results[:limit]

    async def filter_only(
        self,
        project_id: UUID,
        filters: ExtractionFilters,
    ) -> list[Extraction]:
        """Structured query without semantic search."""
        return await self.extractions.list(filters)
```

---

## Data Models

```python
@dataclass
class SourceFilters:
    project_id: UUID
    source_group: str | None = None
    source_type: str | None = None
    status: str | None = None
    fetched_after: datetime | None = None

@dataclass
class ExtractionFilters:
    project_id: UUID
    source_group: str | None = None
    source_groups: list[str] | None = None
    extraction_type: str | None = None
    confidence_min: float | None = None
    jsonb_filters: dict[str, Any] | None = None

@dataclass
class SearchFilters:
    project_id: UUID
    source_groups: list[str] | None = None
    entity_type: str | None = None
    entity_value: str | None = None
    confidence_min: float = 0.5
    jsonb_filters: dict[str, Any] | None = None

@dataclass
class ExtractionSearchResult:
    extraction_id: UUID
    score: float
    data: dict  # JSONB data
    source_group: str
    source_uri: str
    confidence: float

@dataclass
class EmbeddingItem:
    extraction_id: UUID
    text: str
    payload: dict
```

---

## JSONB Query Patterns

```sql
-- Find extractions where category = 'pricing'
SELECT * FROM extractions
WHERE project_id = $1
AND data->>'category' = 'pricing';

-- Find extractions with confidence > 0.8
SELECT * FROM extractions
WHERE project_id = $1
AND (data->>'confidence')::float > 0.8;

-- Find by nested field
SELECT * FROM extractions
WHERE project_id = $1
AND data->'metrics'->>'accuracy' IS NOT NULL;

-- Aggregate by custom field
SELECT
    data->>'category' as category,
    COUNT(*) as count
FROM extractions
WHERE project_id = $1
GROUP BY data->>'category';
```

---

## Qdrant Collection Config

```python
collection_config = {
    "name": "extractions",
    "vectors": {
        "size": 1024,  # BGE-large-en dimension
        "distance": "Cosine"
    },
    "hnsw_config": {
        "m": 16,
        "ef_construct": 100
    },
    "payload_schema": {
        "extraction_id": "keyword",
        "project_id": "keyword",
        "source_group": "keyword",
        "extraction_type": "keyword",
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
    collection: extractions

  embedding:
    base_url: ${OPENAI_EMBEDDING_BASE_URL}
    model: ${RAG_EMBEDDING_MODEL:-bge-large-en}
    batch_size: 25
    dimension: 1024
```

---

## API Endpoints

```python
# POST /api/v1/projects/{project_id}/search
# Request:
{
    "query": "API rate limits",
    "filters": {
        "source_groups": ["Acme Corp"],
        "jsonb_filters": {
            "category": "api",
            "confidence": {">": 0.7}
        }
    },
    "limit": 20
}

# Response:
{
    "results": [
        {
            "extraction_id": "uuid",
            "data": {"fact_text": "...", "category": "api", ...},
            "source_group": "Acme Corp",
            "source_uri": "https://...",
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
src/
├── services/
│   └── storage/
│       ├── __init__.py
│       ├── repositories/
│       │   ├── __init__.py
│       │   ├── source.py         # SourceRepository
│       │   ├── extraction.py     # ExtractionRepository
│       │   ├── entity.py         # EntityRepository
│       │   └── job.py            # JobRepository
│       ├── qdrant/
│       │   ├── __init__.py
│       │   └── repository.py     # QdrantRepository
│       ├── embedding.py          # EmbeddingService
│       └── search.py             # SearchService
├── models/
│   └── storage.py                # Filters, SearchResult
└── api/
    └── v1/
        └── search.py             # Search endpoint
```

---

## Implementation Tasks

### Phase 1: Schema Setup
- [ ] Update init.sql with generalized schema
- [ ] Create ORM models (Project, Source, Extraction, Entity)
- [ ] Test table creation

### Phase 2: Repositories
- [ ] Create SourceRepository
- [ ] Create ExtractionRepository with JSONB query support
- [ ] Create EntityRepository
- [ ] Update JobRepository with project_id

### Phase 3: Qdrant Integration
- [x] Create QdrantRepository
- [x] Implement collection initialization
- [x] Implement upsert/search/delete

### Phase 4: Embedding Service
- [x] Create EmbeddingService
- [x] Test with BGE-large-en endpoint
- [x] Implement batching

### Phase 5: Search Service
- [x] Create SearchService
- [x] Implement hybrid search (vector + JSONB)
- [ ] Create search API endpoint

---

## Testing Checklist

- [x] Unit: Repository CRUD operations (96 tests for SQL repositories)
- [x] Unit: JSONB filter building (included in repository tests)
- [x] Unit: Embedding batching (EmbeddingService, 7 tests)
- [x] Unit: SearchService hybrid search (14 tests)
- [x] Integration: Qdrant collection creation (QdrantRepository, 2 tests)
- [x] Integration: Store extraction with embedding (QdrantRepository, 4 tests)
- [x] Integration: Search returns relevant results (QdrantRepository, 3 tests)
- [x] Integration: Qdrant payload filters work correctly (QdrantRepository, 1 test)
- [x] Integration: Hybrid search with over-fetching and JSONB filtering (SearchService, 14 tests)
