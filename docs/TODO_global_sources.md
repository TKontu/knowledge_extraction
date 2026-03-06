# Global Sources with Project-Scoped Extractions — Implementation Spec

Version: 1.0 (2026-03-04)
Status: **⬜ Not started**
Depends on: Domain dedup validated (✅), extraction pipeline fixes (✅)

## Problem

Sources (scraped web content) are currently tied 1:1 to projects via `sources.project_id` FK. But scraped content is domain-intrinsic — `www.dbsantasalo.com` returns the same markdown regardless of which project crawled it. This causes:

- **Can't re-extract without re-scraping** — sources locked to one project
- **Boilerplate fingerprints duplicated** per project for the same domain
- **Re-extraction appends duplicates** instead of replacing (found during validation on 2026-03-04)
- **No safe way to re-extract a single source_group** without affecting others
- **Same URL crawled by two projects** creates two copies of identical content

**Measured impact**: 12,069 sources across 249 domains in the drivetrain project. If a second project targets the same domains, all content must be re-scraped and re-cleaned.

## Architecture

### Current

```
[Project]
    │ owns (FK, CASCADE)
    ├── [Sources] ← content + cleaned_content + status (EXTRACTED/SKIPPED)
    │       │ owns (FK, CASCADE)
    │       └── [Extractions]
    └── [DomainBoilerplate] ← per (project, domain)
```

### Target

```
[Sources] ← GLOBAL: one copy per URI, content + cleaned_content
    │
    ├── [ProjectSources] ← junction: which projects use which sources
    │       │
    │       └── [Project]
    │               │ owns (FK, CASCADE)
    │               ├── [Extractions] ← project-specific, schema-dependent
    │               └── [Entities] ← project-specific
    │
    └── [DomainBoilerplate] ← GLOBAL: one fingerprint per domain
```

### Cascade behavior

| Parent deleted | Effect |
|---|---|
| Project | CASCADE → project_sources links, extractions, entities. **Sources UNTOUCHED.** |
| Source | CASCADE → project_sources links, extractions for that source. |

---

## Phase 1: Data Model + Migration ⬜

**Risk: HIGH** — Schema change, data migration with dedup. Do first, test on DB backup.

### 1a. New table: `project_sources`

**File: `src/orm_models.py`** — Add new ORM model:

```python
class ProjectSource(Base):
    """Junction table linking projects to their sources."""
    __tablename__ = "project_sources"
    __table_args__ = (
        UniqueConstraint("project_id", "source_id", name="uq_project_sources_project_source"),
        Index("ix_project_sources_project_group", "project_id", "source_group"),
        Index("ix_project_sources_source_id", "source_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("sources.id", ondelete="CASCADE"), nullable=False
    )
    source_group: Mapped[str] = mapped_column(Text, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    created_by_job_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID, ForeignKey("jobs.id", ondelete="SET NULL"), nullable=True
    )

    # Relationships
    project: Mapped["Project"] = relationship("Project", back_populates="source_links")
    source: Mapped["Source"] = relationship("Source", back_populates="project_links")
```

**Why `source_group` is denormalized here**: The extraction pipeline hot path queries `WHERE project_id = ? AND source_group IN (?)`. Without denormalization, every such query requires a JOIN to sources. With 12K+ sources this is fine today, but the denormalization avoids it entirely and costs only a TEXT column.

### 1b. Modify `sources`: remove `project_id`

**File: `src/orm_models.py`** — Source model changes:

- Remove `project_id` mapped column and FK
- Change unique constraint from `(project_id, uri)` to `(uri)`
- Add relationship: `project_links: Mapped[list["ProjectSource"]] = relationship(...)`
- Remove old `project` relationship

### 1c. Modify `domain_boilerplate`: remove `project_id`

**File: `src/orm_models.py`** — DomainBoilerplate model changes:

- Remove `project_id` mapped column and FK
- Change unique constraint from `(project_id, domain)` to `(domain)`

### 1d. Modify `SourceStatus`

**File: `src/constants.py`**:

```python
class SourceStatus(StrEnum):
    PENDING = "pending"      # Created, no content yet
    READY = "ready"          # Content scraped successfully
    FAILED = "failed"        # Scrape failed
    COMPLETED = "completed"  # Alias for ready (backward compat)
```

Remove: `EXTRACTED`, `PARTIAL`, `SKIPPED` — these are project-specific extraction states, not content states.

**"Has this source been extracted for project X?"** becomes:
```python
EXISTS (SELECT 1 FROM extractions WHERE source_id = s.id AND project_id = :pid)
```

### 1e. New repository: `ProjectSourceRepository`

**New file: `src/services/storage/repositories/project_source.py`**

