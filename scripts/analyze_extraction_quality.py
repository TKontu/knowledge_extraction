#!/usr/bin/env python3
"""Post-extraction quality analysis for v2 extractions with inline grounding.

Analyzes grounding scores, confidence, and field-level quality metrics
across extraction runs, properly isolating runs by timestamp boundaries.

Outputs:
  - Per-project summary (extraction counts, run boundaries)
  - Extraction-level grounding distribution (well/borderline/poor)
  - Field-group-level breakdown
  - Per-field grounding analysis (worst fields first)
  - Entity-item grounding for product groups
  - Run-over-run comparison when multiple runs exist
  - Jobs & Wikipedia trial results

Usage:
    .venv/bin/python scripts/analyze_extraction_quality.py
    .venv/bin/python scripts/analyze_extraction_quality.py --project drivetrain
    .venv/bin/python scripts/analyze_extraction_quality.py --latest-only
"""

import argparse
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, UTC

sys.path.insert(0, "src")

from sqlalchemy import select, func, text
from sqlalchemy.orm import Session

from config import settings
from database import engine
from orm_models import Extraction, Project

# ── Project IDs ──────────────────────────────────────────────────────────────

PROJECT_IDS = {
    "drivetrain": "99a19141-9268-40a8-bc9e-ad1fa12243da",
    "jobs": "b972e016-3baa-403f-ae79-22310e4e895a",
    "wikipedia": "6ce9755e-9d77-4926-90dd-86d4cd2b9cda",
}

# Known extraction job boundaries (from job status API)
KNOWN_JOBS = {
    "drivetrain": {
        "job_id": "66c9b300-3f9e-4dce-b13b-b39e1e549b4b",
        "started": "2026-03-08T20:50:10+00:00",
        "completed": "2026-03-09T08:45:14+00:00",
        "expected_extractions": 46950,
    },
    "jobs": {
        "job_id": "60d84d11-7ccb-4a65-934d-6dcffc7a6867",
        "started": "2026-03-08T20:50:17+00:00",
        "completed": "2026-03-09T10:17:50+00:00",
        "expected_extractions": 85,
    },
    "wikipedia": {
        "job_id": "e4dfc752-b58e-4be5-8dab-bb76cd0adc2d",
        "started": "2026-03-08T20:50:18+00:00",
        "completed": "2026-03-09T10:31:31+00:00",
        "expected_extractions": 57,
    },
}


# ── Data Classes ─────────────────────────────────────────────────────────────


@dataclass
class FieldStats:
    count: int = 0
    sum_grounding: float = 0.0
    sum_confidence: float = 0.0
    well: int = 0       # grounding >= 0.8
    borderline: int = 0  # 0.3 <= grounding < 0.8
    poor: int = 0        # grounding < 0.3
    has_quote: int = 0
    has_location: int = 0
    null_placeholders: int = 0  # value=null, conf=0, grounding=0 (merge defaults)

    @property
    def avg_grounding(self) -> float:
        return self.sum_grounding / self.count if self.count else 0.0

    @property
    def avg_confidence(self) -> float:
        return self.sum_confidence / self.count if self.count else 0.0

    @property
    def pct_well(self) -> float:
        return 100.0 * self.well / self.count if self.count else 0.0

    @property
    def pct_borderline(self) -> float:
        return 100.0 * self.borderline / self.count if self.count else 0.0

    @property
    def pct_poor(self) -> float:
        return 100.0 * self.poor / self.count if self.count else 0.0

    def record(self, grounding: float, confidence: float, has_quote: bool, has_loc: bool) -> None:
        self.count += 1
        self.sum_grounding += grounding
        self.sum_confidence += confidence
        if grounding >= 0.8:
            self.well += 1
        elif grounding >= 0.3:
            self.borderline += 1
        else:
            self.poor += 1
        if has_quote:
            self.has_quote += 1
        if has_loc:
            self.has_location += 1


@dataclass
class RunStats:
    """Stats for a single extraction run."""
    project_name: str
    run_label: str
    extraction_count: int = 0
    earliest: datetime | None = None
    latest: datetime | None = None
    # Per field-group
    group_stats: dict[str, FieldStats] = field(default_factory=lambda: defaultdict(FieldStats))
    # Per field (within group)
    field_stats: dict[str, FieldStats] = field(default_factory=lambda: defaultdict(FieldStats))
    # Per entity group (item-level grounding)
    entity_stats: dict[str, FieldStats] = field(default_factory=lambda: defaultdict(FieldStats))
    # Extraction-level confidence distribution
    conf_high: int = 0   # >= 0.8
    conf_mid: int = 0    # 0.3-0.8
    conf_low: int = 0    # < 0.3
    # Overall field-level aggregate
    overall: FieldStats = field(default_factory=FieldStats)


