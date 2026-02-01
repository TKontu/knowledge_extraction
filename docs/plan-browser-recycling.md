# Implementation Plan: Periodic Browser Recycling for Memory Management

## Problem Statement

Long-running camoufox browser instances accumulate memory over time, causing memory pressure and eventual OOM conditions. We need to proactively recycle browsers after a configurable number of requests to prevent memory creep.

## Goals

1. **Memory Management**: Prevent memory accumulation by recycling browsers periodically
2. **Zero Disruption**: In-flight requests must complete without interruption
3. **Configurable**: Allow tuning via environment variable
4. **Observable**: Log recycling events for monitoring
5. **Robust**: Handle edge cases gracefully (concurrent recycling, all browsers recycling)

---

## Architecture Overview

```
Request Flow with Recycling
===========================

Request → Semaphore → _get_next_browser() → _do_scrape() → Increment Counter → Check Threshold
                           ↑                                                         |
                           |                                                         ↓
                    [Skip browsers marked                              If count >= threshold:
                     as "draining"]                                    Mark browser "draining"
                                                                                     |
                                                                                     ↓
                                                              Wait for in-flight → Restart → Reset counter
```

## Design Decisions

### 1. Counter Location: Per-Browser Dictionary

**Decision**: Use `_browser_request_counts: dict[int, int]` in scraper

**Rationale**:
- Allows independent tracking per browser
- Easy to reset on restart
- Matches existing pattern (`_restarting_browsers` is a set keyed by index)

### 2. When to Increment: After Successful Scrape Only

**Decision**: Increment counter after `_do_scrape()` returns successfully

**Rationale**:
- Failed requests shouldn't count toward recycling threshold
- Avoids counting retries multiple times
- Matches intent: "recycle after N successful uses"

### 3. Graceful Recycling: Draining State

**Decision**: Add browser state tracking with "draining" state

**Rationale**:
- Prevents new requests from going to a browser about to restart
- Allows in-flight requests to complete naturally
- Avoids request failures during restart

**State Machine**:
```
READY → DRAINING → (wait for in-flight) → RESTARTING → READY
         ↑                                     ↓
         └─────────────────────────────────────┘
```

### 4. Concurrency Safety

**Decision**: Use existing asyncio patterns (no new locks needed)

**Rationale**:
- `_get_next_browser()` is synchronous (atomic)
- Counter increment happens after scrape (single writer per request)
- State checks in `_get_next_browser()` are atomic reads
- Restart scheduling uses existing deduplication

### 5. Minimum Available Browsers

**Decision**: Never drain the last available browser

**Rationale**:
- Prevents deadlock where all browsers are draining/restarting
- At least one browser always accepts requests
- Simple check: `available_browsers > 1` before marking draining

---

## Implementation Details

### File: `src/services/camoufox/config.py`

Add new configuration field:

```python
class CamoufoxSettings(BaseSettings):
    # ... existing fields ...

    # Browser lifecycle management
    recycle_after_requests: int = Field(
        default=100,
        ge=0,  # 0 = disabled
        description="Recycle browser after this many requests to prevent memory leaks. Set to 0 to disable.",
    )
```

### File: `src/services/camoufox/scraper.py`

#### New Enum for Browser State

```python
from enum import Enum, auto

class BrowserState(Enum):
    """Browser lifecycle states for recycling."""
    READY = auto()      # Accepting requests
    DRAINING = auto()   # No new requests, waiting for in-flight to complete
    RESTARTING = auto() # Being restarted
```

#### New Instance Variables

```python
def __init__(self, config: CamoufoxSettings | None = None) -> None:
    # ... existing initialization ...

    # Browser recycling state
    self._browser_request_counts: dict[int, int] = {}
    self._browser_states: dict[int, BrowserState] = {}
    self._browser_in_flight: dict[int, int] = {}  # Track in-flight per browser
```

#### Modified `start()` Method

Initialize recycling state for each browser:

