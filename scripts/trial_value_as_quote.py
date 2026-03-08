#!/usr/bin/env python3
"""Analyze the value-as-quote echo problem in detail.

Questions:
- When value==quote, is the value actually in the source? (legitimate short match)
- What's the grounding score for these?
- Can we distinguish legitimate from lazy quoting?
- What quote length threshold separates good from bad?
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


def _normalize(s: str) -> str:
    return _WS_RE.sub(" ", s.lower().strip())


def main():
    project_id = UUID("99a19141-9268-40a8-bc9e-ad1fa12243da")

    with Session(engine) as session:
        query = (
            select(Extraction, Source.content, Source.cleaned_content)
            .join(Source, Extraction.source_id == Source.id)
            .where(Source.content.isnot(None))
            .where(Extraction.project_id == project_id)
            .limit(3000)
        )
        rows = session.execute(query).all()

        value_eq_quote = []
        value_neq_quote = []
        total_with_quotes = 0

        for ext, content, cleaned in rows:
            source_text = cleaned or content
            if not source_text:
                continue

            quotes = ext.data.get("_quotes", {})
            if not isinstance(quotes, dict):
                continue

            for fname, value in ext.data.items():
                if fname in ("_quotes", "_conflicts", "_validation", "_quote", "confidence"):
                    continue
                quote = quotes.get(fname)
                if not quote or not isinstance(quote, str) or len(quote) < 2:
                    continue

                total_with_quotes += 1
                value_str = str(value) if value is not None else ""

                is_echo = value_str and _normalize(value_str) == _normalize(quote)
                grounding = verify_quote_in_source(quote, source_text)
                value_in_source = bool(value_str and _normalize(value_str) in _normalize(source_text))
                quote_in_source = _normalize(quote) in _normalize(source_text)

                item = {
                    "field": fname,
                    "ext_type": ext.extraction_type,
                    "source_group": ext.source_group,
                    "value": value_str[:100],
                    "quote": quote[:200],
                    "grounding": grounding,
                    "value_in_source": value_in_source,
                    "quote_in_source": quote_in_source,
                    "quote_len": len(quote),
                    "value_len": len(value_str),
                }

                if is_echo:
                    value_eq_quote.append(item)
                else:
                    value_neq_quote.append(item)

        print(f"\n{'='*80}")
        print("VALUE-AS-QUOTE ECHO ANALYSIS")
        print(f"{'='*80}")
        print(f"\nTotal quotes: {total_with_quotes}")
        print(f"Value == Quote (echo): {len(value_eq_quote)} ({len(value_eq_quote)/total_with_quotes*100:.1f}%)")
        print(f"Value != Quote (normal): {len(value_neq_quote)}")

        # ── Echoed quotes: are they in the source? ──
        print(f"\n{'─'*60}")
        print("ECHOED QUOTES (value == quote): Are they legitimate?")
        print(f"{'─'*60}")

        echo_grounded = sum(1 for i in value_eq_quote if i["grounding"] >= 0.8)
        echo_in_source = sum(1 for i in value_eq_quote if i["value_in_source"])
        echo_not_in_source = sum(1 for i in value_eq_quote if not i["value_in_source"])

        print(f"\n  Value found in source:     {echo_in_source} ({echo_in_source/len(value_eq_quote)*100:.1f}%)")
        print(f"  Value NOT in source:        {echo_not_in_source} ({echo_not_in_source/len(value_eq_quote)*100:.1f}%)")
        print(f"  Quote grounded (>=0.8):     {echo_grounded} ({echo_grounded/len(value_eq_quote)*100:.1f}%)")

        # These are the problematic ones: echoed AND not in source
        print(f"\n  TRULY BAD: echoed AND not in source = {echo_not_in_source}")
        print(f"  LEGITIMATE: echoed AND in source = {echo_in_source} (value IS the source text)")

        # ── By field ──
        print(f"\n  By field:")
        by_field = defaultdict(lambda: {"total": 0, "in_src": 0, "not_in_src": 0})
        for i in value_eq_quote:
            key = f"{i['ext_type']}.{i['field']}"
            by_field[key]["total"] += 1
            if i["value_in_source"]:
                by_field[key]["in_src"] += 1
            else:
                by_field[key]["not_in_src"] += 1

        for fk in sorted(by_field.keys(), key=lambda k: -by_field[k]["total"]):
            d = by_field[fk]
            if d["total"] < 3:
                continue
            print(f"    {fk:45s} total={d['total']:4d}  in_src={d['in_src']:4d}  NOT_in_src={d['not_in_src']:4d}")

        # ── Quote length analysis ──
        print(f"\n{'─'*60}")
        print("QUOTE LENGTH: echoed vs normal")
        print(f"{'─'*60}")

        echo_lens = [i["quote_len"] for i in value_eq_quote]
        normal_lens = [i["quote_len"] for i in value_neq_quote]

        print(f"\n  Echoed:  min={min(echo_lens)}  max={max(echo_lens)}  "
              f"avg={sum(echo_lens)/len(echo_lens):.0f}  median={sorted(echo_lens)[len(echo_lens)//2]}")
        print(f"  Normal:  min={min(normal_lens)}  max={max(normal_lens)}  "
              f"avg={sum(normal_lens)/len(normal_lens):.0f}  median={sorted(normal_lens)[len(normal_lens)//2]}")

        # Length distribution
        print(f"\n  Length distribution of echoed quotes:")
        len_buckets = Counter()
        for l in echo_lens:
            if l < 10:
                len_buckets["<10"] += 1
            elif l < 20:
                len_buckets["10-20"] += 1
            elif l < 30:
                len_buckets["20-30"] += 1
            elif l < 50:
                len_buckets["30-50"] += 1
            else:
                len_buckets["50+"] += 1

        for b in ["<10", "10-20", "20-30", "30-50", "50+"]:
            count = len_buckets.get(b, 0)
            bar = "█" * (count // 2)
            print(f"    {b:8s} {count:4d} {bar}")

        # ── Examples of legitimate echoes (value IS in source) ──
        print(f"\n{'─'*60}")
        print("LEGITIMATE ECHOES (value exists in source as-is):")
        print(f"{'─'*60}")
        legit = [i for i in value_eq_quote if i["value_in_source"]][:8]
        for i in legit:
            print(f"  {i['source_group']}/{i['ext_type']}.{i['field']}")
            print(f"    value=quote=\"{i['value'][:80]}\"  ground={i['grounding']:.2f}")

        # ── Examples of bad echoes (value NOT in source) ──
        print(f"\n{'─'*60}")
        print("BAD ECHOES (value fabricated, echoed as quote):")
        print(f"{'─'*60}")
        bad = [i for i in value_eq_quote if not i["value_in_source"]][:8]
        for i in bad:
            print(f"  {i['source_group']}/{i['ext_type']}.{i['field']}")
            print(f"    value=quote=\"{i['value'][:80]}\"  ground={i['grounding']:.2f}")

        # ── Can grounding alone distinguish them? ──
        print(f"\n{'─'*60}")
        print("CAN GROUNDING DISTINGUISH LEGITIMATE vs BAD ECHOES?")
        print(f"{'─'*60}")

        legit_items = [i for i in value_eq_quote if i["value_in_source"]]
        bad_items = [i for i in value_eq_quote if not i["value_in_source"]]

        if legit_items:
            avg_g_legit = sum(i["grounding"] for i in legit_items) / len(legit_items)
            high_g_legit = sum(1 for i in legit_items if i["grounding"] >= 0.8)
            print(f"\n  Legitimate (value in source, n={len(legit_items)}):")
            print(f"    Avg grounding: {avg_g_legit:.2f}  Ground>=0.8: {high_g_legit/len(legit_items)*100:.0f}%")

        if bad_items:
            avg_g_bad = sum(i["grounding"] for i in bad_items) / len(bad_items)
            high_g_bad = sum(1 for i in bad_items if i["grounding"] >= 0.8)
            print(f"\n  Bad (value NOT in source, n={len(bad_items)}):")
            print(f"    Avg grounding: {avg_g_bad:.2f}  Ground>=0.8: {high_g_bad/len(bad_items)*100:.0f}%")

        print(f"\n  CONCLUSION: Grounding score {'CAN' if avg_g_legit > 0.7 and avg_g_bad < 0.3 else 'CANNOT'} "
              f"distinguish them (legit={avg_g_legit:.2f} vs bad={avg_g_bad:.2f})")


if __name__ == "__main__":
    main()
