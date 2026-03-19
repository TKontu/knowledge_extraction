# Architecture Analysis - Knowledge Extraction Orchestrator

**Date**: 2026-03-02
**Scope**: Critical but objective assessment of the system implementation with viable alternatives

---

## 1. Executive Summary

The Knowledge Extraction Orchestrator is a **well-conceived system** that solves a genuinely complex problem: converting unstructured web content into structured, searchable knowledge through LLM-based extraction. The core pipeline (Crawl -> Extract -> Embed -> Search -> Report) is sound and the template-agnostic design is a significant strength.

However, the implementation has accumulated complexity in ways that create maintenance and reliability risks. The main concerns are: **monolithic pipeline classes** with too many responsibilities, **fragmented error handling**, **dual import paths** breaking test reliability, and several **features built but disabled by default** that may never be activated.

**Overall Rating**: **Solid foundation with architectural debt** - the system works and produces value, but certain structural improvements would significantly reduce maintenance burden and improve reliability.

---

## 2. Strengths

### 2.1 Template-Agnostic Design
The field group abstraction cleanly separates extraction logic from domain knowledge. The same pipeline processes drivetrain specifications, recipes, job postings, or any other schema. This is the system's most important architectural decision and it's done well.

### 2.2 Multi-Backend Storage Strategy
The combination of PostgreSQL (structured data), Qdrant (vector search), and Redis (caching/queuing) is appropriate for the workload. Each backend handles what it's best at.

### 2.3 Non-Destructive Content Pipeline
The three-content model (`content` / `raw_content` / `cleaned_content`) on Source records is excellent. Original data is never modified, cleaning is additive, and the pipeline can always fall back.

### 2.4 Smart Crawl Innovation
The Map -> Filter -> Scrape pipeline for smart crawling is genuinely clever. Using embedding similarity to filter URLs before scraping saves significant time and cost. The fallback to traditional crawling when too few URLs are found is a good safety net.

### 2.5 Adaptive Concurrency
The LLM worker's adaptive concurrency tuning (scale down on timeouts, scale up on success) is a pragmatic solution that avoids manual tuning for different LLM backends.

### 2.6 Job State Machine
Storing smart crawl phase state in the job payload enables resume across scheduler restarts. Combined with checkpoint/resume for extraction jobs, the system handles interruptions gracefully.

### 2.7 Comprehensive API
47 endpoints covering the full lifecycle with consistent patterns (pagination, status codes, UUID validation). The MCP layer mirrors the API cleanly, enabling LLM-driven workflows.

---

## 3. Critical Issues

### 3.1 Monolithic Pipeline Classes (Severity: HIGH)

**Problem**: `ExtractionPipelineService` and `SchemaExtractionPipeline` each handle 5+ distinct responsibilities: extraction orchestration, embedding generation, Qdrant storage, entity extraction, source status updates, and checkpoint management.

**Impact**:
- Tests require 10-15 mock dependencies each
- Adding a new post-extraction step requires modifying the pipeline class
- Embedding failures are coupled to extraction success
- No way to run embedding separately from extraction

**Evidence**: `pipeline.py` is ~900 lines with methods spanning extraction, embedding, entity extraction, and status management.

**Viable Alternative**: Decompose into a pipeline coordinator pattern:

```
ExtractionCoordinator (thin orchestrator)
  ├── ExtractionService      (field group extraction only)
  ├── EmbeddingService       (embed + Qdrant upsert)
  ├── EntityService           (entity extraction + dedup)
  └── StatusTracker          (source status + checkpoints)
```

Each service is independently testable and replaceable. The coordinator's only job is sequencing.

### ~~3.2 Fragmented Exception Handling (Severity: HIGH)~~ ✅ DONE (2026-03-02)

**Resolved**: Exception hierarchy established in `src/exceptions.py`. All 10 custom exceptions now inherit from `AppError` with two-level classification: `TransientError` (retryable) and `PermanentError` (fail-fast). Ambiguous exceptions (`LLMExtractionError`, `ScrapeError`) sit at `AppError` level with per-instance `is_retryable` flag. FastAPI safety-net handler added in `main.py`. 59 tests in `tests/test_exception_hierarchy.py`.

```
AppError(Exception)               — base, code + is_retryable + details
├── TransientError(AppError)      — is_retryable=True
│   ├── QueueFullError            ├── RequestTimeoutError
│   ├── FlareSolverrError         └── RateLimitExceeded
├── PermanentError(AppError)      — is_retryable=False
│   ├── TemplateLoadError         └── PDFConversionError
├── LLMExtractionError(AppError)  — ambiguous
└── ScrapeError(AppError)         — ambiguous
```

