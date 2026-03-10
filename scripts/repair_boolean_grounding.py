#!/usr/bin/env python3
"""Repair boolean grounding for existing v2 extractions.

Two-phase repair:
  Phase 1 (DB-only): Set grounding=0.0 for null-placeholder booleans
      (conf=0, no quote, grounding=0.5 → merge defaults)
  Phase 2 (LLM rescue): For high-confidence booleans without quotes,
      attempt to find supporting passages via LLM rescue.

Usage:
    # Dry-run (report only)
    .venv/bin/python scripts/repair_boolean_grounding.py --dry-run

    # Phase 1 only (fast, no LLM calls)
    .venv/bin/python scripts/repair_boolean_grounding.py --phase1-only

    # Full repair (Phase 1 + Phase 2)
    .venv/bin/python scripts/repair_boolean_grounding.py

    # Specific project
    .venv/bin/python scripts/repair_boolean_grounding.py --project drivetrain
"""

import argparse
import asyncio
import sys
import time
from dataclasses import dataclass

sys.path.insert(0, "src")

from sqlalchemy import text, select
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import flag_modified

from config import settings
from database import engine
from orm_models import Extraction, Source

PROJECT_IDS = {
    "drivetrain": "99a19141-9268-40a8-bc9e-ad1fa12243da",
    "jobs": "b972e016-3baa-403f-ae79-22310e4e895a",
    "wikipedia": "6ce9755e-9d77-4926-90dd-86d4cd2b9cda",
}

# Boolean field groups by project schema
BOOLEAN_GROUPS = {"services", "manufacturing"}

# Minimum confidence to attempt LLM rescue
RESCUE_MIN_CONFIDENCE = 0.5


@dataclass
class RepairStats:
    phase1_updated: int = 0
    phase2_attempted: int = 0
    phase2_rescued: int = 0
    phase2_dropped: int = 0
    extractions_updated: int = 0
    confidence_recomputed: int = 0


def phase1_fix_null_placeholders(
    session: Session, project_id: str, dry_run: bool = False
) -> int:
    """Set grounding=0.0 for boolean merge defaults (conf=0, no quote, grounding~0.5).

    These are not real extractions — they're placeholder records from
    merge_boolean() when the grounding gate dropped all candidates.
    """
    # Find and count affected records
    count_sql = text("""
        SELECT COUNT(*)
        FROM extractions e,
             jsonb_each(e.data) AS kv
        WHERE e.data_version = 2
          AND e.project_id = :pid
          AND e.data->'_meta'->>'group' IN ('services', 'manufacturing')
          AND kv.key != '_meta'
          AND kv.value ? 'grounding'
          AND (kv.value->>'confidence')::numeric = 0
          AND (kv.value->>'quote' IS NULL OR kv.value->>'quote' = '')
          AND (kv.value->>'grounding')::numeric BETWEEN 0.4 AND 0.6
    """)
    count = session.execute(count_sql, {"pid": project_id}).scalar()
    print(f"  Phase 1: {count:,} null-placeholder boolean fields to fix")

    if dry_run or count == 0:
        return count

    # Update grounding to 0.0 for these fields.
    # We need to update each field within the JSONB individually.
    # Strategy: fetch affected extractions, update in Python, write back.
    affected_sql = text("""
        SELECT DISTINCT e.id
        FROM extractions e,
             jsonb_each(e.data) AS kv
        WHERE e.data_version = 2
          AND e.project_id = :pid
          AND e.data->'_meta'->>'group' IN ('services', 'manufacturing')
          AND kv.key != '_meta'
          AND kv.value ? 'grounding'
          AND (kv.value->>'confidence')::numeric = 0
          AND (kv.value->>'quote' IS NULL OR kv.value->>'quote' = '')
          AND (kv.value->>'grounding')::numeric BETWEEN 0.4 AND 0.6
    """)
    extraction_ids = [row[0] for row in session.execute(affected_sql, {"pid": project_id})]
    print(f"  Phase 1: Updating {len(extraction_ids):,} extractions...")

    batch_size = 500
    updated = 0
    for i in range(0, len(extraction_ids), batch_size):
        batch_ids = extraction_ids[i : i + batch_size]
        extractions = (
            session.execute(
                select(Extraction).where(Extraction.id.in_(batch_ids))
            )
            .scalars()
            .all()
        )

        for ext in extractions:
            data = ext.data
            changed = False
            for key, field_data in data.items():
                if key == "_meta":
                    continue
                if not isinstance(field_data, dict) or "grounding" not in field_data:
                    continue
                conf = float(field_data.get("confidence", -1))
                gnd = float(field_data.get("grounding", -1))
                quote = field_data.get("quote")
                if conf == 0.0 and 0.4 <= gnd <= 0.6 and (not quote):
                    field_data["grounding"] = 0.0
                    changed = True
                    updated += 1

            if changed:
                # Force SQLAlchemy to detect the JSONB mutation
                flag_modified(ext, "data")

        session.flush()

        if (i + batch_size) % 2000 == 0 or i + batch_size >= len(extraction_ids):
            session.commit()
            pct = min(100, (i + batch_size) / len(extraction_ids) * 100)
            print(f"    {pct:.0f}% — {updated:,} fields fixed")

    session.commit()
    print(f"  Phase 1 complete: {updated:,} fields set to grounding=0.0")
    return updated


