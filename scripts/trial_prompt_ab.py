#!/usr/bin/env python3
"""A/B trial: Compare current prompts vs anti-hallucination prompts.

Same sources, same field groups — two prompt variants. Measures grounding
improvement from stronger anti-hallucination + quote-not-value instructions.

Approach:
  1. Sample sources from drivetrain project (worst quality baseline)
  2. For each source × field_group, extract with BOTH prompt variants
  3. Compute grounding scores for both
  4. Compare: poorly-grounded rate, overconfident rate, value-as-quote rate,
     response length, per-field breakdown for worst offenders

Usage:
    .venv/bin/python scripts/trial_prompt_ab.py [--limit 30] [--project-id UUID]
    .venv/bin/python scripts/trial_prompt_ab.py --groups company_info  # single group
"""

import asyncio
import json
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass
from uuid import UUID

sys.path.insert(0, "src")

from openai import AsyncOpenAI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config import settings
from database import engine
from orm_models import Project, Source
from services.extraction.content_cleaner import strip_structural_junk
from services.extraction.field_groups import FieldGroup
from services.extraction.grounding import verify_quote_in_source
from services.extraction.schema_adapter import ExtractionContext, SchemaAdapter

# ── Prompt variants ──────────────────────────────────────────────────────────

# The hallucination guard block — the key treatment variable
_HALLUCINATION_GUARD = """
CRITICAL CONSTRAINT: You are a text extraction tool, NOT a knowledge base.
- ONLY extract information that is EXPLICITLY STATED in the provided text below.
- If a field's information is not in the text, you MUST return null — do NOT guess or fill in from your training knowledge.
- Common mistake: inventing headquarters locations, employee counts, or categories from your training data. Do NOT do this.
- If you are unsure whether information is in the text or from your own memory, return null.
"""

_QUOTE_NOT_VALUE_NOTE = (
    '\nThe "quote" must be a VERBATIM excerpt copied directly from the source text, '
    "NOT a restatement of your extracted value. "
    "If your quote would be identical to the value, find a longer surrounding passage instead."
)


def build_system_prompt_baseline(
    field_group: FieldGroup, context: ExtractionContext
) -> str:
    """Current production v2 prompt (baseline A)."""
    field_specs = []
    for f in field_group.fields:
        spec = f'- "{f.name}" ({f.field_type}): {f.description}'
        if f.enum_values:
            spec += f" [options: {', '.join(f.enum_values)}]"
        if f.required:
            spec += " [REQUIRED]"
        field_specs.append(spec)

    fields_str = "\n".join(field_specs)
    example_field = field_group.fields[0].name if field_group.fields else "field"
    quoting_note = (
        '\nInclude a "quote" with each field: a brief verbatim excerpt '
        "(15-50 chars) from the source that supports the value."
    )

    return f"""You are extracting {field_group.description} from {context.source_type}.

Fields to extract:
{fields_str}

{field_group.prompt_hint}

RULES:
- Extract ONLY from the content provided below. Do NOT use outside knowledge.
- If the content does not contain information for a field, set it to null.
- If the content is not relevant to {field_group.description}, set ALL fields to null.
- For boolean fields, return true ONLY if there is explicit evidence. Default to false.
- For list fields, return empty list [] if no items found.

Output JSON with per-field structure. Each field has its own value, confidence, and quote:
{{
  "fields": {{
    "{example_field}": {{"value": <extracted_value>, "confidence": 0.0-1.0, "quote": "exact text from source"}},
    ...
  }}
}}

Confidence per field:
- 0.0 if no information found for this field
- 0.5-0.7 if partial/uncertain information
- 0.8-1.0 if clear, well-supported data
{quoting_note}"""


