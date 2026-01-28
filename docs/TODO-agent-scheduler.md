# TODO: Agent Scheduler - Job Recovery Hardening

**Agent ID:** `agent-scheduler`
**Branch:** `feat/scheduler-hardening`
**Priority:** High

---

## Context

The job scheduler currently uses a 5-second stale threshold for job recovery. This is too aggressive for extraction jobs that can take several minutes. Jobs may be incorrectly marked as stale and recovered while still running, causing duplicate processing.

**Key files to understand:**
- `src/services/scraper/scheduler.py` - Main scheduler with job polling
- `src/orm_models.py` - Job model with `started_at`, `completed_at`, `updated_at` fields

**New metrics available** (added 2026-01-28):
- `scristill_job_duration_seconds_avg{type="..."}`
- `scristill_job_duration_seconds_max{type="..."}`

Use these to inform threshold decisions.

---

## Objective

Increase stale job recovery thresholds to realistic values based on job type, preventing false recovery of long-running jobs.

---

## Tasks

### 1. Add per-job-type stale thresholds

**File:** `src/services/scraper/scheduler.py`

Add configuration for different job types:

```python
# Suggested thresholds (adjust based on metrics)
STALE_THRESHOLDS = {
    "scrape": timedelta(minutes=5),
    "extract": timedelta(minutes=15),
    "crawl": timedelta(minutes=30),
    "report": timedelta(minutes=10),
    "default": timedelta(minutes=10),
}
```

**Modify these methods:**
- `_poll_scrape_jobs()` (~line 185-198)
- `_poll_extract_jobs()` (~line 262-275)
- `_poll_crawl_jobs()` (~line 325-338)

Change from:
```python
stale_threshold = datetime.now(UTC) - timedelta(seconds=self.poll_interval)
```

To:
```python
stale_threshold = datetime.now(UTC) - STALE_THRESHOLDS.get(job_type, STALE_THRESHOLDS["default"])
```

### 2. Add configurable thresholds via settings

**File:** `src/config.py`

Add settings to allow runtime configuration:

```python
# Job recovery settings
job_stale_threshold_scrape: int = Field(default=300, description="Scrape job stale threshold in seconds")
job_stale_threshold_extract: int = Field(default=900, description="Extract job stale threshold in seconds")
job_stale_threshold_crawl: int = Field(default=1800, description="Crawl job stale threshold in seconds")
```

### 3. Update scheduler to use settings

**File:** `src/services/scraper/scheduler.py`

Modify `__init__` to accept settings and use configured thresholds.

### 4. Add logging for stale job recovery

When a job is recovered, log:
- Job ID
- Job type
- How long it was running before being marked stale
- Previous status

---

## Tests

**File:** `tests/test_scheduler_stale_thresholds.py` (new file)

### Test cases:

1. `test_scrape_job_not_stale_within_threshold` - Job running for 3 min should NOT be recovered
2. `test_scrape_job_stale_after_threshold` - Job running for 6 min SHOULD be recovered
3. `test_extract_job_not_stale_within_threshold` - Job running for 10 min should NOT be recovered
4. `test_extract_job_stale_after_threshold` - Job running for 20 min SHOULD be recovered
5. `test_crawl_job_longer_threshold` - Crawl jobs have longer threshold than scrape
6. `test_custom_threshold_from_settings` - Settings override defaults

---

## Constraints

- Do NOT modify job processing logic, only recovery detection
- Do NOT change the `poll_interval` (how often scheduler checks)
- Keep backward compatibility - if settings not provided, use defaults
- Do NOT touch `_process_*` methods, only `_poll_*` methods

---

## Acceptance Criteria

- [ ] Stale thresholds are per-job-type (scrape, extract, crawl)
- [ ] Thresholds are configurable via environment variables
- [ ] Stale job recovery is logged with context
- [ ] All new tests pass
- [ ] Existing scheduler tests still pass
- [ ] `ruff check` passes

---

## Verification

```bash
# Run tests
pytest tests/test_scheduler_stale_thresholds.py -v

# Run existing scheduler tests
pytest tests/test_scheduler*.py -v

# Lint
ruff check src/services/scraper/scheduler.py src/config.py
```
