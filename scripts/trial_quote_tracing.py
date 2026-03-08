#!/usr/bin/env python3
"""Trial: Validate quote-to-source tracing algorithm on production data.

Tests the 3-tier matching proposed in docs/TODO_quote_source_tracing.md
against real v2 extractions to verify coverage assumptions:
  - Tier 1 (~40%): Normalized substring match
  - Tier 2 (~40%): Markdown-stripped match
  - Tier 3 (~15%): Block-level fuzzy match
  - Unmatched (~5%)

Usage:
    cd /projects/knowledge_extraction-orchestrator
    python scripts/trial_quote_tracing.py [--project-id UUID] [--limit N] [--show-examples N]
"""

import argparse
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from uuid import UUID

sys.path.insert(0, "src")

from sqlalchemy import select
from sqlalchemy.orm import Session

from database import engine
from orm_models import Extraction, Source


# ── Tier 1: Normalized substring (same as current locate_in_source) ──

_WS_RE = re.compile(r"\s+")


def _normalize(s: str) -> str:
    return _WS_RE.sub(" ", s.lower().strip())


def tier1_match(quote: str, content: str) -> int | None:
    """Normalized substring search. Returns char offset or None."""
    norm_q = _normalize(quote)
    norm_c = _normalize(content)
    pos = norm_c.find(norm_q)
    return pos if pos >= 0 else None


# ── Tier 2: Markdown-stripped matching ──

# Markdown link: [text](url) or [text](url "title")
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
# Markdown image: ![alt](url)
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
# Bold/italic: **text**, *text*, __text__, _text_ (non-greedy)
_MD_BOLD_ITALIC_RE = re.compile(r"(\*{1,3}|_{1,3})(.+?)\1")
# Table separator rows: | --- | --- |
_MD_TABLE_SEP_RE = re.compile(r"^\|[\s\-:]+\|[\s\-:|]*$", re.MULTILINE)
# Inline code: `code`
_MD_INLINE_CODE_RE = re.compile(r"`([^`]+)`")


def _strip_markdown(text: str) -> str:
    """Strip markdown syntax, return clean text."""
    result = text
    # Images before links (images start with !)
    result = _MD_IMAGE_RE.sub(r"\1", result)
    # Links
    result = _MD_LINK_RE.sub(r"\1", result)
    # Bold/italic
    result = _MD_BOLD_ITALIC_RE.sub(r"\2", result)
    # Table separators
    result = _MD_TABLE_SEP_RE.sub("", result)
    # Inline code
    result = _MD_INLINE_CODE_RE.sub(r"\1", result)
    # Table pipes → spaces
    result = result.replace("|", " ")
    # Collapse whitespace
    result = _WS_RE.sub(" ", result).strip()
    return result


def _strip_markdown_with_map(text: str) -> tuple[str, list[int]]:
    """Strip markdown syntax, return (cleaned_text, offset_map).

    offset_map[i] = index in original text for cleaned char i.
    """
    # Strategy: process text char by char, tracking removals via regex spans
    # First, collect all spans to remove/replace
    replacements: list[tuple[int, int, str]] = []  # (start, end, replacement)

    # Images before links
    for m in _MD_IMAGE_RE.finditer(text):
        # Replace ![alt](url) with alt
        replacements.append((m.start(), m.start(1), ""))  # Remove "!["
        replacements.append((m.end(1), m.end(), ""))  # Remove "](url)"

    for m in _MD_LINK_RE.finditer(text):
        # Check not already handled as image
        if m.start() > 0 and text[m.start() - 1] == "!":
            continue
        replacements.append((m.start(), m.start(1), ""))  # Remove "["
        replacements.append((m.end(1), m.end(), ""))  # Remove "](url)"

    for m in _MD_BOLD_ITALIC_RE.finditer(text):
        marker_len = len(m.group(1))
        replacements.append((m.start(), m.start() + marker_len, ""))
        replacements.append((m.end() - marker_len, m.end(), ""))

    for m in _MD_TABLE_SEP_RE.finditer(text):
        replacements.append((m.start(), m.end(), ""))

    for m in _MD_INLINE_CODE_RE.finditer(text):
        replacements.append((m.start(), m.start() + 1, ""))  # Remove opening `
        replacements.append((m.end() - 1, m.end(), ""))  # Remove closing `

    # Sort by start position, process in order
    replacements.sort(key=lambda r: r[0])

    # Build cleaned text + offset map
    cleaned_chars: list[str] = []
    offset_map: list[int] = []
    skip_until = 0

    for i, ch in enumerate(text):
        if i < skip_until:
            continue

        # Check if this position starts a replacement
        applied = False
        for start, end, repl in replacements:
            if i == start:
                # Add replacement chars with mapped positions
                for j, rc in enumerate(repl):
                    cleaned_chars.append(rc)
                    offset_map.append(start + j)
                skip_until = end
                applied = True
                break

        if not applied:
            # Pipe → space
            if ch == "|":
                cleaned_chars.append(" ")
            else:
                cleaned_chars.append(ch)
            offset_map.append(i)

    return "".join(cleaned_chars), offset_map


