# TechFacts Scraper

Self-hosted system for scraping technical documentation, extracting structured facts via LLM, and generating comparison reports.

## Features

- **Web Scraping**: Firecrawl-based scraping with JS rendering and anti-bot handling
- **Flexible Extraction**: Configurable extraction profiles for different fact types
- **Vector Search**: Semantic search across extracted facts via Qdrant
- **Report Generation**: Single company, comparison, and topic reports
- **Homelab Optimized**: Designed for your CU1/CU2 infrastructure

## Quick Start

### Prerequisites

- Docker & Docker Compose
- Portainer (recommended)
- Existing vLLM gateway at `192.168.0.247:9003`
- BGE-large-en available at `192.168.0.136:9003`

### 1. Clone and Configure

```bash
git clone <repo>
cd techfacts-scraper

# Copy and edit environment
cp .env.example .env
# Edit .env with your settings
```

### 2. Deploy via Portainer

1. In Portainer, create new Stack
2. Upload `docker-compose.yml`
3. Add environment variables from `.env`
4. Deploy

Or via CLI:

```bash
docker compose up -d
```

### 3. Verify Services

```bash
# Check all services running
docker compose ps

# Test Firecrawl
curl http://localhost:3002/health

# Test Pipeline API
curl http://localhost:8000/health
```

### 4. First Scrape

```bash
# Scrape a company's docs
curl -X POST http://localhost:8000/api/v1/scrape \
  -H "Content-Type: application/json" \
  -d '{
    "urls": ["https://docs.example.com/api"],
    "company": "Example Inc",
    "profile": "api_docs"
  }'
```

## Usage

### Scrape URLs

```bash
# Single URL
curl -X POST http://localhost:8000/api/v1/scrape \
  -d '{"urls": ["https://company.com/docs"], "company": "CompanyName"}'

# With sitemap discovery
curl -X POST http://localhost:8000/api/v1/scrape \
  -d '{"urls": ["https://company.com"], "company": "CompanyName", "discover": true}'
```

### Extract Facts

```bash
# Extract with specific profile
curl -X POST http://localhost:8000/api/v1/extract \
  -d '{"page_ids": ["uuid-here"], "profile": "technical_specs"}'

# Custom extraction focus
curl -X POST http://localhost:8000/api/v1/extract \
  -d '{
    "page_ids": ["uuid-here"],
    "profile": "custom",
    "custom_focus": "Extract pricing tiers and feature limits"
  }'
```

### Search Facts

```bash
curl -X POST http://localhost:8000/api/v1/search \
  -d '{"query": "API rate limits", "filters": {"company": "Example Inc"}}'
```

### Generate Reports

```bash
# Single company report
curl -X POST http://localhost:8000/api/v1/reports \
  -d '{"type": "single", "company": "Example Inc", "format": "md"}'

# Comparison report
curl -X POST http://localhost:8000/api/v1/reports \
  -d '{
    "type": "comparison",
    "companies": ["Company A", "Company B"],
    "categories": ["pricing", "api_limits"],
    "format": "md"
  }'
```

## Extraction Profiles

| Profile | Focus | Categories |
|---------|-------|------------|
| `technical_specs` | Hardware, requirements, compatibility | specs, hardware, requirements |
| `api_docs` | Endpoints, auth, rate limits, SDKs | endpoints, auth, rate_limits, sdks |
| `security` | Certifications, compliance, encryption | certifications, compliance, encryption |
| `pricing` | Pricing tiers, features, limits | pricing, features, limits |
| `general` | Broad technical facts | all |
| `custom` | User-defined focus | user-defined |

### Creating Custom Profiles

```bash
curl -X POST http://localhost:8000/api/v1/profiles \
  -d '{
    "name": "infrastructure",
    "categories": ["deployment", "scaling", "regions"],
    "prompt_focus": "Cloud deployment options, scaling capabilities, regional availability",
    "depth": "detailed"
  }'
```

## Configuration

Key environment variables:

```bash
# LLM (via your existing gateway)
OPENAI_BASE_URL=http://192.168.0.247:9003/v1
OPENAI_EMBEDDING_BASE_URL=http://192.168.0.136:9003/v1
LLM_MODEL=gemma3-12b-awq
RAG_EMBEDDING_MODEL=bge-large-en

# Scraping behavior
SCRAPE_DELAY_MIN=2
SCRAPE_DELAY_MAX=5
SCRAPE_MAX_CONCURRENT_PER_DOMAIN=2

# Services
REDIS_URL=redis://redis:6379
QDRANT_URL=http://qdrant:6333
DATABASE_URL=postgresql://user:pass@postgres:5432/techfacts
```

See `.env.example` for full list.

## Architecture

See [ARCHITECTURE.md](./ARCHITECTURE.md) for detailed system design.

```
CU1: Firecrawl + Redis + Qdrant + PostgreSQL + Pipeline API
           ↓
      vLLM Gateway (192.168.0.247:9003)
           ↓
CU1: BGE-large-en (embeddings)
CU2: Qwen3/Gemma (extraction/reports)
```

## Project Structure

```
techfacts-scraper/
├── docker-compose.yml
├── .env.example
├── ARCHITECTURE.md
├── README.md
├── TODO.md
├── pipeline/
│   ├── Dockerfile
│   ├── requirements.txt
│   ├── config.yaml
│   ├── main.py
│   ├── api/
│   ├── services/
│   ├── models/
│   └── prompts/
└── docs/
    ├── TODO_scraper.md
    ├── TODO_extraction.md
    ├── TODO_storage.md
    └── TODO_reports.md
```

## Development

```bash
# Local development (without Docker)
cd pipeline
python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
uvicorn main:app --reload

# Run tests
pytest
```

## Troubleshooting

### Firecrawl not scraping JS content

Check Playwright service is running:
```bash
docker compose logs playwright
```

### LLM timeouts

Increase timeout in config:
```yaml
llm:
  http_timeout_seconds: 900
```

### Cloudflare blocks

Enable FlareSolverr in docker-compose and set:
```bash
USE_FLARESOLVERR=true
```

## License

MIT