def build_system_prompt_treatment(
    field_group: FieldGroup, context: ExtractionContext
) -> str:
    """Anti-hallucination v2 prompt (treatment B)."""
    field_specs = []
    for f in field_group.fields:
        spec = f'- "{f.name}" ({f.field_type}): {f.description}'
        if f.enum_values:
            spec += f" [options: {', '.join(f.enum_values)}]"
        if f.required:
            spec += " [REQUIRED]"
        field_specs.append(spec)

    fields_str = "\n".join(field_specs)
    example_field = field_group.fields[0].name if field_group.fields else "field"

    quoting_note = (
        '\nInclude a "quote" with each field: a brief verbatim excerpt '
        "(15-50 chars) from the source that supports the value." + _QUOTE_NOT_VALUE_NOTE
    )

    return f"""You are extracting {field_group.description} from {context.source_type}.
{_HALLUCINATION_GUARD}
Fields to extract:
{fields_str}

{field_group.prompt_hint}

RULES:
- Extract ONLY from the content provided below. Do NOT use outside knowledge.
- If the content does not contain information for a field, set it to null.
- If the content is not relevant to {field_group.description}, set ALL fields to null.
- For boolean fields, return true ONLY if there is explicit evidence. Default to false.
- For list fields, return empty list [] if no items found.

Output JSON with per-field structure. Each field has its own value, confidence, and quote:
{{
  "fields": {{
    "{example_field}": {{"value": <extracted_value>, "confidence": 0.0-1.0, "quote": "exact text from source"}},
    ...
  }}
}}

Confidence per field:
- 0.0 if no information found for this field
- 0.5-0.7 if partial/uncertain information
- 0.8-1.0 if clear, well-supported data
{quoting_note}"""


def build_entity_prompt_baseline(
    field_group: FieldGroup, context: ExtractionContext
) -> str:
    """Current production v2 entity list prompt (baseline A)."""
    field_specs = []
    id_field = None
    for f in field_group.fields:
        spec = f'- "{f.name}" ({f.field_type}): {f.description or ""}'
        field_specs.append(spec)
        if f.name in context.entity_id_fields and id_field is None:
            id_field = f.name

    fields_str = "\n".join(field_specs)
    output_key = field_group.name
    entity_singular = field_group.name.rstrip("s")  # naive singularize
    max_items = field_group.max_items or 20

    quoting_note = (
        '\nFor each entity, include "_quote": a brief verbatim excerpt '
        "(15-50 chars) from the source identifying this entity."
    )

    return f"""You are extracting {field_group.description} from {context.source_type}.

For each {entity_singular} found, extract:
{fields_str}

{field_group.prompt_hint}

IMPORTANT RULES:
- Extract ONLY from the content provided below. Do NOT use outside knowledge.
- Extract ONLY the most relevant/significant items (max {max_items} items)
- If no {entity_singular} information found, return an empty list.
- Skip generic navigation/coverage lists, not actual entities.

Output JSON with per-entity confidence and quote:
{{
  "{output_key}": [
    {{<fields>, "_confidence": 0.0-1.0, "_quote": "exact text from source"}},
    ...
  ],
  "has_more": true/false
}}

Set "has_more" to true if there are more entities in the content not yet extracted.

Confidence per entity:
- 0.5-0.7 if sparse detail
- 0.8-1.0 if well-supported with clear evidence
{quoting_note}"""


def build_entity_prompt_treatment(
    field_group: FieldGroup, context: ExtractionContext
) -> str:
    """Anti-hallucination v2 entity list prompt (treatment B)."""
    field_specs = []
    id_field = None
    for f in field_group.fields:
        spec = f'- "{f.name}" ({f.field_type}): {f.description or ""}'
        field_specs.append(spec)
        if f.name in context.entity_id_fields and id_field is None:
            id_field = f.name

    fields_str = "\n".join(field_specs)
    output_key = field_group.name
    entity_singular = field_group.name.rstrip("s")
    max_items = field_group.max_items or 20

    quoting_note = (
        '\nFor each entity, include "_quote": a brief verbatim excerpt '
        "(15-50 chars) from the source identifying this entity." + _QUOTE_NOT_VALUE_NOTE
    )

    return f"""You are extracting {field_group.description} from {context.source_type}.
{_HALLUCINATION_GUARD}
For each {entity_singular} found, extract:
{fields_str}

{field_group.prompt_hint}

IMPORTANT RULES:
- Extract ONLY from the content provided below. Do NOT use outside knowledge.
- Extract ONLY the most relevant/significant items (max {max_items} items)
- If no {entity_singular} information found, return an empty list.
- Skip generic navigation/coverage lists, not actual entities.

Output JSON with per-entity confidence and quote:
{{
  "{output_key}": [
    {{<fields>, "_confidence": 0.0-1.0, "_quote": "exact text from source"}},
    ...
  ],
  "has_more": true/false
}}

Set "has_more" to true if there are more entities in the content not yet extracted.

Confidence per entity:
- 0.5-0.7 if sparse detail
- 0.8-1.0 if well-supported with clear evidence
{quoting_note}"""