```python
async def start(self) -> None:
    # ... existing browser creation loop ...

    # Initialize recycling state
    for idx in range(len(self._browsers)):
        self._browser_request_counts[idx] = 0
        self._browser_states[idx] = BrowserState.READY
        self._browser_in_flight[idx] = 0
```

#### Modified `_get_next_browser()` Method

Skip draining/restarting browsers:

```python
def _get_next_browser(self) -> tuple[Browser, int]:
    """Get next available browser using round-robin with health check."""
    if not self._browsers:
        raise RuntimeError("No browsers available - pool not started")

    dead_browser_indices: list[int] = []
    pool_size = len(self._browsers)

    for _ in range(pool_size):
        current_index = self._browser_index
        self._browser_index = (self._browser_index + 1) % pool_size

        browser = self._browsers[current_index]
        state = self._browser_states.get(current_index, BrowserState.READY)

        # Skip browsers that are draining or restarting
        if state != BrowserState.READY:
            logger.debug(
                "skipping_browser",
                browser_index=current_index,
                state=state.name,
            )
            continue

        if browser.is_connected():
            # Schedule restarts for any dead browsers found
            for dead_idx in dead_browser_indices:
                self._schedule_browser_restart(dead_idx)
            return browser, current_index
        else:
            dead_browser_indices.append(current_index)

    # All browsers unavailable - attempt emergency restart
    # ... existing fallback logic ...
```

#### New Method: Track In-Flight Requests Per Browser

```python
@asynccontextmanager
async def _track_browser_request(self, browser_index: int):
    """Track in-flight requests per browser for graceful recycling."""
    self._browser_in_flight[browser_index] = self._browser_in_flight.get(browser_index, 0) + 1
    try:
        yield
    finally:
        self._browser_in_flight[browser_index] -= 1

        # If browser is draining and no more in-flight requests, restart it
        if (
            self._browser_states.get(browser_index) == BrowserState.DRAINING
            and self._browser_in_flight[browser_index] == 0
        ):
            logger.info(
                "browser_drained",
                browser_index=browser_index,
                message="All in-flight requests complete, restarting browser",
            )
            self._schedule_browser_restart(browser_index)
```

#### New Method: Check and Trigger Recycling

```python
def _check_browser_recycle(self, browser_index: int) -> None:
    """Check if browser should be recycled based on request count."""
    if self.config.recycle_after_requests <= 0:
        return  # Recycling disabled

    count = self._browser_request_counts.get(browser_index, 0)
    if count < self.config.recycle_after_requests:
        return  # Not yet at threshold

    # Check if we have other available browsers
    available_count = sum(
        1 for idx, state in self._browser_states.items()
        if state == BrowserState.READY and idx != browser_index
    )

    if available_count == 0:
        logger.warning(
            "recycle_skipped_last_browser",
            browser_index=browser_index,
            request_count=count,
            message="Cannot recycle last available browser",
        )
        return

    logger.info(
        "browser_recycle_triggered",
        browser_index=browser_index,
        request_count=count,
        threshold=self.config.recycle_after_requests,
    )

    # Mark as draining (no new requests)
    self._browser_states[browser_index] = BrowserState.DRAINING

    # If no in-flight requests, restart immediately
    if self._browser_in_flight.get(browser_index, 0) == 0:
        self._schedule_browser_restart(browser_index)
```

#### Modified `scrape()` Method

Add tracking and recycling check:

```python
async def scrape(self, request: ScrapeRequest) -> dict[str, Any]:
    """Scrape a URL and return content."""
    async with self._semaphore:
        try:
            browser, browser_index = self._get_next_browser()
        except RuntimeError as e:
            # ... existing emergency restart logic ...

        async with self._acquire_page():
            async with self._track_browser_request(browser_index):
                try:
                    result = await self._do_scrape(request, browser)

                    # Increment request count and check for recycling
                    self._browser_request_counts[browser_index] = (
                        self._browser_request_counts.get(browser_index, 0) + 1
                    )
                    self._check_browser_recycle(browser_index)

                    return result
                except Exception as e:
                    # ... existing error handling ...
```