# ── Extraction Parsing ───────────────────────────────────────────────────────


def _is_null_placeholder(field_data: dict) -> bool:
    """Detect merge-layer null placeholders (value=null, conf=0, grounding=0).

    These are injected by merge_single_answer / merge_boolean when the grounding
    gate correctly dropped all candidates for a field.
    """
    value = field_data.get("value")
    conf = field_data.get("confidence", -1)
    gnd = field_data.get("grounding", -1)
    # Null placeholder: value is None/empty, both scores are 0
    if value is None and float(conf) == 0.0 and float(gnd) == 0.0:
        return True
    # Boolean placeholder: value=False, both scores are 0
    if value is False and float(conf) == 0.0 and float(gnd) == 0.0:
        return True
    return False


def parse_scalar_fields(data: dict, group_name: str, stats: RunStats) -> None:
    """Parse scalar field groups (company_info, services, manufacturing)."""
    for field_name, field_data in data.items():
        if field_name == "_meta":
            continue
        if not isinstance(field_data, dict):
            continue

        grounding = field_data.get("grounding")
        confidence = field_data.get("confidence")
        if grounding is None or confidence is None:
            continue

        # Detect and count null placeholders separately
        if _is_null_placeholder(field_data):
            key = f"{group_name}.{field_name}"
            stats.field_stats[key].null_placeholders += 1
            stats.group_stats[group_name].null_placeholders += 1
            stats.overall.null_placeholders += 1
            continue

        grounding = float(grounding)
        confidence = float(confidence)
        has_quote = bool(field_data.get("quote"))
        has_loc = bool(field_data.get("location"))

        key = f"{group_name}.{field_name}"
        stats.field_stats[key].record(grounding, confidence, has_quote, has_loc)
        stats.group_stats[group_name].record(grounding, confidence, has_quote, has_loc)
        stats.overall.record(grounding, confidence, has_quote, has_loc)


def parse_entity_fields(data: dict, group_name: str, stats: RunStats) -> None:
    """Parse entity list groups (products_gearbox, products_motor, etc.)."""
    entity_key = None
    for k, v in data.items():
        if k == "_meta":
            continue
        if isinstance(v, dict) and "items" in v:
            entity_key = k
            break

    if not entity_key:
        return

    items = data[entity_key].get("items", [])
    if not isinstance(items, list):
        return

    for item in items:
        if not isinstance(item, dict):
            continue

        grounding = item.get("grounding")
        confidence = item.get("confidence")
        if grounding is None or confidence is None:
            continue

        grounding = float(grounding)
        confidence = float(confidence)
        has_quote = bool(item.get("quote"))
        has_loc = bool(item.get("location"))

        stats.entity_stats[group_name].record(grounding, confidence, has_quote, has_loc)
        stats.group_stats[group_name].record(grounding, confidence, has_quote, has_loc)
        stats.overall.record(grounding, confidence, has_quote, has_loc)

        # Also parse individual entity fields if they have grounding
        fields = item.get("fields", {})
        if isinstance(fields, dict):
            for fname, fval in fields.items():
                if isinstance(fval, dict) and "grounding" in fval:
                    fg = float(fval["grounding"])
                    fc = float(fval.get("confidence", confidence))
                    key = f"{group_name}.entity.{fname}"
                    stats.field_stats[key].record(fg, fc, bool(fval.get("quote")), False)


# Entity groups have items, scalar groups have direct field dicts
ENTITY_GROUPS = {"products_gearbox", "products_motor", "products_accessory"}


def analyze_extraction(data: dict, stats: RunStats) -> None:
    """Analyze a single extraction's data for grounding metrics."""
    meta = data.get("_meta", {})
    group_name = meta.get("group", "unknown")

    if group_name in ENTITY_GROUPS:
        parse_entity_fields(data, group_name, stats)
    else:
        parse_scalar_fields(data, group_name, stats)


# ── Run Detection ────────────────────────────────────────────────────────────


