# Storage Layer Review

**Date**: 2026-03-02
**Scope**: ORM models, repository pattern, database schema, Qdrant vector store, Redis

---

## 1. Overview

The storage layer uses three backends:

| Backend | Purpose | Library |
|---------|---------|---------|
| **PostgreSQL** | Primary data store (projects, sources, extractions, jobs, entities) | SQLAlchemy 2.0 + psycopg 3 |
| **Qdrant** | Vector embeddings for semantic search | qdrant-client 1.12 |
| **Redis** | Caching, job queues, rate limiting, LLM request queue | redis 5.2 |

---

## 2. Database Schema (ORM Models)

### 2.1 Core Extraction Models

#### Project

Template-agnostic configuration container.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID (PK) | Auto-generated |
| `name` | String | Project name |
| `description` | String | Optional description |
| `source_config` | JSONB | How to group sources (`{"type": "web", "group_by": "company"}`) |
| `extraction_schema` | JSONB | Field group definitions (from template) |
| `entity_types` | JSONB | Entity type configurations |
| `prompt_templates` | JSONB | Custom prompt overrides |
| `is_template` | Boolean | Whether this is a template project |
| `is_active` | Boolean | Soft delete flag |
| `created_at` | DateTime | Auto-set |
| `updated_at` | DateTime | Auto-updated |

#### Source

Document/URL storage with multi-content strategy.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID (PK) | Auto-generated |
| `project_id` | UUID (FK) | Parent project |
| `uri` | String | Source URL |
| `source_group` | String | Company/group name |
| `source_type` | String | "web" (default) |
| `title` | String | Page title |
| `content` | Text | Processed markdown for extraction |
| `raw_content` | Text | Original unmodified content |
| `cleaned_content` | Text | Domain-deduped content |
| `status` | Enum | pending, ready, extracted, partial, skipped, completed, failed |
| `meta_data` | JSONB | `{domain, http_status, language, ...}` |
| `classification_page_type` | String | Skip / extract (from classifier) |
| `classification_field_groups` | JSONB | Relevant field groups list |
| `created_by_job_id` | UUID (FK) | Job that created this source |
| `created_at` | DateTime | |
| `fetched_at` | DateTime | When content was fetched |

**Unique constraint**: `(project_id, uri)` - prevents duplicate URLs per project.

#### Extraction

Schema-compliant extracted data.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID (PK) | Auto-generated |
| `source_id` | UUID (FK) | Parent source |
| `project_id` | UUID (FK) | Parent project |
| `extraction_type` | String | Field group name or "generic" |
| `data` | JSONB | Extracted values + metadata (`_quotes`, `_conflicts`, `_validation`) |
| `confidence` | Float | 0.0-1.0 quality score |
| `chunk_index` | Integer | Which chunk this came from |
| `chunk_context` | JSONB | Chunk metadata (header_path, total_chunks) |
| `embedding_id` | String | Reference to Qdrant vector point |
| `source_group` | String | Denormalized for query efficiency |
| `created_at` | DateTime | |

#### Entity

Named entities with deduplication.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID (PK) | Auto-generated |
| `project_id` | UUID (FK) | Parent project |
| `entity_type` | String | product, person, location, spec, etc. |
| `value` | String | Original entity value |
| `normalized_value` | String | Lowercased/canonical form |
| `source_group` | String | Company/group scope |
| `attributes` | JSONB | Numeric details (pricing, limits, specs) |
| `created_at` | DateTime | |

**Dedup scope**: `(project_id, source_group, entity_type, normalized_value)`

#### ExtractionEntity (Junction)

Links extractions to entities.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID (PK) | |
| `extraction_id` | UUID (FK) | |
| `entity_id` | UUID (FK) | |
| `role` | String | "mention", "subject", "pricing_detail", etc. |

#### DomainBoilerplate

Boilerplate fingerprints per (project, domain).

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID (PK) | |
| `project_id` | UUID (FK) | |
| `domain` | String | Netloc domain string |
| `boilerplate_hashes` | JSONB | List of 16-char hex SHA-256 hashes |
| `pages_analyzed` | Integer | How many pages were analyzed |
| `blocks_boilerplate` | Integer | Number of boilerplate blocks found |
| `bytes_removed_avg` | Integer | Average bytes removed per page |
| `threshold_pct` | Float | Threshold used for analysis |
| `min_pages` | Integer | Minimum pages gate |
| `min_block_chars` | Integer | Minimum block size |
| `created_at` | DateTime | |
| `updated_at` | DateTime | |

### 2.2 Job & Control Models

#### Job

Tracks scrape/extract/crawl operations.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID (PK) | |
| `type` | String | crawl, scrape, extract |
| `status` | Enum | queued, running, completed, failed, cancelling, cancelled |
| `payload` | JSONB | Job-specific parameters + state machine data |
| `result` | JSONB | Final output (pages_total, sources_created, etc.) |
| `error` | Text | Error message on failure |
| `started_at` | DateTime | When job began processing |
| `completed_at` | DateTime | When job finished |
| `cancellation_requested_at` | DateTime | For graceful cancellation |
| `created_at` | DateTime | |

#### RateLimit

Domain-specific rate limiting state.

| Column | Type | Purpose |
|--------|------|---------|
| `id` | UUID (PK) | |
| `domain` | String | |
| `daily_count` | Integer | Requests today |
| `daily_reset_at` | DateTime | UTC midnight reset |

