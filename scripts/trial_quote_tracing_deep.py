#!/usr/bin/env python3
"""Deep analysis of unmatched quotes — what's actually failing and why?

Investigates:
1. Are unmatched quotes actually grounded? (verify_quote_in_source says yes?)
2. What patterns cause failures? (hallucination, paraphrasing, unicode, etc.)
3. What does the existing grounding verifier say about them?
4. What would a Tier 2.5 (punctuation-stripped like grounding.py) catch?

Usage:
    .venv/bin/python scripts/trial_quote_tracing_deep.py [--project-id UUID] [--limit N]
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
from services.extraction.grounding import verify_quote_in_source

_WS_RE = re.compile(r"\s+")
_STRIP_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)

# Markdown patterns
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


def classify_failure(quote: str, content: str) -> dict:
    """Classify why a quote failed to match, with detailed diagnostics."""
    norm_q = _normalize(quote)
    norm_c = _normalize(content)

    # Check existing grounding verifier
    grounding_score = verify_quote_in_source(quote, content)

    # Tier 1: normalized substring
    t1 = norm_q in norm_c

    # Tier 2a: punctuation-stripped (what grounding.py does)
    stripped_q = _strip_punct(norm_q)
    stripped_c = _strip_punct(norm_c)
    t2a_punct = stripped_q and stripped_q in stripped_c

    # Tier 2b: markdown-stripped
    md_stripped = _strip_markdown(content)
    norm_md = _normalize(md_stripped)
    t2b_md = norm_q in norm_md

    # Tier 2c: markdown-stripped + punctuation-stripped
    t2c_both = _strip_punct(_normalize(_strip_markdown(content)))
    t2c = stripped_q and stripped_q in t2c_both

    # Check if quote words are individually present
    quote_words = set(norm_q.split())
    content_words = set(norm_c.split())
    word_overlap = (
        len(quote_words & content_words) / len(quote_words) if quote_words else 0
    )

    # Check if it's a short value-like quote (classification, enum, etc.)
    is_short = len(quote.strip()) < 20
    is_value_like = re.match(r"^[\w\s,\-\.]+$", quote.strip()) and is_short

    # Check for unicode mismatches (em-dash, fancy quotes, etc.)
    has_unicode = any(ord(c) > 127 for c in quote)

    # Classify
    if t1:
        category = "tier1_match"  # shouldn't happen in unmatched
    elif t2a_punct:
        category = "punct_strip_fixes"  # grounding.py Tier 2 catches it
    elif t2b_md:
        category = "md_strip_fixes"  # our Tier 2 catches it
    elif t2c:
        category = "md_plus_punct_fixes"  # need both md + punct stripping
    elif word_overlap >= 0.8:
        category = "high_overlap_reworded"  # nearly all words present, reordered
    elif word_overlap >= 0.5:
        category = "partial_overlap"  # some words match, significant paraphrasing
    elif is_value_like:
        category = "fabricated_value"  # short value not in source at all
    elif word_overlap < 0.3:
        category = "hallucinated"  # quote content barely exists in source
    else:
        category = "other"

    return {
        "category": category,
        "grounding_score": grounding_score,
        "word_overlap": word_overlap,
        "t2a_punct": t2a_punct,
        "t2b_md": t2b_md,
        "t2c_both": t2c,
        "is_short": is_short,
        "has_unicode": has_unicode,
        "quote_len": len(quote),
    }


def extract_quotes(data: dict, data_version: int) -> list[tuple[str, str]]:
    """Extract (field_name, quote) pairs."""
    pairs = []
    if data_version >= 2:
        for fname, fdata in data.items():
            if fname.startswith("_"):
                continue
            if isinstance(fdata, dict):
                q = fdata.get("quote")
                if q and isinstance(q, str) and len(q) > 3:
                    pairs.append((fname, q))
            elif isinstance(fdata, list):
                for i, item in enumerate(fdata):
                    if isinstance(item, dict):
                        q = item.get("quote")
                        if q and isinstance(q, str) and len(q) > 3:
                            pairs.append((f"{fname}[{i}]", q))
    else:
        quotes = data.get("_quotes", {})
        if isinstance(quotes, dict):
            for fname, q in quotes.items():
                if q and isinstance(q, str) and len(q) > 3:
                    pairs.append((fname, q))
    return pairs


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--project-id", type=str, default="99a19141-9268-40a8-bc9e-ad1fa12243da"
    )
    parser.add_argument("--limit", type=int, default=2000)
    args = parser.parse_args()

    project_id = UUID(args.project_id) if args.project_id else None

    print(f"\n{'=' * 80}")
    print("DEEP ANALYSIS: Why do quotes fail to locate?")
    print(f"{'=' * 80}\n")

    with Session(engine) as session:
        query = (
            select(Extraction, Source.content, Source.cleaned_content)
            .join(Source, Extraction.source_id == Source.id)
            .where(Source.content.isnot(None))
        )
        if project_id:
            query = query.where(Extraction.project_id == project_id)
        query = query.limit(args.limit)

        rows = session.execute(query).all()
        print(f"Loaded {len(rows)} extractions")

        # Collect ALL quotes and their tier 1 match status
        all_quotes = []
        unmatched_details = []
        total_quotes = 0
        t1_matches = 0

        for ext, content, cleaned_content in rows:
            source_text = cleaned_content or content
            if not source_text or len(source_text) < 50:
                continue

            quote_pairs = extract_quotes(ext.data, ext.data_version)
            for fname, quote in quote_pairs:
                total_quotes += 1
                norm_q = _normalize(quote)
                norm_c = _normalize(source_text)
                if norm_q in norm_c:
                    t1_matches += 1
                else:
                    diag = classify_failure(quote, source_text)
                    diag["field_name"] = fname
                    diag["extraction_type"] = ext.extraction_type
                    diag["source_group"] = ext.source_group
                    diag["quote"] = quote
                    # Store small content snippet for display
                    diag["content_preview"] = source_text[:300]
                    unmatched_details.append(diag)

        print(f"\nTotal quotes: {total_quotes}")
        print(f"Tier 1 matches: {t1_matches} ({t1_matches / total_quotes * 100:.1f}%)")
        print(
            f"Need further matching: {len(unmatched_details)} ({len(unmatched_details) / total_quotes * 100:.1f}%)"
        )

        # ── Categorize failures ──
        categories = Counter(d["category"] for d in unmatched_details)
        print(f"\n{'─' * 60}")
        print("Failure categories (for quotes Tier 1 misses):")
        print(f"{'─' * 60}")
        for cat, count in categories.most_common():
            pct_of_unmatched = count / len(unmatched_details) * 100
            pct_of_total = count / total_quotes * 100
            print(
                f"  {cat:30s} {count:4d} ({pct_of_unmatched:5.1f}% of unmatched, {pct_of_total:4.1f}% of all)"
            )

        # ── What does the existing grounding verifier say? ──
        grounded_scores = [d["grounding_score"] for d in unmatched_details]
        grounded_count = sum(1 for s in grounded_scores if s >= 0.8)
        print(f"\n{'─' * 60}")
        print("Existing grounding verifier on unmatched quotes:")
        print(f"{'─' * 60}")
        print(
            f"  Grounded (score >= 0.8): {grounded_count}/{len(unmatched_details)} "
            f"({grounded_count / len(unmatched_details) * 100:.1f}%)"
        )
        print(
            "  These are quotes the verifier confirms exist but locate_in_source can't find"
        )

        # Score distribution
        score_buckets = Counter()
        for s in grounded_scores:
            if s >= 0.95:
                score_buckets["0.95-1.0"] += 1
            elif s >= 0.8:
                score_buckets["0.80-0.95"] += 1
            elif s >= 0.6:
                score_buckets["0.60-0.80"] += 1
            elif s >= 0.3:
                score_buckets["0.30-0.60"] += 1
            else:
                score_buckets["0.00-0.30"] += 1

        print("  Score distribution:")
        for bucket in ["0.95-1.0", "0.80-0.95", "0.60-0.80", "0.30-0.60", "0.00-0.30"]:
            count = score_buckets.get(bucket, 0)
            print(f"    {bucket}: {count:4d}")

        # ── Recovery potential ──
        print(f"\n{'─' * 60}")
        print("Recovery potential (what each tier would add):")
        print(f"{'─' * 60}")

        recoverable_punct = sum(1 for d in unmatched_details if d["t2a_punct"])
        recoverable_md = sum(
            1 for d in unmatched_details if d["t2b_md"] and not d["t2a_punct"]
        )
        recoverable_both = sum(
            1
            for d in unmatched_details
            if d["t2c_both"] and not d["t2a_punct"] and not d["t2b_md"]
        )
        high_overlap = sum(
            1
            for d in unmatched_details
            if d["word_overlap"] >= 0.6
            and not d["t2a_punct"]
            and not d["t2b_md"]
            and not d["t2c_both"]
        )

        print(f"  Punct-strip only:     {recoverable_punct:4d} (would catch)")
        print(f"  MD-strip only:        {recoverable_md:4d} (would catch)")
        print(f"  MD+punct combined:    {recoverable_both:4d} (would catch)")
        print(f"  Fuzzy (overlap>=0.6): {high_overlap:4d} (would catch)")
        total_recoverable = (
            recoverable_punct + recoverable_md + recoverable_both + high_overlap
        )
        still_unmatched = len(unmatched_details) - total_recoverable
        print(f"  Still unmatched:      {still_unmatched:4d}")
        print(
            f"\n  Projected total match rate: {(t1_matches + total_recoverable) / total_quotes * 100:.1f}%"
        )

        # ── By extraction type ──
        print(f"\n{'─' * 60}")
        print("Failure categories by extraction_type:")
        print(f"{'─' * 60}")
        by_type: dict[str, Counter] = {}
        for d in unmatched_details:
            t = d["extraction_type"]
            if t not in by_type:
                by_type[t] = Counter()
            by_type[t][d["category"]] += 1

        for etype, cats in sorted(by_type.items()):
            total_type = sum(cats.values())
            print(f"\n  {etype} ({total_type} unmatched):")
            for cat, count in cats.most_common():
                print(f"    {cat:30s} {count:3d}")

        # ── Show examples per category ──
        print(f"\n{'─' * 60}")
        print("Examples per failure category:")
        print(f"{'─' * 60}")
        shown_cats = set()
        for d in unmatched_details:
            cat = d["category"]
            if cat in shown_cats:
                continue
            shown_cats.add(cat)
            print(f"\n  [{cat}]")
            # Show up to 3 examples
            examples = [x for x in unmatched_details if x["category"] == cat][:3]
            for ex in examples:
                print(
                    f"    {ex['source_group']} / {ex['extraction_type']}.{ex['field_name']}"
                )
                print(f"    Quote:    '{ex['quote'][:100]}'")
                print(
                    f"    Grounding: {ex['grounding_score']:.2f}  WordOverlap: {ex['word_overlap']:.2f}"
                )
                if ex["has_unicode"]:
                    print("    [has unicode chars]")
                print()


if __name__ == "__main__":
    main()
