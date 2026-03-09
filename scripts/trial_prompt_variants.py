#!/usr/bin/env python3
"""Extended A/B/C/D/E/F trial: Test multiple prompt improvement variants.

Variants:
  A = baseline (current production)
  B = hallucination guard + quote-not-value (validated in trial_prompt_ab.py)
  C = B + context line fix (label metadata to prevent quoting from it)
  D = B + confidence calibration (link confidence to text evidence)
  E = B + post-source reinforcement (sandwich pattern)
  F = B + all three (C+D+E combined)

Focused on company_info + services (highest signal groups from prior trial).

Usage:
    .venv/bin/python scripts/trial_prompt_variants.py [--limit 30]
    .venv/bin/python scripts/trial_prompt_variants.py --limit 20 --groups company_info
"""

import asyncio
import json
import re
import sys
import time
from collections import defaultdict
from dataclasses import dataclass, field
from uuid import UUID

sys.path.insert(0, "src")

from openai import AsyncOpenAI
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from config import settings
from database import engine
from orm_models import Project, Source
from services.extraction.content_cleaner import strip_structural_junk
from services.extraction.grounding import verify_quote_in_source
from services.extraction.schema_adapter import ExtractionContext, SchemaAdapter

# ── Prompt building blocks ───────────────────────────────────────────────────

_HALLUCINATION_GUARD = """
CRITICAL CONSTRAINT: You are a text extraction tool, NOT a knowledge base.
- ONLY extract information that is EXPLICITLY STATED in the provided text below.
- If a field's information is not in the text, you MUST return null — do NOT guess or fill in from your training knowledge.
- Common mistake: inventing headquarters locations, employee counts, or categories from your training data. Do NOT do this.
- If you are unsure whether information is in the text or from your own memory, return null.
"""

_QUOTE_NOT_VALUE_NOTE = (
    ' The "quote" must be a VERBATIM excerpt copied directly from the source text, '
    "NOT a restatement of your extracted value. "
    "If your quote would be identical to the value, find a longer surrounding passage instead."
)

_CONFIDENCE_CALIBRATION = """
Confidence per field:
- 0.0 if no information found for this field in the text
- 0.3-0.5 if you believe this is correct but cannot find explicit text support — be honest
- 0.5-0.7 if partial/uncertain information found in the text
- 0.8-1.0 ONLY if you can point to a specific passage in the text that states this"""

_CONFIDENCE_BASELINE = """
Confidence per field:
- 0.0 if no information found for this field
- 0.5-0.7 if partial/uncertain information
- 0.8-1.0 if clear, well-supported data"""

_POST_SOURCE_REINFORCEMENT = (
    "\nRemember: extract ONLY from the text above. "
    "If information is not explicitly present in the text, return null."
)

# ── Variant definitions ──────────────────────────────────────────────────────

VARIANTS = {
    "A": "baseline",
    "B": "halluc_guard + quote_fix",
    "C": "B + context_line_fix",
    "D": "B + confidence_calib",
    "E": "B + post_source_reinforce",
    "F": "B + C + D + E combined",
}


def _field_specs(field_group) -> str:
    specs = []
    for f in field_group.fields:
        spec = f'- "{f.name}" ({f.field_type}): {f.description}'
        if f.enum_values:
            spec += f" [options: {', '.join(f.enum_values)}]"
        if f.required:
            spec += " [REQUIRED]"
        specs.append(spec)
    return "\n".join(specs)


def build_system_prompt(variant: str, field_group, context: ExtractionContext) -> str:
    """Build system prompt for a given variant."""
    fields_str = _field_specs(field_group)
    example_field = field_group.fields[0].name if field_group.fields else "field"

    # Hallucination guard (B, C, D, E, F)
    guard = _HALLUCINATION_GUARD if variant != "A" else ""

    # Quoting note
    base_quote = (
        '\nInclude a "quote" with each field: a brief verbatim excerpt '
        "(15-50 chars) from the source that supports the value."
    )
    if variant == "A":
        quoting_note = base_quote
    else:
        quoting_note = base_quote + _QUOTE_NOT_VALUE_NOTE

    # Confidence section
    if variant in ("D", "F"):
        confidence = _CONFIDENCE_CALIBRATION
    else:
        confidence = _CONFIDENCE_BASELINE

    if field_group.is_entity_list:
        return _build_entity_system(
            variant, field_group, context, fields_str, guard, quoting_note, confidence
        )

    return f"""You are extracting {field_group.description} from {context.source_type}.
{guard}
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
{confidence}
{quoting_note}"""


