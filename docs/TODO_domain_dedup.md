# Domain-Level Boilerplate Deduplication — Implementation Spec

Version: 1.0 (2026-02-26)
Status: **Planning complete, not yet started**
Depends on: None (independent of `TODO_extraction_reliability.md`)

## Problem

Websites repeat content across pages — cookie banners, footers, product carousels, navigation sidebars. Firecrawl's `onlyMainContent` strips `<header>/<nav>/<footer>` HTML tags but misses `<div>`-based boilerplate. The existing per-page `content_cleaner.py` handles tracking pixels and link-density windowing but has zero cross-page awareness.

**Measured on 12,069 real pages across ~249 domains:**
- 19.5% of all stored content is boilerplate (23.7 MB of 121.4 MB)
- 101 domains (41%) have >10% boilerplate
- Worst cases: 70-90% boilerplate per page (cookie banners, repeated sidebars)
- 84,483 LLM extraction calls each process ~19.5% noise tokens

**Worst offenders:**

| Domain | Pages | Boilerplate % |
|--------|-------|---------------|
| www.wattdrive.com | 16 | 91.3% |
| www.psjengineering.co.za | 61 | 90.5% |
| www.grafimec.com.br | 53 | 82.9% |
| silge.ar | 85 | 77.3% |
| www.tammotor.fi | 53 | 74.3% |
| www.zero-max.com | 84 | 73.2% |
| www.bauergears.com | 100 | 52.4% |

**What the boilerplate is:** cookie consent banners (e.g., OneTrust banner = 4,484 chars on every bauergears.com page), product carousels, "related products" grids, footer legal text, repeated marketing callouts, sidebar navigation.

## Architecture

```
[Scrape/Crawl]
    │  sources.content = Firecrawl markdown (preserved, never modified)
    ▼
[Domain Boilerplate Analysis]  ← NEW (triggered via API/MCP, runs per project)
    │  For each domain: analyze all pages → compute fingerprint → strip blocks
    │  Stores fingerprint in domain_boilerplate table
    │  Stores result in sources.cleaned_content
    ▼
[Extraction Pipeline]  ← 2-line change
    │  Uses source.cleaned_content ?? source.content (gated by feature flag)
    │  Then existing per-page cleaning (Layer 1 + Layer 2) runs on top
    ▼
[LLM]
```

**Compatibility with extraction reliability plan:**
- Domain dedup removes cross-page repeating blocks (cookie banners, carousels, footers)
- Layer 1 (`content_cleaner.py`) removes per-page structural junk (tracking pixels, bare nav links)
- Layer 2 (`content_cleaner.py`) removes nav preamble based on link density
- All three are removal operations — overlap is harmless, they complement each other
- Grounding rules, confidence recalibration, classification all work better with less noise

---

## Phase A: Core Algorithm ⬜

**New file: `src/services/extraction/domain_dedup.py`**

Pure functions, no DB dependency. Follows `content_cleaner.py` pattern.

### Data structures

```python
@dataclass
class DomainFingerprintResult:
    """Result from fingerprint computation."""
    boilerplate_hashes: list[str]
    pages_analyzed: int
    blocks_total: int
    blocks_boilerplate: int

@dataclass
class DomainAnalysisResult:
    """Result from analyzing and cleaning a single domain."""
    domain: str
    pages_analyzed: int
    pages_cleaned: int
    blocks_boilerplate: int
    bytes_removed_total: int

@dataclass
class ProjectAnalysisResult:
    """Result from analyzing all domains in a project."""
    domains_analyzed: int
    domains_with_boilerplate: int
    total_pages_cleaned: int
    total_bytes_removed: int
    domain_results: list[DomainAnalysisResult]
```

### Functions

**`split_into_blocks(content: str, min_block_chars: int = 50) -> list[str]`**
- Split on `\n\s*\n` (2+ blank lines — standard markdown block separator)
- Filter blocks < `min_block_chars` after stripping
- Return list of block strings