### ~~3.3 Dual Import Paths (Severity: MEDIUM-HIGH)~~ ✅ DONE (2026-03-02)

**Resolved**: All `from src.X` imports standardized to `from X` across 6 source files + 29 test files, including mock.patch strings. Commit `d567f96`.

---

## 4. Structural Concerns

### 4.1 Scheduler as God Object

The `JobScheduler` creates, manages, and lifecycle-controls all service instances. It's both a scheduler and a service container.

**Current**:
```python
class JobScheduler:
    # Service creation (lines 100-160)
    # Scrape loop (lines 200-260)
    # Crawl loop (lines 300-380)
    # Extract loop (lines 400-450)
    # LLM worker management (lines 140-160)
```

**Better**: Separate service container from scheduling:
```python
class ServiceContainer:     # Creates and caches services
class JobScheduler:         # Only scheduling logic, receives services
```

### 4.2 Configuration Sprawl

`config.py` has 100+ parameters. While Pydantic Settings handles this well mechanically, the cognitive load is high. Some parameters interact in non-obvious ways.

**Examples of non-obvious interactions**:
- `extraction_chunk_max_tokens` * 4 must align with `EXTRACTION_CONTENT_LIMIT`
- `classification_embedding_high_threshold` must exceed `classification_embedding_low_threshold` (validated, but the relationship isn't obvious from names)
- `domain_dedup_threshold_pct` vs `domain_dedup_min_pages` (gate vs threshold)

**Suggestion**: Group into nested settings classes:
```python
class ExtractionSettings(BaseSettings):
    chunk_max_tokens: int = 5000
    chunk_overlap_tokens: int = 500
    # ...

class CrawlSettings(BaseSettings):
    delay_ms: int = 500
    max_concurrency: int = 5
    # ...
```

### 4.3 Feature Flags Without Activation Strategy

Six extraction reliability features (Phase 1A) are built but default to OFF:
- `extraction_source_quoting_enabled`
- `extraction_conflict_detection_enabled`
- `extraction_validation_enabled`
- `extraction_chunk_overlap_tokens` (0 = disabled)
- etc.

These represent significant engineering investment. Without a clear activation plan, they risk becoming dead code. Consider: either enable them (they've been tested) or document a clear go/no-go criteria.

### 4.4 Classification Complexity

The classification system has three modes (disabled, rule-based, smart) configured via two flags (`classification_enabled`, `smart_classification_enabled`). The smart classifier has a 3-tier confidence system with reranker fallback, Redis-cached embeddings, and template-specific skip pattern overrides.

This is powerful but complex. A page's classification path depends on:
1. Whether classification is enabled
2. Whether smart classification is enabled
3. Template's classification_config override
4. URL pattern match
5. Embedding similarity score tier
6. Reranker confirmation (medium tier only)

Consider documenting the decision tree explicitly for operators.

---

## 5. Subsystem-Level Alternatives

### 5.1 LLM Queue: Redis Streams vs Task Queue

**Current**: Custom Redis Streams implementation with pub/sub notifications, backpressure management, and DLQ.

**Alternative**: Celery or arq (lightweight async task queue).

| Aspect | Current (Custom) | Celery/arq |
|--------|-----------------|------------|
| Complexity | ~400 lines custom code | Library handles it |
| Backpressure | Custom implementation | Built-in |
| DLQ | Custom implementation | Built-in |
| Monitoring | Manual metrics | Flower / built-in |
| Flexibility | Full control | Framework constraints |
| Dependencies | Redis only | Redis + library |

**Assessment**: The custom implementation is reasonable given the specific needs (adaptive concurrency, temperature variation on retry, field-group-aware processing). A generic task queue would need custom extensions for these features anyway. **Current approach is acceptable**, but the custom code should be well-tested.

### 5.2 Job Scheduling: Polling vs Event-Driven

**Current**: Polling loop with `SELECT FOR UPDATE SKIP LOCKED` every 5 seconds.

**Alternative**: PostgreSQL LISTEN/NOTIFY for event-driven job dispatch.

| Aspect | Current (Polling) | LISTEN/NOTIFY |
|--------|-------------------|---------------|
| Latency | Up to 5s | Immediate |
| DB load | Constant queries | On-demand |
| Complexity | Simple, proven | Requires connection management |
| Reliability | Very reliable | Requires reconnection handling |

**Assessment**: For the current scale (10s-100s of jobs), polling is simpler and more reliable. At higher scale (1000s of concurrent jobs), event-driven would reduce database load. **Current approach is appropriate for current scale.**

### 5.3 Content Chunking: Custom vs LangChain/LlamaIndex

**Current**: Custom markdown-aware chunker with header-based splitting, paragraph assembly, and configurable overlap.

**Alternative**: LangChain's `RecursiveCharacterTextSplitter` or LlamaIndex's `SentenceSplitter`.

| Aspect | Current (Custom) | Library |
|--------|-----------------|---------|
| Markdown awareness | Yes (header-based) | Limited |
| CJK support | Yes (custom counting) | Framework-dependent |
| Overlap alignment | Paragraph-aligned | Character-aligned |
| Maintenance | Self-maintained | Community-maintained |
| Dependencies | None | Large framework |

**Assessment**: The custom chunker is well-suited to the markdown-heavy content from web scraping. Library alternatives don't handle markdown headers as first-class splitting points. **Current approach is superior for this use case.**

### 5.4 Extraction: Custom Prompting vs Instructor/Outlines

**Current**: Custom prompt construction with JSON repair for malformed responses.

**Alternative**: [Instructor](https://github.com/jxnl/instructor) for Pydantic-validated LLM outputs, or [Outlines](https://github.com/outlines-dev/outlines) for structured generation.

| Aspect | Current (Custom) | Instructor | Outlines |
|--------|-----------------|------------|----------|
| JSON reliability | `try_repair_json` (multi-stage) | Pydantic validation + retry | Guaranteed valid JSON |
| Schema enforcement | Post-hoc (SchemaValidator) | At generation time | At generation time |
| Model compatibility | Any OpenAI-compatible | Any OpenAI-compatible | Specific backends |
| Retry strategy | Temperature variation | Validation-based retry | N/A (always valid) |
| Custom prompts | Full control | Template-based | Grammar-based |

**Assessment**: For a system that already has working JSON repair and schema validation, switching to Instructor would provide incremental improvement with less code. Outlines would be ideal but requires model-specific integration. **Consider Instructor for new code, but the current approach works.**

### 5.5 Embedding Search: Qdrant Direct vs Hybrid Search

**Current**: Qdrant vector search with PostgreSQL metadata filtering.

**Alternative**: Qdrant's built-in hybrid search (sparse + dense vectors) or PostgreSQL pgvector for unified storage.

| Aspect | Current | Qdrant Hybrid | pgvector |
|--------|---------|---------------|----------|
| Semantic search | Good | Better (BM25 + vector) | Good |
| Infra complexity | 2 systems | 1 system | 1 system |
| Keyword search | No | Yes (sparse vectors) | via tsvector |
| Scale | Excellent | Excellent | Moderate |
| Filtering | Cross-system | Native | Native |

**Assessment**: If keyword search matters (and for knowledge extraction it does), Qdrant hybrid search or adding pgvector would reduce infrastructure complexity. **Consider pgvector if Qdrant becomes a maintenance burden**, or enable Qdrant's built-in hybrid mode.

---

## 6. Pipeline Architecture: Current vs Ideal

### Current Architecture

```
┌─────────────────────────────────────────────────────────────┐
│                     FastAPI Application                       │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐       │
│  │ Crawl API│ │Scrape API│ │Extract AP│ │Report API│  ...   │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘       │
│       │             │            │             │              │
│  ┌────▼─────────────▼────────────▼─────────────▼──────────┐ │
│  │                  Job Queue (DB)                          │ │
│  └────────────────────┬────────────────────────────────────┘ │
│                       │                                      │
│  ┌────────────────────▼────────────────────────────────────┐ │
│  │              Job Scheduler (polling)                      │ │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────────────────┐    │ │
│  │  │ScraperWkr│ │ CrawlWkr │ │ ExtractionPipeline   │    │ │
│  │  │          │ │          │ │ (extract+embed+entity │    │ │
│  │  │          │ │          │ │  +status+checkpoint)  │    │ │
│  │  └──────────┘ └──────────┘ └──────────────────────┘    │ │
│  └─────────────────────────────────────────────────────────┘ │
│                                                              │
│  ┌─────────┐  ┌─────────┐  ┌─────────┐                     │
│  │PostgreSQL│  │  Qdrant  │  │  Redis   │                    │
│  └─────────┘  └─────────┘  └─────────┘                     │
└─────────────────────────────────────────────────────────────┘
```

**Key characteristic**: Monolithic process. All workers run in-process as asyncio tasks within the same FastAPI application.

### Proposed Alternative: Modular Pipeline

For a system of this complexity, a more modular architecture would improve maintainability:

```
┌─────────────────────────────────────────────────────────────┐
│                      API Service (FastAPI)                    │
│  ┌──────────────────────────────────────────────┐            │
│  │  Endpoints + Job Creation + Report Generation │            │
│  └──────────────────────┬───────────────────────┘            │
└─────────────────────────┼───────────────────────────────────┘
                          │ Job Queue (DB or Redis)
┌─────────────────────────┼───────────────────────────────────┐
│                   Worker Service(s)                           │
│  ┌──────────┐ ┌──────────────┐ ┌──────────────────────────┐ │
│  │ Scraper  │ │   Crawler    │ │   Extraction Pipeline     │ │
│  │ Worker   │ │   Worker     │ │  ┌────────────────────┐  │ │
│  │          │ │              │ │  │ ExtractionService   │  │ │
│  │          │ │              │ │  ├────────────────────┤  │ │
│  │          │ │              │ │  │ EmbeddingService    │  │ │
│  │          │ │              │ │  ├────────────────────┤  │ │
│  │          │ │              │ │  │ EntityService       │  │ │
│  │          │ │              │ │  ├────────────────────┤  │ │
│  │          │ │              │ │  │ StatusTracker       │  │ │
│  │          │ │              │ │  └────────────────────┘  │ │
│  └──────────┘ └──────────────┘ └──────────────────────────┘ │
└─────────────────────────────────────────────────────────────┘
```

**Key changes**:
1. **Split API from workers** - API service handles requests; worker service(s) process jobs
2. **Decompose extraction pipeline** - Separate services for extraction, embedding, entities
3. **Job queue as integration boundary** - Services communicate via jobs, not method calls

**Benefits**:
- API stays responsive under heavy extraction load
- Workers can be scaled independently
- Each service is testable in isolation
- Pipeline steps can be retried independently (embedding retry without re-extraction)

**Trade-offs**:
- More infrastructure complexity (multiple processes)
- Inter-service communication overhead
- More complex deployment

**Recommendation**: This split is **not urgent** at current scale. The in-process architecture works. But as the system grows, extracting workers to separate processes would be the natural next step. The decomposition of pipeline classes (Section 3.1) is a prerequisite that should happen first.

---

## 7. Scoring Summary

| Dimension | Score (1-5) | Notes |
|-----------|-------------|-------|
| **Functionality** | 4.5 | Comprehensive feature set, well-designed API |
| **Code Organization** | 3.0 | Good module structure, but pipeline classes too large |
| **Error Handling** | 3.5 | Hierarchy established, TransientError/PermanentError classification, FastAPI handler |
| **Testability** | 3.0 | Good test patterns, but dual import issue + heavy mocking |
| **Configuration** | 4.0 | Well-typed, centralized, but sprawling |
| **Observability** | 4.0 | Structured logging, metrics, job tracking |
| **Scalability Design** | 3.5 | Good for current scale, clear path for growth |
| **Documentation** | 2.5 | Lots of docs exist but many are outdated |
| **Overall** | **3.5** | **Solid, functional system with identifiable improvement areas** |

---

## 8. Prioritized Recommendations

### Immediate (no architectural change)

1. ~~**Fix dual import paths**~~ ✅ DONE (2026-03-02, commit `d567f96`)
2. ~~**Establish exception hierarchy**~~ ✅ DONE (2026-03-02)
3. **Enable Phase 1A features** - They're built and tested; activate or remove
4. **Document classification decision tree** - Operators need to understand page routing

### Short-term (refactoring)

5. **Decompose ExtractionPipelineService** - Split into Coordinator + focused services
6. **Separate ServiceContainer from Scheduler** - Clean dependency management
7. **Group configuration** - Nested Pydantic classes by subsystem

### Medium-term (architecture evolution)

8. **Extract workers to separate process** - When extraction load justifies it
9. **Consider Instructor for LLM responses** - Reduces JSON repair code
10. **Evaluate pgvector or Qdrant hybrid** - Simplify infrastructure if keyword search needed

---

## 9. Conclusion

This is a **working, production-grade system** that delivers real value. The core design decisions (template-agnostic extraction, non-destructive content pipeline, adaptive concurrency) are sound. The main risks are maintainability-related: large classes, scattered error handling, and disabled features.

The recommended improvements are incremental - no fundamental redesign is needed. The path forward is: fix the import issue, decompose the pipeline classes, and then evaluate infrastructure changes based on scale needs.
