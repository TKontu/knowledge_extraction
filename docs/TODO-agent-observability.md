# TODO: Observability Improvements

**Agent:** agent-observability
**Branch:** `feat/observability-improvements`
**Priority:** medium

## Context

Two observability improvements are needed:
1. **Issue #10**: Extraction errors lack context (no chunk ID, LLM response preview, source snippet)
2. **Issue #11**: No extraction quality metrics (duration, dedup counts, entities per extraction)

These are related concerns - both improve visibility into the extraction pipeline.

## Objective

Enhance error logging with full context and add Prometheus metrics for extraction quality monitoring.

## Tasks

### 1. Enhance Error Logging in Pipeline

**File:** `src/services/extraction/pipeline.py`

**Requirements:**
- Find error logging in `process_source()` (around line 194-196)
- Enhance the error log to include more context:

```python
# Current (line 196):
logger.error("fact_processing_failed", error=str(e), fact=fact.fact)

# Enhanced:
logger.error(
    "fact_processing_failed",
    error=str(e),
    error_type=type(e).__name__,
    source_id=str(source_id),
    source_url=source.url if hasattr(source, 'url') else None,
    source_group=source.source_group,
    fact_preview=fact.fact[:500] if fact.fact else None,
    fact_category=fact.category,
    fact_confidence=fact.confidence,
    exc_info=True,
)
```

### 2. Enhance Error Logging in Schema Extraction

**File:** `src/services/extraction/schema_extractor.py`

**Requirements:**
- Find error handling and enhance logging
- Include source_id, chunk info, and response preview where available:

```python
logger.error(
    "schema_extraction_failed",
    error=str(e),
    error_type=type(e).__name__,
    source_id=str(source_id) if source_id else None,
    field_group=field_group_name if 'field_group_name' in locals() else None,
    response_preview=response[:500] if 'response' in locals() and response else None,
    exc_info=True,
)
```

### 3. Enhance Error Logging in LLM Worker

**File:** `src/services/llm/worker.py`

**Requirements:**
- Find error handling (search for `logger.error`)
- Enhance with request context:

```python
logger.error(
    "llm_request_failed",
    error=str(e),
    error_type=type(e).__name__,
    request_id=str(request_id) if 'request_id' in locals() else None,
    prompt_preview=prompt[:300] if 'prompt' in locals() and prompt else None,
    response_preview=response[:500] if 'response' in locals() and response else None,
    retry_count=retry_count if 'retry_count' in locals() else None,
    exc_info=True,
)
```

### 4. Add Quality Metrics to Collector

**File:** `src/services/metrics/collector.py`

**Requirements:**
- Find the `SystemMetrics` dataclass
- Add new fields for quality metrics:

```python
@dataclass
class SystemMetrics:
    # ... existing fields ...

    # Quality metrics (new)
    extractions_by_type: dict[str, int] = field(default_factory=dict)
    avg_confidence_by_type: dict[str, float] = field(default_factory=dict)
    entities_by_type: dict[str, int] = field(default_factory=dict)
```

- Update the `collect()` method to query these metrics:

```python
# Add queries for quality metrics
# Extractions by type
extraction_type_query = (
    session.query(Extraction.extraction_type, func.count(Extraction.id))
    .group_by(Extraction.extraction_type)
    .all()
)
extractions_by_type = {t: c for t, c in extraction_type_query}

# Average confidence by type
confidence_query = (
    session.query(Extraction.extraction_type, func.avg(Extraction.confidence))
    .group_by(Extraction.extraction_type)
    .all()
)
avg_confidence_by_type = {t: float(c) if c else 0.0 for t, c in confidence_query}

# Entities by type
entity_type_query = (
    session.query(Entity.entity_type, func.count(Entity.id))
    .group_by(Entity.entity_type)
    .all()
)
entities_by_type = {t: c for t, c in entity_type_query}
```

### 5. Add Quality Metrics to Prometheus Output

**File:** `src/services/metrics/prometheus.py`

**Requirements:**
- Add new metric sections to `format_prometheus()`:

```python
# Extractions by type
lines.append("# HELP scristill_extractions_by_type Number of extractions by type")
lines.append("# TYPE scristill_extractions_by_type gauge")
for ext_type, count in metrics.extractions_by_type.items():
    lines.append(f'scristill_extractions_by_type{{type="{ext_type}"}} {count}')

# Average confidence by type
lines.append("# HELP scristill_extraction_confidence_avg Average extraction confidence by type")
lines.append("# TYPE scristill_extraction_confidence_avg gauge")
for ext_type, conf in metrics.avg_confidence_by_type.items():
    lines.append(f'scristill_extraction_confidence_avg{{type="{ext_type}"}} {conf:.4f}')

# Entities by type
lines.append("# HELP scristill_entities_by_type Number of entities by type")
lines.append("# TYPE scristill_entities_by_type gauge")
for ent_type, count in metrics.entities_by_type.items():
    lines.append(f'scristill_entities_by_type{{type="{ent_type}"}} {count}')
```

### 6. Write Tests

**File:** `tests/test_metrics_collector.py` (add to existing)

**Requirements:**
- Test that `extractions_by_type` is collected correctly
- Test that `avg_confidence_by_type` is collected correctly
- Test that `entities_by_type` is collected correctly

**File:** `tests/test_prometheus_formatter.py` (add to existing)

**Requirements:**
- Test that new metrics are formatted correctly
- Test empty metrics don't cause errors

## Constraints

- Do NOT add histogram or counter metrics - stick to gauges for simplicity
- Do NOT modify the metrics API endpoint - it already uses these functions
- Do NOT add timing/duration metrics - that requires more infrastructure
- Do NOT run full test suite - only run tests in Test Scope below
- Do NOT lint entire codebase - only lint files in Lint Scope below
- Preserve existing log messages - only enhance them with more context

## Test Scope

**ONLY run these tests - do NOT run `pytest` without arguments:**

```bash
pytest tests/test_metrics_collector.py -v
pytest tests/test_prometheus_formatter.py -v
pytest tests/test_logging.py -v
```

## Lint Scope

**ONLY lint these files - do NOT run `ruff check src/`:**

```bash
ruff check src/services/metrics/collector.py src/services/metrics/prometheus.py src/services/extraction/pipeline.py src/services/extraction/schema_extractor.py src/services/llm/worker.py
ruff format src/services/metrics/collector.py src/services/metrics/prometheus.py src/services/extraction/pipeline.py src/services/extraction/schema_extractor.py src/services/llm/worker.py
```

## Verification

Before creating PR:

1. `pytest tests/test_metrics_collector.py -v` - Must pass
2. `pytest tests/test_prometheus_formatter.py -v` - Must pass
3. `ruff check` on scoped files - Must be clean

## Definition of Done

- [ ] Error logging enhanced in pipeline.py with full context
- [ ] Error logging enhanced in schema_extractor.py with full context
- [ ] Error logging enhanced in worker.py with full context
- [ ] SystemMetrics dataclass extended with quality fields
- [ ] Collector queries new metrics from database
- [ ] Prometheus formatter outputs new metrics
- [ ] Tests added and passing
- [ ] Lint clean (scoped)
- [ ] PR created with title: `feat: add observability improvements for extraction pipeline`
