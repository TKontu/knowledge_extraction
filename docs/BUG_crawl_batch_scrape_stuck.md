## RESOLVED — commit 24271a3 (2026-03-18)

All 5 proposed fixes implemented: (1) DB sessions released before async HTTP calls, (2) unknown batch statuses handled (marks job failed), (3) polling upgraded to INFO log level, (4) 30-min batch scrape timeout added, (5) stale recovery marks jobs FAILED. Kept for historical reference.

---

# BUG: Pipeline deadlocks under concurrent crawl load

**Date:** 2026-03-17
**Status:** Open — root cause confirmed
**Severity:** Critical — deadlocks entire pipeline (API + all workers)

## Symptoms

1. Pipeline API completely unresponsive (curl connects, 0 bytes returned)
2. Pipeline container logs stop completely — no output for hours
3. Firecrawl finishes all work, goes idle
4. Pipeline process alive (0.8% CPU, sleeping state) but frozen
5. Only recovery: restart pipeline container

## Root Cause (confirmed via pg_stat_activity)

**Sync-on-async deadlock** between SQLAlchemy's synchronous sessions and asyncio's event loop.

### Evidence (live from the frozen pipeline)

```sql
-- 8 sessions stuck "idle in transaction" for 2+ hours, all holding FOR UPDATE locks:
PID 162: idle in transaction for 2:12:50  -- SELECT jobs... FOR UPDATE
PID 163: idle in transaction for 2:12:50  -- SELECT jobs... FOR UPDATE
PID 152: idle in transaction for 2:12:47  -- SELECT jobs... FOR UPDATE
PID 140: idle in transaction for 2:12:47  -- SELECT jobs... FOR UPDATE
PID 141: idle in transaction for 2:12:47  -- SELECT jobs... FOR UPDATE
PID 150: idle in transaction for 2:12:47  -- SELECT jobs... FOR UPDATE
PID 149: idle in transaction for 2:12:47  -- SELECT jobs... FOR UPDATE
PID 151: idle in transaction for 2:12:47  -- SELECT jobs... FOR UPDATE

-- 1 session BLOCKED trying to update a locked row:
PID 56577: active for 2:12:47, wait: Lock/transactionid
    UPDATE jobs SET payload=..., result=..., updated_at=... WHERE jobs.id = ...
```

### The deadlock mechanism

```
scheduler.py:_run_single_crawl_worker() — each of the 6 crawl workers does:

    db = SessionLocal()                          # sync: gets connection
    job = db.query(Job)                          # sync: starts transaction
        .filter(status == RUNNING, ...)
        .with_for_update(skip_locked=True)       # acquires row lock in open transaction
        .first()

    worker = CrawlWorker(db=db, ...)
    await worker.process_job(job)                # async: yields to event loop
        → inside: await client.get_batch_scrape_status()  # HTTP call, up to 180s
        → the DB transaction stays OPEN with the row lock held
        → if the HTTP response arrives, the worker needs the event loop to resume

    db.close()                                   # never reached until process_job returns
```

**The deadlock sequence:**

1. **Worker A** does `SELECT ... FOR UPDATE` on job row X → lock acquired, transaction open
2. Worker A calls `await client.get_batch_scrape_status()` → yields to event loop
3. Transaction stays open, lock on row X held
4. **Worker B** picks up the same or related job, does its work, calls `self.db.commit()`
5. The `commit()` includes an `UPDATE jobs SET ... WHERE id = X` (or a row locked by Worker A)
6. `db.commit()` is **synchronous** — it calls psycopg's `conn.execute()` which blocks the thread
7. PostgreSQL makes PID(B) wait for PID(A)'s lock → `db.commit()` **blocks indefinitely**
8. Since this is synchronous, the **entire asyncio event loop is blocked**
9. Worker A's HTTP response arrives, but the event loop can't process it
10. Worker A can never commit/rollback → lock never released → PID(B) waits forever
11. **All async tasks frozen** — no logging, no API responses, no progress

### Why it triggers with many concurrent crawls