```python
class ProjectSourceRepository:
    def __init__(self, session: Session): ...

    def link(self, project_id: UUID, source_id: UUID, source_group: str,
             created_by_job_id: UUID | None = None) -> tuple[ProjectSource, bool]:
        """Link a source to a project. Returns (link, created)."""
        # INSERT ... ON CONFLICT (project_id, source_id) DO NOTHING

    def unlink(self, project_id: UUID, source_id: UUID) -> bool: ...

    def get_sources_for_project(self, project_id: UUID,
                                 source_groups: list[str] | None = None) -> list[Source]:
        """Query sources linked to a project, optionally filtered by source_group."""

    def get_source_groups(self, project_id: UUID) -> list[tuple[str, int]]:
        """Get distinct source_groups and counts for a project."""

    def get_domains_for_project(self, project_id: UUID) -> list[tuple[str, int]]:
        """Get distinct domains and page counts for a project (via JOIN)."""
```

### 1f. Alembic migration

**New file: `alembic/versions/YYYYMMDD_global_sources.py`**

6 steps in a single migration:

```
Step 1: CREATE TABLE project_sources (...)
Step 2: INSERT INTO project_sources (id, project_id, source_id, source_group, ...)
        SELECT gen_random_uuid(), s.project_id, s.id, s.source_group, ...
        FROM sources s
Step 3: Deduplicate sources by URI
        - Rank by: has content (prefer non-null), oldest created_at
        - Remap project_sources.source_id and extractions.source_id to canonical
        - Delete duplicate source rows
        - Dedup project_sources (same project+source after remap)
Step 4: Deduplicate domain_boilerplate by domain
        - Keep row with most pages_analyzed per domain
Step 5: ALTER TABLE sources DROP COLUMN project_id
        DROP old unique constraint, ADD unique(uri)
        ALTER TABLE domain_boilerplate DROP COLUMN project_id
        DROP old unique constraint, ADD unique(domain)
Step 6: UPDATE sources SET status = 'ready'
        WHERE status IN ('extracted', 'skipped', 'partial')
```

**Safety**: Backup DB first (`pg_dump`). Runs in transaction. Estimated <30s for 12K sources.

### Tests for Phase 1

```
TestProjectSourceRepository:
    - test_link_creates_new_link
    - test_link_idempotent (same project+source, no error)
    - test_unlink_removes_link
    - test_get_sources_for_project
    - test_get_sources_filtered_by_source_group
    - test_get_source_groups_returns_counts
    - test_project_delete_cascades_links_not_sources

TestMigration:
    - test_migration_on_prod_backup (manual, run once)
```

---

## Phase 2: Scraping Layer ⬜

**Risk: MEDIUM** — Changes source creation, must handle re-scrape safety.

### 2a. `SourceRepository` changes

**File: `src/services/storage/repositories/source.py`**

Change `create()` to `upsert()` with global URI uniqueness:

```python
def upsert(self, uri: str, source_group: str, content: str | None = None,
           title: str | None = None, metadata: dict | None = None,
           **kwargs) -> tuple[Source, bool]:
    """Upsert source by URI. Re-scrape safe.

    ON CONFLICT (uri) DO UPDATE:
      - content: only if new is non-empty AND different
      - source_group: first-writer-wins (never overwrite)
      - metadata: merge (||), don't replace
      - status: never regress from READY to PENDING
    Returns (source, created).
    """
```

### 2b. Worker changes

**Files: `src/services/scraper/worker.py`, `src/services/scraper/crawl_worker.py`**

```python
# CURRENT:
self.source_repo.create(project_id=project_id, uri=url, source_group=company, ...)

# NEW:
source, created = self.source_repo.upsert(uri=url, source_group=company, content=markdown, ...)
self.project_source_repo.link(project_id=project_id, source_id=source.id, source_group=company)
```

### 2c. Re-scrape safety rules

1. Existing sources with content are **never overwritten** by empty/failed scrape
2. New URLs from deeper crawl are **appended** (new rows)
3. Re-scrape of existing URL updates content **only if new content is non-empty and different**
4. `source_group` on Source uses **first-writer-wins** (never changed on re-scrape)
5. `metadata` is **merged** (`||`), not replaced
6. `status` **never regresses** from READY/COMPLETED to PENDING

### Tests for Phase 2

```
TestSourceUpsert:
    - test_creates_new_source
    - test_upsert_does_not_overwrite_content_with_empty
    - test_upsert_updates_content_when_different
    - test_upsert_preserves_source_group (first-writer-wins)
    - test_upsert_merges_metadata
    - test_upsert_does_not_regress_status

TestWorkerIntegration:
    - test_scrape_creates_source_and_link
    - test_re_scrape_appends_new_urls
    - test_re_scrape_does_not_duplicate_existing_urls
```

---

## Phase 3: Extraction Pipeline ⬜

**Risk: MEDIUM** — Core pipeline query change + re-extraction behavior.