async def phase2_rescue_booleans(
    session: Session, project_id: str, dry_run: bool = False,
    concurrency: int = 5,
) -> RepairStats:
    """LLM rescue for high-confidence boolean fields without quotes."""
    from services.extraction.grounding import verify_quote_in_source
    from services.extraction.llm_grounding import LLMGroundingVerifier
    from services.llm.client import LLMClient

    stats = RepairStats()

    # Find affected extractions with their source IDs
    affected_sql = text("""
        SELECT DISTINCT e.id, e.source_id
        FROM extractions e,
             jsonb_each(e.data) AS kv
        WHERE e.data_version = 2
          AND e.project_id = :pid
          AND e.data->'_meta'->>'group' IN ('services', 'manufacturing')
          AND kv.key != '_meta'
          AND kv.value ? 'grounding'
          AND (kv.value->>'quote' IS NULL OR kv.value->>'quote' = '')
          AND (kv.value->>'confidence')::numeric >= :min_conf
          AND (kv.value->>'grounding')::numeric < 0.8
        ORDER BY e.source_id
    """)
    rows = session.execute(
        affected_sql, {"pid": project_id, "min_conf": RESCUE_MIN_CONFIDENCE}
    ).fetchall()

    if not rows:
        print("  Phase 2: No fields need rescue")
        return stats

    # Group by source_id for efficient content loading
    source_extractions: dict[str, list[str]] = {}
    for ext_id, source_id in rows:
        source_extractions.setdefault(str(source_id), []).append(str(ext_id))

    total_sources = len(source_extractions)
    total_extractions = len(rows)
    print(f"  Phase 2: {total_extractions:,} extractions across {total_sources:,} sources")

    if dry_run:
        return stats

    # Set up LLM client for rescue
    llm_config = settings.llm
    llm_client = LLMClient(llm_config)
    verifier = LLMGroundingVerifier(llm_client=llm_client)
    sem = asyncio.Semaphore(concurrency)

    processed_sources = 0
    commit_interval = 100  # Commit every N sources

    for source_id, ext_ids in source_extractions.items():
        # Load source content once
        source = session.execute(
            select(Source).where(Source.id == source_id)
        ).scalar_one_or_none()
        if not source:
            continue

        content = source.cleaned_content or source.content
        if not content:
            continue

        # Load all affected extractions for this source
        extractions = (
            session.execute(
                select(Extraction).where(Extraction.id.in_(ext_ids))
            )
            .scalars()
            .all()
        )

        for ext in extractions:
            data = ext.data
            changed = False
            rescue_tasks = []

            # Collect fields needing rescue
            for key, field_data in data.items():
                if key == "_meta":
                    continue
                if not isinstance(field_data, dict) or "grounding" not in field_data:
                    continue
                conf = float(field_data.get("confidence", 0))
                gnd = float(field_data.get("grounding", 0))
                quote = field_data.get("quote")
                if conf >= RESCUE_MIN_CONFIDENCE and gnd < 0.8 and not quote:
                    rescue_tasks.append((key, field_data))

            if not rescue_tasks:
                continue

            # Run rescue for each field
            for field_name, field_data in rescue_tasks:
                stats.phase2_attempted += 1
                value = field_data.get("value")

                async with sem:
                    rescue = await verifier.rescue_quote(field_name, value, content)

                if rescue.quote and rescue.grounding >= 0.8:
                    field_data["quote"] = rescue.quote
                    field_data["grounding"] = rescue.grounding
                    changed = True
                    stats.phase2_rescued += 1
                else:
                    # No supporting passage found → set grounding to 0.0
                    field_data["grounding"] = 0.0
                    changed = True
                    stats.phase2_dropped += 1

            if changed:
                flag_modified(ext, "data")
                # Recompute extraction-level confidence
                confidences = []
                for key, field_data in ext.data.items():
                    if key == "_meta":
                        continue
                    if isinstance(field_data, dict) and "confidence" in field_data:
                        confidences.append(float(field_data["confidence"]))
                if confidences:
                    ext.confidence = sum(confidences) / len(confidences)
                    stats.confidence_recomputed += 1
                stats.extractions_updated += 1

        processed_sources += 1
        if processed_sources % commit_interval == 0:
            session.commit()
            pct = processed_sources / total_sources * 100
            print(
                f"    {pct:.0f}% ({processed_sources}/{total_sources} sources) — "
                f"rescued: {stats.phase2_rescued:,}, dropped: {stats.phase2_dropped:,}"
            )

    session.commit()
    print(
        f"  Phase 2 complete: {stats.phase2_attempted:,} fields processed, "
        f"{stats.phase2_rescued:,} rescued, {stats.phase2_dropped:,} dropped"
    )
    return stats


