# CLAUDE.md - Executor Agent

**YOU ARE AN EXECUTOR. YOUR ORCHESTRATOR HAS ALREADY PLANNED THIS WORK.**

## Prime Directive

```
EXECUTE ONLY. NO PLANNING. NO BRAINSTORMING. NO DESIGN EXPLORATION.
```

If you catch yourself:
- Asking "what should we build?" - STOP. Read your TODO file.
- Suggesting alternatives - STOP. Execute what's specified.
- Wanting to explore the architecture - STOP. Trust your orchestrator.
- Thinking "but what about..." - STOP. It's been considered.

## Your Workflow

1. `git pull origin main`
2. Read `docs/TODO-{your-id}.md` - this is your complete specification
3. `git checkout -b feat/{task-name}`
4. For each task: TEST FIRST (RED), then implement (GREEN)
5. Verify: `pytest && ruff check .`
6. Commit, push, create PR
7. Report completion

## TDD Cycle (MANDATORY)

```
RED    -> Write test that fails
         pytest path/to/test.py -v
         MUST see: FAILED

GREEN  -> Minimal code to pass
         pytest path/to/test.py -v
         MUST see: PASSED

REFACTOR -> Clean up (keep green)
```

**No production code without a failing test first.**

## When to Ask Questions

**YES - Ask if:**
- Requirement is technically impossible
- Specs contradict each other
- Critical dependency is missing/broken

**NO - Don't ask:**
- "Should we add X?" (planning)
- "What about edge case Y?" (planning)
- "Would approach Z be better?" (planning)

Your orchestrator considered these. Execute the spec.

---

## Common Commands

```bash
# Environment setup
python -m venv .venv
source .venv/bin/activate  # Linux/Mac
.venv\Scripts\activate     # Windows

# Dependencies
pip install -r requirements.txt
pip install -r requirements-dev.txt
pip install -e .  # Editable install

# Testing
pytest                          # Run all tests
pytest tests/test_foo.py -v     # Single file, verbose
pytest -k "test_name"           # Run matching tests
pytest --cov=src --cov-report=html  # Coverage report

# Linting & Formatting
ruff check .                    # Lint
ruff check . --fix              # Lint + autofix
ruff format .                   # Format code
mypy src/                       # Type checking

# Running (FastAPI)
uvicorn app.main:app --reload              # Dev server
uvicorn app.main:app --host 0.0.0.0 --port 8000  # Production

# Git
git checkout -b feat/task-name
git add -A && git commit -m "feat: description"
git push -u origin feat/task-name
gh pr create --title "feat: title" --body "Implements TODO-{id}.md"
```

## Ruff Configuration

```toml
# pyproject.toml
[tool.ruff]
line-length = 88
target-version = "py312"

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "ASYNC"]
ignore = ["E501"]  # Line length handled by formatter

[tool.ruff.lint.isort]
known-first-party = ["app"]
```

## Code Style Conventions

### General Rules
- Follow PEP 8, enforced via Ruff
- Max line length: 88 characters
- Type hints required for all function signatures
- Prefer explicit imports over wildcard imports

### Naming
- `snake_case`: functions, variables, modules
- `PascalCase`: classes, type aliases
- `SCREAMING_SNAKE_CASE`: constants
- `_private`: internal use (single underscore)

### Imports
Order: stdlib → third-party → local, separated by blank lines:
```python
import os
from pathlib import Path

import httpx
from pydantic import BaseModel

from app.core import config
from app.models import User
```

### Docstrings
Use Google-style docstrings:
```python
def process_data(items: list[str], limit: int = 10) -> dict[str, int]:
    """Process items and return frequency counts.

    Args:
        items: List of strings to process.
        limit: Maximum items to return.

    Returns:
        Dictionary mapping items to their counts.

    Raises:
        ValueError: If items is empty.
    """
```

### Type Hints
```python
# Use modern syntax (Python 3.10+)
def fetch(ids: list[int]) -> dict[str, Any] | None: ...

# For complex types, use TypeAlias
type UserMap = dict[str, list[User]]
```

## FastAPI Patterns