---

## 3. Repository Pattern

Each repository takes a SQLAlchemy `Session`, calls `flush()` (not `commit()`). The caller manages transactions.

### SourceRepository (`src/services/storage/repositories/source.py`)

| Method | Purpose |
|--------|---------|
| `create()` | Create new source |
| `get()` | Get by ID |
| `get_by_uri()` | Get by (project_id, uri) |
| `upsert()` | PostgreSQL `ON CONFLICT DO UPDATE` (race-safe) |
| `update_content()` | Update content fields |
| `update_status()` | Set status |
| `get_by_project_and_status()` | Query by status within project |
| `get_domains_for_project()` | Distinct domains with page counts |
| `get_by_project_and_domain()` | All pages for a domain |

### ExtractionRepository (`src/services/storage/repositories/extraction.py`)

| Method | Purpose |
|--------|---------|
| `create()` | Create single extraction |
| `create_batch()` | Create multiple extractions |
| `get_by_source()` | All extractions for a source |
| `count()` / `list()` | Filtered queries with pagination |
| `query_jsonb()` | Filter by JSON path (PG `#>>` / SQLite `json_extract`) |
| `filter_by_data()` | Multi-field JSONB filtering |
| `find_orphaned()` | Extractions without `embedding_id` |
| `update_embedding_ids_batch()` | Bulk set embedding IDs |

**Cross-DB support**: Handles PostgreSQL (`#>>` operator) and SQLite (`json_extract()`) for JSONB queries.

### EntityRepository (`src/services/storage/repositories/entity.py`)

| Method | Purpose |
|--------|---------|
| `get_or_create()` | Dedup by (project, source_group, type, normalized_value) |
| `list_by_type()` | Entities of a type within project |
| `count_by_type()` | Grouped counts |
| `link_to_extraction()` | Idempotent junction record creation |
| `get_entities_for_extraction()` | Reverse lookup |
| `get_extractions_for_entity()` | Find usages |

### JobRepository (`src/services/storage/repositories/job.py`)

| Method | Purpose |
|--------|---------|
| `get()` / `delete()` | Basic CRUD |
| `request_cancellation()` | Set status to CANCELLING |
| `mark_cancelled()` | Set status to CANCELLED |
| `is_cancellation_requested()` | Worker checkpoint |

---

## 4. Qdrant Vector Store

### Collection Structure

| Field | Value |
|-------|-------|
| **Model** | bge-m3 |
| **Dimensions** | 1024 |
| **Max tokens** | 8192 |
| **Distance** | Cosine similarity |

### Operations

| Operation | Purpose |
|-----------|---------|
| `upsert()` | Store extraction embedding with metadata |
| `search()` | Semantic search with filters |
| `delete()` | Remove by extraction ID |

### Metadata per Point

```json
{
  "project_id": "uuid",
  "source_group": "company_name",
  "extraction_type": "field_group_name",
  "source_id": "uuid",
  "confidence": 0.85
}
```

### Initialization

On startup (in lifespan):
- Creates collection if not exists
- Exponential backoff retry (5 attempts)
- Validates dimensions match embedding model

---

## 5. Redis Usage

### Purposes

| Use Case | Redis Structure |
|----------|----------------|
| **Rate limiting** | String keys with TTL (`ratelimit:{domain}:*`) |
| **LLM request queue** | Redis Streams + Pub/Sub |
| **Field group embeddings cache** | String keys with SHA-256 hash (`fg_embed:{hash}`) |
| **Response storage** | String keys with 300s TTL |

### Connection

```python
# redis_client.py
# Async Redis client with connection pooling
redis = aioredis.from_url(settings.redis_url)
```

---

## 6. Database Connection

```python
# database.py
# SQLAlchemy async engine + session factory
engine = create_async_engine(
    settings.database_url,
    pool_size=5,
    max_overflow=15
)
async_session = async_sessionmaker(engine, class_=AsyncSession)
```

### Connection String

```
postgresql+psycopg://scristill:scristill@192.168.0.136:5432/scristill
```

Uses psycopg v3 (not psycopg2) for native async support.

---

## 7. Data Flow Through Storage

```
Source Creation (Scrape/Crawl):
  Source.content = markdown
  Source.raw_content = original
  Source.status = PENDING
  Source.meta_data = {domain, ...}

Domain Dedup:
  Source.cleaned_content = deduped markdown
  DomainBoilerplate record created

Extraction:
  Extraction.data = JSONB {field: value, ...}
  Extraction.embedding_id → Qdrant point
  Entity records via get_or_create()
  ExtractionEntity links

Job Tracking:
  Job.status: queued → running → completed/failed/cancelled
  Job.payload: state machine data (smart crawl phase, checkpoint)
  Job.result: final statistics
```

---

## 8. Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Repository pattern (flush, not commit) | Caller controls transaction boundaries; enables batching |
| Source upsert on (project_id, uri) | Race-safe duplicate prevention |
| Entity dedup by normalized_value | Canonical form prevents "Widget" vs "widget" duplicates |
| JSONB for extraction data | Schema-agnostic storage; any template works |
| Denormalized source_group on Extraction | Avoids JOIN to Source table for filtered queries |
| Separate content / raw_content / cleaned_content | Non-destructive pipeline; always preservable original |
| PostgreSQL + SQLite compatibility | Production uses PG; tests can use SQLite |
| embedding_id as string (not UUID) | Qdrant point IDs may not be standard UUIDs |
