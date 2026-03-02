# Pipeline Review: LLM Service Layer

**Date**: 2026-03-02
**Scope**: LLM client, Redis queue, worker model, chunking strategy, JSON repair

---

## 1. Overview

The LLM service layer provides a dual-mode interface for interacting with language models. It supports both direct HTTP calls and Redis-queue-based batched processing.

```
Caller → LLMClient → [Direct mode] → AsyncOpenAI → LLM API
                    → [Queue mode]  → Redis Streams → LLMWorker → LLM API
```

**LLM**: Qwen3-30B-A3B-Instruct-4bit (at `192.168.0.247:9003/v1`)
**Embedding**: bge-m3 (at `192.168.0.136:9003/v1`, 1024 dims)
**Reranker**: bge-reranker-v2-m3 (for smart classification confirmation)

---

## 2. LLMClient (`src/services/llm/client.py`)

### Dual-Mode Interface

```python
class LLMClient:
    def __init__(self, llm_queue: LLMRequestQueue | None = None):
        # If llm_queue provided → queue mode
        # Otherwise → direct mode with AsyncOpenAI
```

### Three Main Methods

| Method | Purpose | Output |
|--------|---------|--------|
| `extract_facts()` | Extract 10 most important facts | Facts with confidence scores |
| `extract_entities()` | Named entity recognition | Entities with type definitions |
| `complete()` | Generic LLM completion | Arbitrary structured output |

### Retry Logic

Exponential backoff with temperature variation:

```
Base temperature: 0.1
Per-retry increment: +0.05
Max retries: 3 (configurable)
Backoff: min_backoff * 2^(attempt-1), capped at max_backoff

Example:
  Attempt 1: temp=0.1,  delay=2s
  Attempt 2: temp=0.15, delay=4s
  Attempt 3: temp=0.2,  delay=8s
```

On retry, adds `LLM_RETRY_HINT` to prompt for conciseness.

### JSON Response Handling

LLM responses are parsed as JSON with multi-stage repair:

```python
try_repair_json(raw_text):
    1. Strip code fences (```json ... ```)
    2. Fix unterminated strings
    3. Balance brackets ({}, [])
    4. Remove trailing commas
    5. json.loads() with fallback to partial extraction
```

### Configuration

```python
llm_model = "Qwen3-30B-A3B-Instruct-4bit"
llm_http_timeout = 120      # seconds
llm_max_retries = 3
llm_max_tokens = 8192       # prevents endless generation
llm_retry_backoff_min = 2   # seconds
llm_retry_backoff_max = 30  # seconds
```

---

## 3. LLM Request Queue (`src/services/llm/queue.py`)

Redis Streams-based request queue with adaptive backpressure.

### Architecture

```
Producer (LLMClient)          Consumer (LLMWorker)
   │                                │
   ├── XADD to stream ──────► XREADGROUP (consumer group)
   │                                │
   ├── Subscribe pub/sub            ├── Process request
   │                                │
   ◄── Receive notification ◄──────├── PUBLISH result notification
   │                                │
   ├── GET response key             ├── SET response key (TTL 300s)
   │                                │
   ▼                                ▼
 Result                         Next batch
```

### Request Lifecycle

1. **Submit** (`submit()`): Add request to Redis Stream (if below max depth)
2. **Process** (`process_batch()`): Consumer group reads batch, worker processes
3. **Store** (`store_response()`): Result stored with 300-second TTL
4. **Retrieve** (`wait_for_result()`): Client wakes via pub/sub + polling fallback

### Backpressure Management

```
Queue Depth Thresholds:
  < 50% of max_depth  → "ok"
  50-80% of max_depth → "slow" (should_wait=True at 80%)
  >= max_depth        → "full" (submit rejected)
```

```python
get_backpressure_status() → {
    "status": "ok" | "slow" | "full",
    "queue_depth": 150,
    "threshold": 500,
    "should_wait": False
}
```

### Configuration

```python
llm_queue_enabled = False        # Disabled by default
llm_queue_max_depth = 1000
llm_queue_backpressure_threshold = 500
```

### Reliability Features

- **Request expiration**: `timeout_at` field, checked before processing
- **Dead Letter Queue**: Failed requests moved to DLQ after max retries
- **Fallback polling**: Every 5 seconds if pub/sub notification missed
- **Response TTL**: 300 seconds (auto-cleanup)

---

## 4. LLM Worker (`src/services/llm/worker.py`)

Processes requests from Redis queue with adaptive concurrency.

### Concurrency Model

```python
initial_concurrency = 10  # Starting semaphore size
min_concurrency = 5
max_concurrency = 50
```

