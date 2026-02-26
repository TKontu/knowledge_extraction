"""Domain-level boilerplate deduplication.

Identifies content blocks that repeat across pages within a domain
(cookie banners, footers, product carousels, navigation sidebars)
and strips them.

Core functions are pure (no DB dependency). DomainDedupService
orchestrates DB reads/writes.

Algorithm: block-level SHA-256 hashing with whitespace normalization.
Blocks appearing on ≥70% of pages (configurable) are boilerplate.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections import Counter
from dataclasses import dataclass, field
from urllib.parse import urlparse
from uuid import UUID

from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)


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
    sections_analyzed: int = 0
    sections_with_boilerplate: int = 0


@dataclass
class ProjectAnalysisResult:
    """Result from analyzing all domains in a project."""

    domains_analyzed: int
    domains_with_boilerplate: int
    total_pages_cleaned: int
    total_bytes_removed: int
    domain_results: list[DomainAnalysisResult]


def split_into_blocks(content: str, min_block_chars: int = 50) -> list[str]:
    """Split content on double-newlines, filtering short blocks.

    Args:
        content: Markdown text to split.
        min_block_chars: Minimum characters for a block after stripping.

    Returns:
        List of block strings (unstripped, preserving original whitespace).
    """
    if not content:
        return []
    raw_blocks = re.split(r"\n\s*\n", content)
    return [b for b in raw_blocks if len(b.strip()) >= min_block_chars]


def hash_block(block: str) -> str:
    """Hash a block with whitespace normalization + lowercasing.

    Normalizes whitespace (collapse \\s+ → single space) and lowercases
    before hashing, making it tolerant to minor formatting differences.

    Returns:
        16 hex chars (64-bit) SHA-256 truncation.
    """
    normalized = re.sub(r"\s+", " ", block).strip().lower()
    return hashlib.sha256(normalized.encode()).hexdigest()[:16]


def compute_domain_fingerprint(
    pages: list[str],
    threshold_pct: float = 0.7,
    min_pages: int = 5,
    min_block_chars: int = 50,
    threshold_floor: int | None = None,
) -> DomainFingerprintResult:
    """Compute boilerplate fingerprint for a set of pages from one domain.

    Args:
        pages: List of page content strings.
        threshold_pct: Fraction of pages a block must appear on to be boilerplate.
        min_pages: Minimum pages required before analysis runs (gate).
        min_block_chars: Minimum chars for a block to be considered.
        threshold_floor: Minimum absolute occurrences for a block to be
            boilerplate. Defaults to min_pages if not set. Use a lower
            value for section-level analysis where min_pages already
            gates entry.

    Returns:
        DomainFingerprintResult with boilerplate hashes and statistics.
    """
    n_pages = len(pages)
    if n_pages < min_pages:
        return DomainFingerprintResult(
            boilerplate_hashes=[],
            pages_analyzed=n_pages,
            blocks_total=0,
            blocks_boilerplate=0,
        )

    # Count how many pages each block hash appears on
    hash_page_count: Counter[str] = Counter()
    blocks_total = 0

    for page in pages:
        blocks = split_into_blocks(page, min_block_chars)
        blocks_total += len(blocks)
        # Dedupe per-page: a block appearing twice on one page counts once
        page_hashes = {hash_block(b) for b in blocks}
        hash_page_count.update(page_hashes)

    floor = threshold_floor if threshold_floor is not None else min_pages
    threshold = max(floor, int(n_pages * threshold_pct))
    boilerplate = [h for h, count in hash_page_count.items() if count >= threshold]

    return DomainFingerprintResult(
        boilerplate_hashes=boilerplate,
        pages_analyzed=n_pages,
        blocks_total=blocks_total,
        blocks_boilerplate=len(boilerplate),
    )


def extract_path_prefix(uri: str, depth: int = 1) -> str:
    """Extract the first path segments from a URI.

    Args:
        uri: Full URL string.
        depth: Number of path segments to include.

    Returns:
        Path prefix like "/segment" or "/" for root/empty.
    """
    try:
        path = urlparse(uri).path.rstrip("/")
    except Exception:
        return "/"
    if not path:
        return "/"
    segments = [s for s in path.split("/") if s]
    if not segments:
        return "/"
    prefix_parts = segments[:depth]
    return "/" + "/".join(prefix_parts)


@dataclass
class SectionFingerprintResult:
    """Result from section-level fingerprint computation."""

    section_results: dict[str, DomainFingerprintResult] = field(default_factory=dict)
    sections_analyzed: int = 0
    sections_with_boilerplate: int = 0
    total_section_hashes: int = 0


def compute_section_fingerprints(
    pages_with_uris: list[tuple[str, str]],
    threshold_pct: float = 0.7,
    min_pages: int = 5,
    min_block_chars: int = 50,
    path_depth: int = 1,
    exclude_hashes: set[str] | None = None,
    threshold_floor: int = 3,
) -> SectionFingerprintResult:
    """Compute boilerplate fingerprints per URL path section.

    Groups pages by URL path prefix, computes fingerprints per section,
    and filters out hashes already found at the domain level.

    Args:
        pages_with_uris: List of (content, uri) pairs.
        threshold_pct: Fraction of section pages a block must appear on.
        min_pages: Minimum pages per section before analysis runs (gate).
        min_block_chars: Minimum chars for a block.
        path_depth: Number of path segments for section grouping.
        exclude_hashes: Hashes to exclude (e.g. domain-level boilerplate).
        threshold_floor: Minimum absolute occurrences within a section
            for a block to be boilerplate. Lower than min_pages because
            min_pages already gates entry. Default 3.

    Returns:
        SectionFingerprintResult with per-section results.
    """
    if not pages_with_uris:
        return SectionFingerprintResult()

    exclude = exclude_hashes or set()

    # Group pages by path prefix
    sections: dict[str, list[str]] = {}
    for content, uri in pages_with_uris:
        prefix = extract_path_prefix(uri, depth=path_depth)
        sections.setdefault(prefix, []).append(content)

    result = SectionFingerprintResult()
    for prefix, pages in sections.items():
        if len(pages) < min_pages:
            continue

        fp = compute_domain_fingerprint(
            pages,
            threshold_pct=threshold_pct,
            min_pages=min_pages,
            min_block_chars=min_block_chars,
            threshold_floor=threshold_floor,
        )

        # Filter out domain-level hashes
        section_only = [h for h in fp.boilerplate_hashes if h not in exclude]
        fp_filtered = DomainFingerprintResult(
            boilerplate_hashes=section_only,
            pages_analyzed=fp.pages_analyzed,
            blocks_total=fp.blocks_total,
            blocks_boilerplate=len(section_only),
        )

        result.section_results[prefix] = fp_filtered
        result.sections_analyzed += 1
        if section_only:
            result.sections_with_boilerplate += 1
            result.total_section_hashes += len(section_only)

    return result


def strip_boilerplate(
    content: str,
    boilerplate_hashes: set[str],
    min_block_chars: int = 50,
) -> tuple[str, int]:
    """Remove boilerplate blocks from content.

    Splits content preserving separators, removes blocks whose hash
    matches the boilerplate set, and collapses excessive blank lines.

    Args:
        content: Page content to clean.
        boilerplate_hashes: Set of block hashes to remove.
        min_block_chars: Minimum chars for a block to be hash-checked.

    Returns:
        Tuple of (cleaned_content, bytes_removed).
    """
    if not content or not boilerplate_hashes:
        return content, 0

    # Split preserving separators (captured group stays in result list)
    parts = re.split(r"(\n\s*\n)", content)
    kept: list[str] = []
    original_len = len(content)

    for part in parts:
        # Separators and short parts pass through
        if len(part.strip()) < min_block_chars:
            kept.append(part)
            continue

        h = hash_block(part)
        if h not in boilerplate_hashes:
            kept.append(part)

    cleaned = "".join(kept)
    # Collapse 3+ newlines → 2
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    bytes_removed = original_len - len(cleaned)

    return cleaned, bytes_removed


class DomainDedupService:
    """Orchestrates domain boilerplate analysis with DB persistence."""

    def __init__(self, session: Session, settings: object | None = None):
        from services.storage.repositories.domain_boilerplate import (
            DomainBoilerplateRepository,
        )
        from services.storage.repositories.source import SourceRepository

        self._session = session
        self._settings = settings
        self._source_repo = SourceRepository(session)
        self._bp_repo = DomainBoilerplateRepository(session)

    def analyze_domain(
        self,
        project_id: UUID,
        domain: str,
        threshold_pct: float | None = None,
        min_pages: int | None = None,
        min_block_chars: int | None = None,
    ) -> DomainAnalysisResult:
        """Analyze a single domain for boilerplate and clean its sources.

        Args:
            project_id: Project UUID.
            domain: Domain to analyze.
            threshold_pct: Override default threshold.
            min_pages: Override default min pages.
            min_block_chars: Override default min block chars.

        Returns:
            DomainAnalysisResult with statistics.
        """
        t_pct = threshold_pct if threshold_pct is not None else 0.7
        m_pages = min_pages if min_pages is not None else 5
        m_chars = min_block_chars if min_block_chars is not None else 50

        # Load settings overrides if available
        if self._settings is not None:
            if threshold_pct is None:
                t_pct = getattr(self._settings, "domain_dedup_threshold_pct", t_pct)
            if min_pages is None:
                m_pages = getattr(self._settings, "domain_dedup_min_pages", m_pages)
            if min_block_chars is None:
                m_chars = getattr(
                    self._settings, "domain_dedup_min_block_chars", m_chars
                )

        # 1. Query sources for this domain
        sources = self._source_repo.get_by_project_and_domain(project_id, domain)
        pages = [s.content for s in sources if s.content]

        if not pages:
            return DomainAnalysisResult(
                domain=domain,
                pages_analyzed=0,
                pages_cleaned=0,
                blocks_boilerplate=0,
                bytes_removed_total=0,
            )

        # 2. Pass 1: Domain-level fingerprint
        fp = compute_domain_fingerprint(
            pages, threshold_pct=t_pct, min_pages=m_pages, min_block_chars=m_chars
        )
        domain_hashes = set(fp.boilerplate_hashes)

        # 3. Pass 2: Section-level fingerprints
        pages_with_uris = [
            (s.content, s.uri) for s in sources if s.content and s.uri
        ]
        section_fp = compute_section_fingerprints(
            pages_with_uris,
            threshold_pct=t_pct,
            min_pages=m_pages,
            min_block_chars=m_chars,
            exclude_hashes=domain_hashes,
        )

        # Build a lookup: prefix → section hashes
        section_hash_lookup: dict[str, set[str]] = {}
        all_section_hashes: set[str] = set()
        for prefix, sfp in section_fp.section_results.items():
            if sfp.boilerplate_hashes:
                h_set = set(sfp.boilerplate_hashes)
                section_hash_lookup[prefix] = h_set
                all_section_hashes |= h_set

        # 4. Strip boilerplate from each source using merged hashes
        bytes_removed_total = 0
        pages_cleaned = 0
        has_any_hashes = bool(domain_hashes or all_section_hashes)

        if has_any_hashes:
            for source in sources:
                if not source.content:
                    continue
                # Effective hashes = domain ∪ section(for this source's prefix)
                effective = set(domain_hashes)
                if source.uri and section_hash_lookup:
                    src_prefix = extract_path_prefix(source.uri)
                    if src_prefix in section_hash_lookup:
                        effective |= section_hash_lookup[src_prefix]

                cleaned, removed = strip_boilerplate(
                    source.content, effective, min_block_chars=m_chars
                )
                if removed > 0:
                    source.cleaned_content = cleaned
                    pages_cleaned += 1
                    bytes_removed_total += removed
                else:
                    source.cleaned_content = None

            avg_removed = bytes_removed_total // pages_cleaned if pages_cleaned else 0
        else:
            avg_removed = 0
            # Clear any stale cleaned_content
            for source in sources:
                if source.cleaned_content is not None:
                    source.cleaned_content = None

        # Persist merged hashes (domain ∪ all section hashes)
        all_hashes = sorted(domain_hashes | all_section_hashes)
        total_bp_blocks = fp.blocks_boilerplate + section_fp.total_section_hashes

        self._bp_repo.upsert(
            project_id=project_id,
            domain=domain,
            boilerplate_hashes=all_hashes,
            pages_analyzed=fp.pages_analyzed,
            blocks_total=fp.blocks_total,
            blocks_boilerplate=total_bp_blocks,
            bytes_removed_avg=avg_removed,
            threshold_pct=t_pct,
            min_pages=m_pages,
            min_block_chars=m_chars,
        )

        # 5. flush (caller manages transaction)
        self._session.flush()

        logger.info(
            "domain_dedup_analyzed",
            extra={
                "domain": domain,
                "pages_analyzed": fp.pages_analyzed,
                "pages_cleaned": pages_cleaned,
                "blocks_boilerplate": total_bp_blocks,
                "bytes_removed": bytes_removed_total,
                "sections_analyzed": section_fp.sections_analyzed,
                "sections_with_boilerplate": section_fp.sections_with_boilerplate,
            },
        )

        return DomainAnalysisResult(
            domain=domain,
            pages_analyzed=fp.pages_analyzed,
            pages_cleaned=pages_cleaned,
            blocks_boilerplate=total_bp_blocks,
            bytes_removed_total=bytes_removed_total,
            sections_analyzed=section_fp.sections_analyzed,
            sections_with_boilerplate=section_fp.sections_with_boilerplate,
        )

    def analyze_project(
        self,
        project_id: UUID,
        source_groups: list[str] | None = None,
        threshold_pct: float | None = None,
        min_pages: int | None = None,
        min_block_chars: int | None = None,
    ) -> ProjectAnalysisResult:
        """Analyze all domains in a project for boilerplate.

        Args:
            project_id: Project UUID.
            source_groups: Optional filter by source groups.
            threshold_pct: Override default threshold.
            min_pages: Override default min pages.
            min_block_chars: Override default min block chars.

        Returns:
            ProjectAnalysisResult with per-domain statistics.
        """
        domains = self._source_repo.get_domains_for_project(
            project_id, source_groups=source_groups
        )

        domain_results: list[DomainAnalysisResult] = []
        for domain_name, _page_count in domains:
            result = self.analyze_domain(
                project_id=project_id,
                domain=domain_name,
                threshold_pct=threshold_pct,
                min_pages=min_pages,
                min_block_chars=min_block_chars,
            )
            domain_results.append(result)

        # Commit after all domains processed
        self._session.commit()

        domains_with_bp = sum(1 for r in domain_results if r.blocks_boilerplate > 0)
        total_cleaned = sum(r.pages_cleaned for r in domain_results)
        total_removed = sum(r.bytes_removed_total for r in domain_results)

        logger.info(
            "domain_dedup_project_complete",
            extra={
                "project_id": str(project_id),
                "domains_analyzed": len(domain_results),
                "domains_with_boilerplate": domains_with_bp,
                "total_pages_cleaned": total_cleaned,
                "total_bytes_removed": total_removed,
            },
        )

        return ProjectAnalysisResult(
            domains_analyzed=len(domain_results),
            domains_with_boilerplate=domains_with_bp,
            total_pages_cleaned=total_cleaned,
            total_bytes_removed=total_removed,
            domain_results=domain_results,
        )

    def get_domain_stats(self, project_id: UUID) -> list[dict]:
        """Get per-domain boilerplate statistics for a project.

        Args:
            project_id: Project UUID.

        Returns:
            List of dicts with domain stats.
        """
        records = self._bp_repo.list_by_project(project_id)
        return [
            {
                "domain": r.domain,
                "pages_analyzed": r.pages_analyzed,
                "blocks_total": r.blocks_total,
                "blocks_boilerplate": r.blocks_boilerplate,
                "bytes_removed_avg": r.bytes_removed_avg,
                "threshold_pct": r.threshold_pct,
                "boilerplate_hashes_count": len(r.boilerplate_hashes),
                "updated_at": r.updated_at.isoformat() if r.updated_at else None,
            }
            for r in records
        ]
