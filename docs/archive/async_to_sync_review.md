# Pipeline Review: Async-to-Sync Repository Conversion

## Verified Findings

After code inspection, here are the **confirmed real issues** vs **false positives**:

---

## Real Issues (Confirmed)

### 🔴 1. EntityExtractor._store_entities - async with no async operations
- **File**: `src/services/knowledge/extractor.py:105-132`
- **Code**:
  ```python
  async def _store_entities(self, ...):  # Line 105
      ...
      entity_obj, created = self._entity_repo.get_or_create(...)  # Line 123 - sync call, no await
  ```
- **Problem**: Method is declared `async` but contains zero `await` statements. The only call is to `self._entity_repo.get_or_create()` which is now sync.
- **Fix**: Change to `def _store_entities()` and remove `await` on line 163 where it's called.

### 🔴 2. EmbeddingRecoveryService.find_orphaned_extractions - async with no async operations
- **File**: `src/services/extraction/embedding_recovery.py:63-80`
- **Code**:
  ```python
  async def find_orphaned_extractions(self, ...):  # Line 63
      return self._extraction_repo.find_orphaned(...)  # Line 77 - sync call, no await
  ```
- **Problem**: Method is `async` but only calls sync `find_orphaned()`. No await anywhere.
- **Fix**: Change to `def find_orphaned_extractions()` and update caller at line 170.

### 🔴 3. ReportService._get_project_schema - async with no async operations
- **File**: `src/services/reports/service.py:482-492`
- **Code**:
  ```python
  async def _get_project_schema(self, project_id: UUID):  # Line 482
      project = self._project_repo.get(project_id)  # Line 491 - sync call, no await
      return project.extraction_schema if project else None
  ```
- **Problem**: Method is `async` but only calls sync `self._project_repo.get()`. No await.
- **Fix**: Change to `def _get_project_schema()`.

### 🔴 4. ReportService._gather_data - async with no async operations
- **File**: `src/services/reports/service.py:160-239`
- **Code**:
  ```python
  async def _gather_data(self, ...):  # Line 160
      ...
      extractions = self._extraction_repo.list(...)  # Line 191 - sync, no await
      ...
      entities = self._entity_repo.list(filters=filters)  # Line 221 - sync, no await
  ```
- **Problem**: Entire method has no `await` statements. All repo calls are sync.
- **Fix**: Change to `def _gather_data()`.

### 🔴 5. ReportService._aggregate_for_table - async with no async operations
- **File**: `src/services/reports/service.py:494-647`
- **Code**:
  ```python
  async def _aggregate_for_table(self, ...):  # Line 494
      # ... 150+ lines of sync code with zero await statements
  ```
- **Problem**: Large method with no async operations whatsoever.
- **Fix**: Change to `def _aggregate_for_table()`.

### 🔴 6. ReportService._generate_table_report - async with no async operations
- **File**: `src/services/reports/service.py:701-745`
- **Code**:
  ```python
  async def _generate_table_report(self, ...):  # Line 701
      extraction_schema = await self._get_project_schema(project_id)  # Line 727
      rows, final_columns, labels = await self._aggregate_for_table(...)  # Line 729
  ```
- **Problem**: Only awaits methods that themselves have no async operations.
- **Fix**: Change to `def _generate_table_report()` after fixing #3 and #5.

### 🔴 7. ReportService._generate_comparison_report - async with no async operations
- **File**: `src/services/reports/service.py:333-427`
- **Code**:
  ```python
  async def _generate_comparison_report(self, ...):  # Line 333
      # ... 95 lines of string building, no await anywhere
  ```
- **Problem**: Method builds markdown strings, no async operations.
- **Fix**: Change to `def _generate_comparison_report()`.

### 🟠 8. ExtractionWorker.check_cancellation - async wrapper for sync call
- **File**: `src/services/extraction/worker.py:96-97`
- **Code**:
  ```python
  async def check_cancellation() -> bool:
      return self.job_repo.is_cancellation_requested(job.id)
  ```
- **Problem**: Async callback with no await inside.
- **Complication**: The pipeline.py type signature (line 319) expects `Callable[[], Awaitable[bool]]`, so this MUST be async to satisfy the interface.
- **Fix**: Either keep as-is (works but wasteful) OR change pipeline.py to accept sync callbacks too.

---

## False Positives (Not Issues)

### ✅ EntityExtractor.extract - correctly async
- **File**: `src/services/knowledge/extractor.py:134-184`
- **Reason**: Line 156 calls `await self._llm_client.extract_entities()` which is truly async (LLM API call).
- **Note**: The `await self._store_entities()` on line 163 is wasteful but not wrong.

### ✅ EmbeddingRecoveryService.recover_batch - correctly async
- **File**: `src/services/extraction/embedding_recovery.py:82-150`
- **Reason**:
  - Line 107: `await self._embedding_service.embed_batch()` - truly async
  - Line 124: `await self._qdrant_repo.upsert_batch()` - truly async

### ✅ EmbeddingRecoveryService.run_recovery - correctly async
- **File**: `src/services/extraction/embedding_recovery.py:152-201`
- **Reason**: Calls `await self.recover_batch()` which is truly async.

### ✅ cancel_job endpoint - correctly sync
- **File**: `src/api/v1/jobs.py:146-198`
- **Reason**: Only calls sync repository methods. Other endpoints (`cleanup_job`, `delete_job`) are async because they call `await cleanup_service.cleanup_job_artifacts()` which has truly async Qdrant/DLQ operations.

### ✅ ReportService.generate - correctly async
- **File**: `src/services/reports/service.py:64-158`
- **Reason**: Line 307 in `_generate_single_report` calls `await self._synthesizer.synthesize_facts()` which is truly async (LLM call).

### ✅ ReportService._generate_single_report - correctly async
- **File**: `src/services/reports/service.py:241-331`
- **Reason**: Line 307 calls `await self._synthesizer.synthesize_facts()` - truly async LLM call.

---

## Summary

| Finding | Status | Severity |
|---------|--------|----------|
| EntityExtractor._store_entities | **REAL ISSUE** | Medium |
| EmbeddingRecoveryService.find_orphaned_extractions | **REAL ISSUE** | Medium |
| ReportService._get_project_schema | **REAL ISSUE** | Low |
| ReportService._gather_data | **REAL ISSUE** | Medium |
| ReportService._aggregate_for_table | **REAL ISSUE** | Medium |
| ReportService._generate_table_report | **REAL ISSUE** | Low |
| ReportService._generate_comparison_report | **REAL ISSUE** | Medium |
| ExtractionWorker.check_cancellation | **REAL ISSUE** (interface constraint) | Low |
| EntityExtractor.extract | False positive | N/A |
| EmbeddingRecoveryService.recover_batch | False positive | N/A |
| EmbeddingRecoveryService.run_recovery | False positive | N/A |
| cancel_job endpoint | False positive | N/A |
| ReportService.generate | False positive | N/A |
| ReportService._generate_single_report | False positive | N/A |

**Total Real Issues**: 8
**Total False Positives**: 6

---

## Impact Assessment

These issues are **code quality problems**, not bugs. The code will run correctly because:
1. Python allows calling sync functions from async context without issues
2. Async functions with no await just run synchronously

However, the issues cause:
- **Misleading code** - developers expect async methods to have async operations
- **Minor overhead** - async machinery (coroutine creation) for sync code
- **Maintenance burden** - future developers may add unnecessary awaits
