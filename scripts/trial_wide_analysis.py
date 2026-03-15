#!/usr/bin/env python3
"""Wide trial: Analyze quote/grounding quality across ALL projects and schemas.

Goes beyond position matching to analyze LLM extraction quality:
1. Coverage by project/schema/field type
2. Quote quality patterns (fabrication, paraphrasing, laziness)
3. Grounding score distributions
4. Field-level precision bottlenecks
5. LLM behavioral patterns (what it ignores, where it's imprecise)

Usage:
    .venv/bin/python scripts/trial_wide_analysis.py [--limit N]
"""

import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass

sys.path.insert(0, "src")

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from database import engine
from orm_models import Extraction, Project, Source

_WS_RE = re.compile(r"\s+")
_STRIP_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MD_BOLD_ITALIC_RE = re.compile(r"(\*{1,3}|_{1,3})(.+?)\1")


def _normalize(s: str) -> str:
    return _WS_RE.sub(" ", s.lower().strip())


def _strip_markdown(text: str) -> str:
    result = _MD_IMAGE_RE.sub(r"\1", text)
    result = _MD_LINK_RE.sub(r"\1", result)
    result = _MD_BOLD_ITALIC_RE.sub(r"\2", result)
    result = result.replace("|", " ")
    return result


def _strip_punct(s: str) -> str:
    s = _STRIP_PUNCT_RE.sub("", s)
    return re.sub(r"\s+", " ", s).strip()


# ── Quote quality classification ──


@dataclass
class QuoteAnalysis:
    project_name: str
    source_group: str
    extraction_type: str
    field_name: str
    data_version: int
    quote: str
    value: str | None
    confidence: float
    grounding: float  # from stored data or computed
    quote_len: int
    content_len: int
    # Classification
    category: str  # see classify_quote()
    word_overlap: float
    # Position matching
    tier1_match: bool
    tier_any_match: bool


def classify_quote(
    quote: str, content: str, value: str | None = None
) -> tuple[str, float]:
    """Classify quote quality and return (category, word_overlap)."""
    if not quote or not content:
        return "empty", 0.0

    norm_q = _normalize(quote)
    norm_c = _normalize(content)

    # Check Tier 1
    if norm_q in norm_c:
        return "exact_match", 1.0

    # Word overlap
    q_words = set(norm_q.split())
    c_words = set(norm_c.split())
    overlap = len(q_words & c_words) / len(q_words) if q_words else 0

    # Check with punct/md stripping
    stripped_q = _strip_punct(norm_q)
    stripped_c = _strip_punct(_normalize(_strip_markdown(content)))
    has_punct_match = stripped_q and stripped_q in stripped_c

    # Classify
    quote_lower = quote.strip().lower()

    # "No mention of..." / "Not explicitly mentioned" patterns
    negation_patterns = [
        "no mention",
        "not mentioned",
        "no explicit",
        "not explicitly",
        "not specified",
        "no specific",
        "n/a",
        "none mentioned",
        "not available",
        "no information",
        "not provided",
        "no data",
        "not found",
        "no certifications",
        "no details",
    ]
    if any(p in quote_lower for p in negation_patterns):
        return "negation_quote", overlap

    if has_punct_match:
        return "punct_fixable", overlap

    # Very short fabricated values
    if len(quote.strip()) < 15 and overlap < 0.3:
        return "fabricated_short", overlap

    # Value == quote (LLM just echoed the value as the quote)
    if value and _normalize(str(value)) == norm_q:
        return "value_as_quote", overlap

    # All words present but reordered/reformatted
    if overlap >= 0.85:
        return "reworded_minor", overlap

    if overlap >= 0.6:
        return "reworded_major", overlap

    if overlap >= 0.3:
        return "paraphrased", overlap

    if overlap < 0.1:
        return "hallucinated", overlap

    return "low_overlap", overlap


# ── Data extraction ──