def detect_runs(session: Session, project_id: str) -> list[tuple[datetime, datetime]]:
    """Detect extraction run boundaries by finding timestamp gaps > 1 hour."""
    result = session.execute(
        text("""
            WITH timestamps AS (
                SELECT extracted_at,
                       LAG(extracted_at) OVER (ORDER BY extracted_at) as prev_at
                FROM extractions
                WHERE project_id = :pid AND data_version = 2
            ),
            gaps AS (
                SELECT extracted_at as gap_start,
                       prev_at as prev_end,
                       EXTRACT(EPOCH FROM (extracted_at - prev_at)) as gap_seconds
                FROM timestamps
                WHERE prev_at IS NOT NULL
                  AND EXTRACT(EPOCH FROM (extracted_at - prev_at)) > 3600
            )
            SELECT prev_end, gap_start FROM gaps ORDER BY gap_start
        """),
        {"pid": project_id},
    )
    boundaries = result.fetchall()

    # Get overall min/max
    minmax = session.execute(
        text("""
            SELECT MIN(extracted_at), MAX(extracted_at)
            FROM extractions
            WHERE project_id = :pid AND data_version = 2
        """),
        {"pid": project_id},
    ).fetchone()

    if not minmax or not minmax[0]:
        return []

    overall_min, overall_max = minmax

    # Build run intervals
    runs = []
    run_start = overall_min
    for prev_end, gap_start in boundaries:
        runs.append((run_start, prev_end))
        run_start = gap_start
    runs.append((run_start, overall_max))

    return runs


# ── Main Analysis ────────────────────────────────────────────────────────────


def analyze_project(
    session: Session,
    project_name: str,
    project_id: str,
    latest_only: bool = False,
) -> list[RunStats]:
    """Analyze all extraction runs for a project."""

    # Detect run boundaries
    runs = detect_runs(session, project_id)
    if not runs:
        print(f"  No v2 extractions found for {project_name}")
        return []

    print(f"\n{'='*80}")
    print(f"  PROJECT: {project_name}")
    print(f"  {len(runs)} extraction run(s) detected")
    print(f"{'='*80}")

    for i, (start, end) in enumerate(runs):
        print(f"  Run {i+1}: {start.isoformat()} → {end.isoformat()}")

    if latest_only:
        runs = [runs[-1]]
        print(f"  (analyzing latest run only)")

    all_run_stats = []

    for run_idx, (run_start, run_end) in enumerate(runs):
        run_label = f"Run {run_idx + 1}" if len(runs) > 1 else "Current"
        if latest_only:
            run_label = "Latest"

        stats = RunStats(project_name=project_name, run_label=run_label)

        # Fetch extractions for this run window
        query = (
            select(Extraction.data, Extraction.confidence, Extraction.extracted_at)
            .where(
                Extraction.project_id == project_id,
                Extraction.data_version == 2,
                Extraction.extracted_at >= run_start,
                Extraction.extracted_at <= run_end,
            )
            .order_by(Extraction.extracted_at)
        )

        result = session.execute(query)

        for data, confidence, extracted_at in result:
            stats.extraction_count += 1

            if stats.earliest is None or extracted_at < stats.earliest:
                stats.earliest = extracted_at
            if stats.latest is None or extracted_at > stats.latest:
                stats.latest = extracted_at

            # Extraction-level confidence bucket
            if confidence is not None:
                if confidence >= 0.8:
                    stats.conf_high += 1
                elif confidence >= 0.3:
                    stats.conf_mid += 1
                else:
                    stats.conf_low += 1

            # Analyze inline grounding
            if isinstance(data, dict):
                analyze_extraction(data, stats)

        all_run_stats.append(stats)
        print_run_stats(stats)

    return all_run_stats


# ── Reporting ────────────────────────────────────────────────────────────────