# ── Data structures ──────────────────────────────────────────────────────────


@dataclass
class FieldResult:
    field: str
    value: str
    quote: str
    confidence: float
    grounding: float
    value_is_quote: bool
    variant: str  # "A" or "B"
    source_group: str
    group_name: str


_WS_RE = re.compile(r"\s+")


def _normalize(s: str) -> str:
    return _WS_RE.sub(" ", s.lower().strip())


def parse_v2_fields(
    raw: dict,
    source_text: str,
    variant: str,
    source_group: str,
    group_name: str,
) -> list[FieldResult]:
    """Parse v2 response and compute grounding for each field."""
    results = []
    fields = raw.get("fields", {})
    if not isinstance(fields, dict):
        return results

    for fname, fdata in fields.items():
        if not isinstance(fdata, dict):
            continue
        value = fdata.get("value")
        quote = fdata.get("quote") or ""
        confidence = float(fdata.get("confidence", 0))

        if value is None or quote == "":
            continue

        value_str = str(value) if not isinstance(value, str) else value
        grounding = verify_quote_in_source(quote, source_text)

        value_is_quote = (
            bool(value_str)
            and len(value_str) > 1
            and _normalize(value_str) == _normalize(quote)
        )

        results.append(
            FieldResult(
                field=fname,
                value=value_str[:200],
                quote=quote[:200],
                confidence=confidence,
                grounding=grounding,
                value_is_quote=value_is_quote,
                variant=variant,
                source_group=source_group,
                group_name=group_name,
            )
        )
    return results


def parse_entity_fields(
    raw: dict,
    entity_key: str,
    source_text: str,
    variant: str,
    source_group: str,
    group_name: str,
) -> list[FieldResult]:
    """Parse v2 entity list response and compute grounding for each entity."""
    results = []
    entities = raw.get(entity_key, [])
    if not isinstance(entities, list):
        return results

    for i, entity in enumerate(entities):
        if not isinstance(entity, dict):
            continue
        quote = entity.get("_quote") or ""
        confidence = float(entity.get("_confidence", entity.get("confidence", 0)))

        # Use the entity's identifying field as value
        entity_name = (
            entity.get("name") or entity.get("entity_id") or entity.get("id") or ""
        )
        value_str = str(entity_name)

        if not quote:
            continue

        grounding = verify_quote_in_source(quote, source_text)
        value_is_quote = (
            bool(value_str)
            and len(value_str) > 1
            and _normalize(value_str) == _normalize(quote)
        )

        results.append(
            FieldResult(
                field=f"{entity_key}[{i}]",
                value=value_str[:200],
                quote=quote[:200],
                confidence=confidence,
                grounding=grounding,
                value_is_quote=value_is_quote,
                variant=variant,
                source_group=source_group,
                group_name=group_name,
            )
        )
    return results


# ── LLM call ─────────────────────────────────────────────────────────────────


async def call_llm(
    client: AsyncOpenAI,
    model: str,
    system_prompt: str,
    user_prompt: str,
) -> tuple[dict | None, float]:
    """Call LLM and return (parsed_json, elapsed_seconds)."""
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
        try:
            return json.loads(text), elapsed
        except json.JSONDecodeError:
            return None, elapsed
    except Exception as e:
        elapsed = time.monotonic() - t0
        print(f"  LLM error: {e}")
        return None, elapsed