### 3a. Pipeline query change

**File: `src/services/extraction/pipeline.py`** — `extract_project()`:

```python
# CURRENT (line 237-244):
stmt = select(Source).where(
    Source.project_id == project_id,
    Source.status.in_(allowed_statuses),
    Source.content.isnot(None),
)
if source_groups:
    stmt = stmt.where(Source.source_group.in_(source_groups))

# NEW:
stmt = (
    select(Source)
    .join(ProjectSource, Source.id == ProjectSource.source_id)
    .where(
        ProjectSource.project_id == project_id,
        Source.content.isnot(None),
    )
)
if source_groups:
    stmt = stmt.where(ProjectSource.source_group.in_(source_groups))

# Skip-extracted check (replaces SourceStatus-based check):
if skip_extracted:
    already = (
        select(Extraction.id)
        .where(Extraction.source_id == Source.id, Extraction.project_id == project_id)
        .exists()
    )
    stmt = stmt.where(~already)
```

### 3b. Re-extraction: delete-then-insert

**File: `src/services/storage/repositories/extraction.py`** — Add method:

```python
def delete_by_project_and_source_groups(
    self, project_id: UUID, source_groups: list[str]
) -> int:
    """Delete extractions for specific source_groups within a project."""
    # DELETE FROM extractions
    # WHERE project_id = :pid AND source_group IN (:groups)

def delete_by_project(self, project_id: UUID) -> int:
    """Delete ALL extractions for a project."""
```

**File: `src/services/extraction/pipeline.py`** — In `extract_project()`, before processing:

```python
if not skip_extracted:  # force=True
    if source_groups:
        deleted = extraction_repo.delete_by_project_and_source_groups(project_id, source_groups)
    else:
        deleted = extraction_repo.delete_by_project(project_id)
    logger.info("deleted_old_extractions", count=deleted)
```

### 3c. `extract_source()` explicit project_id

**File: `src/services/extraction/pipeline.py`** — Currently uses `source.project_id`:

```python
# Line 123: project_id=source.project_id
# Change to: project_id=project_id (passed as parameter)
```

### Tests for Phase 3

```
TestExtractionPipelineQuery:
    - test_query_joins_through_project_sources
    - test_source_groups_filter_uses_junction
    - test_skip_extracted_checks_extractions_table

TestReExtraction:
    - test_force_with_source_groups_deletes_only_those_groups
    - test_force_without_source_groups_deletes_all
    - test_re_extract_preserves_other_groups_extractions
    - test_no_duplicate_extractions_after_re_extract
```

---

## Phase 4: Boilerplate Dedup ⬜

**Risk: LOW** — Remove project_id scoping, make global.

### 4a. `DomainBoilerplateRepository` changes

**File: `src/services/storage/repositories/domain_boilerplate.py`**

- `upsert(domain, ...)` — remove `project_id` param
- `get(domain)` — remove `project_id` param
- `list_by_project()` → `list_all()` or `list_by_domains(domains: list[str])`
- `delete_by_project()` → `delete_by_domain(domain)`

### 4b. `DomainDedupService` changes

**File: `src/services/extraction/domain_dedup.py`**

- `analyze_domain(domain, ...)` — queries ALL sources for that domain globally (no project filter)
- `analyze_project(project_id, ...)` — finds domains via project_sources junction, calls `analyze_domain()` for each. Since boilerplate is global, this benefits all projects sharing those domains.
- `get_domain_stats()` — returns global stats, optionally filtered by domains relevant to a project

### 4c. API + MCP changes

**File: `src/api/v1/dedup.py`** — `project_id` becomes optional. Can analyze boilerplate globally or scoped to a project's domains.

**File: `src/ke_mcp/tools/dedup.py`** — `project_id` becomes optional.

### Tests for Phase 4

```
TestGlobalBoilerplate:
    - test_analyze_domain_uses_all_sources_globally
    - test_analyze_project_finds_domains_via_junction
    - test_boilerplate_benefits_all_projects_sharing_domain
    - test_upsert_without_project_id
```

---

## Phase 5: API + MCP Cleanup ⬜

**Risk: LOW** — Update query patterns, maintain interface compatibility.

### 5a. Source listing endpoints

**File: `src/api/v1/sources.py`**

- `list_sources(project_id)` → JOIN through project_sources
- `get_source_summary(project_id)` → GROUP BY via junction

### 5b. Extraction endpoints

**File: `src/api/v1/extraction.py`**

- Source validation: check project_sources link exists (not source.project_id)
- Re-extraction: support `source_groups` filter for targeted delete+re-extract

### 5c. MCP tools

**File: `src/ke_mcp/tools/extraction.py`** — Interface unchanged: `extract_knowledge(project_id, source_groups, force)`. The `source_groups` param now cleanly deletes only those groups' extractions before re-extracting.