**`hash_block(block: str) -> str`**
- Normalize: collapse `\s+` → single space, strip, lowercase
- SHA-256, truncated to 16 hex chars (64-bit — sufficient for per-domain scope)
- Normalization makes it tolerant to minor whitespace formatting differences

**`compute_domain_fingerprint(pages: list[str], threshold_pct=0.7, min_pages=5, min_block_chars=50) -> DomainFingerprintResult`**
- For each page: split into blocks, hash each, dedupe per-page (use `set()`)
- Count how many pages each block hash appears on
- Boilerplate threshold: `max(min_pages, int(len(pages) * threshold_pct))`
- Return hashes exceeding threshold + statistics

**`strip_boilerplate(content: str, boilerplate_hashes: set[str], min_block_chars=50) -> tuple[str, int]`**
- Split content preserving separators: `re.split(r'(\n\s*\n)', content)`
- For each part ≥ `min_block_chars`: hash it, skip if in `boilerplate_hashes`
- Collapse `\n{3,}` → `\n\n` after reconstruction
- Return `(cleaned_content, bytes_removed)`

### Design decisions

- **Block-level, not line-level**: Lines too granular (headings like "Products" would false-positive). Blocks capture full cookie banners, carousels, footer sections.
- **Exact-match hashing with whitespace normalization**: CMS-generated content produces identical blocks. Fuzzy matching (simhash/minhash) can be added later if needed but adds complexity with marginal benefit.
- **70% threshold**: Legitimate content rarely appears verbatim on 70%+ of pages within a domain. Conservative enough to avoid false positives.
- **Per-(project, domain) scoping**: Different projects may crawl different sections of the same domain, so fingerprints are project-specific.

### Tests — `tests/test_domain_dedup.py`

```
TestSplitIntoBlocks:
    - test_splits_on_double_newlines
    - test_filters_short_blocks
    - test_handles_empty_content
    - test_handles_single_block
    - test_preserves_block_content

TestHashBlock:
    - test_deterministic_output
    - test_whitespace_normalized (tabs, extra spaces produce same hash)
    - test_case_insensitive

TestComputeDomainFingerprint:
    - test_identifies_boilerplate_above_threshold
    - test_skips_when_below_min_pages
    - test_respects_threshold_pct
    - test_ignores_unique_blocks
    - test_handles_empty_pages
    - test_real_world_cookie_banner (same block on all pages → detected)

TestStripBoilerplate:
    - test_removes_boilerplate_blocks
    - test_preserves_unique_content
    - test_handles_empty_boilerplate_set
    - test_collapses_blank_lines_after_removal
    - test_returns_correct_bytes_removed
```

---

## Phase B: Data Model ⬜

### New table: `domain_boilerplate`

**Add to `src/orm_models.py`:**

```python
class DomainBoilerplate(Base):
    """Stores per-domain boilerplate fingerprints for deduplication."""

    __tablename__ = "domain_boilerplate"
    __table_args__ = (
        UniqueConstraint("project_id", "domain", name="uq_domain_boilerplate_project_domain"),
    )

    id: Mapped[uuid.UUID] = mapped_column(UUID, primary_key=True, default=uuid4)
    project_id: Mapped[uuid.UUID] = mapped_column(
        UUID, ForeignKey("projects.id", ondelete="CASCADE"), nullable=False
    )
    domain: Mapped[str] = mapped_column(Text, nullable=False, index=True)

    boilerplate_hashes: Mapped[list] = mapped_column(JSON, nullable=False, default=list)

    # Statistics
    pages_analyzed: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocks_total: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    blocks_boilerplate: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    bytes_removed_avg: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    # Algorithm parameters (for reproducibility)
    threshold_pct: Mapped[float] = mapped_column(Float, nullable=False, default=0.7)
    min_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    min_block_chars: Mapped[int] = mapped_column(Integer, nullable=False, default=50)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=lambda: datetime.now(UTC)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(UTC),
        onupdate=lambda: datetime.now(UTC),
    )
```

### New column on `sources`

```python
# Add to Source class, after raw_content
cleaned_content: Mapped[str | None] = mapped_column(Text, nullable=True)
```