def tier2_match(quote: str, content: str) -> tuple[int | None, str | None]:
    """Markdown-stripped matching. Returns (original_offset, matched_span) or (None, None)."""
    stripped_content, offset_map = _strip_markdown_with_map(content)
    norm_q = _normalize(quote)
    norm_s = _normalize(stripped_content)
    pos = norm_s.find(norm_q)
    if pos >= 0:
        # Map position back - norm_s positions don't directly map to offset_map
        # We need stripped_content positions. Find in stripped_content instead.
        stripped_lower = stripped_content.lower()
        # Find in the stripped (non-whitespace-collapsed) version for accurate mapping
        spos = stripped_lower.find(norm_q)
        if spos < 0:
            # Try with whitespace normalization on stripped
            norm_stripped = _normalize(stripped_content)
            spos_norm = norm_stripped.find(norm_q)
            if spos_norm >= 0:
                # Approximate: use the normalized position ratio
                ratio = spos_norm / max(len(norm_stripped), 1)
                orig_pos = int(ratio * len(content))
                span_end = min(orig_pos + len(norm_q) + 50, len(content))
                return orig_pos, content[orig_pos:span_end]
            return None, None

        if spos < len(offset_map):
            orig_start = offset_map[spos]
            end_spos = min(spos + len(norm_q), len(offset_map) - 1)
            orig_end = offset_map[end_spos] + 1
            return orig_start, content[orig_start:orig_end]
        return None, None
    return None, None


# ── Tier 3: Block-level fuzzy matching ──


def tier3_match(
    quote: str, content: str, threshold: float = 0.6
) -> tuple[int | None, float, str | None]:
    """Block-level fuzzy matching. Returns (offset, overlap_score, matched_block) or (None, 0, None)."""
    blocks = content.split("\n\n")
    norm_q = _normalize(_strip_markdown(quote))
    quote_words = set(norm_q.split())

    if not quote_words:
        return None, 0.0, None

    best_overlap = 0.0
    best_block_idx = -1
    best_block = ""

    char_pos = 0
    block_positions: list[int] = []

    for i, block in enumerate(blocks):
        block_positions.append(char_pos)
        char_pos += len(block) + 2  # +2 for \n\n

        stripped = _strip_markdown(block)
        norm_block = _normalize(stripped)
        block_words = set(norm_block.split())

        if not block_words:
            continue

        overlap = len(quote_words & block_words) / len(quote_words)
        if overlap > best_overlap:
            best_overlap = overlap
            best_block_idx = i
            best_block = block

    if best_overlap >= threshold and best_block_idx >= 0:
        # Try line-level within winning block for tighter position
        lines = best_block.split("\n")
        best_line_overlap = 0.0
        best_line_offset = block_positions[best_block_idx]
        best_line_text = best_block

        line_pos = block_positions[best_block_idx]
        for line in lines:
            stripped_line = _strip_markdown(line)
            norm_line = _normalize(stripped_line)
            line_words = set(norm_line.split())
            if line_words:
                line_overlap = len(quote_words & line_words) / len(quote_words)
                if line_overlap > best_line_overlap:
                    best_line_overlap = line_overlap
                    best_line_offset = line_pos
                    best_line_text = line
            line_pos += len(line) + 1  # +1 for \n

        if best_line_overlap >= threshold:
            return best_line_offset, best_line_overlap, best_line_text
        return block_positions[best_block_idx], best_overlap, best_block

    return None, best_overlap, None


