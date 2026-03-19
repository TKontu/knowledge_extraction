# Pipeline Review: Camoufox Browser Recovery Implementation

## Flow
```
server.py:scrape_url → scraper.py:scrape → scraper.py:_get_next_browser → scraper.py:_do_scrape
                                        ↓ (on dead browser found)
                              scraper.py:_schedule_browser_restart (background)
                                        ↓ (on all disconnected)
                              scraper.py:_restart_browser (sync)
```

## Verification Results

### FALSE POSITIVES (Not actual issues)

| Finding | Reason |
|---------|--------|
| **Race condition in `_get_next_browser`** | `_get_next_browser()` is fully synchronous (no `await` inside). `browser.is_connected()` returns `bool` synchronously. In asyncio, without yield points, the function executes atomically - no interleaving possible. |
| **IndexError when pool smaller than expected** | Index 0 always exists if pool started (line 226-227 ensures at least one browser). `browser_idx` from `_get_next_browser` only returns valid indices from actual pool. |
| **Hardcoded `_restart_browser(0)` may not exist** | Same as above - index 0 always exists after successful startup. |
| **`_restarting_browsers` set needs lock** | Asyncio is cooperative - no `await` between check (line 285) and add (line 289), so no interleaving possible. |
| **No bounds validation on index parameter** | All callers (`scrape()`) only pass valid indices: either 0 or values from `_get_next_browser()`. |
| **Error string matching might miss errors** | VERIFIED FALSE POSITIVE. Actual error from logs: `"Browser.new_context: Target page, context or browser has been closed"` - contains `"browser has been closed"` substring. Check works correctly. |
| **No verification after restart** | VERIFIED FALSE POSITIVE. If `camoufox.start()` succeeds, browser is connected. If it fails, exception is caught. Playwright sets `_is_connected=True` on construction. |
| **No circuit breaker needed** | VERIFIED FALSE POSITIVE / DESIGN CHOICE. Failed restarts result in browsers that keep trying on next detection. System self-heals or degrades gracefully. |

### REAL ISSUES (ALL FIXED)

#### Important

- [x] **scraper.py:296 - No timeout on old browser cleanup** ✅ FIXED

  Added `asyncio.wait_for()` with 30-second timeout. Logs warning on timeout.

- [x] **scraper.py:665 - Fire-and-forget task loses exceptions** ✅ FIXED

  Added `_handle_restart_task_result()` callback via `task.add_done_callback()` to log any unexpected exceptions.

- [x] **scraper.py:177-185 - Dead browsers become zombies, never restarted** ✅ FIXED

  Added `_schedule_browser_restart()` method and call it from `_get_next_browser()` when dead browsers are detected. Now schedules background restarts for all dead browsers found while searching for a live one.

#### Minor

- [x] **tests/test_camoufox_browser_pool.py:246-248 - Test expects wrong method call** ✅ FIXED

  Fixed test to assert `camoufox.__aexit__()` is called instead of `browser.close()`, matching actual implementation.

### Acceptable Limitations

- **scraper.py:177-178 - TOCTOU race on `is_connected()`**

  Browser could disconnect between `is_connected()` check and `new_context()` call. This is an inherent limitation - the check significantly reduces cascade failures but can't eliminate them entirely. The error IS handled at line 654-665, so this is acceptable.

## Summary

The implementation is **complete and sound**. All identified issues have been fixed:

**Fixed issues:**
1. ✅ Timeout on browser cleanup (prevents hanging)
2. ✅ Callback for restart task exceptions (proper error logging)
3. ✅ Background restart for dead browsers (pool capacity recovery)
4. ✅ Test fix for `stop()` assertion (pre-existing bug)

**Test results:** 17/17 tests passing