#### Modified `_restart_browser()` Method

Reset state after restart:

```python
async def _restart_browser(self, index: int) -> Browser | None:
    """Restart a browser instance."""
    if index in self._restarting_browsers:
        return None

    self._restarting_browsers.add(index)
    self._browser_states[index] = BrowserState.RESTARTING

    try:
        # ... existing restart logic ...

        # Reset state after successful restart
        self._browser_request_counts[index] = 0
        self._browser_states[index] = BrowserState.READY

        logger.info(
            "browser_restarted",
            browser_index=index,
            message="Browser recycled successfully",
        )

        return new_browser
    except Exception as e:
        # On failure, mark as ready to allow retry
        self._browser_states[index] = BrowserState.READY
        logger.error("browser_restart_failed", browser_index=index, error=str(e))
        return None
    finally:
        self._restarting_browsers.discard(index)
```

---

## Configuration

### Environment Variable

```bash
CAMOUFOX_RECYCLE_AFTER_REQUESTS=100  # Default, recycle after 100 requests per browser
CAMOUFOX_RECYCLE_AFTER_REQUESTS=0    # Disable recycling
CAMOUFOX_RECYCLE_AFTER_REQUESTS=50   # More aggressive recycling for memory-constrained environments
```

### docker-compose.yml Addition

```yaml
- CAMOUFOX_RECYCLE_AFTER_REQUESTS=${CAMOUFOX_RECYCLE_AFTER_REQUESTS:-100}
```

### .env.example Addition

```bash
# Browser lifecycle management
CAMOUFOX_RECYCLE_AFTER_REQUESTS=100  # Recycle browser after N requests (0 = disabled)
```

---

## Test Plan

### Unit Tests: `tests/test_camoufox_browser_recycling.py`

```python
class TestBrowserRecycling:
    """Test browser recycling logic."""

    def test_recycle_after_requests_config_default(self):
        """Test default recycling threshold."""
        settings = CamoufoxSettings()
        assert settings.recycle_after_requests == 100

    def test_recycle_after_requests_config_disabled(self):
        """Test recycling can be disabled with 0."""
        settings = CamoufoxSettings(recycle_after_requests=0)
        assert settings.recycle_after_requests == 0

    @pytest.mark.asyncio
    async def test_request_count_increments_after_successful_scrape(self):
        """Test that request count increments after successful scrape."""
        # Setup mock browser pool
        # Make request
        # Assert count incremented

    @pytest.mark.asyncio
    async def test_request_count_not_incremented_on_failure(self):
        """Test that failed requests don't increment count."""
        # Setup mock browser that fails
        # Make request (expect exception)
        # Assert count unchanged

    @pytest.mark.asyncio
    async def test_browser_marked_draining_at_threshold(self):
        """Test that browser is marked draining when threshold reached."""
        # Setup scraper with threshold=2
        # Make 2 requests
        # Assert browser state is DRAINING

    @pytest.mark.asyncio
    async def test_draining_browser_skipped_in_round_robin(self):
        """Test that draining browsers are skipped."""
        # Setup 3 browsers, mark one as draining
        # Call _get_next_browser()
        # Assert draining browser was skipped

    @pytest.mark.asyncio
    async def test_last_browser_not_drained(self):
        """Test that last available browser is never drained."""
        # Setup 3 browsers, 2 already draining
        # Trigger recycle check on the third
        # Assert it stays READY

    @pytest.mark.asyncio
    async def test_browser_restarts_when_drained_and_no_inflight(self):
        """Test that browser restarts after draining completes."""
        # Setup browser at threshold
        # Make request (triggers draining)
        # Wait for request to complete
        # Assert browser restarted and state is READY

    @pytest.mark.asyncio
    async def test_request_count_reset_after_restart(self):
        """Test that request count resets to 0 after restart."""
        # Trigger restart
        # Assert count is 0

    @pytest.mark.asyncio
    async def test_recycling_disabled_when_threshold_zero(self):
        """Test that recycling is disabled when threshold is 0."""
        # Setup scraper with threshold=0
        # Make 1000 requests
        # Assert no browser state changes

    @pytest.mark.asyncio
    async def test_inflight_requests_complete_before_restart(self):
        """Test that in-flight requests complete before browser restarts."""
        # Setup browser at threshold-1
        # Start slow request
        # Start another request (triggers draining)
        # Assert first request completes successfully
        # Assert browser restarts only after all requests done
```

