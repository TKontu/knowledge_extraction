#!/usr/bin/env python3
"""Investigate the grounding 0.3-0.6 range — what passes the proposed gate?

These are the borderline cases: not clearly fabricated (grounding=0.0)
but not well grounded either. Are they legitimate or should the threshold be higher?
"""

import re
import sys
from collections import Counter
from uuid import UUID

sys.path.insert(0, "src")

from sqlalchemy import select
from sqlalchemy.orm import Session

from database import engine
from orm_models import Extraction, Source
from services.extraction.grounding import (
    _normalize_string,
    verify_quote_in_source,
)

_WS_RE = re.compile(r"\s+")
_STRIP_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


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

        middle_range = []  # grounding 0.3-0.8

        for ext, content, cleaned in rows:
            source_text = cleaned or content
            if not source_text:
                continue

            quotes = ext.data.get("_quotes", {})
            if not isinstance(quotes, dict):
                continue

            for fname, value in ext.data.items():
                if fname in (
                    "_quotes",
                    "_conflicts",
                    "_validation",
                    "_quote",
                    "confidence",
                ):
                    continue
                quote = quotes.get(fname)
                if not quote or not isinstance(quote, str) or len(quote) < 3:
                    continue

                grounding = verify_quote_in_source(quote, source_text)

                if 0.3 <= grounding < 0.8:
                    # Determine which tier produced this score
                    norm_q = _normalize_string(quote)
                    norm_c = _normalize_string(source_text)

                    tier1 = norm_q in norm_c

                    stripped_q = _STRIP_PUNCT_RE.sub("", norm_q)
                    stripped_q = re.sub(r"\s+", " ", stripped_q).strip()
                    stripped_c = _STRIP_PUNCT_RE.sub("", norm_c)
                    stripped_c = re.sub(r"\s+", " ", stripped_c).strip()
                    tier2 = stripped_q and stripped_q in stripped_c

                    # This score comes from word-window (tier 3)
                    # because tier1 would give 1.0, tier2 would give 0.95
                    tier = "word_window"
                    if tier1:
                        tier = "exact"
                    elif tier2:
                        tier = "punct_stripped"

                    # Find the best matching window for context
                    q_words = stripped_q.split()
                    c_words = stripped_c.split()
                    n = len(q_words)
                    best_window = ""
                    best_score = 0.0
                    if n <= len(c_words):
                        for i in range(len(c_words) - n + 1):
                            window = c_words[i : i + n]
                            q_set = set(q_words)
                            w_set = set(window)
                            overlap = len(q_set & w_set) / len(q_set) if q_set else 0
                            if overlap > best_score:
                                best_score = overlap
                                best_window = " ".join(window)

                    # Value in source check
                    val_str = str(value) if value is not None else ""
                    val_in_source = bool(
                        val_str and _normalize(val_str) in _normalize(source_text)
                    )

                    middle_range.append(
                        {
                            "field": fname,
                            "ext_type": ext.extraction_type,
                            "source_group": ext.source_group,
                            "value": val_str[:80],
                            "quote": quote[:150],
                            "grounding": grounding,
                            "tier": tier,
                            "best_window": best_window[:150],
                            "best_window_score": best_score,
                            "val_in_source": val_in_source,
                            "quote_len": len(quote),
                        }
                    )

        print(f"\n{'=' * 80}")
        print(f"GROUNDING MIDDLE RANGE (0.3-0.8): {len(middle_range)} cases")
        print(f"{'=' * 80}")

        if not middle_range:
            print("No cases in this range!")
            return

        # Score distribution within range
        print(f"\n{'─' * 60}")
        print("Score distribution:")
        print(f"{'─' * 60}")
        buckets = Counter()
        for item in middle_range:
            g = item["grounding"]
            if g < 0.4:
                buckets["0.30-0.40"] += 1
            elif g < 0.5:
                buckets["0.40-0.50"] += 1
            elif g < 0.6:
                buckets["0.50-0.60"] += 1
            elif g < 0.7:
                buckets["0.60-0.70"] += 1
            else:
                buckets["0.70-0.80"] += 1

        for b in ["0.30-0.40", "0.40-0.50", "0.50-0.60", "0.60-0.70", "0.70-0.80"]:
            count = buckets.get(b, 0)
            bar = "█" * count
            print(f"  {b}: {count:3d} {bar}")

        # Value in source?
        vis = sum(1 for i in middle_range if i["val_in_source"])
        print(
            f"\n  Value found in source: {vis}/{len(middle_range)} ({vis / len(middle_range) * 100:.0f}%)"
        )

        # By field
        print(f"\n{'─' * 60}")
        print("By field:")
        print(f"{'─' * 60}")
        by_field = Counter(f"{i['ext_type']}.{i['field']}" for i in middle_range)
        for field, count in by_field.most_common(15):
            items = [
                i for i in middle_range if f"{i['ext_type']}.{i['field']}" == field
            ]
            avg_g = sum(i["grounding"] for i in items) / len(items)
            vis_pct = sum(1 for i in items if i["val_in_source"]) / len(items) * 100
            print(
                f"  {field:50s} n={count:3d}  avg_g={avg_g:.2f}  val_in_src={vis_pct:.0f}%"
            )

        # Show ALL examples grouped by quality assessment
        print(f"\n{'─' * 60}")
        print("ALL CASES (is this data useful or noise?):")
        print(f"{'─' * 60}")

        for item in sorted(middle_range, key=lambda x: x["grounding"]):
            g = item["grounding"]
            vis = "✓" if item["val_in_source"] else "✗"
            print(
                f"\n  g={g:.2f} {vis} [{item['source_group']}/{item['ext_type']}.{item['field']}]"
            )
            print(f'    value: "{item["value"]}"')
            print(f'    quote: "{item["quote"]}"')
            print(
                f'    best window ({item["best_window_score"]:.2f}): "{item["best_window"][:120]}"'
            )


if __name__ == "__main__":
    main()
