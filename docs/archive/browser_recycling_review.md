# Pipeline Review: Browser Recycling Implementation

## Flow
```
scrape() → _get_next_browser() → _do_scrape() → increment counter → _should_recycle_browser() → _schedule_browser_restart() → _restart_browser()
```

## Verified Findings

### ~~Critical Issue 1: Race condition on counter increment~~ **FALSE POSITIVE**

**Reason**: In asyncio (single-threaded), `+=` is atomic when there's no `await` between read and write. The counter increment at line 717 has no `await` between it and the previous statement:

```python
result = await self._do_scrape(request, browser)  # await here
# NO await between these lines - runs atomically
if browser_idx < len(self._browser_request_counts):
    self._browser_request_counts[browser_idx] += 1  # atomic in asyncio
```

Verified with test:
```python
# asyncio with atomic += after yield
await asyncio.sleep(0)
counter[0] += 1  # This IS atomic - no interleaving possible
# Result: Expected 200, Got: 200
```

---

## Real Issues

### 🟠 Important: Browser can be used while restart is pending

**Location**: `_get_next_browser()` (lines 156-196) and `_schedule_browser_restart()` (lines 214-228)

**Problem**: `_get_next_browser()` only checks `browser.is_connected()` - it does NOT check `_restarting_browsers`. When recycling schedules a restart:

1. `_schedule_browser_restart(0)` creates a task but returns immediately
2. The task hasn't run yet, so `_restarting_browsers` is still empty
3. Another request calls `_get_next_browser()` → browser 0 is still connected → returns it
4. Request starts using browser 0
5. Restart task runs, closes browser 0
6. Request fails with "browser has been closed" error

**Impact**: Requests can fail unexpectedly during recycling. Mitigated by existing error handling, but causes unnecessary failures.

---

### 🟠 Important: Multiple restart tasks can be created (PRE-EXISTING BUG)

**Location**: `_schedule_browser_restart()` lines 222-228

**Problem**: The check `if index in self._restarting_browsers` happens BEFORE task creation, but `_restarting_browsers.add(index)` happens INSIDE the task. Between check and task execution, multiple calls can pass:

```
Schedule 0: Creating task (set is: set())
Schedule 0: Creating task (set is: set())  ← passes again!
Schedule 0: Creating task (set is: set())  ← passes again!
Task 0: Starting restart
Task 0: Already restarting, skip           ← caught here
Task 0: Already restarting, skip           ← caught here
```

**Impact**: Creates redundant asyncio tasks. Functionally safe (inner check prevents double restart), but wasteful. Recycling makes this more likely to trigger.

---

### 🟡 Minor: Requests failing due to restart increment new browser's counter

**Location**: `scrape()` lines 715-717

**Problem**: The counter is incremented "even on failure - browser still did work". But if a request fails because the browser was closed during restart:

1. Request A uses browser 0 (old), counter = 99
2. Restart closes browser 0, creates new browser 0, resets counter = 0
3. Request A fails (browser closed)
4. Request A increments counter → new browser starts with counter = 1

The failed request (which used the OLD browser) counts against the NEW browser's limit.

**Impact**: Very low - at most 1 extra count per restart. Threshold is typically 100, so ±1 doesn't matter.

---

### 🟡 Minor: INFO log before checking if restart is actually needed

**Location**: `scrape()` lines 720-726

**Problem**: The "scheduling_browser_recycle" INFO log happens BEFORE checking if restart is already in progress:

```python
if self._should_recycle_browser(browser_idx):
    logger.info("scheduling_browser_recycle", ...)  # INFO - always logs
    self._schedule_browser_restart(browser_idx)     # may return early
```

If restart is already in progress, we log:
- INFO: "scheduling_browser_recycle" (from scrape)
- DEBUG: "browser_restart_already_scheduled" (from _schedule_browser_restart)

**Impact**: Noisy logs during high-concurrency recycling scenarios. No functional impact.

---

## Confirmed False Positives

| Original Claim | Why It's False |
|----------------|----------------|
| Race condition on `+=` | No `await` between read/write - atomic in asyncio |
| Counter array size mismatch on partial start | Uses `append()` so indices stay consistent |

---

## Summary

| Severity | Count | Details |
|----------|-------|---------|
| 🔴 Critical | 0 | Original claims were false positives |
| 🟠 Important | 2 | Browser-use-during-restart, multiple-task-creation |
| 🟡 Minor | 2 | Counter off-by-one, log noise |

The implementation is functionally correct. The issues are edge cases that cause:
- Occasional request failures during recycling (handled gracefully)
- Wasted asyncio tasks (no functional impact)
- Slightly imprecise recycling timing (±1 request)
- Extra log messages (no functional impact)

None of these require immediate fixes for production use.
