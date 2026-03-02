# TODO: Fix Dual Import Path Issue in Tests

## Problem

`pyproject.toml` configures `pythonpath = ["src"]`, which adds `src/` to Python's module search path. This means every module can be imported two ways:

```python
from exceptions import QueueFullError        # via pythonpath → module "exceptions"
from src.exceptions import QueueFullError    # via package    → module "src.exceptions"
```

Python loads these as **two separate module objects** in `sys.modules`. Classes from each are **not the same object**:

```python
from exceptions import QueueFullError as A
from src.exceptions import QueueFullError as B
assert A is not B  # True — different class objects!
```

## Impact

This silently breaks:
1. **`pytest.raises(ExceptionClass)`** — won't catch the exception if imported via the wrong path
2. **`isinstance()` checks** — returns False across the boundary
3. **`is` identity checks** — always False across boundary
4. **Monkey-patching in tests** — patching `src.foo.Bar` doesn't affect code that imported `from foo import Bar`

### Confirmed Broken

- `tests/test_extraction_pipeline.py` — `QueueFullError` imported via `src.services.extraction.pipeline` didn't match the exception raised at runtime (which came from `exceptions` module). Fixed as part of the Phase 5 exception centralization work.

### Potentially Broken (needs audit)

Any test using `from src.X import Y` where `Y` is:
- An exception class used in `pytest.raises()`
- A class used in `isinstance()` checks
- A class or function being patched with `unittest.mock.patch`

## Scope

**103 occurrences** of `from src.` imports across **28 test files**.

Key files by count:
- `tests/test_llm_queue.py` — 24 occurrences
- `tests/test_logging.py` — 11 occurrences
- `tests/test_camoufox_browser_pool.py` — 10 occurrences
- `tests/test_llm_worker_prompts.py` — 8 occurrences
- `tests/test_extraction_deduplicator.py` — 6 occurrences
- `tests/test_llm_worker_dlq.py` — 5 occurrences
- `tests/test_scheduler_llm_queue.py` — 4 occurrences

## Root Cause

The codebase mixes two import conventions:
- **Source code** uses bare imports: `from exceptions import X`, `from services.llm.client import Y`
- **Some tests** use `src.`-prefixed imports: `from src.exceptions import X`, `from src.services.llm.client import Y`

Both work for importing, but they create different module identities.

## Fix

**Normalize all test imports to use bare paths** (matching source code convention):

```python
# WRONG — creates src.exceptions module
from src.exceptions import QueueFullError

# CORRECT — matches runtime module identity
from exceptions import QueueFullError
```

This is safe because `pythonpath = ["src"]` ensures bare imports resolve correctly in tests.

### Execution Plan

1. Find-and-replace `from src.` → `from ` in all test files
2. Run full test suite to verify no regressions
3. Verify no `import src.` or `src.` in mock patch targets

### Risk

**Low** — this is a mechanical find-and-replace. The only risk is if any test deliberately relies on the dual-module behavior (unlikely).

## Priority

**Medium** — Most tests work today because they import concrete values (data classes, functions) where identity doesn't matter. But exception catching and mocking ARE silently affected, making some tests less reliable than they appear.