async def main() -> None:
    parser = argparse.ArgumentParser(description="Repair boolean grounding")
    parser.add_argument("--dry-run", action="store_true", help="Report only, no changes")
    parser.add_argument("--phase1-only", action="store_true", help="Only fix null placeholders")
    parser.add_argument(
        "--project", choices=["drivetrain", "jobs", "wikipedia", "all"],
        default="all",
    )
    parser.add_argument("--concurrency", type=int, default=5, help="LLM concurrency for rescue")
    args = parser.parse_args()

    projects = list(PROJECT_IDS.keys()) if args.project == "all" else [args.project]

    print("=" * 70)
    print("  BOOLEAN GROUNDING REPAIR")
    print(f"  Mode: {'DRY RUN' if args.dry_run else 'LIVE' + (' (phase 1 only)' if args.phase1_only else '')}")
    print(f"  Projects: {', '.join(projects)}")
    print("=" * 70)

    with Session(engine) as session:
        for project_name in projects:
            project_id = PROJECT_IDS[project_name]
            print(f"\n{'─'*70}")
            print(f"  {project_name}")
            print(f"{'─'*70}")

            # Phase 1
            phase1_count = phase1_fix_null_placeholders(session, project_id, args.dry_run)

            # Phase 2
            if not args.phase1_only:
                stats = await phase2_rescue_booleans(
                    session, project_id, args.dry_run,
                    concurrency=args.concurrency,
                )
                print(f"\n  Summary for {project_name}:")
                print(f"    Phase 1: {phase1_count:,} null placeholders → grounding=0.0")
                print(f"    Phase 2: {stats.phase2_rescued:,} rescued, {stats.phase2_dropped:,} dropped")
                print(f"    Extractions updated: {stats.extractions_updated:,}")

    print(f"\n{'='*70}")
    print("  Repair complete.")
    print("=" * 70)


if __name__ == "__main__":
    asyncio.run(main())
