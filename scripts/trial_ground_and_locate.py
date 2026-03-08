#!/usr/bin/env python3
"""Trial: Prototype and validate the unified ground_and_locate() algorithm.

Tests the proposed 4-tier matching with position tracking against all
production quotes to confirm projected coverage numbers.

Architecture:
  ground_and_locate(quote, content) -> GroundingResult
    Tier 1: Normalized substring (score=1.0)     — position via str.find()
    Tier 2: Punct-stripped with offset map (0.95) — position via offset map
    Tier 3: MD+punct stripped with offset map (0.9) — position via offset map
    Tier 4: Block-level fuzzy (best_overlap)      — position = block start

Key pre-processing:
  - Strip trailing ellipsis from quotes ("products..." → "products")
  - Normalize unicode dashes (– → -)

Usage:
    .venv/bin/python scripts/trial_ground_and_locate.py [--limit N]
"""

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

# ── Constants ──

_WS_RE = re.compile(r"\s+")
_STRIP_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)
_MD_LINK_RE = re.compile(r"\[([^\]]*)\]\([^)]*\)")
_MD_IMAGE_RE = re.compile(r"!\[([^\]]*)\]\([^)]*\)")
_MD_BOLD_ITALIC_RE = re.compile(r"(\*{1,3}|_{1,3})(.+?)\1")
_MD_TABLE_SEP_RE = re.compile(r"^\|[\s\-:]+\|[\s\-:|]*$", re.MULTILINE)
_MD_INLINE_CODE_RE = re.compile(r"`([^`]+)`")
# Trailing ellipsis: "products..." or "products…"
_TRAILING_ELLIPSIS_RE = re.compile(r"\.{2,}$|…$")
# Unicode dash normalization
_UNICODE_DASHES = str.maketrans({
    "\u2013": "-",  # en-dash
    "\u2014": "-",  # em-dash
    "\u2015": "-",  # horizontal bar
    "\u2212": "-",  # minus sign
})


# ── Result type ──

@dataclass
class GroundingResult:
    score: float               # 0.0-1.0
    source_offset: int | None  # char position in original content
    source_end: int | None     # end char position in original content
    matched_span: str | None   # actual source text at [offset:end]
    match_tier: int            # 1-4, or 0 for unmatched


# ── Pre-processing ──

def _preprocess_quote(quote: str) -> str:
    """Clean up common LLM quoting artifacts before matching."""
    q = quote.strip()
    # Strip trailing ellipsis
    q = _TRAILING_ELLIPSIS_RE.sub("", q).strip()
    # Normalize unicode dashes
    q = q.translate(_UNICODE_DASHES)
    return q


def _normalize(s: str) -> str:
    """Lowercase, collapse whitespace, normalize dashes."""
    s = s.translate(_UNICODE_DASHES)
    return _WS_RE.sub(" ", s.lower().strip())


# ── Offset-mapped transformations ──

def _normalize_with_map(text: str) -> tuple[str, list[int]]:
    """Lowercase + collapse whitespace, tracking char-to-original positions.

    Returns (normalized_text, offset_map) where offset_map[i] = index in original.
    """
    result: list[str] = []
    offset_map: list[int] = []
    text = text.translate(_UNICODE_DASHES)
    prev_space = True  # Start true to skip leading whitespace

    for i, ch in enumerate(text):
        if ch in " \t\n\r":
            if not prev_space and result:
                result.append(" ")
                offset_map.append(i)
            prev_space = True
        else:
            result.append(ch.lower())
            offset_map.append(i)
            prev_space = False

    # Strip trailing space
    if result and result[-1] == " ":
        result.pop()
        offset_map.pop()

    return "".join(result), offset_map