**Note:** The existing `raw_content` column is always NULL but semantically different. `cleaned_content` communicates intent clearly. `content` = original Firecrawl markdown, `cleaned_content` = boilerplate-stripped version.

### Alembic migration: `alembic/versions/20260226_add_domain_boilerplate.py`

- `down_revision = '7b3e4f2a1c8d'` (current head)
- `op.create_table("domain_boilerplate", ...)` with all columns
- `op.add_column("sources", sa.Column("cleaned_content", sa.Text(), nullable=True))`
- Index on `(project_id, domain)` for domain_boilerplate table
- Downgrade drops column and table

---

## Phase C: Repository + Service ⬜

### Repository: `src/services/storage/repositories/domain_boilerplate.py`

Follows `source.py` pattern (takes `Session`, calls `flush()` not `commit()`):

```python
class DomainBoilerplateRepository:
    def __init__(self, session: Session): ...
    def upsert(self, project_id, domain, boilerplate_hashes, stats...) -> DomainBoilerplate
    def get(self, project_id, domain) -> DomainBoilerplate | None
    def list_by_project(self, project_id) -> list[DomainBoilerplate]
    def delete_by_project(self, project_id) -> int
```

### Service class: added to `src/services/extraction/domain_dedup.py`

```python
class DomainDedupService:
    def __init__(self, db: Session, settings: Settings): ...

    def analyze_domain(self, project_id, domain, threshold_pct=None, ...) -> DomainAnalysisResult
        # 1. Query sources for (project_id, domain) via metadata->>'domain'
        # 2. compute_domain_fingerprint([source.content for source in sources])
        # 3. Upsert fingerprint to domain_boilerplate table
        # 4. For each source: strip_boilerplate() → source.cleaned_content = result
        # 5. db.flush() (not commit — caller manages transaction)
        # 6. Return stats

    def analyze_project(self, project_id, source_groups=None, ...) -> ProjectAnalysisResult
        # 1. Get distinct domains for project
        # 2. Call analyze_domain for each
        # 3. db.commit() after all domains processed
        # 4. Return aggregated stats

    def get_domain_stats(self, project_id) -> list[dict]
        # Return per-domain statistics from domain_boilerplate table
```

### Source repository addition: `src/services/storage/repositories/source.py`

```python
def get_domains_for_project(self, project_id: UUID) -> list[tuple[str, int]]:
    """Get distinct domains and page counts for a project.
    Query: SELECT metadata->>'domain', COUNT(*) FROM sources
           WHERE project_id = :id AND content IS NOT NULL GROUP BY 1
    """
```

---

## Phase D: Config + API + MCP ⬜

### Config: `src/config.py`

```python
# Domain Boilerplate Deduplication
domain_dedup_enabled: bool = Field(
    default=False,
    description="Use cleaned_content (domain-deduped) for extraction when available",
)
domain_dedup_threshold_pct: float = Field(
    default=0.7, ge=0.1, le=1.0,
    description="Fraction of pages a block must appear in to be boilerplate",
)
domain_dedup_min_pages: int = Field(
    default=5, ge=2, le=100,
    description="Minimum pages per domain before boilerplate analysis runs",
)
domain_dedup_min_block_chars: int = Field(
    default=50, ge=10, le=500,
    description="Minimum characters for a content block to be considered",
)
```

### API endpoint: `src/api/v1/dedup.py` (new)

```
POST /api/v1/projects/{project_id}/analyze-boilerplate
    Query params: source_groups, threshold_pct, min_pages, min_block_chars
    → Runs analysis synchronously, returns per-domain stats

GET /api/v1/projects/{project_id}/boilerplate-stats
    → Returns per-domain stats from domain_boilerplate table
```

Register router in `src/main.py`.

### MCP tool: `src/ke_mcp/tools/dedup.py` (new)

