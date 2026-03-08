#!/usr/bin/env python3
"""Check actual null offset rate in v2 extractions stored in DB.

The TODO claims ~60% of v2 quotes have char_offset: null.
This script verifies that claim against stored extraction data.
"""

import sys
from collections import Counter
from uuid import UUID

sys.path.insert(0, "src")

from sqlalchemy import select
from sqlalchemy.orm import Session

from database import engine
from orm_models import Extraction


def main():
    print("\n" + "=" * 60)
    print("V2 Extraction Location Data Check")
    print("=" * 60)

    with Session(engine) as session:
        # Get all v2 extractions
        query = select(Extraction).where(Extraction.data_version == 2)
        exts = session.execute(query).scalars().all()
        print(f"\nTotal v2 extractions: {len(exts)}")

        total_quotes = 0
        null_offset = 0
        has_offset = 0
        no_location = 0
        no_quote = 0

        by_type = Counter()
        null_by_type = Counter()

        for ext in exts:
            if not isinstance(ext.data, dict):
                continue
            for fname, fdata in ext.data.items():
                if fname.startswith("_"):
                    continue
                items = []
                if isinstance(fdata, dict) and "quote" in fdata:
                    items.append(fdata)
                elif isinstance(fdata, list):
                    items.extend(x for x in fdata if isinstance(x, dict) and "quote" in x)

                for item in items:
                    quote = item.get("quote")
                    if not quote:
                        no_quote += 1
                        continue

                    total_quotes += 1
                    by_type[ext.extraction_type] += 1
                    loc = item.get("location")
                    if not loc:
                        no_location += 1
                        null_by_type[ext.extraction_type] += 1
                    elif loc.get("char_offset") is None:
                        null_offset += 1
                        null_by_type[ext.extraction_type] += 1
                    else:
                        has_offset += 1

        print(f"\nTotal quotes with text: {total_quotes}")
        print(f"  Has char_offset:     {has_offset} ({has_offset/total_quotes*100:.1f}%)")
        print(f"  Null char_offset:    {null_offset} ({null_offset/total_quotes*100:.1f}%)")
        print(f"  No location at all:  {no_location} ({no_location/total_quotes*100:.1f}%)")
        print(f"  No quote text:       {no_quote}")

        null_total = null_offset + no_location
        print(f"\n  TOTAL without position: {null_total} ({null_total/total_quotes*100:.1f}%)")

        if by_type:
            print(f"\nBy extraction type:")
            for etype in sorted(by_type.keys()):
                total = by_type[etype]
                nulls = null_by_type.get(etype, 0)
                print(f"  {etype:30s}  {nulls}/{total} null ({nulls/total*100:.1f}%)")


if __name__ == "__main__":
    main()