def _punct_strip_with_map(text: str) -> tuple[str, list[int]]:
    """Strip punctuation, tracking positions back to input text.

    Input should already be normalized (lowercase, collapsed whitespace).
    offset_map[i] = index in input text.
    """
    result: list[str] = []
    offset_map: list[int] = []
    prev_space = False

    for i, ch in enumerate(text):
        if re.match(r"[^\w\s]", ch):
            # Skip punctuation
            continue
        if ch == " ":
            if not prev_space and result:
                result.append(" ")
                offset_map.append(i)
            prev_space = True
        else:
            result.append(ch)
            offset_map.append(i)
            prev_space = False

    if result and result[-1] == " ":
        result.pop()
        offset_map.pop()

    return "".join(result), offset_map


def _strip_markdown_with_map(text: str) -> tuple[str, list[int]]:
    """Strip markdown syntax, tracking positions back to original text.

    Returns (stripped_text, offset_map) where offset_map[i] = index in original.
    """
    # Collect spans to remove (start, end) and spans to keep-content-only
    # We build a "keep" mask: for each char, is it kept or removed?
    keep = [True] * len(text)

    # Images: ![alt](url) — remove ![ and ](url), keep alt
    for m in _MD_IMAGE_RE.finditer(text):
        # Remove "!["
        for j in range(m.start(), m.start(1)):
            keep[j] = False
        # Remove "](url)"
        for j in range(m.end(1), m.end()):
            keep[j] = False

    # Links: [text](url) — remove [ and ](url), keep text
    for m in _MD_LINK_RE.finditer(text):
        if m.start() > 0 and text[m.start() - 1] == "!":
            continue  # Already handled as image
        # Remove "["
        keep[m.start()] = False
        # Remove "](url)"
        for j in range(m.end(1), m.end()):
            keep[j] = False

    # Bold/italic: ***text***, **text**, *text*, etc.
    for m in _MD_BOLD_ITALIC_RE.finditer(text):
        marker_len = len(m.group(1))
        for j in range(m.start(), m.start() + marker_len):
            keep[j] = False
        for j in range(m.end() - marker_len, m.end()):
            keep[j] = False

    # Table separator rows
    for m in _MD_TABLE_SEP_RE.finditer(text):
        for j in range(m.start(), m.end()):
            keep[j] = False

    # Inline code: `code`
    for m in _MD_INLINE_CODE_RE.finditer(text):
        keep[m.start()] = False  # opening `
        keep[m.end() - 1] = False  # closing `

    # Build result
    result: list[str] = []
    offset_map: list[int] = []

    for i, ch in enumerate(text):
        if not keep[i]:
            continue
        if ch == "|":
            result.append(" ")
        else:
            result.append(ch)
        offset_map.append(i)

    return "".join(result), offset_map


def _compose_maps(map_a: list[int], map_b: list[int]) -> list[int]:
    """Compose two offset maps: result[i] = map_a[map_b[i]].

    map_b maps positions in text_c → text_b.
    map_a maps positions in text_b → text_a (original).
    Result maps positions in text_c → text_a.
    """
    return [map_a[j] if j < len(map_a) else map_a[-1] for j in map_b]


# ── Tier implementations ──

def _tier1_locate(norm_quote: str, norm_content: str, norm_map: list[int]) -> GroundingResult | None:
    """Tier 1: Normalized substring match with position."""
    pos = norm_content.find(norm_quote)
    if pos < 0:
        return None
    end = pos + len(norm_quote)
    orig_start = norm_map[pos] if pos < len(norm_map) else 0
    orig_end = (norm_map[end - 1] + 1) if end - 1 < len(norm_map) else len(norm_map)
    return GroundingResult(
        score=1.0,
        source_offset=orig_start,
        source_end=orig_end,
        matched_span=None,  # filled by caller
        match_tier=1,
    )


