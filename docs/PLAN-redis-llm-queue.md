# Redis-Based LLM Request Queue Architecture

## Overview

Decouple extraction logic from LLM inference by introducing a Redis-based request queue with dedicated LLM workers. This provides backpressure, visibility, horizontal scaling, and robustness for handling 300+ concurrent domains.

## Current Architecture (Problem)

```
Extraction Worker
       │
       ├── process_source(1) ──► LLM call (blocks)
       ├── process_source(2) ──► LLM call (blocks)
       └── ... (serial or limited parallel)

Problems:
- Direct coupling to vLLM
- No queue visibility
- No backpressure to crawl layer
- Timeouts when vLLM overwhelmed
```

## Proposed Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                        EXTRACTION LAYER                          │
├──────────────────────────────────────────────────────────────────┤
│  Extraction Worker 1    Extraction Worker 2    Extraction Worker 3│
│         │                      │                      │          │
│         └──────────────────────┼──────────────────────┘          │
│                                │                                  │
│                         Push requests                             │
│                                ▼                                  │
├──────────────────────────────────────────────────────────────────┤
│                     REDIS LLM REQUEST QUEUE                       │
│  ┌─────────────────────────────────────────────────────────────┐ │
│  │ Stream: llm:requests                                         │ │
│  │ ┌─────────┬─────────┬─────────┬─────────┬─────────┐        │ │
│  │ │ req-001 │ req-002 │ req-003 │ req-004 │ req-005 │ ...    │ │
│  │ └─────────┴─────────┴─────────┴─────────┴─────────┘        │ │
│  │                                                              │ │
│  │ Queue depth monitoring: XLEN llm:requests                   │ │
│  │ Backpressure threshold: 500 pending requests                │ │
│  └─────────────────────────────────────────────────────────────┘ │
│                                │                                  │
│                          Pull requests                            │
│                                ▼                                  │
├──────────────────────────────────────────────────────────────────┤
│                        LLM WORKER POOL                            │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐              │
│  │ LLM Worker 1│  │ LLM Worker 2│  │ LLM Worker 3│              │
│  │             │  │             │  │             │              │
│  │ Adaptive    │  │ Adaptive    │  │ Adaptive    │              │
│  │ concurrency │  │ concurrency │  │ concurrency │              │
│  │ (10-50 reqs)│  │ (10-50 reqs)│  │ (10-50 reqs)│              │
│  └──────┬──────┘  └──────┬──────┘  └──────┬──────┘              │
│         └────────────────┼────────────────┘                      │
│                          ▼                                        │
├──────────────────────────────────────────────────────────────────┤
│                          vLLM                                     │
│              (200 concurrent, 800 queue)                          │
└──────────────────────────────────────────────────────────────────┘
```

## Components

### 1. LLM Request Message Schema

```python
@dataclass
class LLMRequest:
    """Request message for LLM queue."""
    request_id: str          # UUID for correlation
    request_type: str        # "extract_facts" | "extract_field_group" | "extract_entities"
    payload: dict            # Type-specific payload
    priority: int            # 0=low, 5=normal, 10=high
    created_at: datetime
    timeout_at: datetime     # When to consider request expired
    callback_key: str        # Redis key for response

    # Payload examples:
    # extract_facts: {content, categories, profile_name}
    # extract_field_group: {content, field_group, company_name}
    # extract_entities: {extraction_data, entity_types}
```

### 2. LLM Response Schema

```python
@dataclass
class LLMResponse:
    """Response message stored in Redis."""
    request_id: str
    status: str              # "success" | "error" | "timeout"
    result: dict | None      # Extracted data
    error: str | None        # Error message if failed
    processing_time_ms: int
    completed_at: datetime
```

### 3. Redis Data Structures

```
# Request queue (Redis Stream)
llm:requests                    # Main request stream
llm:requests:priority:high      # High priority stream (optional)

# Response storage (Redis Hash with TTL)
llm:response:{request_id}       # Response data, TTL=300s

# Metrics (Redis keys)
llm:metrics:queue_depth         # Current queue depth
llm:metrics:processing_rate     # Requests/second
llm:metrics:error_rate          # Errors/second
llm:metrics:avg_latency         # Rolling average latency

