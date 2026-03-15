"""Analyze consolidated extraction quality: fill rates, empty fields, provenance metrics.

Connects to the production DB and analyzes all consolidated_extractions
for a given project. Outputs a comprehensive quality report.

Usage:
    python scripts/analyze_quality.py [--project-id UUID]
"""

import argparse
from collections import defaultdict

import psycopg

PROJECT_ID = "99a19141-9268-40a8-bc9e-ad1fa12243da"
DB_DSN = "postgresql://scristill:scristill@192.168.0.136:5432/scristill"


def is_empty(value) -> bool:
    """Check if a value is effectively empty."""
    if value is None:
        return True
    if isinstance(value, str) and value.strip() in (
        "",
        "N/A",
        "n/a",
        "null",
        "None",
        "unknown",
    ):
        return True
    if isinstance(value, list) and len(value) == 0:
        return True
    if isinstance(value, dict) and len(value) == 0:
        return True
    return False


def analyze_entity_list(entities: list, group_name: str, stats: dict) -> None:
    """Analyze fill rates for entity list items."""
    if not entities or not isinstance(entities, list):
        return

    for entity in entities:
        if not isinstance(entity, dict):
            continue
        stats[group_name]["_entity_count"] += 1
        for field_name, value in entity.items():
            if field_name.startswith("_"):
                continue
            stats[group_name][field_name]["total"] += 1
            if is_empty(value):
                stats[group_name][field_name]["empty"] += 1
            else:
                stats[group_name][field_name]["filled"] += 1
                if isinstance(value, str):
                    stats[group_name][field_name]["sample_values"].add(value[:80])


