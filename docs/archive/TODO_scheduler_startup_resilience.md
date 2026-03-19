# TODO: Scheduler Startup Resilience

**Created:** 2026-03-01
**Priority:** High
**Status:** DONE (2026-03-03) — Phases 1 & 2 implemented. Phase 3 (burst limiting) deferred.

## Problem

On container restart, the scheduler immediately polls for all `queued` and `running` jobs and tries to execute them concurrently. When stale test/abandoned jobs accumulate (231 were found), this floods the event loop with simultaneous crawl/scrape/extract workers, starving uvicorn of CPU and causing the API to hang indefinitely (TCP port open, zero HTTP responses).

### Root Cause Chain

1. **No stale job cleanup on startup** — Jobs left `running` from a crashed container are treated as valid work
2. **No throttle on startup replay** — All 8 workers (1 scrape + 6 crawl + 1 extract) start polling immediately, each claiming a job per 5-second cycle
3. **No startup grace period** — Job processing begins before `Application startup complete`, blocking the event loop
4. **Test pollution** — Before transactional rollback was added to conftest.py, pytest created real jobs in the production DB (now fixed)

### Impact

- API completely unresponsive after restart (happened 2026-02-27, lasted 2 days)
- Required manual DB cleanup + container restart to recover
- Camoufox browser pool saturated with `example.com` requests

## Architecture Context

### Startup Flow (after refactor)

```
lifespan() → start_scheduler()
  ├─ ServiceContainer.start()           ← creates 10 app-lifetime services
  └─ JobScheduler(services=container).start()
       ├─ _cleanup_stale_jobs()          ← marks running/cancelling → failed
       ├─ asyncio.create_task(scrape)    ← stagger delay
       ├─ asyncio.create_task(crawl × N) ← stagger delay between each
       └─ asyncio.create_task(extract)   ← stagger delay
```

### Key Files

| File | Relevant Functions |
|------|-------------------|
| `src/main.py` | `lifespan()` — calls `start_scheduler()`/`stop_scheduler()` (unchanged) |
| `src/services/scraper/service_container.py` | `ServiceContainer` — creates/caches/tears down services (NEW) |
| `src/services/scraper/scheduler.py` | `JobScheduler.start()`, `_cleanup_stale_jobs()`, worker loops |
| `src/config.py` | `scheduler_cleanup_stale_on_startup`, `scheduler_startup_stagger_seconds`, `job_stale_threshold_*` |

### Existing Stale Detection

The scheduler already has stale thresholds (config.py:374-386):
- Scrape: 300s (5 min)
- Extract: 900s (15 min)
- Crawl: 1800s (30 min)

But these only trigger a **warning log + re-processing** — they never mark jobs as failed or skip them.

## Proposed Solution

### Phase 1: Startup Cleanup (Critical)

Add a `_cleanup_stale_jobs()` method called once at the start of `JobScheduler.start()`, before any worker tasks are created.

**File:** `src/services/scraper/scheduler.py`

```python
async def _cleanup_stale_jobs(self) -> dict[str, int]:
    """Mark stale running/queued jobs as failed on startup.

    Jobs left in running state from a previous crashed container
    should not be replayed blindly. Mark them failed with a clear
    error message so operators can investigate and re-queue manually.
    """
```

**Logic:**
1. Query all jobs with `status IN ('running', 'cancelling')` — these can't be legitimately running since the container just started
2. Mark them `status='failed'`, `error='Container restart: job was running when previous instance stopped'`
3. Log each with job_id, type, runtime at crash
4. Return counts by type for the startup log

**Queued jobs** should be left alone — they're legitimately waiting. The issue was `running` jobs being re-polled endlessly + overwhelming concurrent queued job pickup.

**Config:**
```python
# config.py
scheduler_cleanup_stale_on_startup: bool = True
```

### Phase 2: Startup Throttle

Add a warmup period where workers start gradually instead of all at once.

**File:** `src/services/scraper/scheduler.py` in `start()`

```python
# Stagger worker startup to avoid thundering herd
self._scrape_task = asyncio.create_task(self._run_scrape_worker())
await asyncio.sleep(2)  # Let scrape worker claim first batch

for i in range(settings.max_concurrent_crawls):
    self._crawl_tasks.append(
        asyncio.create_task(self._run_single_crawl_worker(i))
    )
    await asyncio.sleep(1)  # Stagger crawl workers

await asyncio.sleep(2)
self._extract_task = asyncio.create_task(self._run_extract_worker())
```