# Backpressure signal
llm:backpressure                # "ok" | "slow" | "full"
```

### 4. Queue Operations

```python
class LLMRequestQueue:
    """Redis-based LLM request queue."""

    def __init__(self, redis: Redis):
        self.redis = redis
        self.stream_key = "llm:requests"
        self.consumer_group = "llm-workers"
        self.backpressure_threshold = 500
        self.max_queue_depth = 1000

    async def submit(self, request: LLMRequest) -> str:
        """Submit request to queue. Returns request_id."""
        # Check backpressure
        depth = await self.get_queue_depth()
        if depth >= self.max_queue_depth:
            raise QueueFullError(f"Queue depth {depth} exceeds max {self.max_queue_depth}")

        # Add to stream
        await self.redis.xadd(
            self.stream_key,
            {
                "request_id": request.request_id,
                "data": request.to_json(),
            },
            maxlen=self.max_queue_depth * 2,  # Auto-trim old entries
        )
        return request.request_id

    async def wait_for_result(
        self,
        request_id: str,
        timeout: float = 300.0
    ) -> LLMResponse:
        """Wait for response with polling."""
        response_key = f"llm:response:{request_id}"
        deadline = time.time() + timeout

        while time.time() < deadline:
            result = await self.redis.get(response_key)
            if result:
                return LLMResponse.from_json(result)
            await asyncio.sleep(0.1)  # Poll interval

        raise TimeoutError(f"Request {request_id} timed out after {timeout}s")

    async def get_queue_depth(self) -> int:
        """Get current queue depth."""
        return await self.redis.xlen(self.stream_key)

    async def get_backpressure_status(self) -> str:
        """Get backpressure status for upstream components."""
        depth = await self.get_queue_depth()
        if depth < self.backpressure_threshold * 0.5:
            return "ok"
        elif depth < self.backpressure_threshold:
            return "slow"
        else:
            return "full"
