# TechFacts Scraper - Architecture

## Overview

A self-hosted system for scraping technical documentation from company websites, extracting structured facts via LLM, and generating comparison reports.

## System Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              CU1 (W-2135, 196GB RAM, A2000)                 │
│                                                                             │
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐   ┌─────────────────┐  │
│  │  Firecrawl  │   │   Redis     │   │   Qdrant    │   │   PostgreSQL    │  │
│  │   + Playwright│   │  (queue/    │   │  (vectors)  │   │   (metadata,    │  │
│  │             │   │   cache)    │   │             │   │    jobs, state) │  │
│  └──────┬──────┘   └──────┬──────┘   └──────┬──────┘   └────────┬────────┘  │
│         │                 │                 │                   │           │
│         └─────────────────┴────────┬────────┴───────────────────┘           │
│                                    │                                        │
│                          ┌─────────┴─────────┐                              │
│                          │  Pipeline Service │                              │
│                          │    (FastAPI)      │                              │
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

## Components

### CU1 Services (Portainer Stack)

| Service | Purpose | Port | Resource Notes |
|---------|---------|------|----------------|
| `firecrawl-api` | Web scraping API | 3002 | 2-4GB RAM |
| `playwright` | JS rendering | 3000 | 1-2GB RAM |
| `redis` | Job queue, caching | 6379 | 512MB-2GB |
| `qdrant` | Vector storage | 6333 | Scales with data |
| `postgres` | Metadata, job state | 5432 | 1-2GB |
| `pipeline` | Orchestration API | 8000 | 1-2GB |
| `flaresolverr` | Cloudflare bypass (optional) | 8191 | 1GB |

### External Dependencies (Existing)

| Service | Location | Purpose |
|---------|----------|---------|
| vLLM Gateway | 192.168.0.247:9003 | LLM inference routing |
| vLLM (CU1) | 192.168.0.136:9003 | Embeddings (BGE-large-en) |
| vLLM (CU2) | Via gateway | Extraction LLM (Qwen3/Gemma) |

## Data Flow

### 1. Scrape Flow

```
URL Input → Firecrawl API → Playwright (JS render) → Markdown Output
                ↓
           Rate Limiter (Redis)
                ↓
           Store raw content (PostgreSQL)
```

### 2. Extraction Flow

```
Markdown Content → Chunking (if needed)
        ↓
   Profile Selection (extraction scope)
        ↓
   vLLM Gateway → Extraction LLM
        ↓
   Structured Facts (JSON)
        ↓
   Validation & Deduplication
        ↓
   Store facts (PostgreSQL + Qdrant)
```

### 3. Report Flow

```
Query (company/topic/comparison)
        ↓
   Qdrant vector search + PostgreSQL filters
        ↓
   Fact aggregation
        ↓
   vLLM Gateway → Report LLM
        ↓
   Markdown/PDF output
```

## Data Models

### Extraction Profile

```yaml
profile:
  name: string
  categories: [string]
  prompt_focus: string
  depth: summary | detailed | comprehensive
  custom_instructions: string (optional)
```

### Scraped Page

```sql
pages:
  id: uuid
  url: string (unique)
  domain: string
  company: string
  title: string
  markdown_content: text
  scraped_at: timestamp
  status: pending | completed | failed
  metadata: jsonb
```

### Extracted Fact

```sql
facts:
  id: uuid
  page_id: uuid (FK)
  fact_text: text
  category: string
  confidence: float
  profile_used: string
  extracted_at: timestamp
  metadata: jsonb
  embedding_id: string (Qdrant reference)
```

### Qdrant Payload

```json
{
  "fact_id": "uuid",
  "fact_text": "string",
  "category": "string",
  "company": "string",
  "source_url": "string",
  "confidence": 0.95
}
```

### Job Queue

```sql
jobs:
  id: uuid
  type: scrape | extract | report
  status: queued | running | completed | failed
  priority: int
  payload: jsonb
  result: jsonb
  created_at: timestamp
  started_at: timestamp
  completed_at: timestamp
  error: text
```

## Configuration

### Environment Variables

```bash
# vLLM Gateway (existing)
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
DATABASE_URL=postgresql://user:pass@postgres:5432/techfacts
```

## API Endpoints (Pipeline Service)

### Scraping

```
POST /api/v1/scrape
  body: { urls: [string], company: string, profile: string }
  
GET /api/v1/scrape/{job_id}
  returns: job status and results
```

### Extraction

```
POST /api/v1/extract
  body: { page_ids: [uuid], profile: string }
  
GET /api/v1/profiles
  returns: available extraction profiles

POST /api/v1/profiles
  body: { name, categories, prompt_focus, depth }
```

### Reports

```
POST /api/v1/reports
  body: { type: single|comparison|topic, filters: {...}, format: md|pdf }
  
GET /api/v1/reports/{report_id}
  returns: generated report
```

### Search

```
POST /api/v1/search
  body: { query: string, filters: { company?, category?, date_range? } }
  returns: relevant facts with sources
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
4. **Persistent blocks**: Mark domain as limited, flag for review

### Politeness

- Respect robots.txt (configurable)
- Cache responses (24h default)
- Re-scrape only on explicit request or schedule

## Extraction Profiles

### Built-in Profiles

| Profile | Categories | Use Case |
|---------|------------|----------|
| `technical_specs` | specs, hardware, requirements | Product specifications |
| `api_docs` | endpoints, auth, rate_limits | API documentation |
| `security` | certifications, compliance | Security posture |
| `pricing` | pricing, tiers, limits | Competitive intel |
| `general` | all categories | Broad extraction |

### Custom Profiles

Users can define custom profiles with:
- Category whitelist
- Focus instructions for LLM
- Depth setting (affects prompt detail)
- Custom post-processing rules

## Report Types

| Type | Input | Output |
|------|-------|--------|
| Single Company | company filter | All facts organized by category |
| Comparison | 2+ companies, categories | Side-by-side comparison table |
| Topic | category filter | Cross-company topic summary |
| Executive Summary | company/date filters | LLM-condensed highlights |

## Scaling Considerations

### Current Capacity (MVP)

- ~100-500 pages/day comfortable
- ~10k-50k facts in Qdrant
- Single-threaded extraction (GPU bound)

### Future Scaling

- Add Celery workers for parallel extraction
- Shard Qdrant for larger fact counts
- Add proxy rotation for higher scrape volume
