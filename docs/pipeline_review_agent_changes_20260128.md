# Pipeline Review: Recent Agent Changes (2026-01-28)

## Scope

Review of 4 merged PRs:
- #70 - Scheduler stale thresholds
- #68 - LLM queue pub/sub
- #69 - Storage async wrappers
- #73 - Embedding recovery

---

## Critical (must fix)

### 🔴 C1. Qdrant client created with wrong parameters in recovery endpoint

- **File:** `src/api/v1/extraction.py:379-382`
- **Status:** ✅ VERIFIED REAL - Will crash on any call
- **Issue:** Creates `QdrantClient` using `host` and `port` parameters that don't exist in settings:

```python
qdrant_client = QdrantClient(
    host=settings.qdrant_host,   # ❌ DOES NOT EXIST
    port=settings.qdrant_port,   # ❌ DOES NOT EXIST
)
```

**Config only has `qdrant_url`:**
```python
qdrant_url: str = Field(default="http://localhost:6333", ...)
```

**This will raise `AttributeError: 'Settings' object has no attribute 'qdrant_host'`**

**Fix:** Use existing singleton from `qdrant_connection.py`:
```python
from qdrant_connection import qdrant_client
qdrant_repo = QdrantRepository(qdrant_client)
```

---

### 🔴 C2. Recovery endpoint doesn't commit transaction - DATA LOSS

- **File:** `src/api/v1/extraction.py:324-417`
- **Status:** ✅ VERIFIED REAL - Changes silently rolled back
- **Issue:** The endpoint calls `update_embedding_ids_batch()` which only does `flush()`. With `autocommit=False` (line 23 in database.py), changes are rolled back when session closes.

**Evidence:**
- `database.py:23`: `SessionLocal = sessionmaker(autocommit=False, ...)`
- `database.py:37`: `db.close()` - closes without commit
- `extraction.py:373`: Repository only does `self._session.flush()`
- Recovery endpoint has NO `db.commit()` call

**Impact:** Recovery appears to succeed but `embedding_id` updates are rolled back. Orphaned extractions remain orphaned.

---

## Important (should fix)

### 🟠 I1. Empty fact_text silently processed in recovery

- **File:** `src/services/extraction/embedding_recovery.py:101-104`
- **Status:** ✅ VERIFIED REAL
- **Issue:** Empty `fact_text` values are embedded without filtering:

```python
fact_texts = [
    extraction.data.get("fact_text", "")  # Empty string if missing
    for extraction in extractions
]
embeddings = await self._embedding_service.embed_batch(fact_texts)
```

**Impact:** Wastes embedding API calls, creates meaningless vectors in Qdrant.

---

### 🟠 I2. Deprecated `asyncio.get_event_loop()` usage

- **File:** `src/services/storage/qdrant/repository.py:57, 98, 139, 177, 211`
- **Status:** ✅ REAL but low priority - works but deprecated
- **Issue:** Uses `asyncio.get_event_loop()` which is deprecated in Python 3.10+

**Better:** Use `asyncio.get_running_loop()` or `asyncio.to_thread()`

---

## Minor

### 🟡 M1. ~~Pub/sub cleanup uses `aclose()` which may not exist~~

- **Status:** ❌ FALSE POSITIVE
- **Analysis:** Project uses `redis==5.2.0` which has `aclose()` method for async pubsub

---

### 🟡 M2. Scheduler thresholds recalculated on every poll

- **File:** `src/services/scraper/scheduler.py:201, 281, 353`
- **Status:** ✅ REAL but negligible impact
- **Issue:** `get_stale_thresholds()` called every 5 seconds
- **Impact:** Negligible - just reads from settings singleton

---

### 🟡 M3. ~~Response model missing errors field~~

- **Status:** ❌ FALSE POSITIVE
- **Analysis:** `RecoverySummaryResponse` in `models.py:31-34` DOES have `errors` field:
```python
errors: list[str] = Field(default_factory=list, description="List of error messages encountered")
```

---

## Verified Summary

| Severity | Count | Items |
|----------|-------|-------|
| Critical | 2 | C1 (wrong config - **CRASH**), C2 (no commit - **DATA LOSS**) |
| Important | 2 | I1 (empty text), I2 (deprecated API) |
| Minor | 1 | M2 (threshold caching) |
| False Positives | 2 | M1, M3 |

**BLOCKING:** C1 and C2 must be fixed before the recovery endpoint can be used. Currently it will either crash (C1) or silently lose data (C2).
