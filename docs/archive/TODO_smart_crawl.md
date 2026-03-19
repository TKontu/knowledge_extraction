# Smart Crawl Implementation

## Status: ✅ COMPLETE

**Completed:** 2026-01-31

## Overview

The Smart Crawl feature uses Firecrawl's Map endpoint to discover URLs with metadata, then filters them using embedding-based relevance scoring before batch scraping only relevant URLs.

**Key Principles:**
- Domain/context agnostic - works for ANY context (jobs, products, news, etc.)
- Template-driven but optional - `crawl_config` enhances but isn't required
- Backward compatible - `smart_crawl_enabled=False` uses traditional crawl

## Architecture

```
CrawlRequest (smart_crawl_enabled=True)
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ Phase 1: MAP                                                │
│ FirecrawlClient.map(url, search=focus_terms)                │
│ Returns: [{url, title, description}, ...]                   │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ Phase 2: FILTER                                             │
│ UrlRelevanceFilter.filter_urls(urls, field_groups)          │
│ - Embed field_group descriptions as "target context"        │
│ - Embed URL title+description                               │
│ - Filter by cosine similarity threshold                     │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ Phase 3: SCRAPE                                             │
│ FirecrawlClient.batch_scrape(relevant_urls)                 │
│ Only scrapes URLs that passed the filter                    │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
Store as Sources → Auto-extract (if enabled)
```

---

## Implementation Checklist

### ✅ Phase 1: Firecrawl Parameters
- [x] `CrawlRequest` fields: `allow_subdomains`, `ignore_query_parameters`
- [x] Pass parameters to job payload
- [x] Pass parameters to Firecrawl client

### ✅ Phase 2: Firecrawl Client Extensions
- [x] `MapResult` dataclass
- [x] `BatchScrapeResult` dataclass
- [x] `map()` method with search, limit, subdomains options
- [x] `start_batch_scrape()` method
- [x] `get_batch_scrape_status()` method with pagination support

### ✅ Phase 3: URL Relevance Filter Service
- [x] `FilteredUrl` dataclass
- [x] `UrlFilterResult` dataclass
- [x] `UrlRelevanceFilter` class
- [x] Context embedding from field_groups + focus_terms
- [x] Batch URL metadata embedding
- [x] Cosine similarity filtering
- [x] Configurable threshold

### ✅ Phase 4: CrawlConfig in Templates
- [x] `CrawlConfig` dataclass in `schema_adapter.py`
- [x] `include_patterns`, `exclude_patterns`, `focus_terms`, `relevance_threshold`
- [x] `from_dict()` and `validate()` methods
- [x] `parse_template()` returns 4-tuple with CrawlConfig
- [x] Template validation in `template_loader.py`
- [x] Embed crawl_config in extraction_schema when creating from template

### ✅ Phase 5: Smart Crawl in CrawlWorker
- [x] Detect `smart_crawl_enabled` in job payload
- [x] `_smart_crawl_map_phase()` - discovers URLs via Map API
- [x] `_smart_crawl_filter_phase()` - filters by relevance
- [x] `_smart_crawl_scrape_phase()` - batch scrapes relevant URLs
- [x] `_load_crawl_config()` - loads from embedded extraction_schema
- [x] `_load_project_field_groups()` - loads field groups for context
- [x] Merge focus_terms from request AND template in both Map and Filter phases
- [x] Merge patterns from request AND template in Filter phase
- [x] Store Sources from batch scrape results

### ✅ Phase 6: API Updates
- [x] `CrawlRequest` fields: `smart_crawl_enabled`, `relevance_threshold`, `focus_terms`
- [x] `CrawlStatusResponse` fields: `smart_crawl_enabled`, `smart_crawl_phase`, `urls_discovered`, `urls_relevant`
- [x] Regex validation for `include_paths`, `exclude_paths`
- [x] Status endpoint returns smart crawl progress

### ✅ Phase 7: Configuration
- [x] `smart_crawl_default_relevance_threshold` (default: 0.4)
- [x] `smart_crawl_map_limit` (default: 5000)
- [x] `smart_crawl_batch_max_concurrency` (default: 10)

---

## Test Coverage

### ✅ Unit Tests
| Test File | Status |
|-----------|--------|
| `tests/test_url_filter.py` | ✅ Complete |
| `tests/test_crawl_config.py` | ✅ Complete |
| `tests/test_firecrawl_client_map.py` | ✅ Complete |

### ⚠️ Integration Tests
| Test File | Status |
|-----------|--------|
| `tests/test_smart_crawl_integration.py` | ❌ Not implemented |

**Note:** Full end-to-end integration tests for the Smart Crawl flow are recommended but not yet implemented.

---

## Files Modified/Created

| File | Action | Status |
|------|--------|--------|
| `src/services/scraper/client.py` | Modified | ✅ |
| `src/services/scraper/url_filter.py` | Created | ✅ |
| `src/services/scraper/crawl_worker.py` | Modified | ✅ |
| `src/services/extraction/schema_adapter.py` | Modified | ✅ |
| `src/services/projects/template_loader.py` | Modified | ✅ |
| `src/models.py` | Modified | ✅ |
| `src/api/v1/crawl.py` | Modified | ✅ |
| `src/api/v1/projects.py` | Modified | ✅ |
| `src/config.py` | Modified | ✅ |
| `tests/test_url_filter.py` | Created | ✅ |
| `tests/test_crawl_config.py` | Created | ✅ |
| `tests/test_firecrawl_client_map.py` | Created | ✅ |

---

## Configuration

### Environment Variables

```bash
# Smart Crawl Settings (all optional - have sensible defaults)
SMART_CRAWL_DEFAULT_RELEVANCE_THRESHOLD=0.4  # 0.0-1.0, higher = stricter filtering
SMART_CRAWL_MAP_LIMIT=5000                   # Max URLs from Map endpoint
SMART_CRAWL_BATCH_MAX_CONCURRENCY=10         # Concurrent batch scrape requests
```

### Required Dependencies

Smart Crawl requires:
1. **Embedding service** - `OPENAI_EMBEDDING_BASE_URL` must point to a working embedding API
2. **Firecrawl** - Must support Map and Batch Scrape endpoints (v1 API)

### Usage

Smart crawl is **disabled by default** and enabled per-request:

```json
POST /api/v1/crawl
{
  "url": "https://example.com",
  "project_id": "...",
  "company": "Example Corp",
  "smart_crawl_enabled": true,
  "focus_terms": ["products", "pricing"],
  "relevance_threshold": 0.5
}
```

### Template Configuration (Optional)

Templates can include `crawl_config` to provide default smart crawl settings:

```yaml
# In template YAML
crawl_config:
  focus_terms:
    - "product specifications"
    - "pricing plans"
  relevance_threshold: 0.5
  exclude_patterns:
    - ".*/careers/.*"
    - ".*/news/\\d{4}/.*"
```

---

## Bug Fixes Applied

See `docs/smart_crawl_pipeline_review.md` for the full review history.

### Fixed Issues
1. ✅ Duplicate `_load_crawl_config()` call
2. ✅ CrawlConfig patterns not used in filter phase
3. ✅ Project doesn't store crawl_config (now embedded in extraction_schema)
4. ✅ `_load_crawl_config()` always returned None
5. ✅ No regex validation for URL patterns
6. ✅ Missing smart crawl status in API response
7. ✅ Map phase didn't use template focus_terms

---

*Implementation completed 2026-01-31*