def extract_fields_v2(data: dict) -> list[dict]:
    """Extract all fields from v2 extraction data."""
    fields = []
    if not isinstance(data, dict):
        return fields
    for fname, fdata in data.items():
        if fname.startswith("_"):
            continue
        if isinstance(fdata, dict):
            fields.append(
                {
                    "field_name": fname,
                    "value": fdata.get("value"),
                    "confidence": float(fdata.get("confidence", 0)),
                    "quote": fdata.get("quote"),
                    "grounding": float(fdata.get("grounding", 0)),
                    "location": fdata.get("location"),
                }
            )
        elif isinstance(fdata, list):
            for i, item in enumerate(fdata):
                if isinstance(item, dict):
                    fields.append(
                        {
                            "field_name": f"{fname}[{i}]",
                            "value": item.get("value"),
                            "confidence": float(item.get("confidence", 0)),
                            "quote": item.get("quote"),
                            "grounding": float(item.get("grounding", 0)),
                            "location": item.get("location"),
                        }
                    )
    return fields


def extract_fields_v1(data: dict, grounding_scores: dict | None = None) -> list[dict]:
    """Extract all fields from v1 extraction data."""
    fields = []
    quotes = data.get("_quotes", {})
    conf = float(data.get("confidence", 0))
    gs = grounding_scores or {}

    for fname, value in data.items():
        if fname in ("_quotes", "_conflicts", "_validation", "_quote", "confidence"):
            continue
        quote = quotes.get(fname) if isinstance(quotes, dict) else None
        fields.append(
            {
                "field_name": fname,
                "value": value,
                "confidence": conf,
                "quote": quote,
                "grounding": float(gs.get(fname, 0)) if gs else 0,
                "location": None,
            }
        )
    return fields