# ── Data structures ──


@dataclass
class QuoteAnalysis:
    extraction_id: str
    extraction_type: str
    source_group: str
    field_name: str
    quote: str
    content_len: int
    tier1_offset: int | None
    tier2_offset: int | None
    tier2_span: str | None
    tier3_offset: int | None
    tier3_score: float
    tier3_span: str | None
    winning_tier: int  # 0=unmatched, 1, 2, 3
    has_markdown: bool  # quote differs from md-stripped version


def analyze_quote(
    quote: str,
    content: str,
    extraction_id: str,
    extraction_type: str,
    source_group: str,
    field_name: str,
) -> QuoteAnalysis:
    """Run all 3 tiers on a single quote."""
    t1 = tier1_match(quote, content)
    t2_off, t2_span = tier2_match(quote, content) if t1 is None else (None, None)
    t3_off, t3_score, t3_span = (
        tier3_match(quote, content) if t1 is None and t2_off is None else (None, 0.0, None)
    )

    if t1 is not None:
        winning = 1
    elif t2_off is not None:
        winning = 2
    elif t3_off is not None:
        winning = 3
    else:
        winning = 0

    # Check if content has markdown that could cause mismatches
    stripped = _strip_markdown(content[:2000])
    has_md = stripped != content[:2000].replace("|", " ")

    return QuoteAnalysis(
        extraction_id=extraction_id,
        extraction_type=extraction_type,
        source_group=source_group,
        field_name=field_name,
        quote=quote,
        content_len=len(content),
        tier1_offset=t1,
        tier2_offset=t2_off,
        tier2_span=t2_span,
        tier3_offset=t3_off,
        tier3_score=t3_score,
        tier3_span=t3_span,
        winning_tier=winning,
        has_markdown=has_md,
    )


# ── Main ──


def extract_quotes_from_v2(data: dict) -> list[tuple[str, str]]:
    """Extract (field_name, quote) pairs from v2 extraction data."""
    pairs: list[tuple[str, str]] = []
    if not isinstance(data, dict):
        return pairs
    for field_name, field_data in data.items():
        if field_name.startswith("_"):
            continue
        if isinstance(field_data, dict):
            quote = field_data.get("quote")
            if quote and isinstance(quote, str) and len(quote) > 5:
                pairs.append((field_name, quote))
        elif isinstance(field_data, list):
            for i, item in enumerate(field_data):
                if isinstance(item, dict):
                    quote = item.get("quote")
                    if quote and isinstance(quote, str) and len(quote) > 5:
                        pairs.append((f"{field_name}[{i}]", quote))
    return pairs


def extract_quotes_from_v1(data: dict) -> list[tuple[str, str]]:
    """Extract (field_name, quote) pairs from v1 extraction data."""
    pairs: list[tuple[str, str]] = []
    quotes = data.get("_quotes", {})
    if not isinstance(quotes, dict):
        return pairs
    for field_name, quote in quotes.items():
        if quote and isinstance(quote, str) and len(quote) > 5:
            pairs.append((field_name, quote))
    return pairs