### Integration Test

```python
class TestBrowserRecyclingIntegration:
    """Integration tests for browser recycling."""

    @pytest.mark.asyncio
    async def test_recycling_under_load(self):
        """Test recycling works correctly under concurrent load."""
        # Setup real scraper with low threshold
        # Make many concurrent requests
        # Assert all requests succeed
        # Assert browsers were recycled (check logs or restart count)
```

---

## Observability

### Log Events

| Event | Level | Fields | When |
|-------|-------|--------|------|
| `browser_recycle_triggered` | INFO | browser_index, request_count, threshold | Threshold reached |
| `browser_drained` | INFO | browser_index | All in-flight complete |
| `browser_restarted` | INFO | browser_index | Successful restart |
| `recycle_skipped_last_browser` | WARN | browser_index, request_count | Can't recycle last browser |
| `skipping_browser` | DEBUG | browser_index, state | Browser skipped in selection |

### Metrics (Future Enhancement)

- `camoufox_browser_request_total{browser_index}` - Counter per browser
- `camoufox_browser_restarts_total{browser_index, reason}` - Restart counter
- `camoufox_browser_state{browser_index}` - Gauge (0=ready, 1=draining, 2=restarting)

---

## Edge Cases Handled

1. **All browsers draining simultaneously**: Last browser protection prevents this
2. **Restart fails**: Browser marked READY again to allow requests while retry happens
3. **Counter overflow**: Integer overflow not possible with reasonable thresholds
4. **Concurrent requests during drain**: In-flight tracking ensures completion
5. **Rapid successive restarts**: `_restarting_browsers` set prevents duplicates
6. **Pool not started**: Existing `RuntimeError` handling covers this
7. **Zero threshold**: Explicitly checked and disables feature

---

## Implementation Order

1. **Config** (5 min): Add `recycle_after_requests` to `CamoufoxSettings`
2. **State enum** (5 min): Add `BrowserState` enum
3. **Instance variables** (5 min): Add tracking dicts to `__init__`
4. **Initialization** (5 min): Initialize state in `start()`
5. **Browser selection** (15 min): Modify `_get_next_browser()` to skip non-ready
6. **In-flight tracking** (10 min): Add `_track_browser_request()` context manager
7. **Recycling check** (15 min): Add `_check_browser_recycle()` method
8. **Scrape integration** (10 min): Wire up tracking and check in `scrape()`
9. **Restart reset** (10 min): Reset state in `_restart_browser()`
10. **Tests** (30 min): Write comprehensive unit tests
11. **Config files** (5 min): Update docker-compose.yml and .env.example

---

## Risks and Mitigations

| Risk | Impact | Mitigation |
|------|--------|------------|
| New code introduces bugs | Medium | Comprehensive tests, gradual rollout |
| Performance overhead | Low | Minimal - just counter increments and dict lookups |
| Last-browser protection too conservative | Low | Can increase browser count if needed |
| Restart takes too long, blocking capacity | Medium | Existing 30s timeout on cleanup |

---

## Success Criteria

1. Memory usage stays stable over long periods (no monotonic increase)
2. Zero request failures during recycling
3. All tests pass
4. Log events visible for monitoring
5. Configurable via environment variable