# ── Main ─────────────────────────────────────────────────────────────────────


async def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--limit", type=int, default=30, help="Number of sources to sample"
    )
    parser.add_argument(
        "--project-id", type=str, default="99a19141-9268-40a8-bc9e-ad1fa12243da"
    )
    parser.add_argument(
        "--groups",
        type=str,
        default=None,
        help="Comma-separated field group names to test (default: all)",
    )
    parser.add_argument("--content-limit", type=int, default=20000)
    args = parser.parse_args()

    project_id = UUID(args.project_id)

    print(f"\n{'=' * 80}")
    print("A/B TRIAL: ANTI-HALLUCINATION PROMPT IMPROVEMENT")
    print(f"{'=' * 80}")
    print(f"Project: {args.project_id}")
    print(f"Sources: {args.limit}")
    print(f"Model: {settings.llm_model}")
    print()

    # ── Load project schema & build field groups ──
    with Session(engine) as session:
        project = session.execute(
            select(Project).where(Project.id == project_id)
        ).scalar_one_or_none()
        if not project:
            print(f"Project {project_id} not found!")
            return

        schema = project.extraction_schema
        adapter = SchemaAdapter()
        field_groups = adapter.convert_to_field_groups(schema)
        context = ExtractionContext.from_dict(schema.get("extraction_context"))

        print(f"Schema: {schema.get('name', 'unknown')}")
        print(f"Field groups: {[g.name for g in field_groups]}")

        # Filter groups if requested
        if args.groups:
            wanted = set(args.groups.split(","))
            field_groups = [g for g in field_groups if g.name in wanted]
            print(f"Filtered to: {[g.name for g in field_groups]}")

        if not field_groups:
            print("No field groups to test!")
            return

        # ── Sample sources with content ──
        # Prefer sources that had extractions (we know they're relevant)
        sources = session.execute(
            select(
                Source.id, Source.source_group, Source.content, Source.cleaned_content
            )
            .where(Source.project_id == project_id)
            .where(Source.content.isnot(None))
            .where(func.length(Source.content) > 200)
            .order_by(func.random())
            .limit(args.limit)
        ).all()

        print(f"Sampled {len(sources)} sources\n")

    # ── Run A/B extraction ──
    client = AsyncOpenAI(
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key or "not-needed",
        timeout=120,
    )
    model = settings.llm_model

    all_results_a: list[FieldResult] = []
    all_results_b: list[FieldResult] = []
    timing_a: list[float] = []
    timing_b: list[float] = []
    errors_a = 0
    errors_b = 0

    total_calls = len(sources) * len(field_groups) * 2
    call_idx = 0

    for source_id, source_group, content, cleaned_content in sources:
        source_text = cleaned_content or content
        if not source_text or len(source_text) < 50:
            continue

        cleaned = strip_structural_junk(source_text)
        truncated = cleaned[: args.content_limit]

        for group in field_groups:
            call_idx += 2

            # Build user prompt (same for both variants)
            context_line = (
                f"{context.source_label}: {source_group}\n\n" if source_group else ""
            )
            user_prompt = (
                f"{context_line}Extract {group.name} information from "
                f"ONLY the content below:\n\n---\n{truncated}\n---"
            )

            # ── Variant A: baseline ──
            if group.is_entity_list:
                sys_a = build_entity_prompt_baseline(group, context)
            else:
                sys_a = build_system_prompt_baseline(group, context)
            raw_a, elapsed_a = await call_llm(client, model, sys_a, user_prompt)
            timing_a.append(elapsed_a)

            if raw_a:
                if group.is_entity_list:
                    results_a = parse_entity_fields(
                        raw_a,
                        group.name,
                        source_text,
                        "A",
                        source_group or "",
                        group.name,
                    )
                else:
                    results_a = parse_v2_fields(
                        raw_a, source_text, "A", source_group or "", group.name
                    )
                all_results_a.extend(results_a)
            else:
                errors_a += 1

            # ── Variant B: anti-hallucination ──
            if group.is_entity_list:
                sys_b = build_entity_prompt_treatment(group, context)
            else:
                sys_b = build_system_prompt_treatment(group, context)
            raw_b, elapsed_b = await call_llm(client, model, sys_b, user_prompt)
            timing_b.append(elapsed_b)

            if raw_b:
                if group.is_entity_list:
                    results_b = parse_entity_fields(
                        raw_b,
                        group.name,
                        source_text,
                        "B",
                        source_group or "",
                        group.name,
                    )
                else:
                    results_b = parse_v2_fields(
                        raw_b, source_text, "B", source_group or "", group.name
                    )
                all_results_b.extend(results_b)
            else:
                errors_b += 1

            # Progress
            pct = call_idx / total_calls * 100
            print(
                f"\r  [{call_idx}/{total_calls}] ({pct:.0f}%) "
                f"{source_group or '?'}/{group.name} "
                f"A:{len(results_a) if raw_a else 'ERR'} B:{len(results_b) if raw_b else 'ERR'}  ",
                end="",
                flush=True,
            )

    print(f"\n\n{'=' * 80}")
    print("RESULTS")
    print(f"{'=' * 80}\n")

    # ── Analysis ──
    for label, results, timings, errs in [
        ("A (baseline)", all_results_a, timing_a, errors_a),
        ("B (anti-hallucination)", all_results_b, timing_b, errors_b),
    ]:
        print(f"{'─' * 60}")
        print(f"Variant {label}")
        print(f"{'─' * 60}")

        n = len(results)
        if n == 0:
            print("  No results!")
            continue

        well = sum(1 for r in results if r.grounding >= 0.8)
        poor = sum(1 for r in results if r.grounding < 0.3)
        overconf = sum(1 for r in results if r.confidence >= 0.8 and r.grounding < 0.3)
        val_eq_q = sum(1 for r in results if r.value_is_quote)
        bad_echo = sum(1 for r in results if r.value_is_quote and r.grounding < 0.3)
        avg_g = sum(r.grounding for r in results) / n
        avg_c = sum(r.confidence for r in results) / n
        avg_t = sum(timings) / len(timings) if timings else 0
        null_fields = sum(1 for r in results if r.value in ("None", "", "null"))

        print(f"  Fields extracted: {n}")
        print(f"  LLM errors: {errs}")
        print(f"  Avg latency: {avg_t:.2f}s")
        print(f"  Avg grounding: {avg_g:.3f}")
        print(f"  Avg confidence: {avg_c:.3f}")
        print(f"  Well grounded (>=0.8): {well}/{n} ({well / n * 100:.1f}%)")
        print(f"  Poorly grounded (<0.3): {poor}/{n} ({poor / n * 100:.1f}%)")
        print(
            f"  Overconfident (conf>=0.8 & ground<0.3): {overconf}/{n} ({overconf / n * 100:.1f}%)"
        )
        print(f"  Value == quote: {val_eq_q}/{n} ({val_eq_q / n * 100:.1f}%)")
        print(
            f"  Bad echo (val==quote & ground<0.3): {bad_echo}/{n} ({bad_echo / n * 100:.1f}%)"
        )
        print()

    # ── Paired comparison ──
    print(f"\n{'─' * 60}")
    print("PAIRED COMPARISON (B vs A)")
    print(f"{'─' * 60}")

    na, nb = len(all_results_a), len(all_results_b)
    if na > 0 and nb > 0:
        well_a = sum(1 for r in all_results_a if r.grounding >= 0.8) / na * 100
        well_b = sum(1 for r in all_results_b if r.grounding >= 0.8) / nb * 100
        poor_a = sum(1 for r in all_results_a if r.grounding < 0.3) / na * 100
        poor_b = sum(1 for r in all_results_b if r.grounding < 0.3) / nb * 100
        oc_a = (
            sum(1 for r in all_results_a if r.confidence >= 0.8 and r.grounding < 0.3)
            / na
            * 100
        )
        oc_b = (
            sum(1 for r in all_results_b if r.confidence >= 0.8 and r.grounding < 0.3)
            / nb
            * 100
        )
        be_a = (
            sum(1 for r in all_results_a if r.value_is_quote and r.grounding < 0.3)
            / na
            * 100
        )
        be_b = (
            sum(1 for r in all_results_b if r.value_is_quote and r.grounding < 0.3)
            / nb
            * 100
        )

        print(f"\n  {'Metric':<40s} {'A':>8s} {'B':>8s} {'Delta':>8s}")
        print(f"  {'─' * 64}")

        for name, va, vb in [
            ("Well grounded (>=0.8) %", well_a, well_b),
            ("Poorly grounded (<0.3) %", poor_a, poor_b),
            ("Overconfident %", oc_a, oc_b),
            ("Bad echo %", be_a, be_b),
        ]:
            delta = vb - va
            direction = "↑" if delta > 0 else "↓" if delta < 0 else "="
            # For "well grounded" higher is better; for others lower is better
            print(f"  {name:<40s} {va:7.1f}% {vb:7.1f}% {direction}{abs(delta):6.1f}pp")

        avg_t_a = sum(timing_a) / len(timing_a) if timing_a else 0
        avg_t_b = sum(timing_b) / len(timing_b) if timing_b else 0
        print(
            f"  {'Avg latency (s)':<40s} {avg_t_a:7.2f}s {avg_t_b:7.2f}s {avg_t_b - avg_t_a:+.2f}s"
        )
        print(f"  {'Fields extracted':<40s} {na:>7d}  {nb:>7d}  {nb - na:+d}")

    # ── Per-field breakdown for problem fields ──
    print(f"\n{'─' * 60}")
    print("PER-FIELD BREAKDOWN (problem fields)")
    print(f"{'─' * 60}")

    # Collect by group.field
    by_field_a: dict[str, list[FieldResult]] = defaultdict(list)
    by_field_b: dict[str, list[FieldResult]] = defaultdict(list)
    for r in all_results_a:
        base = re.sub(r"\[\d+\]$", "", r.field)
        by_field_a[f"{r.group_name}.{base}"].append(r)
    for r in all_results_b:
        base = re.sub(r"\[\d+\]$", "", r.field)
        by_field_b[f"{r.group_name}.{base}"].append(r)

    all_field_keys = sorted(set(by_field_a.keys()) | set(by_field_b.keys()))

    print(
        f"\n  {'Field':<45s} {'n_A':>4s} {'G_A':>5s} {'P_A%':>5s}  {'n_B':>4s} {'G_B':>5s} {'P_B%':>5s}  {'Δpoor':>6s}"
    )
    print(f"  {'─' * 85}")

    for fk in all_field_keys:
        items_a = by_field_a.get(fk, [])
        items_b = by_field_b.get(fk, [])
        n_a = len(items_a)
        n_b = len(items_b)
        if n_a < 2 and n_b < 2:
            continue

        avg_g_a = sum(r.grounding for r in items_a) / n_a if n_a else 0
        avg_g_b = sum(r.grounding for r in items_b) / n_b if n_b else 0
        poor_a = sum(1 for r in items_a if r.grounding < 0.3) / n_a * 100 if n_a else 0
        poor_b = sum(1 for r in items_b if r.grounding < 0.3) / n_b * 100 if n_b else 0
        delta = poor_b - poor_a
        direction = "↓" if delta < 0 else "↑" if delta > 0 else "="

        print(
            f"  {fk:<45s} {n_a:4d} {avg_g_a:5.2f} {poor_a:4.0f}%  "
            f"{n_b:4d} {avg_g_b:5.2f} {poor_b:4.0f}%  {direction}{abs(delta):5.1f}pp"
        )

    # ── Examples: fields that IMPROVED in B ──
    print(f"\n{'─' * 60}")
    print("EXAMPLES: Fields poorly grounded in A but improved in B")
    print(f"{'─' * 60}")

    # Find source_group+field combos where A was poorly grounded
    a_by_key: dict[str, FieldResult] = {}
    for r in all_results_a:
        key = f"{r.source_group}|{r.group_name}|{r.field}"
        a_by_key[key] = r

    b_by_key: dict[str, FieldResult] = {}
    for r in all_results_b:
        key = f"{r.source_group}|{r.group_name}|{r.field}"
        b_by_key[key] = r

    improvements = []
    regressions = []
    for key in set(a_by_key.keys()) & set(b_by_key.keys()):
        ra, rb = a_by_key[key], b_by_key[key]
        if ra.grounding < 0.3 and rb.grounding >= 0.8:
            improvements.append((ra, rb))
        elif ra.grounding >= 0.8 and rb.grounding < 0.3:
            regressions.append((ra, rb))

    print(f"\n  Improved (A<0.3 → B>=0.8): {len(improvements)}")
    for ra, rb in improvements[:8]:
        print(f"    {ra.source_group}/{ra.group_name}.{ra.field}")
        print(
            f'      A: ground={ra.grounding:.2f} conf={ra.confidence:.2f} val="{ra.value[:50]}" q="{ra.quote[:50]}"'
        )
        print(
            f'      B: ground={rb.grounding:.2f} conf={rb.confidence:.2f} val="{rb.value[:50]}" q="{rb.quote[:50]}"'
        )
        print()

    print(f"  Regressed (A>=0.8 → B<0.3): {len(regressions)}")
    for ra, rb in regressions[:5]:
        print(f"    {ra.source_group}/{ra.group_name}.{ra.field}")
        print(
            f'      A: ground={ra.grounding:.2f} conf={ra.confidence:.2f} val="{ra.value[:50]}" q="{ra.quote[:50]}"'
        )
        print(
            f'      B: ground={rb.grounding:.2f} conf={rb.confidence:.2f} val="{rb.value[:50]}" q="{rb.quote[:50]}"'
        )
        print()

    # ── Examples: still poorly grounded in BOTH ──
    both_poor = []
    for key in set(a_by_key.keys()) & set(b_by_key.keys()):
        ra, rb = a_by_key[key], b_by_key[key]
        if ra.grounding < 0.3 and rb.grounding < 0.3:
            both_poor.append((ra, rb))

    if both_poor:
        print(f"  Still poor in both: {len(both_poor)}")
        for ra, rb in both_poor[:5]:
            print(f"    {ra.source_group}/{ra.group_name}.{ra.field}")
            print(f'      A: val="{ra.value[:50]}" q="{ra.quote[:50]}"')
            print(f'      B: val="{rb.value[:50]}" q="{rb.quote[:50]}"')
            print()

    # ── Verdict ──
    print(f"\n{'=' * 80}")
    print("VERDICT")
    print(f"{'=' * 80}")

    if na > 0 and nb > 0:
        poor_rate_a = sum(1 for r in all_results_a if r.grounding < 0.3) / na * 100
        poor_rate_b = sum(1 for r in all_results_b if r.grounding < 0.3) / nb * 100
        delta_poor = poor_rate_b - poor_rate_a

        if delta_poor < -2:
            print(
                f"\n  ✓ IMPROVEMENT: Poorly-grounded dropped by {abs(delta_poor):.1f}pp ({poor_rate_a:.1f}% → {poor_rate_b:.1f}%)"
            )
            print("  → Recommend deploying anti-hallucination prompt to production")
        elif delta_poor > 2:
            print(
                f"\n  ✗ REGRESSION: Poorly-grounded increased by {delta_poor:.1f}pp ({poor_rate_a:.1f}% → {poor_rate_b:.1f}%)"
            )
            print("  → Do NOT deploy — prompt changes hurt quality")
        else:
            print(
                f"\n  ~ NEUTRAL: Poorly-grounded changed by {delta_poor:+.1f}pp ({poor_rate_a:.1f}% → {poor_rate_b:.1f}%)"
            )
            print("  → Marginal effect — consider if latency/token cost is acceptable")

        if improvements:
            print(f"  Specific improvements: {len(improvements)} fields fixed")
        if regressions:
            print(f"  ⚠ Regressions: {len(regressions)} fields worsened")

    print()


if __name__ == "__main__":
    asyncio.run(main())