def run_trial(
    project_id: UUID | None,
    limit: int = 500,
    show_examples: int = 5,
    v2_only: bool = False,
) -> None:
    print(f"\n{'='*80}")
    print("TRIAL: Quote-to-Source Tracing Algorithm Validation")
    print(f"{'='*80}\n")

    with Session(engine) as session:
        # Query extractions with source content
        query = (
            select(Extraction, Source.content, Source.cleaned_content)
            .join(Source, Extraction.source_id == Source.id)
            .where(Source.content.isnot(None))
        )
        if project_id:
            query = query.where(Extraction.project_id == project_id)
        if v2_only:
            query = query.where(Extraction.data_version == 2)
        query = query.limit(limit)

        rows = session.execute(query).all()
        print(f"Loaded {len(rows)} extractions")

        results: list[QuoteAnalysis] = []
        skipped_no_quotes = 0

        for ext, content, cleaned_content in rows:
            # Use cleaned_content if available, else content
            source_text = cleaned_content or content
            if not source_text or len(source_text) < 50:
                continue

            # Extract quotes based on data version
            if ext.data_version >= 2:
                quote_pairs = extract_quotes_from_v2(ext.data)
            else:
                quote_pairs = extract_quotes_from_v1(ext.data)

            if not quote_pairs:
                skipped_no_quotes += 1
                continue

            for field_name, quote in quote_pairs:
                analysis = analyze_quote(
                    quote=quote,
                    content=source_text,
                    extraction_id=str(ext.id),
                    extraction_type=ext.extraction_type,
                    source_group=ext.source_group,
                    field_name=field_name,
                )
                results.append(analysis)

        if not results:
            print("No quotes found to analyze!")
            return

        # ── Summary Statistics ──
        total = len(results)
        tier_counts = Counter(r.winning_tier for r in results)

        print(f"\n{'─'*60}")
        print(f"RESULTS: {total} quotes analyzed from {len(rows)} extractions")
        print(f"(Skipped {skipped_no_quotes} extractions with no quotes)")
        print(f"{'─'*60}\n")

        print("Coverage by tier:")
        for tier in [1, 2, 3, 0]:
            count = tier_counts.get(tier, 0)
            pct = count / total * 100
            label = {
                1: "Tier 1 (normalized substring)",
                2: "Tier 2 (markdown-stripped)",
                3: "Tier 3 (block fuzzy)",
                0: "UNMATCHED",
            }[tier]
            bar = "█" * int(pct / 2)
            print(f"  {label:40s} {count:5d} ({pct:5.1f}%) {bar}")

        matched = total - tier_counts.get(0, 0)
        print(f"\n  TOTAL MATCHED: {matched}/{total} ({matched/total*100:.1f}%)")

        # Expected vs actual comparison
        print(f"\n{'─'*60}")
        print("Expected vs Actual:")
        print(f"{'─'*60}")
        expected = {1: 40, 2: 40, 3: 15, 0: 5}
        for tier in [1, 2, 3, 0]:
            actual_pct = tier_counts.get(tier, 0) / total * 100
            exp = expected[tier]
            diff = actual_pct - exp
            label = {1: "Tier 1", 2: "Tier 2", 3: "Tier 3", 0: "Unmatched"}[tier]
            print(f"  {label:12s}  expected={exp:5.1f}%  actual={actual_pct:5.1f}%  diff={diff:+5.1f}%")

        # ── Breakdown by content type ──
        print(f"\n{'─'*60}")
        print("Breakdown by extraction_type:")
        print(f"{'─'*60}")
        by_type: dict[str, Counter] = defaultdict(Counter)
        for r in results:
            by_type[r.extraction_type][r.winning_tier] += 1

        for etype, tcounts in sorted(by_type.items()):
            total_type = sum(tcounts.values())
            t1 = tcounts.get(1, 0)
            t2 = tcounts.get(2, 0)
            t3 = tcounts.get(3, 0)
            t0 = tcounts.get(0, 0)
            print(
                f"  {etype:30s}  n={total_type:4d}  "
                f"T1={t1/total_type*100:5.1f}%  "
                f"T2={t2/total_type*100:5.1f}%  "
                f"T3={t3/total_type*100:5.1f}%  "
                f"Unmatched={t0/total_type*100:5.1f}%"
            )

        # ── Markdown presence analysis ──
        md_results = [r for r in results if r.has_markdown]
        non_md = [r for r in results if not r.has_markdown]
        print(f"\n{'─'*60}")
        print("Markdown presence analysis:")
        print(f"{'─'*60}")
        print(f"  Sources with markdown: {len(md_results)}/{total} ({len(md_results)/total*100:.1f}%)")
        if md_results:
            md_tier = Counter(r.winning_tier for r in md_results)
            print(f"    Tier 1: {md_tier.get(1,0)/len(md_results)*100:.1f}%  "
                  f"Tier 2: {md_tier.get(2,0)/len(md_results)*100:.1f}%  "
                  f"Tier 3: {md_tier.get(3,0)/len(md_results)*100:.1f}%  "
                  f"Unmatched: {md_tier.get(0,0)/len(md_results)*100:.1f}%")
        if non_md:
            non_md_tier = Counter(r.winning_tier for r in non_md)
            print(f"  Sources without markdown: {len(non_md)}")
            print(f"    Tier 1: {non_md_tier.get(1,0)/len(non_md)*100:.1f}%  "
                  f"Tier 2: {non_md_tier.get(2,0)/len(non_md)*100:.1f}%  "
                  f"Tier 3: {non_md_tier.get(3,0)/len(non_md)*100:.1f}%  "
                  f"Unmatched: {non_md_tier.get(0,0)/len(non_md)*100:.1f}%")

        # ── Tier 3 score distribution ──
        t3_results = [r for r in results if r.winning_tier == 3]
        if t3_results:
            print(f"\n{'─'*60}")
            print("Tier 3 score distribution:")
            print(f"{'─'*60}")
            buckets = Counter()
            for r in t3_results:
                bucket = f"{int(r.tier3_score * 10) * 10}-{int(r.tier3_score * 10) * 10 + 10}%"
                buckets[bucket] += 1
            for bucket in sorted(buckets.keys()):
                print(f"  {bucket:10s} {buckets[bucket]:4d}")

        # ── Unmatched analysis ──
        unmatched = [r for r in results if r.winning_tier == 0]
        if unmatched:
            print(f"\n{'─'*60}")
            print(f"Unmatched quotes analysis ({len(unmatched)} total):")
            print(f"{'─'*60}")

            # Check what the existing grounding verifier says
            from services.extraction.grounding import verify_quote_in_source

            grounded_but_unlocated = 0
            for r in unmatched:
                # Re-fetch source content for verification
                pass

            # Show quote length distribution
            lengths = [len(r.quote) for r in unmatched]
            print(f"  Quote length: min={min(lengths)}, max={max(lengths)}, "
                  f"avg={sum(lengths)/len(lengths):.0f}")

        # ── Examples ──
        if show_examples > 0:
            for tier in [2, 3, 0]:
                tier_results = [r for r in results if r.winning_tier == tier]
                if not tier_results:
                    continue
                label = {2: "Tier 2 (markdown-stripped)", 3: "Tier 3 (block fuzzy)", 0: "UNMATCHED"}[tier]
                print(f"\n{'─'*60}")
                print(f"Example: {label} (showing {min(show_examples, len(tier_results))})")
                print(f"{'─'*60}")
                for r in tier_results[:show_examples]:
                    print(f"\n  Source: {r.source_group} / {r.extraction_type}.{r.field_name}")
                    print(f"  Quote:  {r.quote[:120]}{'...' if len(r.quote)>120 else ''}")
                    if tier == 2 and r.tier2_span:
                        print(f"  Span:   {r.tier2_span[:120]}{'...' if len(r.tier2_span)>120 else ''}")
                    elif tier == 3 and r.tier3_span:
                        print(f"  Score:  {r.tier3_score:.2f}")
                        print(f"  Block:  {r.tier3_span[:120]}{'...' if len(r.tier3_span)>120 else ''}")
                    elif tier == 0:
                        # Show what verify_quote_in_source returns
                        print(f"  (all 3 tiers failed to locate this quote)")

    print(f"\n{'='*80}")
    print("TRIAL COMPLETE")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Trial: Quote-to-source tracing validation")
    parser.add_argument(
        "--project-id",
        type=str,
        default=None,
        help="Project UUID to analyze (default: all projects)",
    )
    parser.add_argument("--limit", type=int, default=500, help="Max extractions to analyze")
    parser.add_argument("--show-examples", type=int, default=5, help="Examples per tier to show")
    parser.add_argument("--v2-only", action="store_true", help="Only analyze v2 extractions")
    args = parser.parse_args()

    pid = UUID(args.project_id) if args.project_id else None
    run_trial(pid, limit=args.limit, show_examples=args.show_examples, v2_only=args.v2_only)
