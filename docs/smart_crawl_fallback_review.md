# Pipeline Review: Smart Crawl Fallback Implementation

**Review Date:** 2026-02-04
**Status:** Verified against code

## Verified Findings

### 🔴 Critical: 10-Second Delay Before Fallback Job Resumes

**Files:** `crawl_worker.py:522`, `scheduler.py:283-291`, `orm_models.py:85`

**Verified Flow:**
1. Fallback triggers, commits changes at line 522
2. `updated_at` auto-updates to NOW via `onupdate` trigger (orm_models.py:85)
3. Scheduler checks running jobs with `updated_at < poll_threshold` (scheduler.py:291)
4. `poll_threshold` = `now - 10 seconds` (crawl_poll_interval default)
5. Job won't match until 10 seconds pass

**Impact:** Every fallback incurs an unnecessary 10-second delay.

**Fix:** Set `job.status = "queued"` in fallback block. Queued jobs are checked first (scheduler.py:268-278) with no threshold delay.

---

### 🟠 Important: started_at Timestamp Overwritten

**Files:** `crawl_worker.py:120`, `crawl_worker.py:460`

**Verified Flow:**
1. Smart crawl sets `job.started_at = datetime.now(UTC)` at line 460
2. Fallback triggers, clears smart crawl data but NOT `started_at`
3. Traditional crawl checks `if not firecrawl_job_id:` (line 99) - TRUE after fallback
4. Line 120 executes: `job.started_at = datetime.now(UTC)` - OVERWRITES original

**Impact:** Job duration metrics exclude smart crawl map attempt time. A job that took 15s (5s smart crawl + 10s delay + traditional crawl) will show only traditional crawl duration.

**Fix:** Don't overwrite `started_at` if already set:
```python
if not job.started_at:
    job.started_at = datetime.now(UTC)
```

---

### 🟡 Minor: job.result Overwritten (FALSE POSITIVE - Low Impact)

**Verified:** Yes, `job.result` is overwritten at line 142 during traditional crawl polling.

**But:** Fallback info is logged at lines 505-510, and final completion result is what matters for job status queries. This is acceptable behavior.

---

### 🟡 Minor: Threshold Hardcoded (REAL but Low Priority)

**File:** `crawl_worker.py:25`

`SMART_CRAWL_MIN_URLS_THRESHOLD = 3` is hardcoded. Could be in settings for per-deployment tuning, but current value is reasonable.

---

### ✅ Not an Issue: Job Status "running" Semantics

**Original concern:** Job staying "running" after fallback violates queue semantics.

**Verified:** This IS the root cause of the 10-second delay (Finding #1). Changing to "queued" fixes both the semantic issue AND the delay. Single fix addresses both concerns.

---

## Summary

| Finding | Severity | Real Issue? | Fix Complexity |
|---------|----------|-------------|----------------|
| 10-second delay | Critical | ✅ YES | Simple (1 line) |
| started_at overwritten | Important | ✅ YES | Simple (3 lines) |
| job.result overwritten | Minor | ⚠️ Low impact | Skip |
| Threshold hardcoded | Minor | ✅ YES | Simple (move to config) |

## Recommended Fix

```python
# In _smart_crawl_map_phase, fallback block (lines 512-524):

            if map_result.total < SMART_CRAWL_MIN_URLS_THRESHOLD:
                logger.warning(
                    "smart_crawl_fallback_triggered",
                    job_id=str(job.id),
                    urls_discovered=map_result.total,
                    threshold=SMART_CRAWL_MIN_URLS_THRESHOLD,
                    reason="Too few URLs discovered, falling back to traditional crawl",
                )
                # Switch to traditional crawl mode
                payload["smart_crawl_enabled"] = False
                payload.pop("smart_crawl_phase", None)
                payload.pop("mapped_urls", None)
                flag_modified(job, "payload")
                job.status = "queued"  # FIX: Reset to queued for immediate pickup
                job.result = {
                    "phase": "fallback",
                    "smart_crawl_urls_found": map_result.total,
                    "fallback_reason": "insufficient_urls",
                }
                self.db.commit()
                return
```

```python
# In process_job, traditional crawl start block (around line 120):

                job.payload["firecrawl_job_id"] = firecrawl_job_id
                flag_modified(job, "payload")
                job.status = "running"
                if not job.started_at:  # FIX: Don't overwrite if already set
                    job.started_at = datetime.now(UTC)
                self.db.commit()
```