def _build_entity_system(
    variant, field_group, context, fields_str, guard, quoting_note, confidence,
):
    output_key = field_group.name
    entity_singular = field_group.name.rstrip("s")
    max_items = field_group.max_items or 20

    return f"""You are extracting {field_group.description} from {context.source_type}.
{guard}
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
{confidence}
{quoting_note}"""


def build_user_prompt(
    variant: str, content: str, field_group, context: ExtractionContext,
    source_context: str | None, content_limit: int,
) -> str:
    """Build user prompt for a given variant."""
    cleaned = strip_structural_junk(content)
    truncated = cleaned[:content_limit]

    # Context line handling
    if source_context:
        if variant in ("C", "F"):
            # Variant C/F: label metadata explicitly
            context_line = (
                f"[METADATA — do not quote from this line] "
                f"{context.source_label}: {source_context}\n\n"
            )
        else:
            context_line = f"{context.source_label}: {source_context}\n\n"
    else:
        context_line = ""

    base = (
        f"{context_line}Extract {field_group.name} information from "
        f"ONLY the content below:\n\n---\n{truncated}\n---"
    )

    # Post-source reinforcement (E, F)
    if variant in ("E", "F"):
        base += _POST_SOURCE_REINFORCEMENT

    return base


# ── Data structures ──────────────────────────────────────────────────────────

@dataclass
class FieldResult:
    field: str
    value: str
    quote: str
    confidence: float
    grounding: float
    value_is_quote: bool
    variant: str
    source_group: str
    group_name: str


_WS_RE = re.compile(r"\s+")


def _normalize(s: str) -> str:
    return _WS_RE.sub(" ", s.lower().strip())


def parse_fields(
    raw: dict, field_group, source_text: str, variant: str, source_group: str,
) -> list[FieldResult]:
    """Parse response and compute grounding."""
    if field_group.is_entity_list:
        return _parse_entity_fields(raw, field_group, source_text, variant, source_group)
    return _parse_v2_fields(raw, source_text, variant, source_group, field_group.name)


def _parse_v2_fields(raw, source_text, variant, source_group, group_name):
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
        if value is None or not quote:
            continue
        value_str = str(value) if not isinstance(value, str) else value
        grounding = verify_quote_in_source(quote, source_text)
        value_is_quote = (
            bool(value_str) and len(value_str) > 1
            and _normalize(value_str) == _normalize(quote)
        )
        results.append(FieldResult(
            field=fname, value=value_str[:200], quote=quote[:200],
            confidence=confidence, grounding=grounding,
            value_is_quote=value_is_quote, variant=variant,
            source_group=source_group, group_name=group_name,
        ))
    return results


def _parse_entity_fields(raw, field_group, source_text, variant, source_group):
    results = []
    entities = raw.get(field_group.name, [])
    if not isinstance(entities, list):
        return results
    for i, entity in enumerate(entities):
        if not isinstance(entity, dict):
            continue
        quote = entity.get("_quote") or ""
        confidence = float(entity.get("_confidence", entity.get("confidence", 0)))
        name = entity.get("name") or entity.get("entity_id") or entity.get("id") or ""
        value_str = str(name)
        if not quote:
            continue
        grounding = verify_quote_in_source(quote, source_text)
        value_is_quote = (
            bool(value_str) and len(value_str) > 1
            and _normalize(value_str) == _normalize(quote)
        )
        results.append(FieldResult(
            field=f"{field_group.name}[{i}]", value=value_str[:200], quote=quote[:200],
            confidence=confidence, grounding=grounding,
            value_is_quote=value_is_quote, variant=variant,
            source_group=source_group, group_name=field_group.name,
        ))
    return results


# ── LLM call ─────────────────────────────────────────────────────────────────

async def call_llm(client, model, system_prompt, user_prompt):
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


# ── Analysis ─────────────────────────────────────────────────────────────────

