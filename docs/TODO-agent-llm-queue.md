# TODO: Agent LLM Queue - Replace Polling with Pub/Sub

**Agent ID:** `agent-llm-queue`
**Branch:** `feat/llm-queue-pubsub`
**Priority:** Medium

---

## Context

The LLM queue currently uses polling to wait for responses:

```python
# Current: polls every 100ms for up to 300 seconds = 3000 Redis round-trips per request
while time.time() < deadline:
    result = await self.redis.get(response_key)
    if result:
        return response
    await asyncio.sleep(self.poll_interval)  # 0.1s
```

This creates unnecessary Redis load under high concurrency.

**Key files:**
- `src/services/llm/queue.py` - `LLMRequestQueue.wait_for_result()` method
- `src/services/llm/worker.py` - `LLMWorker._process_request()` stores responses
- `src/redis_client.py` - Redis client setup

---

## Objective

Replace the polling pattern with Redis pub/sub for response notification, reducing Redis round-trips from O(n*3000) to O(n*2) per request.

---

## Tasks

### 1. Add Pub/Sub Channel for Responses

**File:** `src/services/llm/queue.py`

Add pub/sub notification when response is ready:

```python
# Channel naming convention
def _response_channel(self, request_id: str) -> str:
    return f"llm:response:notify:{request_id}"
```

### 2. Modify Worker to Publish Notification

**File:** `src/services/llm/worker.py`

After storing response, publish notification:

```python
# In _process_request() after storing result
await self._redis.set(response_key, json.dumps(response), ex=self._response_ttl)
await self._redis.publish(self._response_channel(request_id), "ready")
```

### 3. Modify Queue to Subscribe Instead of Poll

**File:** `src/services/llm/queue.py`

Replace polling loop with pub/sub wait:

```python
async def wait_for_result(
    self,
    request_id: str,
    timeout: float = 300.0,
) -> LLMResponse:
    """Wait for LLM response using pub/sub notification."""
    response_key = f"llm:response:{request_id}"
    channel = self._response_channel(request_id)

    # Check if already complete (in case response arrived before subscribe)
    result = await self.redis.get(response_key)
    if result:
        return self._parse_response(result)

    # Subscribe and wait for notification
    pubsub = self.redis.pubsub()
    await pubsub.subscribe(channel)
    try:
        # Wait for message with timeout
        async for message in pubsub.listen():
            if message["type"] == "message":
                # Notification received, fetch result
                result = await self.redis.get(response_key)
                if result:
                    return self._parse_response(result)
    except asyncio.TimeoutError:
        raise RequestTimeoutError(f"Request {request_id} timed out")
    finally:
        await pubsub.unsubscribe(channel)
        await pubsub.close()
```

### 4. Add Fallback Polling for Edge Cases

Keep a fallback polling mechanism for cases where pub/sub message might be missed:

```python
async def wait_for_result(
    self,
    request_id: str,
    timeout: float = 300.0,
    poll_fallback_interval: float = 5.0,  # Check every 5s as fallback
) -> LLMResponse:
    """Wait for response with pub/sub + fallback polling."""
    ...
```

### 5. Handle Connection Issues

Add proper cleanup and reconnection handling:
- Unsubscribe on timeout/error
- Handle Redis disconnection gracefully
- Log when falling back to polling

---

## Tests

**File:** `tests/test_llm_queue_pubsub.py` (new file)

### Test cases:

1. `test_wait_for_result_receives_pubsub_notification` - Normal flow works
2. `test_wait_for_result_returns_cached_response` - Already-complete requests return immediately
3. `test_wait_for_result_timeout` - Proper timeout handling
4. `test_wait_for_result_cleans_up_subscription` - No leaked subscriptions
5. `test_worker_publishes_notification` - Worker sends pub/sub message
6. `test_concurrent_requests_isolated` - Multiple requests don't interfere

**File:** `tests/test_llm_queue.py` (update existing)

Ensure existing tests still pass.

---

## Constraints

- Do NOT change the request submission flow (`submit()` method)
- Do NOT change the response storage format
- Keep backward compatibility - existing code should work during transition
- Response TTL (300s) remains unchanged
- Must handle the case where response arrives before subscribe

---

## Acceptance Criteria

- [ ] Worker publishes notification after storing response
- [ ] Queue uses pub/sub to wait for response
- [ ] Fallback polling exists for edge cases
- [ ] Proper cleanup of subscriptions (no leaks)
- [ ] Timeout still works correctly
- [ ] All new tests pass
- [ ] Existing LLM queue tests pass
- [ ] `ruff check` passes

---

## Verification

```bash
# Run new tests
pytest tests/test_llm_queue_pubsub.py -v

# Run existing queue tests
pytest tests/test_llm_queue.py -v
pytest tests/test_llm_worker.py -v

# Lint
ruff check src/services/llm/queue.py src/services/llm/worker.py
```

---

## Performance Expectation

Before: ~3000 Redis GET calls per request (polling 100ms for 300s)
After: ~2 Redis calls per request (1 subscribe, 1 GET after notification)

This is a **1500x reduction** in Redis operations per LLM request.
