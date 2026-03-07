"""Backfill grounding scores for existing extractions.

Reads all extractions for a project (paginated), computes string-match
grounding scores, and batch-updates the grounding_scores column.

With --llm flag, also runs LLM verification on fields where string-match
scored 0.0 but a quote exists.

Usage:
    python scripts/backfill_grounding_scores.py --project-id <uuid>
    python scripts/backfill_grounding_scores.py --project-id <uuid> --llm
    python scripts/backfill_grounding_scores.py --project-id <uuid> --batch-size 1000
    python scripts/backfill_grounding_scores.py --project-id <uuid> --dry-run
"""

from __future__ import annotations

import argparse
import asyncio
import sys
import time
from collections import defaultdict
from uuid import UUID

# Add src to path for imports
sys.path.insert(0, "src")

from sqlalchemy.orm import Session  # noqa: E402

from database import engine  # noqa: E402
from orm_models import Project  # noqa: E402
from services.extraction.extraction_items import safe_data_version  # noqa: E402
from services.extraction.grounding import (  # noqa: E402
    compute_grounding_scores,
    extract_field_types_from_schema,
)
from services.storage.repositories.extraction import (  # noqa: E402
    ExtractionFilters,
    ExtractionRepository,
)


def backfill_project(
    project_id: UUID,
    batch_size: int = 500,
    dry_run: bool = False,
) -> dict:
    """Backfill string-match grounding scores for all extractions in a project.

    Args:
        project_id: Project UUID
        batch_size: Number of extractions to process per batch
        dry_run: If True, compute but don't write to DB

    Returns:
        Summary stats dict
    """
    with Session(engine) as session:
        # Load project and schema
        project = session.get(Project, project_id)
        if not project:
            print(f"Project {project_id} not found")
            return {"error": "project_not_found"}

        schema = project.extraction_schema
        if not schema or not schema.get("field_groups"):
            print(f"Project {project_id} has no extraction schema")
            return {"error": "no_schema"}

        field_types_by_group = extract_field_types_from_schema(schema)
        print(f"Schema groups: {list(field_types_by_group.keys())}")

        repo = ExtractionRepository(session)
        filters = ExtractionFilters(project_id=project_id)

        total_count = repo.count(filters)
        print(f"Total extractions: {total_count}")

        # Stats tracking
        stats: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
        processed = 0
        updated = 0
        skipped = 0
        start_time = time.time()

        offset = 0
        while offset < total_count:
            extractions = repo.list(filters, limit=batch_size, offset=offset)
            if not extractions:
                break

            updates: list[tuple[UUID, dict[str, float]]] = []

            for ext in extractions:
                # v2 extractions have inline grounding — skip backfill
                if safe_data_version(ext) >= 2:
                    skipped += 1
                    continue

                field_types = field_types_by_group.get(ext.extraction_type, {})
                if not field_types:
                    skipped += 1
                    continue

                scores = compute_grounding_scores(ext.data, field_types)
                if scores:
                    updates.append((ext.id, scores))

                    # Track per-field stats
                    for field_name, score in scores.items():
                        if score >= 0.5:
                            stats[field_name]["grounded"] += 1
                        else:
                            stats[field_name]["ungrounded"] += 1

                processed += 1

            if updates and not dry_run:
                updated += repo.update_grounding_scores_batch(updates)
                session.commit()

            offset += batch_size
            elapsed = time.time() - start_time
            rate = processed / elapsed if elapsed > 0 else 0
            print(
                f"  Processed {processed}/{total_count} "
                f"({rate:.0f}/s, updated={updated}, skipped={skipped})"
            )

        # Print summary
        elapsed = time.time() - start_time
        print(f"\nString-match pass completed in {elapsed:.1f}s")
        print(f"  Processed: {processed}")
        print(f"  Updated: {updated}")
        print(f"  Skipped (no field types): {skipped}")

        _print_stats(stats)

        return {
            "processed": processed,
            "updated": updated,
            "skipped": skipped,
            "elapsed": elapsed,
            "field_stats": dict(stats),
        }