**File: `src/ke_mcp/tools/sources.py`** — `list_sources(project_id)` unchanged interface, queries through junction internally.

### 5d. Job cleanup changes

**File: `src/services/scraper/scheduler.py`** or wherever job cleanup lives:

- Change `delete_by_job_id` to delete project_sources links (not sources)
- Only delete orphan sources (no remaining project_sources links)

### Tests for Phase 5

```
TestAPICompatibility:
    - test_list_sources_returns_same_results
    - test_get_source_summary_returns_same_structure
    - test_extract_knowledge_mcp_interface_unchanged
    - test_project_delete_preserves_global_sources
```

---

## Implementation Order

| Phase | What | Risk | Estimated Files |
|-------|------|------|----------------|
| 1 | Data model + migration | HIGH | 4 modified, 2 new |
| 2 | Scraping layer | MEDIUM | 3 modified |
| 3 | Extraction pipeline | MEDIUM | 3 modified |
| 4 | Boilerplate dedup | LOW | 3 modified |
| 5 | API + MCP cleanup | LOW | 5+ modified |

**Total**: ~18 files modified/created, 1 migration.

---

## Key Design Decisions

### Why `source_group` stays on Source AND on ProjectSource

- **On Source**: Intrinsic property — "this page came from Acme Corp's website". Set once during first scrape.
- **On ProjectSource**: Denormalized for query performance. The extraction pipeline hot path filters by `(project_id, source_group)`. Without denormalization, every such query needs a JOIN through sources.
- **In practice**: Both values will always match. The denormalization is a performance optimization, not a semantic difference.

### Why SourceStatus loses EXTRACTED/SKIPPED

These are **project-specific** states (a source can be "extracted" for project A but not for project B). With global sources, the source's status should only reflect its **content readiness**:
- PENDING → no content yet
- READY → content available
- FAILED → scrape failed

The "already extracted?" check moves to: `EXISTS (extractions WHERE source_id AND project_id)`.

### Why boilerplate becomes global

Domain fingerprints are **domain-intrinsic**: the same nav, footer, cookie banner appears regardless of which project crawled the site. Sharing fingerprints globally means:
- Boilerplate analysis done for project A automatically benefits project B
- One fingerprint per domain, not one per (project, domain)
- `cleaned_content` on Source is global — all projects get the clean version

### Re-scrape safety: first-writer-wins + no content regression

- `source_group`: Never overwritten (if two crawls target the same URL from different companies, the first company's label sticks)
- `content`: Only updated if new content is non-empty AND different from existing
- `status`: Never regresses from READY/COMPLETED to PENDING
- `metadata`: Merged (PostgreSQL `||` operator), not replaced

---

## Key Risks

| Risk | Mitigation |
|---|---|
| Migration data loss (URI dedup) | Backup first. Keep most complete source. Preserve all project_sources links. |
| Performance (JOIN vs direct FK) | Index `(project_id, source_group)` on project_sources. 12K sources is trivial. |
| Classification columns on Source | Keep as-is. Content-intrinsic for common case. Re-extraction re-classifies. |
| Job cleanup deleting shared sources | Delete project_sources links, only delete orphan sources. |
| Same URL, different companies | First-writer-wins on Source.source_group. project_sources.source_group can differ. |
| Cascade on project delete | Only project_sources + extractions + entities cascade. Sources untouched. |

---

## Files Summary

**New (2):**
- `src/services/storage/repositories/project_source.py` — ProjectSource CRUD
- `alembic/versions/YYYYMMDD_global_sources.py` — 6-step migration

**Modified (~16):**
- `src/orm_models.py` — Add ProjectSource, remove project_id from Source + DomainBoilerplate
- `src/constants.py` — Simplify SourceStatus
- `src/services/storage/repositories/source.py` — Global upsert, remove project_id queries
- `src/services/storage/repositories/domain_boilerplate.py` — Remove project_id
- `src/services/storage/repositories/extraction.py` — Add delete_by_project_and_source_groups
- `src/services/extraction/pipeline.py` — JOIN through project_sources, explicit project_id
- `src/services/extraction/domain_dedup.py` — Global boilerplate analysis
- `src/services/extraction/worker.py` — Pass project_id, handle source_group deletion
- `src/services/scraper/worker.py` — Upsert + link pattern
- `src/services/scraper/crawl_worker.py` — Upsert + link pattern
- `src/api/v1/sources.py` — Query through junction
- `src/api/v1/extraction.py` — Source_group-scoped re-extraction
- `src/api/v1/dedup.py` — Global boilerplate API
- `src/ke_mcp/tools/dedup.py` — Optional project_id
- `src/ke_mcp/tools/extraction.py` — Unchanged interface
- `src/ke_mcp/tools/sources.py` — Query through junction
