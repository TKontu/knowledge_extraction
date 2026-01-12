# Knowledge Extraction Pipeline - Architecture

## Overview

A self-hosted, **project-based** system for scraping documentation, extracting structured data via LLM, and generating comparison reports. Each project defines its own extraction schema, entity types, and configuration.

## System Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CU1 (W-2135, 196GB RAM, A2000)                 │
│                                                                             │
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐   ┌─────────────────┐  │
│  │  Firecrawl  │   │   Redis     │   │   Qdrant    │   │   PostgreSQL    │  │
│  │ + Playwright│   │  (queue/    │   │  (vectors)  │   │   (projects,    │  │
│  │             │   │   cache)    │   │             │   │  sources, etc)  │  │
│  └──────┬──────┘   └──────┬──────┘   └──────┬──────┘   └────────┬────────┘  │
│         │                 │                 │                   │           │
│         └─────────────────┴────────┬────────┴───────────────────┘           │
│                                    │                                        │
│                          ┌─────────┴─────────┐                              │
│                          │  Pipeline Service │                              │
│                          │    (FastAPI)      │                              │
│                          │                   │                              │
│                          │  ┌─────────────┐  │                              │
│                          │  │ Extraction  │  │                              │
│                          │  │ Pipeline    │  │                              │
│                          │  │ Service     │  │                              │
│                          │  └─────────────┘  │                              │
│                          └─────────┬─────────┘                              │
│                                    │                                        │
└────────────────────────────────────┼────────────────────────────────────────┘
                                     │
                          ┌──────────┴──────────┐
                          │   vLLM Gateway      │
                          │  (model switching)  │
                          └──────────┬──────────┘
                                     │
              ┌──────────────────────┼──────────────────────┐
              │                      │                      │
              ▼                      ▼                      ▼
┌─────────────────────┐  ┌─────────────────────┐  ┌─────────────────────┐
│  CU1: BGE-large-en  │  │  CU2: Qwen3/Gemma   │  │  CU2: Qwen3-VL     │
│  (embeddings)       │  │  (extraction)       │  │  (optional vision) │
└─────────────────────┘  └─────────────────────┘  └─────────────────────┘
```

## Core Concepts

### Project-Based Architecture

Everything is scoped to a **Project**:

```
Project
├── extraction_schema    # What fields to extract (JSONB)
├── entity_types         # What entities to recognize (JSONB)
├── source_config        # How to handle sources
├── prompt_templates     # Custom LLM prompts (optional)
│
├── Sources              # Scraped content (grouped by source_group)
├── Extractions          # Extracted data matching schema
├── Entities             # Normalized entities (deduplicated)
└── Reports              # Generated reports
```

### Source Groups

Within a project, sources are organized by `source_group` (e.g., company name, paper ID, contract ID). This enables:
- Per-group extraction and deduplication
- Comparison reports across groups
- Entity queries by group

## Components

### CU1 Services (Portainer Stack)

| Service | Purpose | Port | Resource Notes |
|---------|---------|------|----------------|
| `firecrawl-api` | Web scraping API | 3002 | 2-4GB RAM |
| `playwright` | JS rendering | 3000 | 1-2GB RAM |
| `redis` | Job queue, caching | 6379 | 512MB-2GB |
| `qdrant` | Vector storage | 6333 | Scales with data |
| `postgres` | All metadata, state | 5432 | 1-2GB |
| `pipeline` | Orchestration API | 8000 | 1-2GB |
| `flaresolverr` | Cloudflare bypass (optional) | 8191 | 1GB |

### External Dependencies (Existing)

| Service | Location | Purpose |
|---------|----------|---------|
| vLLM Gateway | 192.168.0.247:9003 | LLM inference routing |
| vLLM (CU1) | 192.168.0.136:9003 | Embeddings (BGE-large-en) |
| vLLM (CU2) | Via gateway | Extraction LLM (Qwen3/Gemma) |

## Data Models

### Project

```sql
CREATE TABLE projects (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT UNIQUE NOT NULL,
    description TEXT,
    source_config JSONB NOT NULL DEFAULT '{"type": "web", "group_by": "company"}',
    extraction_schema JSONB NOT NULL,      -- Dynamic schema definition
    entity_types JSONB NOT NULL DEFAULT '[]',  -- Entity type definitions
    prompt_templates JSONB NOT NULL DEFAULT '{}',
    is_template BOOLEAN DEFAULT FALSE,
    is_active BOOLEAN DEFAULT TRUE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Source (Scraped Content)

```sql
CREATE TABLE sources (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    source_group TEXT NOT NULL,            -- e.g., "company_a", "paper_123"
    url TEXT NOT NULL,
    title TEXT,
    content TEXT,                          -- Markdown content
    content_hash TEXT,                     -- For change detection
    status TEXT DEFAULT 'pending',         -- pending, completed, failed
    metadata JSONB DEFAULT '{}',
    scraped_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(project_id, url)
);
```

### Extraction

```sql
CREATE TABLE extractions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    source_id UUID REFERENCES sources(id) ON DELETE CASCADE,
    source_group TEXT NOT NULL,
    extraction_type TEXT,                  -- Category/type of extraction
    data JSONB NOT NULL,                   -- Extracted data matching schema
    confidence FLOAT DEFAULT 0.8,
    profile_used TEXT,
    embedding_id TEXT,                     -- Qdrant vector ID
    metadata JSONB DEFAULT '{}',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Entity

```sql
CREATE TABLE entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    source_group TEXT NOT NULL,
    entity_type TEXT NOT NULL,             -- From project.entity_types
    value TEXT NOT NULL,                   -- Original text
    normalized_value TEXT NOT NULL,        -- For matching/deduplication
    attributes JSONB DEFAULT '{}',         -- Type-specific (numeric_value, unit, etc.)
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(project_id, source_group, entity_type, normalized_value)
);

