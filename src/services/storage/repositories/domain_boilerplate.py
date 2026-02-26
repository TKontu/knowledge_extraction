"""Repository for DomainBoilerplate CRUD operations."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from orm_models import DomainBoilerplate


class DomainBoilerplateRepository:
    """Repository for managing DomainBoilerplate entities."""

    def __init__(self, session: Session):
        self._session = session

    def upsert(
        self,
        project_id: UUID,
        domain: str,
        boilerplate_hashes: list[str],
        pages_analyzed: int = 0,
        blocks_total: int = 0,
        blocks_boilerplate: int = 0,
        bytes_removed_avg: int = 0,
        threshold_pct: float = 0.7,
        min_pages: int = 5,
        min_block_chars: int = 50,
    ) -> DomainBoilerplate:
        """Insert or update a domain boilerplate fingerprint.

        Args:
            project_id: Project UUID.
            domain: Domain string (e.g. "www.example.com").
            boilerplate_hashes: List of block hashes identified as boilerplate.
            pages_analyzed: Number of pages analyzed.
            blocks_total: Total blocks across all pages.
            blocks_boilerplate: Number of boilerplate blocks found.
            bytes_removed_avg: Average bytes removed per page.
            threshold_pct: Threshold fraction used for detection.
            min_pages: Minimum pages parameter used.
            min_block_chars: Minimum block chars parameter used.

        Returns:
            DomainBoilerplate instance (created or updated).
        """
        values = {
            "project_id": project_id,
            "domain": domain,
            "boilerplate_hashes": boilerplate_hashes,
            "pages_analyzed": pages_analyzed,
            "blocks_total": blocks_total,
            "blocks_boilerplate": blocks_boilerplate,
            "bytes_removed_avg": bytes_removed_avg,
            "threshold_pct": threshold_pct,
            "min_pages": min_pages,
            "min_block_chars": min_block_chars,
        }

        stmt = pg_insert(DomainBoilerplate).values(**values)
        stmt = stmt.on_conflict_do_update(
            constraint="uq_domain_boilerplate_project_domain",
            set_={
                "boilerplate_hashes": stmt.excluded.boilerplate_hashes,
                "pages_analyzed": stmt.excluded.pages_analyzed,
                "blocks_total": stmt.excluded.blocks_total,
                "blocks_boilerplate": stmt.excluded.blocks_boilerplate,
                "bytes_removed_avg": stmt.excluded.bytes_removed_avg,
                "threshold_pct": stmt.excluded.threshold_pct,
                "min_pages": stmt.excluded.min_pages,
                "min_block_chars": stmt.excluded.min_block_chars,
            },
        ).returning(DomainBoilerplate.id)

        result = self._session.execute(stmt)
        record_id = result.scalar_one()
        self._session.flush()

        return self._session.execute(
            select(DomainBoilerplate).where(DomainBoilerplate.id == record_id)
        ).scalar_one()

    def get(self, project_id: UUID, domain: str) -> DomainBoilerplate | None:
        """Get a domain boilerplate record by project and domain.

        Args:
            project_id: Project UUID.
            domain: Domain string.

        Returns:
            DomainBoilerplate instance or None.
        """
        result = self._session.execute(
            select(DomainBoilerplate).where(
                DomainBoilerplate.project_id == project_id,
                DomainBoilerplate.domain == domain,
            )
        )
        return result.scalar_one_or_none()

    def list_by_project(self, project_id: UUID) -> list[DomainBoilerplate]:
        """List all domain boilerplate records for a project.

        Args:
            project_id: Project UUID.

        Returns:
            List of DomainBoilerplate instances, ordered by domain.
        """
        result = self._session.execute(
            select(DomainBoilerplate)
            .where(DomainBoilerplate.project_id == project_id)
            .order_by(DomainBoilerplate.domain)
        )
        return list(result.scalars().all())

    def delete_by_project(self, project_id: UUID) -> int:
        """Delete all domain boilerplate records for a project.

        Args:
            project_id: Project UUID.

        Returns:
            Number of records deleted.
        """
        result = self._session.execute(
            delete(DomainBoilerplate).where(DomainBoilerplate.project_id == project_id)
        )
        self._session.flush()
        return result.rowcount