def analyze_variant(label: str, results: list[FieldResult], timings: list[float], errors: int):
    """Print analysis for one variant."""
    n = len(results)
    if n == 0:
        print(f"  {label}: No results (errors={errors})")
        return {}

    well = sum(1 for r in results if r.grounding >= 0.8)
    poor = sum(1 for r in results if r.grounding < 0.3)
    overconf = sum(1 for r in results if r.confidence >= 0.8 and r.grounding < 0.3)
    val_eq_q = sum(1 for r in results if r.value_is_quote)
    bad_echo = sum(1 for r in results if r.value_is_quote and r.grounding < 0.3)
    avg_g = sum(r.grounding for r in results) / n
    avg_c = sum(r.confidence for r in results) / n
    avg_t = sum(timings) / len(timings) if timings else 0

    # Low-confidence fabrications: conf < 0.5 — does calibration help?
    low_conf = sum(1 for r in results if r.confidence < 0.5)
    low_conf_correct = sum(1 for r in results if r.confidence < 0.5 and r.grounding < 0.3)

    return {
        "label": label,
        "n": n,
        "errors": errors,
        "well_pct": well / n * 100,
        "poor_pct": poor / n * 100,
        "overconf_pct": overconf / n * 100,
        "val_eq_q_pct": val_eq_q / n * 100,
        "bad_echo_pct": bad_echo / n * 100,
        "avg_g": avg_g,
        "avg_c": avg_c,
        "avg_t": avg_t,
        "low_conf": low_conf,
        "low_conf_correct": low_conf_correct,
    }


# ── Main ─────────────────────────────────────────────────────────────────────