-- Junction table for extraction-entity relationships
CREATE TABLE extraction_entities (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    extraction_id UUID REFERENCES extractions(id) ON DELETE CASCADE,
    entity_id UUID REFERENCES entities(id) ON DELETE CASCADE,
    role TEXT DEFAULT 'mention',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    UNIQUE(extraction_id, entity_id, role)
);
```

### Report

```sql
CREATE TABLE reports (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID REFERENCES projects(id) ON DELETE CASCADE,
    type TEXT NOT NULL,                    -- single, comparison
    title TEXT NOT NULL,
    content TEXT NOT NULL,                 -- Generated markdown
    source_groups TEXT[] NOT NULL,         -- Groups included
    categories TEXT[] DEFAULT '{}',
    extraction_ids UUID[] DEFAULT '{}',
    format TEXT DEFAULT 'md',
    created_at TIMESTAMPTZ DEFAULT NOW()
);
```

### Job

```sql
CREATE TABLE jobs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    project_id UUID,
    job_type TEXT NOT NULL,                -- scrape, extract, report
    status TEXT DEFAULT 'queued',          -- queued, running, completed, failed
    priority INTEGER DEFAULT 0,
    payload JSONB NOT NULL,
    result JSONB,
    error TEXT,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    started_at TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);
```

### Qdrant Payload

```json
{
  "extraction_id": "uuid",
  "project_id": "uuid",
  "source_group": "company_a",
  "extraction_type": "api",
  "text": "Pro plan API rate limit is 10,000 requests per minute",
  "confidence": 0.95
}
```

## Data Flows

### 1. Scrape Flow

```
URL Input
    │
    ▼
┌─────────────────┐
│  ScraperWorker  │ ←── Rate limiter (Redis)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ FirecrawlClient │ ←── Playwright (JS rendering)
└────────┬────────┘
         │
         ▼
┌─────────────────┐
│ SourceRepository│ ←── Store in PostgreSQL
└─────────────────┘
```

### 2. Extraction Pipeline Flow

```
Source (markdown content)
         │
         ▼