def _tier2_locate(
    norm_quote_stripped: str,
    norm_content: str,
    norm_map: list[int],
) -> GroundingResult | None:
    """Tier 2: Punct-stripped match with position tracking."""
    content_stripped, punct_map = _punct_strip_with_map(norm_content)
    pos = content_stripped.find(norm_quote_stripped)
    if pos < 0:
        return None
    end = pos + len(norm_quote_stripped)
    # punct_map maps stripped → norm. norm_map maps norm → original.
    composed = _compose_maps(norm_map, punct_map)
    orig_start = composed[pos] if pos < len(composed) else 0
    orig_end = (composed[end - 1] + 1) if end - 1 < len(composed) else 0
    return GroundingResult(
        score=0.95,
        source_offset=orig_start,
        source_end=orig_end,
        matched_span=None,
        match_tier=2,
    )


def _tier3_locate(
    norm_quote_stripped: str,
    content: str,
) -> GroundingResult | None:
    """Tier 3: Markdown-stripped + punct-stripped with position tracking."""
    md_stripped, md_map = _strip_markdown_with_map(content)
    md_norm, md_norm_map = _normalize_with_map(md_stripped)
    md_punct, md_punct_map = _punct_strip_with_map(md_norm)

    pos = md_punct.find(norm_quote_stripped)
    if pos < 0:
        return None
    end = pos + len(norm_quote_stripped)

    # Compose: md_punct → md_norm → md_stripped → original
    map_to_md_norm = _compose_maps(md_norm_map, md_punct_map) if md_punct_map else md_norm_map
    map_to_md_stripped = _compose_maps(md_map, map_to_md_norm) if map_to_md_norm else md_map
    # map_to_md_stripped already maps to original content positions

    orig_start = map_to_md_stripped[pos] if pos < len(map_to_md_stripped) else 0
    orig_end = (map_to_md_stripped[end - 1] + 1) if end - 1 < len(map_to_md_stripped) else 0
    return GroundingResult(
        score=0.9,
        source_offset=orig_start,
        source_end=orig_end,
        matched_span=None,
        match_tier=3,
    )


def _tier4_locate(
    norm_quote: str,
    content: str,
    threshold: float = 0.6,
) -> GroundingResult | None:
    """Tier 4: Block-level fuzzy matching with position tracking."""
    quote_words = norm_quote.split()
    if not quote_words:
        return None

    quote_set = set(quote_words)
    blocks = content.split("\n\n")

    best_overlap = 0.0
    best_block_idx = -1
    best_block = ""

    block_positions: list[int] = []
    char_pos = 0
    for i, block in enumerate(blocks):
        block_positions.append(char_pos)
        char_pos += len(block) + 2

        stripped = _strip_markdown_with_map(block)[0]
        norm_block = _normalize(stripped)
        block_words = set(norm_block.split())
        if not block_words:
            continue

        overlap = len(quote_set & block_words) / len(quote_set)
        if overlap > best_overlap:
            best_overlap = overlap
            best_block_idx = i
            best_block = block

    if best_overlap < threshold or best_block_idx < 0:
        return None

    # Try line-level within winning block
    lines = best_block.split("\n")
    best_line_overlap = 0.0
    best_line_offset = block_positions[best_block_idx]
    best_line_text = best_block

    line_pos = block_positions[best_block_idx]
    for line in lines:
        stripped_line = _strip_markdown_with_map(line)[0]
        norm_line = _normalize(stripped_line)
        line_words = set(norm_line.split())
        if line_words:
            line_overlap = len(quote_set & line_words) / len(quote_set)
            if line_overlap > best_line_overlap:
                best_line_overlap = line_overlap
                best_line_offset = line_pos
                best_line_text = line
        line_pos += len(line) + 1

    if best_line_overlap >= threshold:
        return GroundingResult(
            score=best_line_overlap,
            source_offset=best_line_offset,
            source_end=best_line_offset + len(best_line_text),
            matched_span=best_line_text,
            match_tier=4,
        )

    return GroundingResult(
        score=best_overlap,
        source_offset=block_positions[best_block_idx],
        source_end=block_positions[best_block_idx] + len(best_block),
        matched_span=best_block[:200],
        match_tier=4,
    )


# ── Unified entry point ──