```

### 5. LLM Worker

```python
class LLMWorker:
    """Worker that processes LLM requests from queue."""

    def __init__(
        self,
        redis: Redis,
        llm_client: AsyncOpenAI,
        worker_id: str,
        initial_concurrency: int = 10,
        max_concurrency: int = 50,
        min_concurrency: int = 5,
    ):
        self.redis = redis
        self.llm_client = llm_client
        self.worker_id = worker_id
        self.stream_key = "llm:requests"
        self.consumer_group = "llm-workers"

        # Adaptive concurrency
        self.concurrency = initial_concurrency
        self.max_concurrency = max_concurrency
        self.min_concurrency = min_concurrency
        self.semaphore = asyncio.Semaphore(initial_concurrency)

        # Metrics for adaptive tuning
        self.success_count = 0
        self.timeout_count = 0
        self.last_adjustment = time.time()

    async def start(self):
        """Start processing requests."""
        # Ensure consumer group exists
        try:
            await self.redis.xgroup_create(
                self.stream_key,
                self.consumer_group,
                mkstream=True
            )
        except ResponseError:
            pass  # Group already exists

        # Process loop
        while True:
            await self._process_batch()
            await self._maybe_adjust_concurrency()

    async def _process_batch(self):
        """Read and process a batch of requests."""
        # Read up to `concurrency` messages
        messages = await self.redis.xreadgroup(
            groupname=self.consumer_group,
            consumername=self.worker_id,
            streams={self.stream_key: ">"},
            count=self.concurrency,
            block=1000,  # 1 second block
        )

        if not messages:
            return

        # Process all messages concurrently
        tasks = []
        for stream, entries in messages:
            for entry_id, data in entries:
                task = asyncio.create_task(
                    self._process_request(entry_id, data)
                )
                tasks.append(task)

        await asyncio.gather(*tasks, return_exceptions=True)

    async def _process_request(self, entry_id: str, data: dict):
        """Process a single LLM request."""
        request = LLMRequest.from_json(data["data"])
        response_key = f"llm:response:{request.request_id}"

        async with self.semaphore:
            start_time = time.time()
            try:
                # Check if request expired
                if datetime.now() > request.timeout_at:
                    response = LLMResponse(
                        request_id=request.request_id,
                        status="timeout",
                        result=None,
                        error="Request expired before processing",
                        processing_time_ms=0,
                        completed_at=datetime.now(),
                    )
                else:
                    # Execute LLM call based on request type
                    result = await self._execute_llm_call(request)

                    response = LLMResponse(
                        request_id=request.request_id,
                        status="success",
                        result=result,
                        error=None,
                        processing_time_ms=int((time.time() - start_time) * 1000),
                        completed_at=datetime.now(),
                    )
                    self.success_count += 1

            except Exception as e:
                response = LLMResponse(
                    request_id=request.request_id,
                    status="error",
                    result=None,
                    error=str(e),
                    processing_time_ms=int((time.time() - start_time) * 1000),
                    completed_at=datetime.now(),
                )
                if "timeout" in str(e).lower():
                    self.timeout_count += 1

            # Store response in Redis with TTL
            await self.redis.setex(
                response_key,
                300,  # 5 minute TTL
                response.to_json(),
            )

            # Acknowledge message
            await self.redis.xack(
                self.stream_key,
                self.consumer_group,
                entry_id,
            )

    async def _execute_llm_call(self, request: LLMRequest) -> dict:
        """Execute the actual LLM call based on request type."""
        if request.request_type == "extract_facts":
            return await self._extract_facts(request.payload)
        elif request.request_type == "extract_field_group":
            return await self._extract_field_group(request.payload)
        elif request.request_type == "extract_entities":
            return await self._extract_entities(request.payload)
        else:
            raise ValueError(f"Unknown request type: {request.request_type}")

    async def _maybe_adjust_concurrency(self):
        """Adjust concurrency based on success/timeout ratio."""
        now = time.time()
        if now - self.last_adjustment < 10:  # Adjust every 10 seconds
            return

        self.last_adjustment = now
        total = self.success_count + self.timeout_count

        if total < 10:  # Not enough data
            return

        timeout_rate = self.timeout_count / total

        if timeout_rate > 0.1:  # >10% timeouts, back off
            new_concurrency = max(
                self.min_concurrency,
                int(self.concurrency * 0.7)
            )
            logger.warning(
                "llm_worker_backing_off",
                worker_id=self.worker_id,
                timeout_rate=timeout_rate,
                old_concurrency=self.concurrency,
                new_concurrency=new_concurrency,
            )
            self.concurrency = new_concurrency
            self.semaphore = asyncio.Semaphore(new_concurrency)

        elif timeout_rate < 0.02 and self.success_count > 50:  # <2% timeouts, scale up
            new_concurrency = min(
                self.max_concurrency,
                int(self.concurrency * 1.2)
            )
            logger.info(
                "llm_worker_scaling_up",
                worker_id=self.worker_id,
                timeout_rate=timeout_rate,
                old_concurrency=self.concurrency,
                new_concurrency=new_concurrency,
            )
            self.concurrency = new_concurrency
            self.semaphore = asyncio.Semaphore(new_concurrency)

        # Reset counters
        self.success_count = 0
        self.timeout_count = 0
```

### 6. Extraction Worker Changes

```python
# Before: Direct LLM call
class SchemaExtractor:
    async def extract_field_group(self, content, field_group, company_name):
        response = await self.client.chat.completions.create(...)
        return response

# After: Queue-based call
class SchemaExtractor:
    def __init__(self, llm_queue: LLMRequestQueue):
        self.llm_queue = llm_queue

    async def extract_field_group(self, content, field_group, company_name):
        request = LLMRequest(
            request_id=str(uuid4()),
            request_type="extract_field_group",
            payload={
                "content": content,
                "field_group": field_group.to_dict(),
                "company_name": company_name,
            },
            priority=5,
            created_at=datetime.now(),
            timeout_at=datetime.now() + timedelta(seconds=300),
            callback_key=f"llm:response:{request_id}",
        )

        # Submit and wait
        await self.llm_queue.submit(request)
        response = await self.llm_queue.wait_for_result(request.request_id)

        if response.status == "error":
            raise LLMError(response.error)

        return response.result
```

### 7. Backpressure Integration

```python
# In crawl API endpoint
@router.post("/crawl")
async def create_crawl(request: CrawlRequest):
    # Check extraction backpressure before accepting crawl
    llm_queue = get_llm_queue()
    backpressure = await llm_queue.get_backpressure_status()

    if backpressure == "full":
        raise HTTPException(
            status_code=503,
            detail="System at capacity. Extraction queue full. Retry later.",
            headers={"Retry-After": "60"},
        )
    elif backpressure == "slow":
        # Accept but warn
        logger.warning("accepting_crawl_under_backpressure", url=request.url)

    # Proceed with crawl creation
    ...
