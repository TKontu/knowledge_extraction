#!/usr/bin/env python3
"""Phase B baseline trial: Test deployed prompt improvements + list field caps.

Tests the EXACT production prompt code path (SchemaExtractor._build_system_prompt_v2)
against real data from all 3 schemas to establish a documented quality baseline.

Challenges tested:
  - Brazilian city lists (Multengrenagens: 146K char pages with municipality enums)
  - Entity list extraction (products, job requirements, related entities)
  - List field truncation (certifications, locations, benefits_list)
  - Hallucination on company_info (headquarters, employee counts)
  - Quote-not-value compliance

Metrics captured:
  - Grounding scores (well/partial/poor) per field and schema
  - List field sizes (verifies max_items cap)
  - Value-is-quote rate (echo detection)
  - Truncation events (finish_reason=length)
  - Response sizes
  - Per-schema and per-field breakdown

Usage:
    .venv/bin/python scripts/trial_phase_b_baseline.py
    .venv/bin/python scripts/trial_phase_b_baseline.py --limit 10 --schemas drivetrain
    .venv/bin/python scripts/trial_phase_b_baseline.py --targeted-only  # just hard cases
"""

import asyncio
import json
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import datetime, UTC
from pathlib import Path
from uuid import UUID

sys.path.insert(0, "src")

from openai import AsyncOpenAI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config import settings
from database import engine
from orm_models import Project, Source
from services.extraction.content_cleaner import strip_structural_junk
from services.extraction.field_groups import FieldDefinition, FieldGroup
from services.extraction.grounding import verify_quote_in_source
from services.extraction.schema_adapter import ExtractionContext, SchemaAdapter
from services.extraction.schema_extractor import (
    SchemaExtractor,
    _HALLUCINATION_GUARD,
    _QUOTE_NOT_VALUE_NOTE,
)

# ── Schema configurations ──────────────────────────────────────────────────

SCHEMA_CONFIGS = {
    "drivetrain": {
        "project_id": "99a19141-9268-40a8-bc9e-ad1fa12243da",
        "label": "Industrial Drivetrain",
        "sample_limit": 15,  # per-schema default
        "groups_filter": None,  # all groups
    },
    "wikipedia": {
        "project_id": "6ce9755e-9d77-4926-90dd-86d4cd2b9cda",
        "label": "Wikipedia Articles",
        "sample_limit": 10,
        "groups_filter": None,
    },
    "jobs": {
        "project_id": "b972e016-3baa-403f-ae79-22310e4e895a",
        "label": "Job Listings",
        "sample_limit": 10,
        "groups_filter": None,
    },
}

# Targeted hard cases: sources known to stress-test list extraction
TARGETED_SOURCES = {
    "drivetrain": [
        # Multengrenagens - Brazilian city lists (hallucination test)
        "f430c468-4f3c-4004-a69e-63940fd43460",
        # Timken - 84 ISO mentions, 30K chars (many certifications + locations)
        "f202e7f3-5d4e-4911-a6e6-8e54d6cdfc1f",
        # Rotork - 28K, multi-office/plant/facility
        "a8051fec-df95-4817-81fa-a6a19e02e063",
        # Bonfiglioli - 40 ISO mentions, multi-location global company
        "b5d4e0a8-2f93-446d-8172-411dec02373d",  # Bonfiglioli 15K
        "87ed538c-6588-483b-bb69-8e770f0f1b2b",  # Bonfiglioli 8K
    ],
    "jobs": [
        # Large RemoteOK pages - many job requirements + benefits lists
        "56f08f18-c831-4827-8667-3b4ba399d8dd",
    ],
    "wikipedia": [
        # Large Wikipedia articles - many related entities
        "66ce1faa-268a-4d79-8c40-c612957406fa",
        "b14524c1-05de-499f-8b47-bfafba7b86a1",
    ],
}


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass
class FieldResult:
    field: str
    value: str
    quote: str
    confidence: float
    grounding: float
    value_is_quote: bool
    source_group: str
    group_name: str
    schema_name: str
    is_list_field: bool = False
    list_item_count: int = 0


@dataclass
class ExtractionResult:
    source_id: str
    source_group: str
    group_name: str
    schema_name: str
    elapsed: float
    raw_response: dict | None
    error: str | None
    finish_reason: str | None
    response_length: int
    field_results: list[FieldResult]
    list_field_sizes: dict[str, int]  # field_name -> item count