def ground_and_locate(quote: str, content: str) -> GroundingResult:
    """Unified grounding + location in one call.

    Pre-processes quote (strip ellipsis, normalize dashes), then tries
    4 tiers of matching with decreasing strictness, each returning position.
    """
    if not quote or not content:
        return GroundingResult(0.0, None, None, None, 0)

    clean_quote = _preprocess_quote(quote)
    if not clean_quote:
        return GroundingResult(0.0, None, None, None, 0)

    # Pre-compute normalized content with offset map (used by tiers 1-2)
    norm_content, norm_map = _normalize_with_map(content)
    norm_quote = _normalize(clean_quote)

    if not norm_quote:
        return GroundingResult(0.0, None, None, None, 0)

    # Tier 1: Normalized substring
    result = _tier1_locate(norm_quote, norm_content, norm_map)
    if result:
        result.matched_span = content[result.source_offset:result.source_end]
        return result

    # Tier 2: Punct-stripped
    norm_quote_stripped = _STRIP_PUNCT_RE.sub("", norm_quote)
    norm_quote_stripped = _WS_RE.sub(" ", norm_quote_stripped).strip()
    if norm_quote_stripped:
        result = _tier2_locate(norm_quote_stripped, norm_content, norm_map)
        if result:
            result.matched_span = content[result.source_offset:result.source_end]
            return result

    # Tier 3: Markdown + punct stripped
    if norm_quote_stripped:
        result = _tier3_locate(norm_quote_stripped, content)
        if result:
            result.matched_span = content[result.source_offset:result.source_end]
            return result

    # Tier 4: Block fuzzy
    result = _tier4_locate(norm_quote, content)
    if result:
        return result

    return GroundingResult(0.0, None, None, None, 0)