def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--limit", type=int, default=5000, help="Max extractions per project"
    )
    args = parser.parse_args()

    print(f"\n{'=' * 80}")
    print("WIDE TRIAL: LLM Extraction Quality Analysis Across All Projects")
    print(f"{'=' * 80}\n")

    with Session(engine) as session:
        # ── Discover all projects ──
        projects = session.execute(select(Project)).scalars().all()
        print(f"Found {len(projects)} projects\n")

        all_analyses: list[QuoteAnalysis] = []
        project_stats: dict[str, dict] = {}

        for project in projects:
            # Count extractions
            ext_count = session.execute(
                select(func.count(Extraction.id)).where(
                    Extraction.project_id == project.id
                )
            ).scalar()

            if ext_count == 0:
                continue

            # Get extraction type distribution
            type_counts = session.execute(
                select(Extraction.extraction_type, func.count(Extraction.id))
                .where(Extraction.project_id == project.id)
                .group_by(Extraction.extraction_type)
            ).all()

            # Data version distribution
            version_counts = session.execute(
                select(Extraction.data_version, func.count(Extraction.id))
                .where(Extraction.project_id == project.id)
                .group_by(Extraction.data_version)
            ).all()

            pname = project.name or str(project.id)[:8]
            print(f"── {pname} ({ext_count} extractions) ──")
            for etype, count in type_counts:
                print(f"    {etype}: {count}")
            for ver, count in version_counts:
                print(f"    v{ver}: {count}")

            project_stats[pname] = {
                "id": project.id,
                "ext_count": ext_count,
                "types": {t: c for t, c in type_counts},
                "versions": {v: c for v, c in version_counts},
            }

            # ── Load extractions with source content ──
            query = (
                select(Extraction, Source.content, Source.cleaned_content)
                .join(Source, Extraction.source_id == Source.id)
                .where(Extraction.project_id == project.id)
                .where(Source.content.isnot(None))
                .limit(args.limit)
            )
            rows = session.execute(query).all()

            for ext, content, cleaned in rows:
                source_text = cleaned or content
                if not source_text or len(source_text) < 30:
                    continue

                if ext.data_version >= 2:
                    fields = extract_fields_v2(ext.data)
                else:
                    fields = extract_fields_v1(ext.data, ext.grounding_scores)

                for f in fields:
                    quote = f["quote"]
                    if not quote or not isinstance(quote, str) or len(quote) < 3:
                        continue

                    cat, overlap = classify_quote(quote, source_text, f.get("value"))

                    # Check position matching
                    norm_q = _normalize(quote)
                    norm_c = _normalize(source_text)
                    t1 = norm_q in norm_c

                    # Quick check if any tier would match
                    t_any = t1
                    if not t_any:
                        sq = _strip_punct(norm_q)
                        sc = _strip_punct(_normalize(_strip_markdown(source_text)))
                        t_any = bool(sq and sq in sc)
                    if not t_any:
                        # Block fuzzy
                        q_words = set(norm_q.split())
                        for block in source_text.split("\n\n"):
                            b_norm = _normalize(_strip_markdown(block))
                            b_words = set(b_norm.split())
                            if b_words and len(q_words & b_words) / len(q_words) >= 0.6:
                                t_any = True
                                break

                    analysis = QuoteAnalysis(
                        project_name=pname,
                        source_group=ext.source_group,
                        extraction_type=ext.extraction_type,
                        field_name=f["field_name"],
                        data_version=ext.data_version,
                        quote=quote,
                        value=str(f["value"])[:100] if f["value"] is not None else None,
                        confidence=f["confidence"],
                        grounding=f["grounding"],
                        quote_len=len(quote),
                        content_len=len(source_text),
                        category=cat,
                        word_overlap=overlap,
                        tier1_match=t1,
                        tier_any_match=t_any,
                    )
                    all_analyses.append(analysis)

        if not all_analyses:
            print("\nNo quotes found across any project!")
            return

        total = len(all_analyses)
        print(f"\n\n{'=' * 80}")
        print(f"ANALYSIS: {total} quotes across {len(project_stats)} projects")
        print(f"{'=' * 80}")

        # ── 1. Overall category distribution ──
        cats = Counter(a.category for a in all_analyses)
        print(f"\n{'─' * 60}")
        print("1. QUOTE QUALITY CATEGORIES (all projects)")
        print(f"{'─' * 60}")

        # Group into quality tiers
        good_cats = {"exact_match", "punct_fixable", "reworded_minor"}
        ok_cats = {"reworded_major", "paraphrased"}
        bad_cats = {
            "negation_quote",
            "fabricated_short",
            "value_as_quote",
            "hallucinated",
            "low_overlap",
        }

        good = sum(cats.get(c, 0) for c in good_cats)
        ok = sum(cats.get(c, 0) for c in ok_cats)
        bad = sum(cats.get(c, 0) for c in bad_cats)

        print(f"\n  GOOD (locatable):     {good:5d} ({good / total * 100:5.1f}%)")
        print(f"  ACCEPTABLE (fuzzy):   {ok:5d} ({ok / total * 100:5.1f}%)")
        print(f"  BAD (unfixable):      {bad:5d} ({bad / total * 100:5.1f}%)")

        print("\n  Detail:")
        for cat, count in cats.most_common():
            pct = count / total * 100
            bar = "█" * int(pct / 2)
            print(f"    {cat:25s} {count:5d} ({pct:5.1f}%) {bar}")

        # ── 2. By project ──
        print(f"\n{'─' * 60}")
        print("2. QUALITY BY PROJECT")
        print(f"{'─' * 60}")
        by_project: dict[str, list] = defaultdict(list)
        for a in all_analyses:
            by_project[a.project_name].append(a)

        for pname in sorted(by_project.keys()):
            analyses = by_project[pname]
            n = len(analyses)
            p_good = sum(1 for a in analyses if a.category in good_cats)
            p_bad = sum(1 for a in analyses if a.category in bad_cats)
            avg_grounding = sum(a.grounding for a in analyses) / n if n else 0
            avg_conf = sum(a.confidence for a in analyses) / n if n else 0
            t1_pct = sum(1 for a in analyses if a.tier1_match) / n * 100
            tany_pct = sum(1 for a in analyses if a.tier_any_match) / n * 100
            print(f"\n  {pname} (n={n}):")
            print(
                f"    Good: {p_good / n * 100:.0f}%  Bad: {p_bad / n * 100:.0f}%  "
                f"Avg grounding: {avg_grounding:.2f}  Avg conf: {avg_conf:.2f}"
            )
            print(f"    Tier1: {t1_pct:.0f}%  Any-tier: {tany_pct:.0f}%")

        # ── 3. By extraction type ──
        print(f"\n{'─' * 60}")
        print("3. QUALITY BY EXTRACTION TYPE")
        print(f"{'─' * 60}")
        by_type: dict[str, list] = defaultdict(list)
        for a in all_analyses:
            by_type[a.extraction_type].append(a)

        for etype in sorted(by_type.keys()):
            analyses = by_type[etype]
            n = len(analyses)
            p_bad = sum(1 for a in analyses if a.category in bad_cats)
            avg_g = sum(a.grounding for a in analyses) / n
            print(
                f"\n  {etype} (n={n}, bad={p_bad / n * 100:.0f}%, avg_g={avg_g:.2f}):"
            )
            type_cats = Counter(a.category for a in analyses)
            for cat, count in type_cats.most_common(5):
                print(f"    {cat:25s} {count:4d} ({count / n * 100:5.1f}%)")

        # ── 4. By field name (precision bottlenecks) ──
        print(f"\n{'─' * 60}")
        print("4. FIELD-LEVEL PRECISION BOTTLENECKS")
        print(f"{'─' * 60}")
        by_field: dict[str, list] = defaultdict(list)
        for a in all_analyses:
            # Strip list indices for grouping
            base_field = re.sub(r"\[\d+\]$", "", a.field_name)
            by_field[f"{a.extraction_type}.{base_field}"].append(a)

        # Sort by bad rate descending
        field_quality = []
        for field_key, analyses in by_field.items():
            n = len(analyses)
            if n < 3:  # Skip rare fields
                continue
            n_bad = sum(1 for a in analyses if a.category in bad_cats)
            avg_g = sum(a.grounding for a in analyses) / n
            field_quality.append((field_key, n, n_bad, n_bad / n, avg_g))

        field_quality.sort(key=lambda x: -x[3])
        print("\n  Worst fields (highest bad quote rate):")
        for fk, n, n_bad, bad_rate, avg_g in field_quality[:20]:
            bar = "▓" * int(bad_rate * 20)
            print(
                f"    {fk:50s}  n={n:4d}  bad={bad_rate * 100:5.1f}%  g={avg_g:.2f}  {bar}"
            )

        print("\n  Best fields (lowest bad quote rate):")
        for fk, n, n_bad, bad_rate, avg_g in field_quality[-10:]:
            print(f"    {fk:50s}  n={n:4d}  bad={bad_rate * 100:5.1f}%  g={avg_g:.2f}")

        # ── 5. LLM behavioral patterns ──
        print(f"\n{'─' * 60}")
        print("5. LLM BEHAVIORAL PATTERNS")
        print(f"{'─' * 60}")

        # Pattern: Negation quotes ("No mention of X")
        negations = [a for a in all_analyses if a.category == "negation_quote"]
        if negations:
            print(
                f"\n  A. NEGATION QUOTES: {len(negations)} ({len(negations) / total * 100:.1f}%)"
            )
            print("     LLM says 'no mention of X' instead of omitting the field")
            neg_fields = Counter(
                re.sub(r"\[\d+\]$", "", a.field_name) for a in negations
            )
            print("     Top fields:")
            for field, count in neg_fields.most_common(10):
                print(f"       {field:40s} {count:4d}")
            # Show examples
            print("     Examples:")
            for a in negations[:5]:
                print(f'       {a.extraction_type}.{a.field_name}: "{a.quote[:80]}"')

        # Pattern: Value echoed as quote
        echoed = [a for a in all_analyses if a.category == "value_as_quote"]
        if echoed:
            print(
                f"\n  B. VALUE-AS-QUOTE: {len(echoed)} ({len(echoed) / total * 100:.1f}%)"
            )
            print("     LLM just repeats the value as the supporting quote")
            print("     Examples:")
            for a in echoed[:5]:
                print(
                    f'       {a.field_name}: value="{a.value[:60]}" quote="{a.quote[:60]}"'
                )

        # Pattern: Fabricated short values
        fabricated = [a for a in all_analyses if a.category == "fabricated_short"]
        if fabricated:
            print(
                f"\n  C. FABRICATED SHORT VALUES: {len(fabricated)} ({len(fabricated) / total * 100:.1f}%)"
            )
            print("     Short values not found in source at all")
            fab_fields = Counter(
                f"{a.extraction_type}.{re.sub(r'\\[\\d+\\]$', '', a.field_name)}"
                for a in fabricated
            )
            print("     Top fields:")
            for field, count in fab_fields.most_common(10):
                print(f"       {field:50s} {count:4d}")
            print("     Examples:")
            for a in fabricated[:5]:
                print(f'       {a.field_name}: "{a.quote}" (g={a.grounding:.2f})')

        # Pattern: Hallucinated (long quotes with no source basis)
        hallucinated = [a for a in all_analyses if a.category == "hallucinated"]
        if hallucinated:
            print(
                f"\n  D. HALLUCINATED QUOTES: {len(hallucinated)} ({len(hallucinated) / total * 100:.1f}%)"
            )
            print("     Long quotes with <10% word overlap with source")
            hal_fields = Counter(
                f"{a.extraction_type}.{re.sub(r'\\[\\d+\\]$', '', a.field_name)}"
                for a in hallucinated
            )
            print("     Top fields:")
            for field, count in hal_fields.most_common(10):
                print(f"       {field:50s} {count:4d}")
            print("     Examples:")
            for a in hallucinated[:5]:
                print(f'       {a.source_group}/{a.field_name}: "{a.quote[:100]}"')
                print(
                    f"         overlap={a.word_overlap:.2f} g={a.grounding:.2f} conf={a.confidence:.2f}"
                )

        # ── 6. Confidence vs grounding correlation ──
        print(f"\n{'─' * 60}")
        print("6. CONFIDENCE vs GROUNDING CORRELATION")
        print(f"{'─' * 60}")

        # Bucket by confidence, show avg grounding
        conf_buckets: dict[str, list] = defaultdict(list)
        for a in all_analyses:
            if a.confidence >= 0.9:
                bucket = "0.9-1.0"
            elif a.confidence >= 0.7:
                bucket = "0.7-0.9"
            elif a.confidence >= 0.5:
                bucket = "0.5-0.7"
            elif a.confidence >= 0.3:
                bucket = "0.3-0.5"
            else:
                bucket = "0.0-0.3"
            conf_buckets[bucket].append(a)

        print(
            f"\n  {'Confidence':15s} {'N':>6s} {'Avg Ground':>10s} {'Bad%':>6s} {'T1 Match%':>10s}"
        )
        for bucket in ["0.9-1.0", "0.7-0.9", "0.5-0.7", "0.3-0.5", "0.0-0.3"]:
            items = conf_buckets.get(bucket, [])
            if not items:
                continue
            n = len(items)
            avg_g = sum(a.grounding for a in items) / n
            bad_pct = sum(1 for a in items if a.category in bad_cats) / n * 100
            t1_pct = sum(1 for a in items if a.tier1_match) / n * 100
            print(f"  {bucket:15s} {n:6d} {avg_g:10.2f} {bad_pct:5.1f}% {t1_pct:9.1f}%")

        # ── 7. Grounding score distribution ──
        print(f"\n{'─' * 60}")
        print("7. GROUNDING SCORE DISTRIBUTION")
        print(f"{'─' * 60}")

        g_buckets = Counter()
        for a in all_analyses:
            if a.grounding >= 0.95:
                g_buckets["0.95-1.00"] += 1
            elif a.grounding >= 0.8:
                g_buckets["0.80-0.95"] += 1
            elif a.grounding >= 0.6:
                g_buckets["0.60-0.80"] += 1
            elif a.grounding >= 0.3:
                g_buckets["0.30-0.60"] += 1
            elif a.grounding > 0:
                g_buckets["0.01-0.30"] += 1
            else:
                g_buckets["0.00"] += 1

        for bucket in [
            "0.95-1.00",
            "0.80-0.95",
            "0.60-0.80",
            "0.30-0.60",
            "0.01-0.30",
            "0.00",
        ]:
            count = g_buckets.get(bucket, 0)
            bar = "█" * int(count / total * 100 / 2)
            print(f"  {bucket:10s} {count:5d} ({count / total * 100:5.1f}%) {bar}")

        # ── 8. Quote length analysis ──
        print(f"\n{'─' * 60}")
        print("8. QUOTE LENGTH vs QUALITY")
        print(f"{'─' * 60}")

        len_buckets: dict[str, list] = defaultdict(list)
        for a in all_analyses:
            if a.quote_len < 20:
                b = "<20"
            elif a.quote_len < 50:
                b = "20-50"
            elif a.quote_len < 100:
                b = "50-100"
            elif a.quote_len < 200:
                b = "100-200"
            else:
                b = "200+"
            len_buckets[b].append(a)

        print(
            f"\n  {'Length':10s} {'N':>6s} {'Bad%':>6s} {'Avg Ground':>10s} {'T1%':>6s}"
        )
        for b in ["<20", "20-50", "50-100", "100-200", "200+"]:
            items = len_buckets.get(b, [])
            if not items:
                continue
            n = len(items)
            bad_pct = sum(1 for a in items if a.category in bad_cats) / n * 100
            avg_g = sum(a.grounding for a in items) / n
            t1_pct = sum(1 for a in items if a.tier1_match) / n * 100
            print(f"  {b:10s} {n:6d} {bad_pct:5.1f}% {avg_g:10.2f} {t1_pct:5.1f}%")

        # ── 9. v1 vs v2 comparison ──
        print(f"\n{'─' * 60}")
        print("9. v1 vs v2 DATA VERSION COMPARISON")
        print(f"{'─' * 60}")
        for ver in [1, 2]:
            ver_items = [a for a in all_analyses if a.data_version == ver]
            if not ver_items:
                continue
            n = len(ver_items)
            bad_pct = sum(1 for a in ver_items if a.category in bad_cats) / n * 100
            avg_g = sum(a.grounding for a in ver_items) / n
            t1_pct = sum(1 for a in ver_items if a.tier1_match) / n * 100
            neg_pct = (
                sum(1 for a in ver_items if a.category == "negation_quote") / n * 100
            )
            print(f"\n  v{ver} (n={n}):")
            print(
                f"    Bad: {bad_pct:.1f}%  Negation: {neg_pct:.1f}%  "
                f"Avg grounding: {avg_g:.2f}  Tier1 match: {t1_pct:.1f}%"
            )
            ver_cats = Counter(a.category for a in ver_items)
            for cat, count in ver_cats.most_common(5):
                print(f"    {cat:25s} {count:4d} ({count / n * 100:5.1f}%)")

        # ── 10. Actionable recommendations ──
        print(f"\n{'=' * 80}")
        print("10. IMPROVEMENT OPPORTUNITIES (ranked by impact)")
        print(f"{'=' * 80}")

        improvements = []

        # Negation quotes
        if negations:
            improvements.append(
                (
                    len(negations),
                    "PROMPT: Instruct LLM to OMIT fields it can't find instead of writing 'no mention of X'",
                    f"{len(negations)} negation quotes ({len(negations) / total * 100:.1f}%)",
                    "Prompt change + post-processing filter",
                )
            )

        # Fabricated short values
        if fabricated:
            improvements.append(
                (
                    len(fabricated),
                    "FILTER: Drop fields with grounding=0.0 and quote_len<15",
                    f"{len(fabricated)} fabricated short values ({len(fabricated) / total * 100:.1f}%)",
                    "Post-extraction confidence gate",
                )
            )

        # Hallucinated
        if hallucinated:
            improvements.append(
                (
                    len(hallucinated),
                    "FILTER: Drop fields with grounding<0.3 (quote doesn't exist in source)",
                    f"{len(hallucinated)} hallucinated quotes ({len(hallucinated) / total * 100:.1f}%)",
                    "Post-extraction grounding gate",
                )
            )

        # Value-as-quote
        if echoed:
            improvements.append(
                (
                    len(echoed),
                    "PROMPT: Instruct LLM to quote the SOURCE TEXT, not echo the extracted value",
                    f"{len(echoed)} value-as-quote ({len(echoed) / total * 100:.1f}%)",
                    "Prompt improvement",
                )
            )

        # Low-grounding high-confidence
        overconfident = [
            a for a in all_analyses if a.confidence >= 0.8 and a.grounding < 0.3
        ]
        if overconfident:
            improvements.append(
                (
                    len(overconfident),
                    "CALIBRATION: LLM reports high confidence but quote is ungrounded",
                    f"{len(overconfident)} overconfident extractions ({len(overconfident) / total * 100:.1f}%)",
                    "Constrain confidence to min(stated_conf, grounding)",
                )
            )

        improvements.sort(key=lambda x: -x[0])
        for i, (count, action, impact, mechanism) in enumerate(improvements, 1):
            print(f"\n  {i}. {action}")
            print(f"     Impact: {impact}")
            print(f"     How: {mechanism}")

    print(f"\n{'=' * 80}")
    print("TRIAL COMPLETE")
    print(f"{'=' * 80}\n")


if __name__ == "__main__":
    main()