_WS_RE = re.compile(r"\s+")


def _normalize(s: str) -> str:
    return _WS_RE.sub(" ", s.lower().strip())


# ── Parsing ──────────────────────────────────────────────────────────────────

def parse_v2_fields(
    raw: dict, source_text: str, source_group: str,
    group_name: str, schema_name: str, field_group: FieldGroup,
) -> tuple[list[FieldResult], dict[str, int]]:
    """Parse v2 response, compute grounding, track list sizes."""
    results = []
    list_sizes: dict[str, int] = {}
    fields = raw.get("fields", {})
    if not isinstance(fields, dict):
        return results, list_sizes

    # Build field type lookup
    field_types = {f.name: f.field_type for f in field_group.fields}

    for fname, fdata in fields.items():
        if not isinstance(fdata, dict):
            continue
        value = fdata.get("value")
        quote = fdata.get("quote") or ""
        confidence = float(fdata.get("confidence", 0))

        is_list = field_types.get(fname) == "list"

        # Track list field sizes regardless of grounding
        if is_list and isinstance(value, list):
            list_sizes[fname] = len(value)

        if value is None or not quote:
            continue

        value_str = json.dumps(value) if isinstance(value, (list, dict)) else str(value)
        grounding = verify_quote_in_source(quote, source_text)

        value_is_quote = (
            bool(value_str) and len(value_str) > 1
            and not isinstance(value, (list, dict))
            and _normalize(value_str) == _normalize(quote)
        )

        results.append(FieldResult(
            field=fname, value=value_str[:300], quote=quote[:200],
            confidence=confidence, grounding=grounding,
            value_is_quote=value_is_quote, source_group=source_group,
            group_name=group_name, schema_name=schema_name,
            is_list_field=is_list,
            list_item_count=len(value) if is_list and isinstance(value, list) else 0,
        ))
    return results, list_sizes


def parse_entity_fields(
    raw: dict, field_group: FieldGroup, source_text: str,
    source_group: str, schema_name: str,
) -> tuple[list[FieldResult], dict[str, int]]:
    """Parse v2 entity list response."""
    results = []
    entity_key = field_group.name
    entities = raw.get(entity_key, [])
    list_sizes = {entity_key: len(entities) if isinstance(entities, list) else 0}

    if not isinstance(entities, list):
        return results, list_sizes

    for i, entity in enumerate(entities):
        if not isinstance(entity, dict):
            continue
        quote = entity.get("_quote") or ""
        confidence = float(entity.get("_confidence", entity.get("confidence", 0)))
        name = entity.get("name") or entity.get("product_name") or entity.get("entity_id") or ""
        value_str = str(name)

        if not quote:
            continue

        grounding = verify_quote_in_source(quote, source_text)
        value_is_quote = (
            bool(value_str) and len(value_str) > 1
            and _normalize(value_str) == _normalize(quote)
        )

        results.append(FieldResult(
            field=f"{entity_key}[{i}]", value=value_str[:200], quote=quote[:200],
            confidence=confidence, grounding=grounding,
            value_is_quote=value_is_quote, source_group=source_group,
            group_name=entity_key, schema_name=schema_name,
        ))
    return results, list_sizes


# ── LLM call (uses production code path) ─────────────────────────────────────

async def extract_with_production_prompt(
    client: AsyncOpenAI,
    model: str,
    content: str,
    field_group: FieldGroup,
    context: ExtractionContext,
    source_context: str | None,
    content_limit: int,
) -> tuple[dict | None, float, str | None, int]:
    """Call LLM using the exact production prompt builder.

    Returns (parsed_json, elapsed_seconds, finish_reason, response_length).
    """
    # Build prompts via production SchemaExtractor
    extractor = SchemaExtractor(
        llm=settings.llm,
        content_limit=content_limit,
        source_quoting=True,
        data_version=2,
        context=context,
    )

    system_prompt = extractor._build_system_prompt(field_group)
    user_prompt = extractor._build_user_prompt(content, field_group, source_context)

    t0 = time.monotonic()
    try:
        response = await client.chat.completions.create(
            model=model,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=8192,
        )
        elapsed = time.monotonic() - t0
        text = response.choices[0].message.content
        finish_reason = response.choices[0].finish_reason
        response_length = len(text) if text else 0

        try:
            return json.loads(text), elapsed, finish_reason, response_length
        except json.JSONDecodeError:
            return None, elapsed, finish_reason, response_length
    except Exception as e:
        elapsed = time.monotonic() - t0
        print(f"  LLM error: {e}")
        return None, elapsed, None, 0


