#!/usr/bin/env python3
"""Compute grounding scores on-the-fly for v1 data to get real quality metrics.

The wide analysis showed 99.1% grounding=0.0 because v1 never had backfill.
This script computes real grounding scores for a representative sample.

Usage:
    .venv/bin/python scripts/trial_grounding_realtime.py [--limit N]
"""

import re
import sys
from collections import Counter, defaultdict
from uuid import UUID

sys.path.insert(0, "src")

from sqlalchemy import select
from sqlalchemy.orm import Session

from database import engine
from orm_models import Extraction, Source
from services.extraction.grounding import verify_quote_in_source

_WS_RE = re.compile(r"\s+")
_STRIP_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def _normalize(s: str) -> str:
    return _WS_RE.sub(" ", s.lower().strip())


def extract_quote_pairs(data: dict, data_version: int) -> list[dict]:
    """Extract field data with quotes."""
    fields = []
    if data_version >= 2:
        for fname, fdata in data.items():
            if fname.startswith("_"):
                continue
            if isinstance(fdata, dict) and fdata.get("quote"):
                fields.append({
                    "field": fname, "value": fdata.get("value"),
                    "quote": fdata["quote"], "confidence": float(fdata.get("confidence", 0)),
                    "stored_grounding": float(fdata.get("grounding", 0)),
                })
            elif isinstance(fdata, list):
                for i, item in enumerate(fdata):
                    if isinstance(item, dict) and item.get("quote"):
                        fields.append({
                            "field": f"{fname}[{i}]", "value": item.get("value"),
                            "quote": item["quote"], "confidence": float(item.get("confidence", 0)),
                            "stored_grounding": float(item.get("grounding", 0)),
                        })
    else:
        quotes = data.get("_quotes", {})
        conf = float(data.get("confidence", 0))
        if isinstance(quotes, dict):
            for fname, q in quotes.items():
                if q and isinstance(q, str) and len(q) > 3:
                    fields.append({
                        "field": fname, "value": data.get(fname),
                        "quote": q, "confidence": conf,
                        "stored_grounding": 0.0,
                    })
    return fields


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=3000)
    parser.add_argument("--project-id", type=str, default="99a19141-9268-40a8-bc9e-ad1fa12243da")
    args = parser.parse_args()

    project_id = UUID(args.project_id)

    print(f"\n{'='*80}")
    print("REAL-TIME GROUNDING ANALYSIS")
    print(f"{'='*80}\n")

    with Session(engine) as session:
        query = (
            select(Extraction, Source.content, Source.cleaned_content)
            .join(Source, Extraction.source_id == Source.id)
            .where(Source.content.isnot(None))
            .where(Extraction.project_id == project_id)
            .limit(args.limit)
        )
        rows = session.execute(query).all()
        print(f"Loaded {len(rows)} extractions")

        # Collect all analyses
        all_items: list[dict] = []

        for ext, content, cleaned in rows:
            source_text = cleaned or content
            if not source_text or len(source_text) < 30:
                continue

            fields = extract_quote_pairs(ext.data, ext.data_version)
            for f in fields:
                quote = f["quote"]
                # Compute real grounding
                real_grounding = verify_quote_in_source(quote, source_text)

                # Check if value is in quote
                value_str = str(f["value"]) if f["value"] is not None else ""
                value_in_quote = False
                if value_str and len(value_str) > 1:
                    value_in_quote = _normalize(value_str) in _normalize(quote)

                # Check if quote is exact substring
                norm_q = _normalize(quote)
                norm_c = _normalize(source_text)
                exact_match = norm_q in norm_c

                all_items.append({
                    "ext_type": ext.extraction_type,
                    "source_group": ext.source_group,
                    "field": f["field"],
                    "value": value_str[:100],
                    "quote": quote,
                    "confidence": f["confidence"],
                    "grounding": real_grounding,
                    "value_in_quote": value_in_quote,
                    "exact_match": exact_match,
                    "quote_len": len(quote),
                })

        total = len(all_items)
        print(f"Analyzed {total} field-quote pairs\n")

        # ── 1. Grounding distribution (real) ──
        print(f"{'─'*60}")
        print("1. REAL GROUNDING SCORE DISTRIBUTION")
        print(f"{'─'*60}")

        g_buckets = Counter()
        for item in all_items:
            g = item["grounding"]
            if g >= 0.95:
                g_buckets["0.95-1.00"] += 1
            elif g >= 0.8:
                g_buckets["0.80-0.95"] += 1
            elif g >= 0.6:
                g_buckets["0.60-0.80"] += 1
            elif g >= 0.3:
                g_buckets["0.30-0.60"] += 1
            elif g > 0:
                g_buckets["0.01-0.30"] += 1
            else:
                g_buckets["0.00"] += 1

        for bucket in ["0.95-1.00", "0.80-0.95", "0.60-0.80", "0.30-0.60", "0.01-0.30", "0.00"]:
            count = g_buckets.get(bucket, 0)
            bar = "█" * int(count / total * 100 / 2)
            print(f"  {bucket:10s} {count:5d} ({count/total*100:5.1f}%) {bar}")

        well_grounded = sum(1 for i in all_items if i["grounding"] >= 0.8)
        ungrounded = sum(1 for i in all_items if i["grounding"] < 0.3)
        print(f"\n  Well grounded (>=0.8): {well_grounded}/{total} ({well_grounded/total*100:.1f}%)")
        print(f"  Poorly grounded (<0.3): {ungrounded}/{total} ({ungrounded/total*100:.1f}%)")

        # ── 2. Confidence vs Grounding (real) ──
        print(f"\n{'─'*60}")
        print("2. CONFIDENCE vs REAL GROUNDING")
        print(f"{'─'*60}")

        conf_buckets: dict[str, list] = defaultdict(list)
        for item in all_items:
            if item["confidence"] >= 0.9:
                b = "0.9-1.0"
            elif item["confidence"] >= 0.7:
                b = "0.7-0.9"
            elif item["confidence"] >= 0.5:
                b = "0.5-0.7"
            else:
                b = "0.0-0.5"
            conf_buckets[b].append(item)

        print(f"\n  {'Confidence':12s} {'N':>6s} {'Avg Ground':>10s} {'Ground>=0.8':>12s} {'Ground<0.3':>12s}")
        for b in ["0.9-1.0", "0.7-0.9", "0.5-0.7", "0.0-0.5"]:
            items = conf_buckets.get(b, [])
            if not items:
                continue
            n = len(items)
            avg_g = sum(i["grounding"] for i in items) / n
            good = sum(1 for i in items if i["grounding"] >= 0.8)
            bad = sum(1 for i in items if i["grounding"] < 0.3)
            print(f"  {b:12s} {n:6d} {avg_g:10.2f} {good/n*100:10.1f}% {bad/n*100:10.1f}%")

        # Overconfident: high confidence, low grounding
        overconfident = [i for i in all_items if i["confidence"] >= 0.8 and i["grounding"] < 0.3]
        print(f"\n  Overconfident (conf>=0.8 AND grounding<0.3): {len(overconfident)} ({len(overconfident)/total*100:.1f}%)")

        # ── 3. By extraction type ──
        print(f"\n{'─'*60}")
        print("3. GROUNDING BY EXTRACTION TYPE")
        print(f"{'─'*60}")

        by_type: dict[str, list] = defaultdict(list)
        for item in all_items:
            by_type[item["ext_type"]].append(item)

        for etype in sorted(by_type.keys()):
            items = by_type[etype]
            n = len(items)
            avg_g = sum(i["grounding"] for i in items) / n
            well_g = sum(1 for i in items if i["grounding"] >= 0.8)
            poor_g = sum(1 for i in items if i["grounding"] < 0.3)
            print(f"\n  {etype} (n={n}):")
            print(f"    Avg grounding: {avg_g:.2f}  Well(>=0.8): {well_g/n*100:.0f}%  Poor(<0.3): {poor_g/n*100:.0f}%")

        # ── 4. Field-level grounding bottlenecks ──
        print(f"\n{'─'*60}")
        print("4. FIELD-LEVEL GROUNDING BOTTLENECKS")
        print(f"{'─'*60}")

        by_field: dict[str, list] = defaultdict(list)
        for item in all_items:
            base = re.sub(r"\[\d+\]$", "", item["field"])
            by_field[f"{item['ext_type']}.{base}"].append(item)

        field_stats = []
        for fkey, items in by_field.items():
            n = len(items)
            if n < 5:
                continue
            avg_g = sum(i["grounding"] for i in items) / n
            poor = sum(1 for i in items if i["grounding"] < 0.3)
            avg_conf = sum(i["confidence"] for i in items) / n
            val_in_q = sum(1 for i in items if i["value_in_quote"]) / n * 100
            field_stats.append((fkey, n, avg_g, poor / n, avg_conf, val_in_q))

        field_stats.sort(key=lambda x: x[2])  # Sort by avg grounding ascending

        print(f"\n  Worst grounded fields:")
        print(f"  {'Field':50s} {'N':>5s} {'Avg_G':>6s} {'Poor%':>6s} {'Conf':>5s} {'Val∈Q':>6s}")
        for fk, n, avg_g, poor_rate, avg_c, viq in field_stats[:15]:
            print(f"  {fk:50s} {n:5d} {avg_g:6.2f} {poor_rate*100:5.1f}% {avg_c:5.2f} {viq:5.1f}%")

        print(f"\n  Best grounded fields:")
        for fk, n, avg_g, poor_rate, avg_c, viq in field_stats[-10:]:
            print(f"  {fk:50s} {n:5d} {avg_g:6.2f} {poor_rate*100:5.1f}% {avg_c:5.2f} {viq:5.1f}%")

        # ── 5. Overconfident examples ──
        if overconfident:
            print(f"\n{'─'*60}")
            print(f"5. OVERCONFIDENT EXAMPLES (conf>=0.8, grounding<0.3)")
            print(f"{'─'*60}")

            oc_fields = Counter(f"{i['ext_type']}.{re.sub(r'\\[\\d+\\]$', '', i['field'])}" for i in overconfident)
            print(f"\n  Top overconfident fields:")
            for field, count in oc_fields.most_common(10):
                print(f"    {field:50s} {count:4d}")

            print(f"\n  Examples:")
            for item in overconfident[:10]:
                print(f"    {item['source_group']}/{item['ext_type']}.{item['field']}")
                print(f"      conf={item['confidence']:.2f} ground={item['grounding']:.2f}")
                print(f"      quote: \"{item['quote'][:80]}\"")
                print(f"      value: \"{item['value'][:60]}\"")
                print()

        # ── 6. Value-in-quote analysis ──
        print(f"\n{'─'*60}")
        print("6. VALUE-IN-QUOTE ANALYSIS")
        print(f"{'─'*60}")

        has_val = [i for i in all_items if i["value"] and len(i["value"]) > 1]
        if has_val:
            viq_count = sum(1 for i in has_val if i["value_in_quote"])
            print(f"  Total with values: {len(has_val)}")
            print(f"  Value found in quote: {viq_count} ({viq_count/len(has_val)*100:.1f}%)")
            print(f"  Value NOT in quote: {len(has_val)-viq_count} ({(len(has_val)-viq_count)/len(has_val)*100:.1f}%)")

            # Fields where value is rarely in quote
            print(f"\n  Fields where value is rarely in quote:")
            for fk, n, avg_g, poor_rate, avg_c, viq in field_stats:
                if viq < 50 and n >= 10:
                    print(f"    {fk:50s}  val_in_quote: {viq:.0f}%  (n={n})")

        # ── 7. Quote provides no additional information ──
        print(f"\n{'─'*60}")
        print("7. QUOTES THAT ADD NO INFORMATION (value == quote)")
        print(f"{'─'*60}")

        value_is_quote = [i for i in all_items if i["value"]
                          and _normalize(i["value"]) == _normalize(i["quote"])]
        print(f"  Value exactly equals quote: {len(value_is_quote)} ({len(value_is_quote)/total*100:.1f}%)")
        if value_is_quote:
            viq_fields = Counter(f"{i['ext_type']}.{re.sub(r'\\[\\d+\\]$', '', i['field'])}" for i in value_is_quote)
            for field, count in viq_fields.most_common(10):
                print(f"    {field:50s} {count:4d}")

    print(f"\n{'='*80}")
    print("TRIAL COMPLETE")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    main()