```python
@mcp.tool()
async def analyze_boilerplate(
    project_id: Annotated[str, "Project UUID"],
    source_groups: Annotated[list[str] | None, "Optional source groups to analyze"] = None,
    threshold_pct: Annotated[float | None, "Boilerplate threshold (default 0.7)"] = None,
    min_pages: Annotated[int | None, "Min pages per domain (default 5)"] = None,
    ctx: Context = None,
) -> dict:
    """Analyze domains for boilerplate content and clean sources.

    Scans all pages per domain, identifies repeating blocks (cookie banners,
    navs, footers), stores cleaned versions. Extraction automatically uses
    cleaned content when domain_dedup_enabled=True.
    """
```

Register in `src/ke_mcp/tools/__init__.py`. Add client method to `src/ke_mcp/client.py`.

---

## Phase E: Pipeline Integration ⬜

**`src/services/extraction/pipeline.py`** — 2-line change

Line 536 (schema extraction path):
```python
# Before:
markdown=source.content,
# After:
markdown=(source.cleaned_content or source.content) if settings.domain_dedup_enabled else source.content,
```

Line 149 (generic extraction path):
```python
markdown=(source.cleaned_content or source.content) if settings.domain_dedup_enabled else source.content,
```

`domain_dedup_enabled=False` by default → zero behavior change until explicitly enabled.

---

## Phase F: Enable + Validate ⬜

1. Run `analyze_boilerplate` on Industrial Drivetrain Companies project (`99a19141-...`)
2. Inspect stats — expect ~19.5% average content reduction
3. Spot-check `cleaned_content` for bauergears.com (cookie banner removed?), flender.com (product carousel removed?)
4. Flip `domain_dedup_enabled=True`
5. Re-extract a test domain (e.g., David Brown Santasalo)
6. Compare extraction quality before/after

---

## Implementation Order

| Phase | What | Risk | Files |
|-------|------|------|-------|
| A | Core algorithm (pure functions + tests) | None | `domain_dedup.py`, `test_domain_dedup.py` |
| B | Data model (migration + ORM) | Low — additive | `orm_models.py`, migration |
| C | Repository + service class | Low — no pipeline changes | `domain_boilerplate.py` repo, `domain_dedup.py` service |
| D | Config + API + MCP | Low — new endpoints only | `config.py`, `dedup.py` api/mcp, `client.py`, `main.py` |
| E | Pipeline integration (2 lines) | Medium — gated by False flag | `pipeline.py` |
| F | Enable + validate | Operational | Config change only |

---

## Files Summary

**New (6):**
- `src/services/extraction/domain_dedup.py` — Core algorithm + service class
- `src/services/storage/repositories/domain_boilerplate.py` — Repository
- `alembic/versions/20260226_add_domain_boilerplate.py` — Migration
- `src/api/v1/dedup.py` — REST API endpoints
- `src/ke_mcp/tools/dedup.py` — MCP tool
- `tests/test_domain_dedup.py` — Tests

**Modified (7):**
- `src/orm_models.py` — DomainBoilerplate model + cleaned_content on Source
- `src/config.py` — 4 settings
- `src/services/extraction/pipeline.py` — 2 lines (prefer cleaned_content)
- `src/services/storage/repositories/source.py` — get_domains_for_project helper
- `src/main.py` — register dedup router
- `src/ke_mcp/client.py` — analyze_boilerplate method
- `src/ke_mcp/tools/__init__.py` — register dedup tools

---

## Key Technical Facts

- **Block separator**: `\n\s*\n` — standard markdown block boundary
- **Hash function**: SHA-256 truncated to 16 hex chars, with whitespace normalization + lowercasing
- **Threshold**: 70% of pages within a domain (configurable)
- **Min pages**: 5 per domain (below this, skip analysis — too few pages to identify patterns)
- **Min block chars**: 50 (below this, blocks are too short — headings, blank lines)
- **Scope**: Per (project_id, domain) — different projects may crawl different sections
- **Original content preserved**: `sources.content` is never modified; `cleaned_content` is a separate column
- **Feature flag**: `domain_dedup_enabled=False` by default — zero impact until explicitly enabled
- **Idempotent**: Re-running analysis updates all fingerprints and cleaned_content