**Adaptive Tuning** (evaluated every 10 seconds):
- If timeout rate > 10%: scale down to 70% of current
- If timeout rate < 2% AND success > 50: scale up by 20%
- Deferred adjustments when tasks are active (avoids race conditions)

### Request Type Handlers

| Request Type | Handler | Purpose |
|-------------|---------|---------|
| `extract_facts` | `_extract_facts()` | Fact extraction with JSON repair |
| `extract_field_group` | `_extract_field_group()` | Schema-based field extraction |
| `extract_entities` | `_extract_entities()` | Entity recognition |
| `complete` | `_complete()` | Generic completions |

### Processing Flow

```
1. Consumer group reads up to `concurrency` messages
2. Create async task per message (semaphore-gated)
3. Check request expiration before processing
4. Call appropriate handler
5. Store response (success or error)
6. Track success/timeout ratio for adaptive tuning
```

### Failure Handling

```
On failure:
  If retry_count < max_retries:
    Re-queue with incremented retry_count + temperature variation
  Else:
    Move to DLQ (full request context preserved)
    Send error response to client
```

---

## 5. Content Chunking (`src/services/llm/chunking.py`)

### Token Counting

```python
def estimate_tokens(text: str) -> int:
    # English/Latin: ~4 chars per token
    # CJK characters: ~1.5 chars per token (conservative)
    cjk_count = count_cjk_characters(text)
    latin_count = len(text) - cjk_count
    return int(latin_count / 4 + cjk_count / 1.5)
```

### Chunking Algorithm

```
Input: content (markdown text), max_tokens, overlap_tokens

1. Split by H2+ headers (preserve H1)
2. Group sections until max_tokens exceeded
3. Large sections → split by paragraphs
4. Large paragraphs → split by words
5. Overlap: prepend paragraph-aligned tail from previous chunk
```

### Output

```python
@dataclass
class DocumentChunk:
    content: str          # The actual text
    chunk_index: int      # Position in sequence
    total_chunks: int     # Total count
    header_path: str      # Breadcrumb for context ("## Section > ### Subsection")
```

### Configuration

```python
extraction_chunk_max_tokens = 5000   # Per chunk (was 8000)
extraction_chunk_overlap_tokens = 500 # Overlap between chunks (was 0)
# Aligned: 5000 tokens * 4 chars/token = 20000 chars = EXTRACTION_CONTENT_LIMIT
```

---

## 6. Data Flow: Direct vs Queue Mode

### Direct Mode (default)

```
SchemaExtractor.extract_with_limit()
  → Build system + user prompts
  → LLMClient.complete()
    → AsyncOpenAI.chat.completions.create()
    → Parse JSON response
    → Retry on failure (up to 3 times)
  → Return extraction dict
```

Pros: Simpler, lower latency per request
Cons: No centralized backpressure, each caller manages own concurrency

### Queue Mode (opt-in)

```
SchemaExtractor.extract_with_limit()
  → Build system + user prompts
  → LLMClient.complete()
    → LLMRequestQueue.submit()
      → Redis XADD
    → LLMRequestQueue.wait_for_result()
      → Pub/Sub wait + polling fallback
  → Return extraction dict

Meanwhile:
  LLMWorker.process_batch()
    → Redis XREADGROUP
    → Semaphore-gated LLM call
    → Redis SET response + PUBLISH notification
```

Pros: Centralized backpressure, adaptive concurrency, DLQ for failures
Cons: Additional latency, Redis dependency, more complex error handling

---

## 7. Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| Dual-mode (direct/queue) | Queue adds overhead; direct mode sufficient for most workloads |
| Temperature variation on retry | Identical prompts can produce identical bad outputs; small temp changes help |
| JSON repair pipeline | Small LLMs frequently produce malformed JSON; multi-stage repair catches most issues |
| Adaptive concurrency | Automatically finds optimal throughput without manual tuning |
| 5000 token chunks | Aligned with 20000 char content limit; fits Qwen3-30B's 32K context comfortably |
| Paragraph-aligned overlap | Clean semantic boundaries; avoids splitting mid-sentence |
| 300s response TTL | Balances memory usage with late-arriving client retrieval |
| Pub/Sub + polling fallback | Pub/Sub is faster but can miss messages; polling provides reliability |

---

## 8. Observability

### Structured Logging

```python
logger.info("llm_request_completed", model=..., tokens=..., duration_ms=...)
logger.warning("llm_retry", attempt=..., temperature=..., error=...)
logger.error("llm_request_failed", model=..., error=..., retries_exhausted=True)
```

### Metrics (via MetricsCollector)

- Total LLM requests (by type)
- Success/failure/timeout rates
- Queue depth (when queue mode enabled)
- Adaptive concurrency level
- Average response time