# ── Trial runner ──

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
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--project-id", type=str, default="99a19141-9268-40a8-bc9e-ad1fa12243da")
    parser.add_argument("--limit", type=int, default=2000)
    parser.add_argument("--show-position-examples", type=int, default=5)
    args = parser.parse_args()

    project_id = UUID(args.project_id)

    print(f"\n{'='*80}")
    print("TRIAL: Unified ground_and_locate() prototype")
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

        tier_counts = Counter()
        total = 0
        position_examples: dict[int, list] = defaultdict(list)
        position_errors: list[dict] = []

        for ext, content, cleaned in rows:
            source_text = cleaned or content
            if not source_text or len(source_text) < 50:
                continue

            for fname, quote in extract_quotes(ext.data, ext.data_version):
                total += 1
                result = ground_and_locate(quote, source_text)
                tier_counts[result.match_tier] += 1

                # Validate position: does matched_span actually contain quote words?
                if result.source_offset is not None and result.matched_span:
                    # Collect examples
                    if len(position_examples[result.match_tier]) < args.show_position_examples:
                        position_examples[result.match_tier].append({
                            "source_group": ext.source_group,
                            "ext_type": ext.extraction_type,
                            "field": fname,
                            "quote": quote,
                            "tier": result.match_tier,
                            "score": result.score,
                            "offset": result.source_offset,
                            "end": result.source_end,
                            "span": result.matched_span[:200],
                        })

                    # Validate: check if extracted span makes sense
                    if result.match_tier in (1, 2, 3):
                        norm_q = _normalize(_preprocess_quote(quote))
                        norm_span = _normalize(result.matched_span)
                        q_words = set(norm_q.split())
                        s_words = set(norm_span.split())
                        if q_words and len(q_words & s_words) / len(q_words) < 0.5:
                            position_errors.append({
                                "tier": result.match_tier,
                                "quote": quote[:100],
                                "span": result.matched_span[:100],
                                "source_group": ext.source_group,
                                "field": fname,
                            })

        if total == 0:
            print("No quotes found!")
            return

        # ── Results ──
        print(f"\n{'─'*60}")
        print(f"RESULTS: {total} quotes analyzed")
        print(f"{'─'*60}\n")

        print("Coverage by tier:")
        for tier in [1, 2, 3, 4, 0]:
            count = tier_counts.get(tier, 0)
            pct = count / total * 100
            label = {
                1: "Tier 1 (normalized substring)",
                2: "Tier 2 (punct-stripped)",
                3: "Tier 3 (md+punct stripped)",
                4: "Tier 4 (block fuzzy)",
                0: "UNMATCHED",
            }[tier]
            bar = "█" * int(pct / 2)
            print(f"  {label:40s} {count:5d} ({pct:5.1f}%) {bar}")

        matched = total - tier_counts.get(0, 0)
        print(f"\n  TOTAL MATCHED: {matched}/{total} ({matched/total*100:.1f}%)")

        # Compare with old (Tier 1 only)
        old_matched = tier_counts.get(1, 0)
        improvement = matched - old_matched
        print(f"  Improvement over current: +{improvement} quotes ({improvement/total*100:.1f}%)")

        # ── Position validation ──
        if position_errors:
            print(f"\n{'─'*60}")
            print(f"POSITION ERRORS: {len(position_errors)} spans with <50% word overlap")
            print(f"{'─'*60}")
            for err in position_errors[:10]:
                print(f"  Tier {err['tier']}: {err['source_group']}/{err['field']}")
                print(f"    Quote: \"{err['quote']}\"")
                print(f"    Span:  \"{err['span']}\"")
                print()
        else:
            print(f"\n  Position validation: ALL spans verified (>50% word overlap)")

        # ── Position examples ──
        for tier in [2, 3, 4]:
            examples = position_examples.get(tier, [])
            if not examples:
                continue
            label = {2: "Tier 2 (punct-stripped)", 3: "Tier 3 (md+punct)", 4: "Tier 4 (fuzzy)"}[tier]
            print(f"\n{'─'*60}")
            print(f"Position examples: {label}")
            print(f"{'─'*60}")
            for ex in examples:
                print(f"\n  [{ex['source_group']} / {ex['ext_type']}.{ex['field']}]")
                print(f"  Quote:    \"{ex['quote'][:120]}\"")
                print(f"  Offset:   {ex['offset']}-{ex['end']}")
                print(f"  Span:     \"{ex['span'][:120]}\"")
                if ex["tier"] == 4:
                    print(f"  Score:    {ex['score']:.2f}")

        # ── Unmatched analysis ──
        # Re-run to get unmatched details
        from services.extraction.grounding import verify_quote_in_source

        unmatched_grounded = 0
        unmatched_total = 0
        unmatched_examples: list[dict] = []

        for ext, content, cleaned in rows:
            source_text = cleaned or content
            if not source_text or len(source_text) < 50:
                continue
            for fname, quote in extract_quotes(ext.data, ext.data_version):
                result = ground_and_locate(quote, source_text)
                if result.match_tier == 0:
                    unmatched_total += 1
                    gs = verify_quote_in_source(quote, source_text)
                    if gs >= 0.8:
                        unmatched_grounded += 1
                    if len(unmatched_examples) < 10:
                        unmatched_examples.append({
                            "quote": quote[:120],
                            "grounding": gs,
                            "source_group": ext.source_group,
                            "field": fname,
                        })

        if unmatched_total:
            print(f"\n{'─'*60}")
            print(f"Unmatched analysis ({unmatched_total} quotes):")
            print(f"{'─'*60}")
            print(f"  Still grounded (score>=0.8): {unmatched_grounded}/{unmatched_total}")
            print(f"  Truly unfindable: {unmatched_total - unmatched_grounded}")
            print(f"\n  Examples:")
            for ex in unmatched_examples[:8]:
                print(f"    g={ex['grounding']:.2f}  {ex['source_group']}/{ex['field']}: \"{ex['quote']}\"")

    print(f"\n{'='*80}")
    print("TRIAL COMPLETE")
    print(f"{'='*80}\n")


if __name__ == "__main__":
    main()