┌─────────────────────────────────────────────────────────────┐
│                 ExtractionPipelineService                    │
│                                                             │
│  ┌─────────────┐                                            │
│  │  Chunking   │  Split large docs by headers               │
│  └──────┬──────┘                                            │
│         │                                                   │
│         ▼                                                   │
│  ┌─────────────┐                                            │
│  │  LLMClient  │  Extract structured data (JSON mode)       │
│  └──────┬──────┘                                            │
│         │                                                   │
│         ▼                                                   │
│  ┌─────────────┐                                            │
│  │  Validator  │  Schema validation, confidence threshold   │
│  └──────┬──────┘                                            │
│         │                                                   │
│         ▼                                                   │
│  ┌──────────────────┐                                       │
│  │ Deduplicator     │  Embedding similarity check (0.90)    │
│  │ (EmbeddingService│  Skip if duplicate found              │
│  │  + Qdrant)       │                                       │
│  └──────┬───────────┘                                       │
│         │                                                   │
│         ▼                                                   │
│  ┌─────────────┐                                            │
│  │ Extraction  │  Store in PostgreSQL                       │
│  │ Repository  │                                            │
│  └──────┬──────┘                                            │
│         │                                                   │
│         ▼                                                   │
│  ┌─────────────┐                                            │
│  │ Qdrant      │  Store embedding vector                    │
│  │ Repository  │                                            │
│  └──────┬──────┘                                            │
│         │                                                   │
│         ▼                                                   │
│  ┌──────────────────┐                                       │
│  │ EntityExtractor  │  Extract entities from extraction     │
│  │ (LLM + normalize)│  Store with deduplication             │
│  └──────────────────┘                                       │
│                                                             │
└─────────────────────────────────────────────────────────────┘
```

### 3. Search Flow

```
Search Query + Filters
         │
         ▼
┌─────────────────┐
│  SearchService  │
└────────┬────────┘
         │
    ┌────┴────┐
    │         │
    ▼         ▼
┌───────┐ ┌──────────┐
│Qdrant │ │PostgreSQL│
│(vector│ │(JSONB    │
│search)│ │filters)  │
└───┬───┘ └────┬─────┘
    │          │
    └────┬─────┘
         │
         ▼
┌─────────────────┐
│ Merge & Rank    │  Over-fetch from Qdrant, filter by JSONB
└────────┬────────┘
         │
         ▼
   Search Results
```

### 4. Report Flow

```
Report Request (type, source_groups, entity_types)
         │
         ▼
┌─────────────────┐
│  ReportService  │
└────────┬────────┘
         │
    ┌────┴────────────────┐
    │                     │
    ▼                     ▼
┌───────────────┐  ┌────────────────┐
│ Extraction    │  │ Entity         │
│ Repository    │  │ Repository     │
│ (get facts)   │  │ (get entities) │
└───────┬───────┘  └───────┬────────┘
        │                  │
        └────────┬─────────┘
                 │
                 ▼
        ┌────────────────┐
        │ Build Markdown │
        │ - Sections     │
        │ - Entity tables│
        │ - Comparisons  │
        └────────┬───────┘
                 │
                 ▼
        ┌────────────────┐
        │ Report         │
        │ Repository     │
        └────────────────┘
```

## API Endpoints

### Projects
```
POST   /api/v1/projects                    Create project
POST   /api/v1/projects/from-template      Create from template
GET    /api/v1/projects                    List projects
GET    /api/v1/projects/templates          List templates
GET    /api/v1/projects/{id}               Get project
PUT    /api/v1/projects/{id}               Update project
DELETE /api/v1/projects/{id}               Soft delete
```

### Scraping
```
POST   /api/v1/scrape                      Start scrape job
GET    /api/v1/scrape/{job_id}             Get job status
```

### Extraction
```
POST   /api/v1/projects/{id}/extract       Start extraction
GET    /api/v1/projects/{id}/extractions   List extractions (with filters)
```

### Entities
```
GET    /api/v1/projects/{id}/entities           List entities
GET    /api/v1/projects/{id}/entities/types     Entity type counts
GET    /api/v1/projects/{id}/entities/{eid}     Get entity
GET    /api/v1/projects/{id}/entities/by-value  Find by value
```

### Search
```
POST   /api/v1/projects/{id}/search        Hybrid search
```

### Reports
```
POST   /api/v1/projects/{id}/reports       Generate report
GET    /api/v1/projects/{id}/reports       List reports
GET    /api/v1/projects/{id}/reports/{rid} Get report
```

### Jobs & Observability
```
GET    /api/v1/jobs                        List jobs (with filters)
GET    /health                             Health check
GET    /metrics                            Prometheus metrics
```

## Key Services

### ExtractionPipelineService

Orchestrates the complete extraction flow:

```python
class ExtractionPipelineService:
    async def process_source(self, source: Source, project: Project) -> list[Extraction]:
        # 1. Chunk content
        # 2. Extract via LLM
        # 3. Validate extractions
        # 4. Check duplicates (skip if found)
        # 5. Store extraction + embedding
        # 6. Extract entities
        return extractions