- With 1-2 crawl jobs, workers rarely contend for the same rows
- With 13 jobs, 6 crawl workers + 3 other workers = 9 tasks all doing `SELECT ... FOR UPDATE` on the `jobs` table
- Smart crawl chains map→filter→scrape in one `process_job()` call, holding the transaction for potentially minutes during HTTP calls
- The `with_for_update(skip_locked=True)` should skip locked rows, but workers can still try to UPDATE rows locked by other workers (e.g., when a worker updates its own job's result/status, another worker might be trying to claim that same row via the RUNNING query)

### Why SKIP LOCKED doesn't prevent it

`SKIP LOCKED` prevents blocking on the initial SELECT. But the deadlock happens later:

1. Worker A: `SELECT ... FOR UPDATE SKIP LOCKED` on job 1 → gets lock on job 1
2. Worker B: `SELECT ... FOR UPDATE SKIP LOCKED` on job 2 → gets lock on job 2
3. Worker A: inside `process_job()`, does `self.db.commit()` which flushes changes to job 1 → this commit acquires additional locks or waits for WAL
4. Worker B: inside `process_job()`, does `self.db.commit()` which flushes changes to job 2
5. If Worker B's commit includes a query that touches a row locked by Worker A (e.g., via a trigger, FK constraint, or autoflush scanning other dirty rows), it blocks synchronously

The pg_stat_activity evidence shows this exact pattern: PID 56577 is blocked on `UPDATE jobs` waiting for a `transactionid` lock held by one of the idle-in-transaction sessions.

## Key Files

| File | Line(s) | Role |
|------|---------|------|
| `src/services/scraper/scheduler.py` | 245-303 | Worker loop: holds DB session across entire `process_job()` |
| `src/services/scraper/scheduler.py` | 255, 272 | `with_for_update(skip_locked=True)` — acquires row locks |
| `src/services/scraper/crawl_worker.py` | 741-843 | `_smart_crawl_scrape_phase()` — awaits HTTP while holding transaction |
| `src/services/scraper/crawl_worker.py` | 531, 696 | Map→filter→scrape chaining — long-lived transaction |
| `src/services/scraper/client.py` | 146-158 | FirecrawlClient timeout=180s |
| `src/database.py` | 14-23 | Synchronous engine (`create_engine`, not `create_async_engine`) |

## Additional bugs discovered (real, confirmed)

### 1. Batch scrape `status="error"` silently ignored

**File:** `crawl_worker.py:787-843`, `client.py:846-858`

When `get_batch_scrape_status()` throws an exception, client returns `BatchScrapeResult(status="error")`. The if-chain in `_smart_crawl_scrape_phase` only checks `"scraping"`, `"failed"`, `"completed"`. `"error"` falls through silently. Line 784 already refreshed `updated_at`, so stale detection never catches it. Creates infinite silent poll loop.

**Confirmed:** Code-visible at `client.py:854` and `crawl_worker.py:787-843`. Note: in this specific incident the error path was NOT the trigger (no ERROR-level logs from client), but it IS a real bug that would cause silent infinite polling if triggered.

### 2. Batch scrape polling logged at DEBUG level

**File:** `crawl_worker.py:788`

`logger.debug("smart_crawl_batch_scrape_progress", ...)` — invisible in production (INFO level). Makes it impossible to distinguish "silently polling" from "completely stuck."

**Confirmed:** Line 788, verified against production behavior (no polling logs visible).

### 3. No batch scrape timeout

**File:** `crawl_worker.py:741-843`

No maximum duration for batch scrape polling. If status never becomes "completed" (or due to bug #1), the job polls indefinitely.

**Confirmed:** No timeout logic exists in the scrape phase.

### 4. Stale recovery doesn't fail jobs

**File:** `scheduler.py:276-291`

When a RUNNING job exceeds the stale threshold (30 min), the scheduler only logs a warning and re-processes it via `process_job()`. Since `process_job` updates `updated_at` (line 784), the stale timer resets each time. No escape mechanism.

**Confirmed:** Lines 276-291 log warning only, no status change.

## Fixes

### Fix 1 (critical): Don't hold DB sessions during async HTTP calls

The root cause fix. Restructure the scheduler to release the DB connection before awaiting:

```python
# scheduler.py — new pattern
async def _run_single_crawl_worker(self, worker_id: int) -> None:
    while self._running and not shutdown.is_shutting_down:
        try:
            # Phase 1: Claim job (short-lived DB session)
            job_data = None
            db = SessionLocal()
            try:
                job = (db.query(Job)
                    .filter(Job.type == JobType.CRAWL, Job.status == JobStatus.QUEUED)
                    .with_for_update(skip_locked=True)
                    .first())
                if not job:
                    # Check RUNNING jobs needing poll
                    poll_threshold = datetime.now(UTC) - timedelta(seconds=settings.crawl.poll_interval)
                    job = (db.query(Job)
                        .filter(Job.type == JobType.CRAWL, Job.status == JobStatus.RUNNING,
                                Job.updated_at < poll_threshold)
                        .with_for_update(skip_locked=True)
                        .first())
                if job:
                    job.status = JobStatus.RUNNING
                    db.commit()  # Release FOR UPDATE lock immediately
                    # Serialize what the worker needs (detach from session)
                    job_data = {
                        "id": job.id, "payload": dict(job.payload),
                        "result": dict(job.result) if job.result else None,
                        "started_at": job.started_at,
                    }
            finally:
                db.close()  # Connection returned to pool BEFORE any HTTP calls

            # Phase 2: Process job (creates own short-lived sessions as needed)
            if job_data:
                await self._process_crawl_job(job_data)
            else:
                await asyncio.sleep(self.poll_interval)
        except Exception as e:
            logger.error("crawl_worker_error", worker_id=worker_id, error=str(e))
            await asyncio.sleep(self.poll_interval)
```

This eliminates the deadlock by ensuring no transaction is open during async HTTP calls.

### Fix 2: Handle unknown batch scrape statuses

```python
# crawl_worker.py — after the "completed" block (line ~843)
        else:
            logger.error("smart_crawl_unknown_batch_status",
                         job_id=str(job.id), status=status.status, error=status.error)
            job.status = JobStatus.FAILED
            job.error = f"Batch scrape error: {status.error or status.status}"
            job.completed_at = datetime.now(UTC)
            self.db.commit()
```

### Fix 3: Upgrade polling to INFO level

Change `crawl_worker.py:788` from `logger.debug` to `logger.info`.

### Fix 4: Add batch scrape timeout

Add a configurable max polling duration (e.g., 30 min). Fail the job if exceeded.

### Fix 5: Stale recovery should fail jobs

After exceeding 2× stale threshold, mark the job FAILED instead of re-processing.

## Immediate Workaround

1. Restart pipeline container (startup cleanup marks RUNNING → FAILED)
2. Launch crawls in batches of 2-3 max to avoid lock contention