async def llm_verify_project(
    project_id: UUID,
    batch_size: int = 100,
    dry_run: bool = False,
) -> dict:
    """Run LLM verification on extractions with unresolved grounding scores.

    Only processes extractions where grounding_scores has 0.0 entries
    with non-empty quotes. Requires string-match pass to have run first.

    Args:
        project_id: Project UUID
        batch_size: Number of extractions to process per batch
        dry_run: If True, compute but don't write to DB

    Returns:
        Summary stats dict
    """
    from config import settings  # noqa: E402
    from services.extraction.llm_grounding import LLMGroundingVerifier  # noqa: E402
    from services.llm.client import LLMClient  # noqa: E402

    model = settings.grounding_llm_verify_model or settings.llm_model
    print(f"LLM verification model: {model}")

    llm_client = LLMClient(settings.llm)
    verifier = LLMGroundingVerifier(llm_client=llm_client, model=model)

    try:
        with Session(engine) as session:
            project = session.get(Project, project_id)
            if not project:
                print(f"Project {project_id} not found")
                return {"error": "project_not_found"}

            schema = project.extraction_schema
            field_types_by_group = extract_field_types_from_schema(schema)

            repo = ExtractionRepository(session)
            filters = ExtractionFilters(project_id=project_id)

            total_count = repo.count(filters)
            print(f"Total extractions: {total_count}")

            verified = 0
            upgraded = 0
            rejected = 0
            errors = 0
            start_time = time.time()

            offset = 0
            while offset < total_count:
                extractions = repo.list(filters, limit=batch_size, offset=offset)
                if not extractions:
                    break

                for ext in extractions:
                    if not ext.grounding_scores:
                        continue

                    # Check if any fields need LLM verification
                    has_unresolved = any(
                        score == 0.0 and (ext.data.get("_quotes", {}) or {}).get(field)
                        for field, score in ext.grounding_scores.items()
                    )
                    if not has_unresolved:
                        continue

                    field_types = field_types_by_group.get(ext.extraction_type, {})
                    if not field_types:
                        continue

                    updated_scores = await verifier.verify_extraction(
                        ext.data, ext.grounding_scores, field_types
                    )

                    # Count changes
                    for field, new_score in updated_scores.items():
                        old_score = ext.grounding_scores.get(field, 0.0)
                        if new_score > old_score:
                            upgraded += 1
                        elif new_score == 0.0 and old_score == 0.0:
                            # LLM confirmed it's ungrounded
                            rejected += 1

                    if updated_scores != ext.grounding_scores and not dry_run:
                        repo.update_grounding_scores(ext.id, updated_scores)

                    verified += 1

                if not dry_run:
                    session.commit()

                offset += batch_size
                elapsed = time.time() - start_time
                print(
                    f"  LLM verified {verified} extractions "
                    f"(upgraded={upgraded}, rejected={rejected}, "
                    f"elapsed={elapsed:.0f}s)"
                )

            elapsed = time.time() - start_time
            print(f"\nLLM verification completed in {elapsed:.1f}s")
            print(f"  Verified: {verified}")
            print(f"  Upgraded (0→1): {upgraded}")
            print(f"  Confirmed ungrounded: {rejected}")
            print(f"  Errors: {errors}")

            return {
                "verified": verified,
                "upgraded": upgraded,
                "rejected": rejected,
                "errors": errors,
                "elapsed": elapsed,
            }
    finally:
        await llm_client.close()


def _print_stats(stats: dict[str, dict[str, int]]) -> None:
    if not stats:
        return
    print("\nPer-field grounding stats:")
    for field_name, counts in sorted(stats.items()):
        total = counts["grounded"] + counts["ungrounded"]
        pct = counts["grounded"] / total * 100 if total > 0 else 0
        print(
            f"  {field_name}: {counts['grounded']} grounded "
            f"({pct:.0f}%), {counts['ungrounded']} ungrounded"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill grounding scores for extractions"
    )
    parser.add_argument("--project-id", type=str, required=True, help="Project UUID")
    parser.add_argument(
        "--batch-size", type=int, default=500, help="Batch size (default: 500)"
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Compute scores without writing to DB",
    )
    parser.add_argument(
        "--llm",
        action="store_true",
        help="Run LLM verification after string-match pass",
    )
    args = parser.parse_args()

    project_id = UUID(args.project_id)

    # Always run string-match pass first
    backfill_project(project_id, batch_size=args.batch_size, dry_run=args.dry_run)

    # Optionally run LLM verification
    if args.llm:
        print("\n" + "=" * 60)
        print("Starting LLM verification pass...")
        print("=" * 60 + "\n")
        asyncio.run(
            llm_verify_project(
                project_id,
                batch_size=min(args.batch_size, 100),
                dry_run=args.dry_run,
            )
        )


if __name__ == "__main__":
    main()