```

### 8. Monitoring & Observability

```python
class LLMQueueMetrics:
    """Prometheus metrics for LLM queue."""

    def __init__(self, redis: Redis):
        self.redis = redis

        # Prometheus metrics
        self.queue_depth = Gauge(
            "llm_queue_depth",
            "Current LLM request queue depth"
        )
        self.processing_rate = Counter(
            "llm_requests_processed_total",
            "Total LLM requests processed",
            ["status", "request_type"]
        )
        self.latency = Histogram(
            "llm_request_latency_seconds",
            "LLM request latency",
            ["request_type"]
        )
        self.worker_concurrency = Gauge(
            "llm_worker_concurrency",
            "Current worker concurrency level",
            ["worker_id"]
        )

    async def collect(self):
        """Collect metrics from Redis."""
        depth = await self.redis.xlen("llm:requests")
        self.queue_depth.set(depth)
```

## Configuration

```python
# config.py additions
class Settings(BaseSettings):
    # LLM Queue settings
    llm_queue_max_depth: int = Field(
        default=1000,
        description="Maximum LLM request queue depth",
    )
    llm_queue_backpressure_threshold: int = Field(
        default=500,
        description="Queue depth that triggers backpressure",
    )
    llm_worker_count: int = Field(
        default=3,
        description="Number of LLM worker processes",
    )
    llm_worker_initial_concurrency: int = Field(
        default=20,
        description="Initial concurrent requests per worker",
    )
    llm_worker_max_concurrency: int = Field(
        default=50,
        description="Maximum concurrent requests per worker",
    )
    llm_worker_min_concurrency: int = Field(
        default=5,
        description="Minimum concurrent requests per worker",
    )
    llm_request_timeout: int = Field(
        default=300,
        description="LLM request timeout in seconds",
    )
```

## File Structure

```
src/
├── services/
│   ├── llm/
│   │   ├── __init__.py
│   │   ├── client.py           # Existing - keep for direct calls if needed
│   │   ├── queue.py            # NEW: LLMRequestQueue
│   │   ├── worker.py           # NEW: LLMWorker
│   │   ├── models.py           # NEW: LLMRequest, LLMResponse
│   │   └── metrics.py          # NEW: LLMQueueMetrics
│   ├── extraction/
│   │   ├── schema_extractor.py # MODIFY: Use queue instead of direct calls
│   │   └── ...
```

## Implementation Phases

### Phase 1: Core Queue Infrastructure (2-3 days)
- [ ] Create `LLMRequest` and `LLMResponse` models
- [ ] Implement `LLMRequestQueue` class
- [ ] Write tests for queue operations
- [ ] Add Redis Stream consumer group setup

### Phase 2: LLM Worker (2-3 days)
- [ ] Implement `LLMWorker` class
- [ ] Add adaptive concurrency logic
- [ ] Write tests for worker processing
- [ ] Add worker to scheduler startup

### Phase 3: Extraction Integration (2 days)
- [ ] Modify `SchemaExtractor` to use queue
- [ ] Modify `LLMClient` (fact extraction) to use queue
- [ ] Update dependency injection
- [ ] Write integration tests

### Phase 4: Backpressure & Monitoring (1-2 days)
- [ ] Add backpressure checks to crawl API
- [ ] Implement Prometheus metrics
- [ ] Add Grafana dashboard
- [ ] Write load tests

### Phase 5: Production Hardening (1-2 days)
- [ ] Add dead letter queue for failed requests
- [ ] Implement request retry logic
- [ ] Add circuit breaker for vLLM failures
- [ ] Load test with 300 domain simulation

## Rollback Plan

If issues occur:
1. Feature flag: `USE_LLM_QUEUE=false` falls back to direct LLM calls
2. Queue can be drained by workers even if new requests go direct
3. No data loss - requests either processed or returned with error

## Success Metrics

| Metric | Target |
|--------|--------|
| Queue depth under load | < 500 (backpressure threshold) |
| Request timeout rate | < 2% |
| vLLM utilization | > 80% |
| P99 latency | < 60 seconds |
| Throughput | > 10 requests/second sustained |