# ── Report ───────────────────────────────────────────────────────────────────

def print_report(
    all_results: list[ExtractionResult],
    all_fields: list[FieldResult],
    start_time: float,
):
    """Print comprehensive baseline report."""
    elapsed_total = time.monotonic() - start_time

    print(f"\n{'='*90}")
    print(f"  PHASE B BASELINE REPORT — {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"{'='*90}")
    print(f"  Model: {settings.llm_model}")
    print(f"  Total extractions: {len(all_results)}")
    print(f"  Total fields scored: {len(all_fields)}")
    print(f"  Wall time: {elapsed_total:.0f}s")
    print(f"  Prompt features: hallucination_guard=ON, quote_not_value=ON, list_max_items=ON")

    # ── Overall metrics ──
    n = len(all_fields)
    if n == 0:
        print("\n  No field results to analyze!")
        return

    well = sum(1 for r in all_fields if r.grounding >= 0.8)
    partial = sum(1 for r in all_fields if 0.3 <= r.grounding < 0.8)
    poor = sum(1 for r in all_fields if r.grounding < 0.3)
    overconf = sum(1 for r in all_fields if r.confidence >= 0.8 and r.grounding < 0.3)
    val_eq_q = sum(1 for r in all_fields if r.value_is_quote)
    bad_echo = sum(1 for r in all_fields if r.value_is_quote and r.grounding < 0.3)
    avg_g = sum(r.grounding for r in all_fields) / n
    avg_c = sum(r.confidence for r in all_fields) / n

    print(f"\n{'─'*70}")
    print("  OVERALL QUALITY METRICS")
    print(f"{'─'*70}")
    print(f"  Fields with grounding data:     {n}")
    print(f"  Avg grounding:                  {avg_g:.3f}")
    print(f"  Avg confidence:                 {avg_c:.3f}")
    print(f"  Well grounded (>=0.8):          {well}/{n} ({well/n*100:.1f}%)")
    print(f"  Partially grounded (0.3-0.8):   {partial}/{n} ({partial/n*100:.1f}%)")
    print(f"  Poorly grounded (<0.3):         {poor}/{n} ({poor/n*100:.1f}%)")
    print(f"  Overconfident (c>=0.8 & g<0.3): {overconf}/{n} ({overconf/n*100:.1f}%)")
    print(f"  Value == quote (echo):          {val_eq_q}/{n} ({val_eq_q/n*100:.1f}%)")
    print(f"  Bad echo (echo & g<0.3):        {bad_echo}/{n} ({bad_echo/n*100:.1f}%)")

    # ── Truncation & errors ──
    truncations = sum(1 for r in all_results if r.finish_reason == "length")
    errors = sum(1 for r in all_results if r.error)
    avg_resp_len = (
        sum(r.response_length for r in all_results if r.raw_response) /
        max(1, sum(1 for r in all_results if r.raw_response))
    )
    avg_latency = (
        sum(r.elapsed for r in all_results) / len(all_results)
        if all_results else 0
    )

    print(f"\n{'─'*70}")
    print("  EXTRACTION HEALTH")
    print(f"{'─'*70}")
    print(f"  Truncations (finish=length):    {truncations}/{len(all_results)}")
    print(f"  LLM errors:                     {errors}/{len(all_results)}")
    print(f"  Avg response length:            {avg_resp_len:.0f} chars")
    print(f"  Avg latency:                    {avg_latency:.2f}s")

    # ── List field sizes ──
    print(f"\n{'─'*70}")
    print("  LIST FIELD SIZES (max_items enforcement)")
    print(f"{'─'*70}")

    list_sizes_all: dict[str, list[int]] = defaultdict(list)
    for r in all_results:
        for fname, size in r.list_field_sizes.items():
            list_sizes_all[f"{r.schema_name}/{r.group_name}.{fname}"].append(size)

    if list_sizes_all:
        print(f"\n  {'Field':<55s} {'n':>4s} {'avg':>6s} {'max':>5s} {'>20':>5s}")
        print(f"  {'─'*80}")
        for fk in sorted(list_sizes_all.keys()):
            sizes = list_sizes_all[fk]
            avg_s = sum(sizes) / len(sizes) if sizes else 0
            max_s = max(sizes) if sizes else 0
            over_20 = sum(1 for s in sizes if s > 20)
            print(f"  {fk:<55s} {len(sizes):4d} {avg_s:6.1f} {max_s:5d} {over_20:5d}")
    else:
        print("  No list fields found")

    # ── Per-schema breakdown ──
    print(f"\n{'─'*70}")
    print("  PER-SCHEMA BREAKDOWN")
    print(f"{'─'*70}")

    schemas = sorted(set(r.schema_name for r in all_fields))
    print(f"\n  {'Schema':<20s} {'n':>5s} {'avg_g':>7s} {'well%':>7s} {'poor%':>7s} {'overc%':>7s} {'echo%':>7s}")
    print(f"  {'─'*65}")

    for schema in schemas:
        items = [r for r in all_fields if r.schema_name == schema]
        sn = len(items)
        if sn == 0:
            continue
        s_well = sum(1 for r in items if r.grounding >= 0.8) / sn * 100
        s_poor = sum(1 for r in items if r.grounding < 0.3) / sn * 100
        s_overc = sum(1 for r in items if r.confidence >= 0.8 and r.grounding < 0.3) / sn * 100
        s_echo = sum(1 for r in items if r.value_is_quote) / sn * 100
        s_avg_g = sum(r.grounding for r in items) / sn

        print(f"  {schema:<20s} {sn:5d} {s_avg_g:7.3f} {s_well:6.1f}% {s_poor:6.1f}% {s_overc:6.1f}% {s_echo:6.1f}%")

    # ── Per-field breakdown ──
    print(f"\n{'─'*70}")
    print("  PER-FIELD BREAKDOWN")
    print(f"{'─'*70}")

    by_field: dict[str, list[FieldResult]] = defaultdict(list)
    for r in all_fields:
        base = re.sub(r"\[\d+\]$", "", r.field)
        by_field[f"{r.schema_name}/{r.group_name}.{base}"].append(r)

    all_fks = sorted(by_field.keys())

    print(f"\n  {'Field':<55s} {'n':>4s} {'avg_g':>6s} {'poor%':>6s} {'echo%':>6s}")
    print(f"  {'─'*80}")

    for fk in all_fks:
        items = by_field[fk]
        fn = len(items)
        if fn < 2:
            continue
        f_avg_g = sum(r.grounding for r in items) / fn
        f_poor = sum(1 for r in items if r.grounding < 0.3) / fn * 100
        f_echo = sum(1 for r in items if r.value_is_quote) / fn * 100
        print(f"  {fk:<55s} {fn:4d} {f_avg_g:6.3f} {f_poor:5.1f}% {f_echo:5.1f}%")

    # ── Targeted case results ──
    targeted = [r for r in all_results if r.source_id in {
        sid for sids in TARGETED_SOURCES.values() for sid in sids
    }]
    if targeted:
        print(f"\n{'─'*70}")
        print("  TARGETED HARD CASES")
        print(f"{'─'*70}")

        for r in targeted:
            print(f"\n  Source: {r.source_group} ({r.source_id[:12]}...)")
            print(f"  Group: {r.group_name} | Schema: {r.schema_name}")
            print(f"  Finish: {r.finish_reason} | Response: {r.response_length} chars | Time: {r.elapsed:.1f}s")

            if r.list_field_sizes:
                for fname, size in r.list_field_sizes.items():
                    print(f"  List field '{fname}': {size} items")

            if r.error:
                print(f"  ERROR: {r.error}")
            else:
                for fr in r.field_results:
                    status = "✓" if fr.grounding >= 0.8 else "~" if fr.grounding >= 0.3 else "✗"
                    print(f"    {status} {fr.field}: g={fr.grounding:.2f} c={fr.confidence:.2f} v=\"{fr.value[:60]}\"")
                    if fr.grounding < 0.3:
                        print(f"      quote=\"{fr.quote[:80]}\"")

    # ── Worst offenders ──
    print(f"\n{'─'*70}")
    print("  WORST OFFENDERS (poorly grounded, high confidence)")
    print(f"{'─'*70}")

    worst = sorted(
        [r for r in all_fields if r.confidence >= 0.7 and r.grounding < 0.3],
        key=lambda r: r.confidence - r.grounding,
        reverse=True,
    )[:15]

    for r in worst:
        print(f"\n  {r.schema_name}/{r.group_name}.{r.field} [{r.source_group}]")
        print(f"    conf={r.confidence:.2f} ground={r.grounding:.2f} echo={r.value_is_quote}")
        print(f"    value=\"{r.value[:80]}\"")
        print(f"    quote=\"{r.quote[:80]}\"")

    # ── Summary baseline numbers ──
    print(f"\n{'='*90}")
    print("  BASELINE REFERENCE NUMBERS")
    print(f"{'='*90}")
    print(f"  Date:                {datetime.now(UTC).strftime('%Y-%m-%d')}")
    print(f"  Model:               {settings.llm_model}")
    print(f"  Schemas:             {', '.join(schemas)}")
    print(f"  Total fields:        {n}")
    print(f"  Well grounded:       {well/n*100:.1f}%")
    print(f"  Poorly grounded:     {poor/n*100:.1f}%")
    print(f"  Overconfident:       {overconf/n*100:.1f}%")
    print(f"  Value==quote (echo): {val_eq_q/n*100:.1f}%")
    print(f"  Truncations:         {truncations}/{len(all_results)}")
    print(f"  Avg grounding:       {avg_g:.3f}")
    print(f"  Avg latency:         {avg_latency:.2f}s")
    print(f"{'='*90}\n")


# ── Main ─────────────────────────────────────────────────────────────────────

async def main():
    import argparse

    parser = argparse.ArgumentParser(
        description="Phase B baseline trial across multiple schemas"
    )
    parser.add_argument("--limit", type=int, default=None,
                        help="Override per-schema source limit")
    parser.add_argument("--schemas", type=str, default=None,
                        help="Comma-separated schemas to test (default: all)")
    parser.add_argument("--groups", type=str, default=None,
                        help="Comma-separated field group names to test")
    parser.add_argument("--content-limit", type=int, default=20000)
    parser.add_argument("--targeted-only", action="store_true",
                        help="Only run targeted hard cases (skip random sampling)")
    parser.add_argument("--concurrency", type=int, default=3,
                        help="Max concurrent LLM calls")
    args = parser.parse_args()

    # Filter schemas
    if args.schemas:
        schema_keys = [s.strip() for s in args.schemas.split(",")]
    else:
        schema_keys = list(SCHEMA_CONFIGS.keys())

    start_time = time.monotonic()

    print(f"\n{'='*90}")
    print("  PHASE B BASELINE TRIAL")
    print(f"{'='*90}")
    print(f"  Schemas: {', '.join(schema_keys)}")
    print(f"  Model: {settings.llm_model}")
    print(f"  Content limit: {args.content_limit}")
    print(f"  Targeted only: {args.targeted_only}")
    print(f"  Concurrency: {args.concurrency}")
    print()

    # ── Load all schemas and sample sources ──
    adapter = SchemaAdapter()
    work_items: list[tuple] = []  # (schema_name, source_id, source_group, content, field_groups, context)

    with Session(engine) as session:
        for schema_key in schema_keys:
            cfg = SCHEMA_CONFIGS[schema_key]
            project_id = UUID(cfg["project_id"])

            project = session.execute(
                select(Project).where(Project.id == project_id)
            ).scalar_one_or_none()

            if not project:
                print(f"  ⚠ Project {cfg['label']} not found, skipping")
                continue

            schema = project.extraction_schema
            field_groups = adapter.convert_to_field_groups(schema)
            context = ExtractionContext.from_dict(schema.get("extraction_context"))

            # Apply group filter
            if args.groups:
                wanted = set(args.groups.split(","))
                field_groups = [g for g in field_groups if g.name in wanted]

            if not field_groups:
                print(f"  ⚠ No field groups for {cfg['label']}, skipping")
                continue

            print(f"  {cfg['label']}: {len(field_groups)} groups ({', '.join(g.name for g in field_groups)})")

            # Collect targeted sources
            targeted_ids = TARGETED_SOURCES.get(schema_key, [])
            if targeted_ids:
                targeted_sources = session.execute(
                    select(Source.id, Source.source_group, Source.content, Source.cleaned_content)
                    .where(Source.id.in_([UUID(sid) for sid in targeted_ids]))
                    .where(Source.content.isnot(None))
                ).all()
                for src in targeted_sources:
                    text = src.cleaned_content or src.content
                    if text and len(text) > 50:
                        work_items.append((
                            schema_key, str(src.id), src.source_group, text,
                            field_groups, context,
                        ))
                print(f"    Targeted: {len(targeted_sources)} sources")

            # Random sample (unless targeted-only)
            if not args.targeted_only:
                limit = args.limit or cfg["sample_limit"]
                # Exclude targeted sources from random sample
                exclude_ids = [UUID(sid) for sid in targeted_ids]
                q = (
                    select(Source.id, Source.source_group, Source.content, Source.cleaned_content)
                    .where(Source.project_id == project_id)
                    .where(Source.content.isnot(None))
                    .where(func.length(Source.content) > 200)
                )
                if exclude_ids:
                    q = q.where(Source.id.notin_(exclude_ids))
                q = q.order_by(func.random()).limit(limit)

                random_sources = session.execute(q).all()
                for src in random_sources:
                    text = src.cleaned_content or src.content
                    if text and len(text) > 50:
                        work_items.append((
                            schema_key, str(src.id), src.source_group, text,
                            field_groups, context,
                        ))
                print(f"    Random: {len(random_sources)} sources")

    if not work_items:
        print("\n  No work items! Check project IDs and data.")
        return

    # Count total calls
    total_calls = sum(len(fgs) for _, _, _, _, fgs, _ in work_items)
    print(f"\n  Total extraction calls: {total_calls}")
    print()

    # ── Run extractions ──
    client = AsyncOpenAI(
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key or "not-needed",
        timeout=300,
    )
    model = settings.llm_model
    semaphore = asyncio.Semaphore(args.concurrency)

    all_extraction_results: list[ExtractionResult] = []
    all_field_results: list[FieldResult] = []
    call_idx = 0

    async def process_one(
        schema_name: str, source_id: str, source_group: str,
        content: str, group: FieldGroup, context: ExtractionContext,
    ) -> ExtractionResult:
        nonlocal call_idx

        async with semaphore:
            raw, elapsed, finish_reason, resp_len = await extract_with_production_prompt(
                client, model, content, group, context,
                source_group, args.content_limit,
            )

        call_idx += 1
        pct = call_idx / total_calls * 100

        if raw is None:
            print(
                f"\r  [{call_idx}/{total_calls}] ({pct:.0f}%) "
                f"{schema_name}/{source_group}/{group.name} → ERR     ",
                end="", flush=True,
            )
            return ExtractionResult(
                source_id=source_id, source_group=source_group or "",
                group_name=group.name, schema_name=schema_name,
                elapsed=elapsed, raw_response=None, error="LLM error",
                finish_reason=finish_reason, response_length=resp_len,
                field_results=[], list_field_sizes={},
            )

        # Parse results
        if group.is_entity_list:
            field_results, list_sizes = parse_entity_fields(
                raw, group, content, source_group or "", schema_name,
            )
        else:
            field_results, list_sizes = parse_v2_fields(
                raw, content, source_group or "", group.name, schema_name, group,
            )

        n_fields = len(field_results)
        trunc = "T" if finish_reason == "length" else ""
        print(
            f"\r  [{call_idx}/{total_calls}] ({pct:.0f}%) "
            f"{schema_name}/{source_group}/{group.name} → {n_fields}f {trunc}    ",
            end="", flush=True,
        )

        return ExtractionResult(
            source_id=source_id, source_group=source_group or "",
            group_name=group.name, schema_name=schema_name,
            elapsed=elapsed, raw_response=raw, error=None,
            finish_reason=finish_reason, response_length=resp_len,
            field_results=field_results, list_field_sizes=list_sizes,
        )

    # Build all tasks
    tasks = []
    for schema_name, source_id, source_group, content, field_groups, context in work_items:
        for group in field_groups:
            tasks.append(process_one(
                schema_name, source_id, source_group, content, group, context,
            ))

    # Run with concurrency control
    results = await asyncio.gather(*tasks)

    for r in results:
        all_extraction_results.append(r)
        all_field_results.extend(r.field_results)

    print()  # newline after progress

    # ── Print report ──
    print_report(all_extraction_results, all_field_results, start_time)


if __name__ == "__main__":
    asyncio.run(main())