def analyze_flat_fields(data: dict, group_name: str, stats: dict) -> None:
    """Analyze fill rates for flat (non-entity-list) fields."""
    for field_name, value in data.items():
        if field_name.startswith("_"):
            continue
        stats[group_name][field_name]["total"] += 1
        if is_empty(value):
            stats[group_name][field_name]["empty"] += 1
        else:
            stats[group_name][field_name]["filled"] += 1
            if isinstance(value, (str, int, float, bool)):
                stats[group_name][field_name]["sample_values"].add(str(value)[:80])
            elif isinstance(value, list):
                stats[group_name][field_name]["list_lengths"].append(len(value))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Analyze consolidated extraction quality"
    )
    parser.add_argument("--project-id", default=PROJECT_ID)
    args = parser.parse_args()

    conn = psycopg.connect(DB_DSN)
    cur = conn.cursor()

    # Fetch all consolidated extractions
    cur.execute(
        """
        SELECT extraction_type, source_group, data, provenance, source_count, grounded_count
        FROM consolidated_extractions
        WHERE project_id = %s
        ORDER BY extraction_type, source_group
        """,
        (args.project_id,),
    )
    rows = cur.fetchall()
    print(f"Total consolidated records: {len(rows)}")
    print()

    # ── Per-type summary ──
    type_stats: dict = defaultdict(
        lambda: {
            "count": 0,
            "total_sources": 0,
            "total_grounded": 0,
        }
    )
    for ext_type, sg, data, prov, src_count, grounded_count in rows:
        type_stats[ext_type]["count"] += 1
        type_stats[ext_type]["total_sources"] += src_count
        type_stats[ext_type]["total_grounded"] += grounded_count

    print("=" * 90)
    print(
        f"{'Extraction Type':<25} {'Records':>8} {'Avg Sources':>12} {'Avg Grounded':>13} {'Ground %':>9}"
    )
    print("-" * 90)
    for ext_type in sorted(type_stats.keys()):
        s = type_stats[ext_type]
        avg_src = s["total_sources"] / s["count"] if s["count"] else 0
        avg_gnd = s["total_grounded"] / s["count"] if s["count"] else 0
        gnd_pct = (
            100 * s["total_grounded"] / s["total_sources"] if s["total_sources"] else 0
        )
        print(
            f"{ext_type:<25} {s['count']:>8} {avg_src:>12.1f} {avg_gnd:>13.1f} {gnd_pct:>8.1f}%"
        )
    print("=" * 90)
    print()

    # ── Per-field fill rate analysis ──
    # Structure: stats[group_name][field_name] = {total, filled, empty, sample_values}
    def make_group():
        d = defaultdict(
            lambda: {
                "total": 0,
                "filled": 0,
                "empty": 0,
                "sample_values": set(),
                "list_lengths": [],
            }
        )
        d["_entity_count"] = 0
        return d

    field_stats: dict = defaultdict(make_group)

    # Entity list types (data looks like {"group_name": [...]})
    entity_list_types = set()
    flat_types = set()

    for ext_type, sg, data, prov, src_count, grounded_count in rows:
        if not data or not isinstance(data, dict):
            continue

        # Detect structure: entity list vs flat
        # Entity lists have a single key matching extraction_type containing a list
        keys = [k for k in data.keys() if not k.startswith("_")]
        if len(keys) == 1 and isinstance(data.get(keys[0]), list):
            entity_list_types.add(ext_type)
            analyze_entity_list(data[keys[0]], ext_type, field_stats)
        else:
            flat_types.add(ext_type)
            analyze_flat_fields(data, ext_type, field_stats)

    # ── Print field-level report ──
    print("FIELD-LEVEL FILL RATES")
    print("=" * 90)

    # Collect all fields for sorting by fill rate
    all_fields = []

    for group_name in sorted(field_stats.keys()):
        group = field_stats[group_name]
        is_entity = group_name in entity_list_types
        entity_count = group.get("_entity_count", 0)

        print(f"\n{'[ENTITY LIST]' if is_entity else '[FLAT]'} {group_name}")
        if is_entity:
            print(f"  Total entities: {entity_count}")
        print(
            f"  {'Field':<35} {'Filled':>8} {'Empty':>8} {'Total':>8} {'Fill %':>8} {'Empty %':>8}"
        )
        print(f"  {'-' * 78}")

        for field_name in sorted(group.keys()):
            if field_name.startswith("_"):
                continue
            f = group[field_name]
            if f["total"] == 0:
                continue
            fill_pct = 100 * f["filled"] / f["total"]
            empty_pct = 100 * f["empty"] / f["total"]
            marker = " ◄◄" if empty_pct > 50 else (" ◄" if empty_pct > 25 else "")
            print(
                f"  {field_name:<35} {f['filled']:>8} {f['empty']:>8} {f['total']:>8} {fill_pct:>7.1f}% {empty_pct:>7.1f}%{marker}"
            )

            all_fields.append(
                {
                    "group": group_name,
                    "field": field_name,
                    "filled": f["filled"],
                    "empty": f["empty"],
                    "total": f["total"],
                    "fill_pct": fill_pct,
                }
            )

            # Show sample values for low-fill fields
            if 0 < fill_pct < 30 and f["sample_values"]:
                samples = list(f["sample_values"])[:3]
                print(f"    samples: {samples}")

            # Show list length stats
            if f["list_lengths"]:
                lengths = f["list_lengths"]
                avg_len = sum(lengths) / len(lengths)
                non_empty = [l for l in lengths if l > 0]
                fill_rate = len(non_empty) / len(lengths) * 100
                print(
                    f"    list: avg_len={avg_len:.1f}, non_empty={len(non_empty)}/{len(lengths)} ({fill_rate:.1f}%)"
                )

    # ── Provenance quality analysis ──
    print("\n")
    print("PROVENANCE / GROUNDING QUALITY")
    print("=" * 90)

    prov_stats: dict = defaultdict(
        lambda: defaultdict(
            lambda: {
                "weights": [],
                "source_counts": [],
                "agreements": [],
                "grounded_counts": [],
            }
        )
    )

    for ext_type, sg, data, prov, src_count, grounded_count in rows:
        if not prov or not isinstance(prov, dict):
            continue

        for field_name, field_prov in prov.items():
            if field_name.startswith("_"):
                continue
            if not isinstance(field_prov, dict):
                continue

            p = prov_stats[ext_type][field_name]
            if "winning_weight" in field_prov:
                p["weights"].append(field_prov["winning_weight"])
            if "source_count" in field_prov:
                p["source_counts"].append(field_prov["source_count"])
            if "agreement" in field_prov:
                p["agreements"].append(field_prov["agreement"])
            if "grounded_count" in field_prov:
                p["grounded_counts"].append(field_prov["grounded_count"])

    for ext_type in sorted(prov_stats.keys()):
        print(f"\n{ext_type}")
        print(
            f"  {'Field':<35} {'Avg Weight':>10} {'Avg Agree':>10} {'Avg Srcs':>9} {'Avg Grnd':>9}"
        )
        print(f"  {'-' * 78}")
        for field_name in sorted(prov_stats[ext_type].keys()):
            p = prov_stats[ext_type][field_name]
            avg_w = sum(p["weights"]) / len(p["weights"]) if p["weights"] else 0
            avg_a = (
                sum(p["agreements"]) / len(p["agreements"]) if p["agreements"] else 0
            )
            avg_s = (
                sum(p["source_counts"]) / len(p["source_counts"])
                if p["source_counts"]
                else 0
            )
            avg_g = (
                sum(p["grounded_counts"]) / len(p["grounded_counts"])
                if p["grounded_counts"]
                else 0
            )
            marker = " ◄◄" if avg_w < 0.3 else (" ◄" if avg_w < 0.5 else "")
            print(
                f"  {field_name:<35} {avg_w:>10.3f} {avg_a:>10.3f} {avg_s:>9.1f} {avg_g:>9.1f}{marker}"
            )

    # ── Worst fields summary ──
    print("\n")
    print("WORST FIELDS BY FILL RATE (bottom 15)")
    print("=" * 90)
    all_fields.sort(key=lambda x: x["fill_pct"])
    print(f"{'Group':<25} {'Field':<30} {'Fill %':>8} {'Empty':>7} {'Total':>7}")
    print("-" * 90)
    for f in all_fields[:15]:
        print(
            f"{f['group']:<25} {f['field']:<30} {f['fill_pct']:>7.1f}% {f['empty']:>7} {f['total']:>7}"
        )

    conn.close()
    print("\nDone.")


if __name__ == "__main__":
    main()