**Config:**
```python
# config.py
scheduler_startup_stagger_seconds: float = 1.0
```

### Phase 3: Queued Job Rate Limit on Startup

Add a configurable limit on how many queued jobs each worker processes in the first N seconds after startup.

**File:** `src/services/scraper/scheduler.py`

Each worker polling loop gets a startup burst limiter:

```python
async def _run_scrape_worker(self):
    startup_time = datetime.now(UTC)
    startup_burst_limit = settings.scheduler_startup_burst_limit  # e.g., 5
    startup_window = timedelta(seconds=settings.scheduler_startup_window)  # e.g., 60s
    jobs_since_startup = 0

    while self._running:
        # During startup window, throttle job pickup
        if datetime.now(UTC) - startup_time < startup_window:
            if jobs_since_startup >= startup_burst_limit:
                await asyncio.sleep(self._poll_interval)
                continue

        # ... normal polling logic ...
        jobs_since_startup += 1
```

**Config:**
```python
# config.py
scheduler_startup_burst_limit: int = 5       # Max jobs per worker in startup window
scheduler_startup_window_seconds: int = 60   # Duration of startup throttle
```

## Tasks

### Phase 1 — Startup Cleanup ✅ DONE

1. ✅ **`_cleanup_stale_jobs()` on `JobScheduler`** — marks running/cancelling → failed with `skip_locked=True`
2. ✅ **Config flag** — `scheduler_cleanup_stale_on_startup: bool = True`
3. ✅ **Tests** — `tests/test_scheduler_startup.py`: running/cancelling marked failed, queued untouched, config disable, error resilience

### Phase 2 — Staggered Startup ✅ DONE

4. ✅ **Stagger delays** — `await asyncio.sleep(stagger)` between each worker launch
5. ✅ **Config** — `scheduler_startup_stagger_seconds: float = 1.0`
6. ✅ **Tests** — stagger delay count verified, zero stagger works

### ServiceContainer Extraction ✅ DONE (added to plan)

7. ✅ **`ServiceContainer`** — `src/services/scraper/service_container.py` creates/caches/tears down 10 services
8. ✅ **Refactored `JobScheduler`** — takes `services: ServiceContainer`, 489→310 lines
9. ✅ **Tests** — `tests/test_service_container.py`: start/stop/property-before-start/context-manager
10. ✅ **Existing tests updated** — `test_scheduler_llm_worker.py`, `test_scheduler_recovery.py`, `test_scheduler_stale_thresholds.py`

### Phase 3 — Burst Limiting (deferred, lower priority)

11. **Add startup burst limiter to each worker loop**
    - Modify `_run_scrape_worker()`, `_run_single_crawl_worker()`, `_run_extract_worker()`
    - Add config: `scheduler_startup_burst_limit`, `scheduler_startup_window_seconds`

## Verification

After implementation, verify with:

```bash
# Create fake stale jobs
psql -U scristill -d scristill -c "
INSERT INTO jobs (id, project_id, type, status, payload, created_at, updated_at)
VALUES
  (gen_random_uuid(), (SELECT id FROM projects LIMIT 1), 'scrape', 'running', '{\"test\":true}', now() - interval '1 hour', now() - interval '1 hour'),
  (gen_random_uuid(), (SELECT id FROM projects LIMIT 1), 'crawl', 'running', '{\"test\":true}', now() - interval '1 hour', now() - interval '1 hour');
"

# Restart container, check logs for:
# "scheduler_startup_cleanup" with correct counts
# No flood of job processing logs
# API responds within 5 seconds of startup
```

## Constraints

- Do NOT change the `SELECT FOR UPDATE SKIP LOCKED` pattern — it correctly prevents race conditions
- Do NOT remove stale threshold detection in worker loops — it's still useful for runtime recovery
- Do NOT auto-delete jobs — mark as failed so they can be investigated
- Queued jobs should still be processed normally (just throttled on startup)
- All new config flags must default to backward-compatible values (cleanup on by default is safe since the old behavior was to re-process, which is strictly worse)