```

### EntityExtractor

Extracts and normalizes entities from extractions:

```python
class EntityExtractor:
    async def extract(self, extraction: Extraction, project: Project) -> list[Entity]:
        # 1. Build prompt from project.entity_types
        # 2. Call LLM for entity extraction
        # 3. Normalize values (lowercase, numeric parsing)
        # 4. Store with deduplication (get_or_create)
        # 5. Link to extraction
        return entities
```

### ExtractionDeduplicator

Prevents duplicate extractions using embedding similarity:

```python
class ExtractionDeduplicator:
    async def check_duplicate(self, text: str, project_id: UUID, source_group: str) -> bool:
        # 1. Generate embedding
        # 2. Search Qdrant (same project + source_group)
        # 3. Return True if similarity > 0.90
        return is_duplicate
```

### ReportService

Generates reports with entity comparison tables:

```python
class ReportService:
    async def generate(self, project_id: UUID, request: ReportRequest) -> Report:
        # 1. Gather extractions by source_group
        # 2. Gather entities by source_group and type
        # 3. Build markdown (single or comparison)
        # 4. Build entity tables for comparison
        # 5. Store and return report
        return report
```

## Configuration

### Environment Variables

```bash
# vLLM Gateway
OPENAI_BASE_URL=http://192.168.0.247:9003/v1
OPENAI_EMBEDDING_BASE_URL=http://192.168.0.136:9003/v1
OPENAI_API_KEY=ollama

# Models
LLM_MODEL=gemma3-12b-awq
RAG_EMBEDDING_MODEL=bge-large-en

# Scraping
SCRAPE_DELAY_MIN=2
SCRAPE_DELAY_MAX=5
SCRAPE_MAX_CONCURRENT_PER_DOMAIN=2
SCRAPE_DAILY_LIMIT_PER_DOMAIN=500

# Services
REDIS_URL=redis://redis:6379
QDRANT_URL=http://qdrant:6333
DATABASE_URL=postgresql://user:pass@postgres:5432/extraction

# Security
API_KEY=your-secure-key

# Deduplication
DEDUP_THRESHOLD=0.90
```

## Scraping Strategy

### Rate Limiting

- Per-domain delays: 2-5s randomized
- Max concurrent per domain: 2
- Daily limit per domain: 500 pages
- Exponential backoff on 429/503

### Anti-Bot Handling

1. **Default**: Standard Firecrawl + Playwright
2. **On 403/429**: Exponential backoff, retry 3x
3. **On Cloudflare**: Route through FlareSolverr
4. **Persistent blocks**: Mark domain as limited

## Scaling Considerations

### Current Capacity (MVP)

- ~100-500 pages/day comfortable
- ~10k-50k extractions in Qdrant
- Single-threaded extraction (GPU bound)

### Future Scaling

- Add Celery workers for parallel extraction
- Shard Qdrant for larger extraction counts
- Add proxy rotation for higher scrape volume
- Horizontal scaling of pipeline service

## Test Coverage

**583 tests** covering:
- All repositories (Project, Source, Extraction, Entity, Qdrant)
- All services (Embedding, Search, Deduplication, Pipeline, Report)
- All API endpoints
- Middleware (auth, logging, request ID)
- Scraper components (Firecrawl, rate limiter, worker)
