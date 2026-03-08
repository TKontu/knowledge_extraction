#!/usr/bin/env python3
"""Extract detailed examples from each failure category to understand patterns.

Shows quote vs source content side-by-side for each category.
"""

import re
import sys
from collections import defaultdict
from uuid import UUID

sys.path.insert(0, "src")

from sqlalchemy import select
from sqlalchemy.orm import Session

from database import engine
from orm_models import Extraction, Source

_WS_RE = re.compile(r"\s+")
_STRIP_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MD_BOLD_ITALIC_RE = re.compile(r"(\*{1,3}|_{1,3})(.+?)\1")
_MD_TABLE_SEP_RE = re.compile(r"^\|[\s\-:]+\|[\s\-:|]*$", re.MULTILINE)
_MD_INLINE_CODE_RE = re.compile(r"`([^`]+)`")


def _normalize(s: str) -> str:
    return _WS_RE.sub(" ", s.lower().strip())


def _strip_markdown(text: str) -> str:
    result = text
    result = _MD_IMAGE_RE.sub(r"\1", result)
    result = _MD_LINK_RE.sub(r"\1", result)
    result = _MD_BOLD_ITALIC_RE.sub(r"\2", result)
    result = _MD_TABLE_SEP_RE.sub("", result)
    result = _MD_INLINE_CODE_RE.sub(r"\1", result)
    result = result.replace("|", " ")
    return result


def _strip_punct(s: str) -> str:
    s = _STRIP_PUNCT_RE.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


def find_best_window(quote_words: list[str], content: str, window_size: int | None = None) -> tuple[float, str]:
    """Find best matching window in content, return (score, matched_text)."""
    content_words = content.split()
    n = window_size or len(quote_words)
    if not quote_words or n > len(content_words):
        return 0.0, ""

    quote_set = set(quote_words)
    best_score = 0.0
    best_start = 0

    for i in range(len(content_words) - n + 1):
        window = content_words[i:i + n]
        overlap = len(quote_set & set(window)) / len(quote_set)
        if overlap > best_score:
            best_score = overlap
            best_start = i

    matched = " ".join(content_words[best_start:best_start + n])
    return best_score, matched


def classify_and_detail(quote: str, content: str) -> dict:
    norm_q = _normalize(quote)
    norm_c = _normalize(content)

    # Tier 1
    if norm_q in norm_c:
        return {"category": "tier1", "detail": ""}

    # Punct-strip
    stripped_q = _strip_punct(norm_q)
    stripped_c = _strip_punct(norm_c)
    t2a = stripped_q and stripped_q in stripped_c

    # MD-strip
    md_c = _normalize(_strip_markdown(content))
    t2b = norm_q in md_c

    # MD+punct
    t2c = stripped_q and stripped_q in _strip_punct(md_c)

    # Word analysis
    quote_words = norm_q.split()
    content_words_set = set(norm_c.split())
    present = [w for w in quote_words if w in content_words_set]
    missing = [w for w in quote_words if w not in content_words_set]
    overlap = len(present) / len(quote_words) if quote_words else 0

    # Find best matching region in source
    best_score, best_match = find_best_window(quote_words, _strip_punct(md_c))

    # Classify
    if t2a:
        cat = "punct_strip"
        # Find what punct differs
        diff_chars = set(norm_q) - set(stripped_q)
        detail = f"punct_removed: {diff_chars}"
    elif t2b:
        cat = "md_strip"
        detail = "markdown syntax blocking match"
    elif t2c:
        cat = "md_plus_punct"
        detail = "needs both md + punct strip"
    elif overlap >= 0.8:
        cat = "reworded"
        detail = f"missing_words: {missing}"
    elif overlap >= 0.5:
        cat = "partial"
        detail = f"missing: {missing}"
    elif len(quote.strip()) < 20 and overlap < 0.3:
        cat = "fabricated"
        detail = f"short value, overlap={overlap:.2f}"
    elif overlap < 0.3:
        cat = "hallucinated"
        detail = f"overlap={overlap:.2f}"
    else:
        cat = "other"
        detail = f"overlap={overlap:.2f}"

    return {
        "category": cat,
        "detail": detail,
        "overlap": overlap,
        "present_words": present,
        "missing_words": missing,
        "best_window_score": best_score,
        "best_window_text": best_match,
    }


def extract_quotes(data: dict, data_version: int) -> list[tuple[str, str]]:
    pairs = []
    if data_version >= 2:
        for fname, fdata in data.items():
            if fname.startswith("_"):
                continue
            if isinstance(fdata, dict) and fdata.get("quote"):
                pairs.append((fname, fdata["quote"]))
            elif isinstance(fdata, list):
                for i, item in enumerate(fdata):
                    if isinstance(item, dict) and item.get("quote"):
                        pairs.append((f"{fname}[{i}]", item["quote"]))
    else:
        quotes = data.get("_quotes", {})
        if isinstance(quotes, dict):
            for fname, q in quotes.items():
                if q and len(q) > 3:
                    pairs.append((fname, q))
    return pairs


def main():
    project_id = UUID("99a19141-9268-40a8-bc9e-ad1fa12243da")

    with Session(engine) as session:
        query = (
            select(Extraction, Source.content, Source.cleaned_content)
            .join(Source, Extraction.source_id == Source.id)
            .where(Source.content.isnot(None))
            .where(Extraction.project_id == project_id)
            .limit(2000)
        )
        rows = session.execute(query).all()

        by_cat: dict[str, list] = defaultdict(list)

        for ext, content, cleaned in rows:
            source_text = cleaned or content
            if not source_text or len(source_text) < 50:
                continue
            for fname, quote in extract_quotes(ext.data, ext.data_version):
                result = classify_and_detail(quote, source_text)
                if result["category"] == "tier1":
                    continue
                result["quote"] = quote
                result["field"] = fname
                result["source_group"] = ext.source_group
                result["ext_type"] = ext.extraction_type
                result["content_snippet"] = source_text[:5000]
                by_cat[result["category"]].append(result)

        # Show detailed examples per category
        for cat in ["reworded", "punct_strip", "partial", "hallucinated",
                     "fabricated", "md_plus_punct", "md_strip", "other"]:
            examples = by_cat.get(cat, [])
            if not examples:
                continue
            print(f"\n{'='*80}")
            print(f"  {cat.upper()} — {len(examples)} cases")
            print(f"{'='*80}")

            for ex in examples[:8]:
                print(f"\n  [{ex['source_group']} / {ex['ext_type']}.{ex['field']}]")
                print(f"  Quote:   \"{ex['quote'][:150]}\"")
                print(f"  Detail:  {ex['detail']}")
                if ex.get("missing_words"):
                    print(f"  Missing: {ex['missing_words'][:10]}")
                if ex.get("best_window_score", 0) > 0.3:
                    print(f"  Best window ({ex['best_window_score']:.2f}): \"{ex['best_window_text'][:150]}\"")
                print()

        # Summary
        print(f"\n{'='*80}")
        print("SUMMARY")
        print(f"{'='*80}")
        total = sum(len(v) for v in by_cat.values())
        for cat in ["reworded", "punct_strip", "partial", "hallucinated",
                     "fabricated", "md_plus_punct", "md_strip", "other"]:
            count = len(by_cat.get(cat, []))
            print(f"  {cat:20s} {count:4d} ({count/total*100:.1f}%)")
        print(f"  {'TOTAL':20s} {total:4d}")


if __name__ == "__main__":
    main()