def print_run_stats(stats: RunStats) -> None:
    """Print formatted stats for a single run."""
    n = stats.extraction_count
    if n == 0:
        print(f"\n  {stats.run_label}: No extractions")
        return

    print(f"\n{'─'*80}")
    print(f"  {stats.project_name} — {stats.run_label}")
    print(f"  {n:,} extractions | {stats.earliest} → {stats.latest}")
    print(f"{'─'*80}")

    # Extraction-level confidence
    total_conf = stats.conf_high + stats.conf_mid + stats.conf_low
    if total_conf > 0:
        print(f"\n  EXTRACTION-LEVEL CONFIDENCE (n={total_conf:,})")
        print(f"  {'Bucket':<20} {'Count':>8} {'Pct':>8}")
        print(f"  {'─'*38}")
        print(f"  {'High (>=0.8)':<20} {stats.conf_high:>8,} {100*stats.conf_high/total_conf:>7.1f}%")
        print(f"  {'Medium (0.3-0.8)':<20} {stats.conf_mid:>8,} {100*stats.conf_mid/total_conf:>7.1f}%")
        print(f"  {'Low (<0.3)':<20} {stats.conf_low:>8,} {100*stats.conf_low/total_conf:>7.1f}%")

    # Overall field-level grounding
    o = stats.overall
    total_with_nulls = o.count + o.null_placeholders
    if total_with_nulls > 0:
        print(f"\n  FIELD-LEVEL GROUNDING (n={o.count:,} real + {o.null_placeholders:,} null placeholders)")
        print(f"  {'Metric':<30} {'Value':>10}")
        print(f"  {'─'*42}")
        print(f"  {'Null placeholders filtered':<30} {o.null_placeholders:>10,}")
        print(f"  {'Real field observations':<30} {o.count:>10,}")
        if o.count > 0:
            print(f"  {'Avg grounding':<30} {o.avg_grounding:>10.3f}")
            print(f"  {'Avg confidence':<30} {o.avg_confidence:>10.3f}")
            print(f"  {'Well-grounded (>=0.8)':<30} {o.pct_well:>9.1f}%")
            print(f"  {'Borderline (0.3-0.8)':<30} {o.pct_borderline:>9.1f}%")
            print(f"  {'Poorly-grounded (<0.3)':<30} {o.pct_poor:>9.1f}%")
            print(f"  {'Has quote':<30} {100*o.has_quote/o.count:>9.1f}%")
            print(f"  {'Has location':<30} {100*o.has_location/o.count:>9.1f}%")

    # Per field-group breakdown
    if stats.group_stats:
        print(f"\n  PER FIELD-GROUP BREAKDOWN (null placeholders excluded)")
        print(f"  {'Group':<30} {'Real':>7} {'Nulls':>7} {'AvgGnd':>8} {'Well%':>7} {'Bord%':>7} {'Poor%':>7} {'AvgConf':>8}")
        print(f"  {'─'*84}")
        for group_name in sorted(stats.group_stats, key=lambda g: stats.group_stats[g].avg_grounding):
            gs = stats.group_stats[group_name]
            if gs.count == 0:
                print(
                    f"  {group_name:<30} {gs.count:>7,} {gs.null_placeholders:>7,} "
                    f"{'N/A':>8} {'N/A':>7} {'N/A':>7} {'N/A':>7} {'N/A':>8}"
                )
            else:
                print(
                    f"  {group_name:<30} {gs.count:>7,} {gs.null_placeholders:>7,} {gs.avg_grounding:>8.3f} "
                    f"{gs.pct_well:>6.1f}% {gs.pct_borderline:>6.1f}% {gs.pct_poor:>6.1f}% "
                    f"{gs.avg_confidence:>8.3f}"
                )

    # Entity item grounding
    if stats.entity_stats:
        print(f"\n  ENTITY ITEM GROUNDING")
        print(f"  {'Entity Group':<30} {'Items':>7} {'AvgGnd':>8} {'Well%':>7} {'Bord%':>7} {'Poor%':>7} {'Quote%':>7}")
        print(f"  {'─'*76}")
        for group_name in sorted(stats.entity_stats, key=lambda g: stats.entity_stats[g].avg_grounding):
            es = stats.entity_stats[group_name]
            print(
                f"  {group_name:<30} {es.count:>7,} {es.avg_grounding:>8.3f} "
                f"{es.pct_well:>6.1f}% {es.pct_borderline:>6.1f}% {es.pct_poor:>6.1f}% "
                f"{100*es.has_quote/es.count if es.count else 0:>6.1f}%"
            )

    # Per-field breakdown (worst grounding first, top 25)
    if stats.field_stats:
        print(f"\n  PER-FIELD BREAKDOWN (null placeholders excluded, sorted by avg grounding)")
        print(f"  {'Field':<45} {'Real':>6} {'Nulls':>6} {'AvgGnd':>8} {'Well%':>7} {'Poor%':>7}")
        print(f"  {'─'*82}")
        # Sort by avg_grounding, putting fields with 0 real observations last
        sorted_fields = sorted(
            stats.field_stats.items(),
            key=lambda kv: (kv[1].count == 0, kv[1].avg_grounding),
        )
        for fname, fs in sorted_fields[:30]:
            if fs.count == 0:
                print(
                    f"  {fname:<45} {fs.count:>6,} {fs.null_placeholders:>6,} "
                    f"{'N/A':>8} {'N/A':>7} {'N/A':>7}"
                )
            else:
                print(
                    f"  {fname:<45} {fs.count:>6,} {fs.null_placeholders:>6,} {fs.avg_grounding:>8.3f} "
                    f"{fs.pct_well:>6.1f}% {fs.pct_poor:>6.1f}%"
                )
        if len(sorted_fields) > 30:
            print(f"  ... and {len(sorted_fields) - 30} more fields")