async def main():
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=30)
    parser.add_argument("--project-id", type=str,
                        default="99a19141-9268-40a8-bc9e-ad1fa12243da")
    parser.add_argument("--groups", type=str, default="company_info,services",
                        help="Comma-separated field group names")
    parser.add_argument("--content-limit", type=int, default=20000)
    args = parser.parse_args()

    project_id = UUID(args.project_id)
    variant_keys = list(VARIANTS.keys())

    print(f"\n{'='*80}")
    print("MULTI-VARIANT PROMPT TRIAL")
    print(f"{'='*80}")
    print(f"Variants: {', '.join(f'{k}={v}' for k, v in VARIANTS.items())}")
    print(f"Project: {args.project_id}")
    print(f"Sources: {args.limit}")
    print(f"Model: {settings.llm_model}")
    print()

    # ── Load project schema ──
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

        wanted = set(args.groups.split(","))
        field_groups = [g for g in field_groups if g.name in wanted]
        print(f"Field groups: {[g.name for g in field_groups]}")

        if not field_groups:
            print("No field groups matched!")
            return

        sources = session.execute(
            select(Source.id, Source.source_group, Source.content, Source.cleaned_content)
            .where(Source.project_id == project_id)
            .where(Source.content.isnot(None))
            .where(func.length(Source.content) > 200)
            .order_by(func.random())
            .limit(args.limit)
        ).all()

        print(f"Sampled {len(sources)} sources\n")

    # ── Run extraction ──
    client = AsyncOpenAI(
        base_url=settings.openai_base_url,
        api_key=settings.openai_api_key or "not-needed",
        timeout=120,
    )
    model = settings.llm_model

    # Per-variant accumulators
    all_results: dict[str, list[FieldResult]] = {v: [] for v in variant_keys}
    all_timings: dict[str, list[float]] = {v: [] for v in variant_keys}
    all_errors: dict[str, int] = {v: 0 for v in variant_keys}

    total_calls = len(sources) * len(field_groups) * len(variant_keys)
    call_idx = 0

    for source_id, source_group, content, cleaned_content in sources:
        source_text = cleaned_content or content
        if not source_text or len(source_text) < 50:
            continue

        for group in field_groups:
            for v in variant_keys:
                call_idx += 1

                sys_prompt = build_system_prompt(v, group, context)
                usr_prompt = build_user_prompt(
                    v, source_text, group, context,
                    source_group, args.content_limit,
                )

                raw, elapsed = await call_llm(client, model, sys_prompt, usr_prompt)
                all_timings[v].append(elapsed)

                if raw:
                    results = parse_fields(raw, group, source_text, v, source_group or "")
                    all_results[v].extend(results)
                    n_fields = len(results)
                else:
                    all_errors[v] += 1
                    n_fields = "ERR"

                pct = call_idx / total_calls * 100
                print(
                    f"\r  [{call_idx}/{total_calls}] ({pct:.0f}%) "
                    f"{source_group or '?'}/{group.name}/{v} → {n_fields}  ",
                    end="", flush=True,
                )

    # ── Results ──
    print(f"\n\n{'='*80}")
    print("RESULTS SUMMARY")
    print(f"{'='*80}\n")

    stats = {}
    for v in variant_keys:
        s = analyze_variant(
            f"{v} ({VARIANTS[v]})", all_results[v], all_timings[v], all_errors[v]
        )
        stats[v] = s

    # Comparison table
    print(f"\n  {'Metric':<35s}", end="")
    for v in variant_keys:
        print(f" {'['+v+']':>9s}", end="")
    print()
    print(f"  {'─'*35}", end="")
    for _ in variant_keys:
        print(f" {'─'*9}", end="")
    print()

    metrics = [
        ("Fields extracted", "n", "{:>7d}  "),
        ("Avg grounding", "avg_g", "{:>7.3f}  "),
        ("Avg confidence", "avg_c", "{:>7.3f}  "),
        ("Well grounded %", "well_pct", "{:>6.1f}%  "),
        ("Poorly grounded %", "poor_pct", "{:>6.1f}%  "),
        ("Overconfident %", "overconf_pct", "{:>6.1f}%  "),
        ("Value == quote %", "val_eq_q_pct", "{:>6.1f}%  "),
        ("Bad echo %", "bad_echo_pct", "{:>6.1f}%  "),
        ("Avg latency (s)", "avg_t", "{:>7.2f}  "),
        ("LLM errors", "errors", "{:>7d}  "),
        ("Low conf (<0.5)", "low_conf", "{:>7d}  "),
    ]

    for label, key, fmt in metrics:
        print(f"  {label:<35s}", end="")
        for v in variant_keys:
            val = stats[v].get(key, 0)
            print(f" {fmt.format(val)}", end="")
        print()

    # ── Delta vs A ──
    print(f"\n{'─'*60}")
    print("DELTA vs BASELINE (A)")
    print(f"{'─'*60}")

    a = stats.get("A", {})
    if a:
        delta_metrics = [
            ("Well grounded", "well_pct", True),   # higher is better
            ("Poorly grounded", "poor_pct", False), # lower is better
            ("Overconfident", "overconf_pct", False),
            ("Value == quote", "val_eq_q_pct", False),
            ("Avg latency", "avg_t", False),
        ]

        print(f"\n  {'Metric':<30s}", end="")
        for v in variant_keys[1:]:  # skip A
            print(f"  {'Δ['+v+']':>9s}", end="")
        print()
        print(f"  {'─'*30}", end="")
        for _ in variant_keys[1:]:
            print(f"  {'─'*9}", end="")
        print()

        for label, key, higher_better in delta_metrics:
            print(f"  {label:<30s}", end="")
            for v in variant_keys[1:]:
                va = a.get(key, 0)
                vb = stats[v].get(key, 0)
                delta = vb - va
                if key == "avg_t":
                    arrow = "↓" if delta < 0 else "↑"
                    print(f"  {arrow}{abs(delta):>6.2f}s ", end="")
                else:
                    arrow = "↑" if (delta > 0) == higher_better else "↓" if delta != 0 else "="
                    print(f"  {arrow}{abs(delta):>5.1f}pp ", end="")
            print()

    # ── Per-field breakdown ──
    print(f"\n{'─'*60}")
    print("PER-FIELD: POORLY GROUNDED RATE BY VARIANT")
    print(f"{'─'*60}")

    by_field: dict[str, dict[str, list[FieldResult]]] = defaultdict(lambda: defaultdict(list))
    for v in variant_keys:
        for r in all_results[v]:
            base = re.sub(r"\[\d+\]$", "", r.field)
            fk = f"{r.group_name}.{base}"
            by_field[fk][v].append(r)

    all_fks = sorted(by_field.keys())

    print(f"\n  {'Field':<40s}", end="")
    for v in variant_keys:
        print(f"  {'['+v+']':>8s}", end="")
    print()
    print(f"  {'─'*40}", end="")
    for _ in variant_keys:
        print(f"  {'─'*8}", end="")
    print()

    for fk in all_fks:
        # Skip fields with very few samples
        max_n = max(len(by_field[fk].get(v, [])) for v in variant_keys)
        if max_n < 3:
            continue

        print(f"  {fk:<40s}", end="")
        for v in variant_keys:
            items = by_field[fk].get(v, [])
            if not items:
                print(f"  {'—':>8s}", end="")
            else:
                poor = sum(1 for r in items if r.grounding < 0.3)
                pct = poor / len(items) * 100
                n = len(items)
                print(f"  {pct:4.0f}%/{n:<2d}", end="")
        print()

    # ── Paired: who fixed what ──
    print(f"\n{'─'*60}")
    print("PAIRED FIXES (A→poor, variant→well) AND REGRESSIONS")
    print(f"{'─'*60}")

    # Build per-variant keyed results
    keyed: dict[str, dict[str, FieldResult]] = {v: {} for v in variant_keys}
    for v in variant_keys:
        for r in all_results[v]:
            key = f"{r.source_group}|{r.group_name}|{r.field}"
            keyed[v][key] = r

    a_keys = keyed.get("A", {})
    for v in variant_keys[1:]:
        v_keys = keyed.get(v, {})
        shared = set(a_keys.keys()) & set(v_keys.keys())
        fixes = [(a_keys[k], v_keys[k]) for k in shared
                 if a_keys[k].grounding < 0.3 and v_keys[k].grounding >= 0.8]
        regs = [(a_keys[k], v_keys[k]) for k in shared
                if a_keys[k].grounding >= 0.8 and v_keys[k].grounding < 0.3]

        print(f"\n  [{v}] Fixes: {len(fixes)}, Regressions: {len(regs)}")
        for ra, rb in fixes[:5]:
            print(f"    ✓ {ra.source_group}/{ra.group_name}.{ra.field}")
            print(f"      A: g={ra.grounding:.2f} c={ra.confidence:.2f} q=\"{ra.quote[:60]}\"")
            print(f"      {v}: g={rb.grounding:.2f} c={rb.confidence:.2f} q=\"{rb.quote[:60]}\"")
        for ra, rb in regs[:3]:
            print(f"    ✗ {ra.source_group}/{ra.group_name}.{ra.field}")
            print(f"      A: g={ra.grounding:.2f} c={ra.confidence:.2f} q=\"{ra.quote[:60]}\"")
            print(f"      {v}: g={rb.grounding:.2f} c={rb.confidence:.2f} q=\"{rb.quote[:60]}\"")

    # ── Verdict ──
    print(f"\n{'='*80}")
    print("VERDICT")
    print(f"{'='*80}")

    if a:
        a_poor = a.get("poor_pct", 0)
        best_v = "A"
        best_poor = a_poor
        for v in variant_keys[1:]:
            v_poor = stats[v].get("poor_pct", 0)
            if v_poor < best_poor:
                best_poor = v_poor
                best_v = v

        print(f"\n  Best variant: [{best_v}] ({VARIANTS[best_v]})")
        print(f"  Poorly grounded: {a_poor:.1f}% (A) → {best_poor:.1f}% ({best_v}) = {best_poor - a_poor:+.1f}pp")
        print()

        for v in variant_keys[1:]:
            s = stats[v]
            delta_poor = s.get("poor_pct", 0) - a_poor
            delta_well = s.get("well_pct", 0) - a.get("well_pct", 0)
            # Count regressions
            v_keys_set = keyed.get(v, {})
            shared = set(a_keys.keys()) & set(v_keys_set.keys())
            regs = sum(1 for k in shared
                       if a_keys[k].grounding >= 0.8 and v_keys_set[k].grounding < 0.3)

            if delta_poor < -2 and regs == 0:
                verdict = "✓ DEPLOY"
            elif regs > 0:
                verdict = f"⚠ {regs} regressions"
            elif abs(delta_poor) <= 2:
                verdict = "~ NEUTRAL"
            else:
                verdict = "✗ REGRESSION"

            print(f"  [{v}] {verdict}  (Δpoor={delta_poor:+.1f}pp, Δwell={delta_well:+.1f}pp)")

    print()


if __name__ == "__main__":
    asyncio.run(main())
