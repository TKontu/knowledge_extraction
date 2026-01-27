# TODO: Data Model Improvements (Migrations)

**Agent:** agent-schema
**Branch:** `feat/schema-improvements`
**Priority:** medium

## Context

Two data model improvements are needed:
1. **Issue #8**: When entity extraction fails, extractions have no flag indicating incompleteness
2. **Issue #9**: Sources have no reference to the job that created them (no audit trail)

Both are simple column additions that can share one Alembic migration.

## Objective

Add `entities_extracted` flag to Extraction table and `created_by_job_id` foreign key to Source table via Alembic migration.

## Tasks

### 1. Create Alembic Migration

**File:** `alembic/versions/{timestamp}_add_extraction_and_source_columns.py` (new file)

**Requirements:**
- Use Alembic to generate migration: `cd src && alembic revision -m "add_extraction_and_source_columns"`
- Add two columns:

```python
def upgrade() -> None:
    # Add entities_extracted flag to extractions table
    op.add_column(
        'extractions',
        sa.Column('entities_extracted', sa.Boolean(), nullable=True, default=False)
    )

    # Add created_by_job_id to sources table
    op.add_column(
        'sources',
        sa.Column('created_by_job_id', UUID(), nullable=True)
    )

    # Add foreign key constraint
    op.create_foreign_key(
        'fk_sources_created_by_job_id',
        'sources', 'jobs',
        ['created_by_job_id'], ['id'],
        ondelete='SET NULL'
    )

    # Add index for job_id lookups
    op.create_index(
        'idx_sources_created_by_job_id',
        'sources',
        ['created_by_job_id']
    )

def downgrade() -> None:
    op.drop_index('idx_sources_created_by_job_id', table_name='sources')
    op.drop_constraint('fk_sources_created_by_job_id', 'sources', type_='foreignkey')
    op.drop_column('sources', 'created_by_job_id')
    op.drop_column('extractions', 'entities_extracted')
```

### 2. Update Extraction ORM Model

**File:** `src/orm_models.py`

**Requirements:**
- Find the `Extraction` class
- Add new column:

```python
entities_extracted: Mapped[bool] = mapped_column(
    Boolean, default=False, nullable=True
)
```

### 3. Update Source ORM Model

**File:** `src/orm_models.py`

**Requirements:**
- Find the `Source` class
- Add new column and relationship:

```python
created_by_job_id: Mapped[Optional[uuid.UUID]] = mapped_column(
    UUID, ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True, index=True
)

# Optional: Add relationship for convenience
created_by_job: Mapped[Optional["Job"]] = relationship("Job", foreign_keys=[created_by_job_id])
```

### 4. Update EntityExtractor to Set Flag

**File:** `src/services/knowledge/extractor.py`

**Requirements:**
- After successful entity extraction, set `entities_extracted = True` on the extraction
- Find where entities are linked to extraction and add the flag update
- This should be done in the `extract()` method after all entities are processed

**Implementation hint:**
```python
# At end of extract() method, after all entities linked:
# Need to update the extraction record
# The extractor has access to extraction_id but may need session access
# Check how other updates are done in this file
```

**Note:** If the extractor doesn't have direct session access, you may need to:
- Add `extraction_repo` as a dependency, OR
- Return the flag status and let the caller update it

### 5. Update Crawl Worker to Set Job ID

**File:** `src/services/scraper/crawl_worker.py`

**Requirements:**
- When creating sources from crawl results, set `created_by_job_id`
- Find where `Source` objects are created and add the job_id field

### 6. Write Tests

**File:** `tests/test_orm_models.py` (add to existing)

**Requirements:**
- Test that `Extraction.entities_extracted` defaults to False
- Test that `Source.created_by_job_id` can be set and retrieved
- Test foreign key relationship works

**File:** `tests/test_entity_extractor.py` (add to existing)

**Requirements:**
- Test that `entities_extracted` is set to True after successful extraction

## Constraints

- Do NOT modify any other tables or columns
- Do NOT change existing column definitions
- Columns should be nullable to avoid breaking existing data
- Do NOT run full test suite - only run tests in Test Scope below
- Do NOT lint entire codebase - only lint files in Lint Scope below
- Do NOT run the migration automatically - just create it

## Test Scope

**ONLY run these tests - do NOT run `pytest` without arguments:**

```bash
pytest tests/test_orm_models.py -v
pytest tests/test_entity_extractor.py -v
pytest tests/test_generalized_orm_models.py -v
```

## Lint Scope

**ONLY lint these files - do NOT run `ruff check src/`:**

```bash
ruff check src/orm_models.py src/services/knowledge/extractor.py src/services/scraper/crawl_worker.py
ruff format src/orm_models.py src/services/knowledge/extractor.py src/services/scraper/crawl_worker.py
```

## Verification

Before creating PR:

1. Migration file created and syntactically valid
2. `pytest tests/test_orm_models.py -v` - Must pass
3. `pytest tests/test_entity_extractor.py -v` - Must pass
4. `ruff check` on scoped files - Must be clean

## Definition of Done

- [ ] Alembic migration created with both columns
- [ ] `Extraction.entities_extracted` column added to ORM
- [ ] `Source.created_by_job_id` column added to ORM
- [ ] EntityExtractor sets flag after successful extraction
- [ ] Crawl worker sets job_id when creating sources
- [ ] Tests added and passing
- [ ] Lint clean (scoped)
- [ ] PR created with title: `feat: add entities_extracted flag and source job tracking`