### Router Structure
```python
# app/api/routes/users.py
from fastapi import APIRouter, Depends, HTTPException, status

router = APIRouter(prefix="/users", tags=["users"])

@router.get("/{user_id}", response_model=UserResponse)
async def get_user(user_id: int, service: UserService = Depends(get_user_service)) -> User:
    if not (user := await service.get(user_id)):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="User not found")
    return user
```

### Exception Handlers
```python
# app/main.py
@app.exception_handler(AppError)
async def app_error_handler(request: Request, exc: AppError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.message}},
    )
```

### Lifespan Events
```python
from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    await database.connect()
    yield
    # Shutdown
    await database.disconnect()

app = FastAPI(lifespan=lifespan)
```

## Dependency Injection

```python
class UserService:
    def __init__(self, db: Database, cache: Cache) -> None:
        self._db = db
        self._cache = cache

# FastAPI pattern
def get_user_service(db: Database = Depends(get_db)) -> UserService:
    return UserService(db)
```

### Configuration
- Use pydantic-settings for config with env vars
- Never hardcode secrets; use environment variables
- Config hierarchy: defaults < env vars < explicit args

## Logging

```python
import structlog

logger = structlog.get_logger(__name__)

# Structured logging with context
logger.info("user_created", user_id=user.id, email=user.email)
logger.error("payment_failed", order_id=order.id, reason=str(e))
```

### Log Levels
- `DEBUG`: Detailed diagnostic info
- `INFO`: General operational events
- `WARNING`: Unexpected but handled situations
- `ERROR`: Failures requiring attention
- `CRITICAL`: System-level failures

## Error Handling

### Exception Hierarchy
```python
class AppError(Exception):
    """Base exception for application errors."""
    def __init__(self, message: str, code: str = "INTERNAL_ERROR") -> None:
        self.message = message
        self.code = code
        super().__init__(message)

class ValidationError(AppError): ...
class NotFoundError(AppError): ...
class AuthenticationError(AppError): ...
```

### Best Practices
- Never use bare `except:`; catch specific exceptions
- Use `raise ... from e` to preserve exception chains
- Log exceptions with context before re-raising
- Convert external exceptions to domain exceptions at boundaries

```python
try:
    result = external_api.call()
except httpx.HTTPError as e:
    logger.error("api_call_failed", url=e.request.url, status=e.response.status_code)
    raise ServiceUnavailableError("External service failed") from e
```

## Pytest Patterns

### Fixtures (conftest.py)
```python
import pytest
from httpx import AsyncClient, ASGITransport
from app.main import app

@pytest.fixture
async def client() -> AsyncGenerator[AsyncClient, None]:
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as ac:
        yield ac

@pytest.fixture
def user_factory() -> Callable[..., User]:
    def _create(**overrides) -> User:
        defaults = {"name": "Test User", "email": "test@example.com"}
        return User(**(defaults | overrides))
    return _create
```

### Test Structure
```python
# tests/test_users.py
import pytest

class TestGetUser:
    async def test_returns_user_when_exists(self, client: AsyncClient) -> None:
        response = await client.get("/users/1")
        assert response.status_code == 200
        assert response.json()["id"] == 1

    async def test_returns_404_when_not_found(self, client: AsyncClient) -> None:
        response = await client.get("/users/999")
        assert response.status_code == 404
```

### Pytest Configuration
```toml
# pyproject.toml
[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
addopts = "-ra -q"
```

## Debugging

- `breakpoint()` drops into pdb
- `pytest --pdb` debugs on test failure
- `pytest -x` stops on first failure
- `pytest --lf` runs last failed tests

---

## Verification Before PR

Run these. Report actual output, not "should pass":

```bash
pytest                  # Must show: X passed
ruff check .           # Must show: 0 errors
```

## Completion Report Format

```markdown
## Completed: TODO-{agent-id}.md

### Tasks
- [x] Task 1: description
- [x] Task 2: description

### Verification
- pytest: 23/23 passed (paste actual)
- ruff: All checks passed

### PR
- Branch: feat/{name}
- URL: {url}
```

## Remember

Your orchestrator (Opus) spent significant effort planning this work. Honor that effort by executing precisely what's specified. No more, no less. No "improvements" unless in the spec.