def print_comparison(runs: list[RunStats]) -> None:
    """Print run-over-run comparison if multiple runs exist."""
    if len(runs) < 2:
        return

    print(f"\n{'='*80}")
    print(f"  RUN-OVER-RUN COMPARISON: {runs[0].project_name}")
    print(f"{'='*80}")
    print(f"  {'Metric':<30}", end="")
    for r in runs:
        print(f" {r.run_label:>15}", end="")
    print()
    print(f"  {'─'*(30 + 16 * len(runs))}")

    print(f"  {'Extractions':<30}", end="")
    for r in runs:
        print(f" {r.extraction_count:>15,}", end="")
    print()

    print(f"  {'Fields analyzed':<30}", end="")
    for r in runs:
        print(f" {r.overall.count:>15,}", end="")
    print()

    print(f"  {'Avg grounding':<30}", end="")
    for r in runs:
        print(f" {r.overall.avg_grounding:>15.3f}", end="")
    print()

    print(f"  {'Well-grounded %':<30}", end="")
    for r in runs:
        print(f" {r.overall.pct_well:>14.1f}%", end="")
    print()

    print(f"  {'Borderline %':<30}", end="")
    for r in runs:
        print(f" {r.overall.pct_borderline:>14.1f}%", end="")
    print()

    print(f"  {'Poorly-grounded %':<30}", end="")
    for r in runs:
        print(f" {r.overall.pct_poor:>14.1f}%", end="")
    print()

    # Delta for key metrics if exactly 2 runs
    if len(runs) == 2:
        old, new = runs
        if old.overall.count > 0 and new.overall.count > 0:
            dg = new.overall.avg_grounding - old.overall.avg_grounding
            dw = new.overall.pct_well - old.overall.pct_well
            dp = new.overall.pct_poor - old.overall.pct_poor
            print(f"\n  DELTA (Run 2 - Run 1):")
            print(f"    Avg grounding:    {dg:+.3f}")
            print(f"    Well-grounded %:  {dw:+.1f}pp")
            print(f"    Poorly-grounded %: {dp:+.1f}pp")

    # Per-group comparison
    all_groups = set()
    for r in runs:
        all_groups.update(r.group_stats.keys())

    if all_groups:
        print(f"\n  PER-GROUP COMPARISON (avg grounding)")
        print(f"  {'Group':<25}", end="")
        for r in runs:
            print(f" {r.run_label:>15}", end="")
        if len(runs) == 2:
            print(f" {'Delta':>10}", end="")
        print()
        print(f"  {'─'*(25 + 16 * len(runs) + (11 if len(runs) == 2 else 0))}")

        for group in sorted(all_groups):
            print(f"  {group:<25}", end="")
            vals = []
            for r in runs:
                gs = r.group_stats.get(group)
                if gs and gs.count > 0:
                    print(f" {gs.avg_grounding:>15.3f}", end="")
                    vals.append(gs.avg_grounding)
                else:
                    print(f" {'N/A':>15}", end="")
                    vals.append(None)
            if len(runs) == 2 and all(v is not None for v in vals):
                delta = vals[1] - vals[0]
                print(f" {delta:>+10.3f}", end="")
            print()


# ── Entry Point ──────────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Analyze v2 extraction quality")
    parser.add_argument(
        "--project",
        choices=["drivetrain", "jobs", "wikipedia", "all"],
        default="all",
        help="Which project to analyze",
    )
    parser.add_argument(
        "--latest-only",
        action="store_true",
        help="Only analyze the latest extraction run per project",
    )
    args = parser.parse_args()

    projects = (
        list(PROJECT_IDS.keys()) if args.project == "all" else [args.project]
    )

    print("=" * 80)
    print("  EXTRACTION QUALITY ANALYSIS — v2 with inline grounding")
    print(f"  Analysis time: {datetime.now(UTC).isoformat()}")
    print(f"  Projects: {', '.join(projects)}")
    print(f"  Mode: {'latest run only' if args.latest_only else 'all runs (with comparison)'}")
    print("=" * 80)

    with Session(engine) as session:
        for project_name in projects:
            project_id = PROJECT_IDS[project_name]
            run_stats = analyze_project(
                session, project_name, project_id, latest_only=args.latest_only
            )
            if len(run_stats) > 1:
                print_comparison(run_stats)

    print(f"\n{'='*80}")
    print("  Analysis complete.")
    print("=" * 80)


if __name__ == "__main__":
    main()
