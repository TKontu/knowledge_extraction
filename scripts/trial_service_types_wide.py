"""Wide A/B test: current vs proposed service_types prompt.

Runs A (current) and B (enriched description) on a large sample of sources
that contain service-related keywords, across many companies.

Focuses on:
  1. Does B extract more service_types items per source?
  2. Does B reduce hallucinated/parroted enum values?
  3. Does B change provides_services detection rate?
  4. Are there regressions on good companies?

Usage:
    cd src && python ../scripts/trial_service_types_wide.py [--companies N] [--sources-per-company N]
"""

import argparse
import asyncio
import json
import random
from collections import defaultdict

import psycopg
from openai import AsyncOpenAI

DB_DSN = "postgresql://scristill:scristill@192.168.0.136:5432/scristill"
PROJECT_ID = "99a19141-9268-40a8-bc9e-ad1fa12243da"
LLM_BASE_URL = "http://192.168.0.247:9003/v1"
LLM_MODEL = "Qwen3-30B-A3B-it-4bit"
CONTENT_LIMIT = 20000

HALLUCINATION_GUARD = (
    "CRITICAL CONSTRAINT: You are a text extraction tool, NOT a knowledge base.\n"
    "- ONLY extract information that is EXPLICITLY STATED in the provided text below.\n"
    "- If a field's information is not in the text, you MUST return null — do NOT guess, "
    "infer from general knowledge, or fill in plausible-sounding values.\n"
    "- It is MUCH better to return null than to fabricate data."
)

# The 6 enum values from the current field description — used to detect parroting
ENUM_VALUES = {
    "repair",
    "maintenance",
    "refurbishment",
    "installation",
    "commissioning",
    "field service",
}


def _build_prompt(prompt_hint: str, service_types_desc: str) -> str:
    return f"""You are extracting Service and repair capabilities from company documentation.
{HALLUCINATION_GUARD}
Fields to extract:
- "provides_services" (boolean): Whether the company provides repair/maintenance/refurbishment services [REQUIRED]
- "services_gearboxes" (boolean): Provides service for gearboxes [REQUIRED]
- "services_motors" (boolean): Provides service for motors [REQUIRED]
- "services_drivetrain_accessories" (boolean): Provides service for drivetrain accessories [REQUIRED]
- "provides_field_service" (boolean): Provides on-site/field service at customer locations [REQUIRED]
- "service_types" (list): {service_types_desc}

{prompt_hint}

RULES:
- Extract ONLY from the content provided below. Do NOT use outside knowledge.
- If the content does not contain information for a field, set it to null.
- If the content is not relevant to Service and repair capabilities, set ALL fields to null.
- For boolean fields, return true ONLY if there is explicit evidence. Default to false.
- For list fields, return empty list [] if no items found. Return at most 20 items per list field — prioritize the most significant/relevant items.

Output JSON with per-field structure. Each field has its own value, confidence, and quote:
{{
  "fields": {{
    "provides_services": {{"value": <extracted_value>, "confidence": 0.0-1.0, "quote": "exact text from source"}},
    ...
  }}
}}

Confidence per field:
- 0.0 if no information found for this field
- 0.5-0.7 if partial/uncertain information
- 0.8-1.0 if clear, well-supported data

Include a "quote" with each field: a brief verbatim excerpt (15-50 chars) from the source that supports the value.
The "quote" must be a VERBATIM excerpt copied directly from the source text, NOT a restatement of your extracted value."""


PROMPT_HINT = (
    "Look for SERVICE offerings:\n"
    "- Repair services, maintenance programs, overhaul\n"
    "- Service centers, field service teams (on-site service at customer locations)\n"
    "- Spare parts supply, technical support\n"
    "- Field service = technicians travel to customer site"
)

PROMPTS = {
    "A_current": _build_prompt(
        prompt_hint=PROMPT_HINT,
        service_types_desc=(
            "Types: repair, maintenance, refurbishment, installation, commissioning, field service"
        ),
    ),
    "B_desc": _build_prompt(
        prompt_hint=PROMPT_HINT,
        service_types_desc=(
            "List of specific service types offered. Examples: repair, maintenance, "
            "refurbishment, installation, commissioning, field service, overhaul, "
            "spare parts, technical support, inspection, testing. "
            "Extract the actual terms used on the page, in any language."
        ),
    ),
}


def build_user_prompt(content: str, company: str) -> str:
    return f"""Company: {company}

Extract services information from ONLY the content below:

---
{content[:CONTENT_LIMIT]}
---"""


async def extract_services(
    client: AsyncOpenAI, content: str, company: str, prompt: str
) -> dict:
    user_prompt = build_user_prompt(content, company)
    try:
        response = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=4096,
        )
        text = response.choices[0].message.content
        data = json.loads(text)
        return data.get("fields", data)
    except Exception as e:
        return {"_error": str(e)}


def get_val(field_data):
    if isinstance(field_data, dict):
        return field_data.get("value")
    return field_data


def score(fields: dict) -> dict:
    if "_error" in fields:
        return {
            "provides_services": False,
            "service_types": [],
            "n_types": 0,
            "n_enum_only": 0,
            "n_novel": 0,
            "is_parrot": False,
        }
    ps = get_val(fields.get("provides_services"))
    st = get_val(fields.get("service_types"))
    if not isinstance(st, list):
        st = []
    flat = []
    for item in st:
        if isinstance(item, dict):
            flat.append(str(item.get("value", item)).lower().strip())
        else:
            flat.append(str(item).lower().strip())

    # Count enum-only vs novel types
    n_enum_only = sum(1 for t in flat if t in ENUM_VALUES)
    n_novel = len(flat) - n_enum_only
    # "Parrot" = all extracted types are from the enum list AND count >= 5
    is_parrot = len(flat) >= 5 and n_novel == 0

    return {
        "provides_services": bool(ps),
        "service_types": flat,
        "n_types": len(flat),
        "n_enum_only": n_enum_only,
        "n_novel": n_novel,
        "is_parrot": is_parrot,
    }


def fetch_sources(
    n_companies: int,
    sources_per_company: int,
) -> list[tuple[str, str, str]]:
    """Fetch service-keyword-rich sources across many companies."""
    conn = psycopg.connect(DB_DSN)
    cur = conn.cursor()

    # Get all companies sorted by service-keyword source count
    cur.execute(
        """
        SELECT source_group, count(*) as cnt FROM sources
        WHERE project_id = %(pid)s AND status = 'extracted'
          AND content IS NOT NULL AND length(content) > 500
          AND (content ILIKE '%%service%%' OR content ILIKE '%%repair%%'
               OR content ILIKE '%%maintenance%%' OR content ILIKE '%%overhaul%%'
               OR content ILIKE '%%mantenimiento%%' OR content ILIKE '%%reparación%%'
               OR content ILIKE '%%manutenção%%' OR content ILIKE '%%field service%%')
        GROUP BY source_group
        HAVING count(*) >= 3
        ORDER BY count(*) DESC
        """,
        {"pid": PROJECT_ID},
    )
    all_companies = [r[0] for r in cur.fetchall()]

    # Sample: mix of known gap, known good, and random
    must_include = {
        # Known gap (had provides_services=True but no service_types in production)
        "Tool Solutions",
        "Bauergears",
        "Tammotor",
        "Gearmotions",
        "Dimotec",
        "Sew Eurodrive CL",
        "Geiger",
        "Bakerhughes",
        "Precipart",
        "Parvalux",
        "Cammec",
        "Commercialgear",
        "Ondrivesus",
        # Known good (have service_types in production)
        "Allisontransmission",
        "Dbsantasalo",
        "Amigaeng",
        "Abb",
        "Flender",
        "Regalrexnord",
        "Rotork",
        "Boschrexroth",
    }
    chosen = [c for c in all_companies if c in must_include]
    remaining = [c for c in all_companies if c not in must_include]
    random.seed(42)
    extra = random.sample(remaining, min(n_companies - len(chosen), len(remaining)))
    companies = chosen + extra
    companies = companies[:n_companies]

    sources = []
    for company in companies:
        cur.execute(
            """
            SELECT source_group, uri, COALESCE(cleaned_content, content)
            FROM sources
            WHERE project_id = %(pid)s AND source_group = %(sg)s
              AND status = 'extracted'
              AND content IS NOT NULL AND length(content) > 500
              AND (content ILIKE '%%service%%' OR content ILIKE '%%repair%%'
                   OR content ILIKE '%%maintenance%%' OR content ILIKE '%%overhaul%%'
                   OR content ILIKE '%%mantenimiento%%' OR content ILIKE '%%reparación%%'
                   OR content ILIKE '%%manutenção%%' OR content ILIKE '%%field service%%')
            ORDER BY random()
            LIMIT %(lim)s
            """,
            {"pid": PROJECT_ID, "sg": company, "lim": sources_per_company},
        )
        for row in cur.fetchall():
            sources.append(row)

    conn.close()
    return sources


async def run_trial(
    sources: list[tuple[str, str, str]],
    concurrency: int = 8,
) -> None:
    client = AsyncOpenAI(base_url=LLM_BASE_URL, api_key="ollama")

    agg = {name: defaultdict(int) for name in PROMPTS}
    per_source = []

    sem = asyncio.Semaphore(concurrency)
    completed = 0

    async def process_source(company: str, url: str, content: str) -> None:
        nonlocal completed
        async with sem:
            results = {}
            for prompt_name, prompt in PROMPTS.items():
                fields = await extract_services(client, content, company, prompt)
                results[prompt_name] = score(fields)

            per_source.append({"company": company, "url": url[:80], **results})

            for prompt_name, s in results.items():
                a = agg[prompt_name]
                a["sources"] += 1
                a["svc_true"] += int(s["provides_services"])
                a["has_types"] += int(s["n_types"] > 0)
                a["total_types"] += s["n_types"]
                a["total_enum"] += s["n_enum_only"]
                a["total_novel"] += s["n_novel"]
                a["parrot"] += int(s["is_parrot"])
                a["svc_true_no_types"] += int(
                    s["provides_services"] and s["n_types"] == 0
                )
                a["svc_true_has_types"] += int(
                    s["provides_services"] and s["n_types"] > 0
                )

            completed += 1
            if completed % 25 == 0:
                print(f"  ... {completed}/{len(sources)} sources processed")

    print(f"\nProcessing {len(sources)} sources with {len(PROMPTS)} prompts...")
    tasks = [process_source(sg, url, content) for sg, url, content in sources]
    await asyncio.gather(*tasks)

    # ── Per-source diffs (only where A and B differ meaningfully) ──
    print(f"\n{'=' * 110}")
    print(
        "PER-SOURCE DIFFERENCES (where provides_services or service_types count differs)"
    )
    print(f"{'=' * 110}")

    diffs_shown = 0
    for r in sorted(per_source, key=lambda x: x["company"]):
        a = r["A_current"]
        b = r["B_desc"]
        svc_diff = a["provides_services"] != b["provides_services"]
        types_diff = abs(a["n_types"] - b["n_types"]) >= 2
        parrot_diff = a["is_parrot"] and not b["is_parrot"]

        if not (svc_diff or types_diff or parrot_diff):
            continue
        diffs_shown += 1

        flags = []
        if svc_diff:
            flags.append("SVC_DIFF")
        if parrot_diff:
            flags.append("A_PARROT")
        if types_diff:
            flags.append(f"TYPES:{a['n_types']}→{b['n_types']}")
        flag_str = " ".join(flags)

        print(f"\n  {r['company']:<25} | {r['url']}")
        print(
            f"    A: svc={a['provides_services']}, types({a['n_types']}): "
            f"enum={a['n_enum_only']}, novel={a['n_novel']} "
            f"{'[PARROT]' if a['is_parrot'] else ''}"
        )
        a_types = ", ".join(a["service_types"][:6])
        print(f"       [{a_types}]")
        print(
            f"    B: svc={b['provides_services']}, types({b['n_types']}): "
            f"enum={b['n_enum_only']}, novel={b['n_novel']}"
        )
        b_types = ", ".join(b["service_types"][:6])
        print(f"       [{b_types}]")
        print(f"    → {flag_str}")

    print(f"\n  ({diffs_shown} sources with meaningful differences)")

    # ── Aggregate ──
    print(f"\n{'=' * 110}")
    print("AGGREGATE COMPARISON")
    print(f"{'=' * 110}")
    print(f"  {'Metric':<40} {'A_current':>12} {'B_desc':>12} {'Delta':>8} {'Dir':>10}")
    print(f"  {'-' * 85}")

    metrics = [
        ("Sources tested", "sources", None),
        ("provides_services = True", "svc_true", None),
        ("Has any service_types", "has_types", "higher"),
        ("svc=True with types", "svc_true_has_types", "higher"),
        ("svc=True WITHOUT types", "svc_true_no_types", "lower"),
        ("Total service_types items", "total_types", "higher"),
        ("  - Enum-only items", "total_enum", "lower"),
        ("  - Novel items", "total_novel", "higher"),
        ("Parrot detections (all-enum, >=5)", "parrot", "lower"),
    ]

    for label, key, direction in metrics:
        a_val = agg["A_current"][key]
        b_val = agg["B_desc"][key]
        delta = b_val - a_val
        sign = "+" if delta > 0 else ""
        if direction == "higher":
            arrow = "✓" if delta > 0 else ("✗" if delta < 0 else "=")
        elif direction == "lower":
            arrow = "✓" if delta < 0 else ("✗" if delta > 0 else "=")
        else:
            arrow = ""
        print(f"  {label:<40} {a_val:>12} {b_val:>12} {sign}{delta:>7} {arrow:>10}")

    # Rates
    print()
    for name in PROMPTS:
        a = agg[name]
        svc_rate = 100 * a["svc_true"] / a["sources"] if a["sources"] else 0
        type_rate = (
            100 * a["svc_true_has_types"] / a["svc_true"] if a["svc_true"] else 0
        )
        avg_types = a["total_types"] / a["svc_true"] if a["svc_true"] else 0
        novel_pct = 100 * a["total_novel"] / a["total_types"] if a["total_types"] else 0
        parrot_pct = 100 * a["parrot"] / a["svc_true"] if a["svc_true"] else 0
        print(
            f"  {name}: svc_rate={svc_rate:.1f}%, type_when_svc={type_rate:.1f}%, "
            f"avg_types_per_svc={avg_types:.1f}, novel%={novel_pct:.1f}%, "
            f"parrot%={parrot_pct:.1f}%"
        )

    # ── Agreement analysis ──
    print(f"\n{'=' * 110}")
    print("AGREEMENT ANALYSIS")
    print(f"{'=' * 110}")
    both_true = sum(
        1
        for r in per_source
        if r["A_current"]["provides_services"] and r["B_desc"]["provides_services"]
    )
    both_false = sum(
        1
        for r in per_source
        if not r["A_current"]["provides_services"]
        and not r["B_desc"]["provides_services"]
    )
    a_only = sum(
        1
        for r in per_source
        if r["A_current"]["provides_services"] and not r["B_desc"]["provides_services"]
    )
    b_only = sum(
        1
        for r in per_source
        if not r["A_current"]["provides_services"] and r["B_desc"]["provides_services"]
    )
    total = len(per_source)
    print("  provides_services agreement:")
    print(f"    Both True:  {both_true:>5} ({100 * both_true / total:.1f}%)")
    print(f"    Both False: {both_false:>5} ({100 * both_false / total:.1f}%)")
    print(
        f"    A only:     {a_only:>5} ({100 * a_only / total:.1f}%)  ← A says True, B says False"
    )
    print(
        f"    B only:     {b_only:>5} ({100 * b_only / total:.1f}%)  ← B says True, A says False"
    )
    print(f"    Agreement:  {100 * (both_true + both_false) / total:.1f}%")

    # ── Verdict ──
    print(f"\n{'=' * 110}")
    print("VERDICT")
    print(f"{'=' * 110}")
    a_a = agg["A_current"]
    b_a = agg["B_desc"]

    improvements = []
    regressions = []

    if b_a["total_novel"] > a_a["total_novel"]:
        improvements.append(
            f"More novel types: {a_a['total_novel']}→{b_a['total_novel']}"
        )
    elif b_a["total_novel"] < a_a["total_novel"]:
        regressions.append(
            f"Fewer novel types: {a_a['total_novel']}→{b_a['total_novel']}"
        )

    if b_a["parrot"] < a_a["parrot"]:
        improvements.append(f"Fewer parrots: {a_a['parrot']}→{b_a['parrot']}")
    elif b_a["parrot"] > a_a["parrot"]:
        regressions.append(f"More parrots: {a_a['parrot']}→{b_a['parrot']}")

    if b_a["svc_true_no_types"] < a_a["svc_true_no_types"]:
        improvements.append(
            f"Fewer svc-true-no-types: {a_a['svc_true_no_types']}→{b_a['svc_true_no_types']}"
        )

    if a_only > b_only + 5:
        improvements.append(
            f"B has fewer false-positive svc detections ({a_only} A-only vs {b_only} B-only)"
        )
    elif b_only > a_only + 5:
        regressions.append(f"B has more svc detections A missed ({b_only} B-only)")

    print(f"  Improvements: {len(improvements)}")
    for imp in improvements:
        print(f"    ✓ {imp}")
    print(f"  Regressions: {len(regressions)}")
    for reg in regressions:
        print(f"    ✗ {reg}")

    if len(improvements) > len(regressions) and len(regressions) == 0:
        print("  → B is strictly better. Safe to deploy.")
    elif len(improvements) > len(regressions):
        print("  → B is net-better. Review regressions before deploying.")
    elif len(regressions) > len(improvements):
        print("  → B has regressions. Do not deploy without investigation.")
    else:
        print("  → Inconclusive. No clear winner.")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Wide A/B test for service_types prompt"
    )
    parser.add_argument("--companies", type=int, default=60, help="Number of companies")
    parser.add_argument(
        "--sources-per-company", type=int, default=4, help="Sources per company"
    )
    parser.add_argument(
        "--concurrency", type=int, default=8, help="Max concurrent LLM calls"
    )
    args = parser.parse_args()

    print("=" * 110)
    print("SERVICE_TYPES WIDE A/B TRIAL")
    print(f"  Model: {LLM_MODEL}")
    print(f"  Companies: {args.companies}, Sources/company: {args.sources_per_company}")
    print("  Prompts: A_current (enum desc) vs B_desc (enriched desc)")
    print("=" * 110)

    sources = fetch_sources(args.companies, args.sources_per_company)
    companies = set(s[0] for s in sources)
    print(f"Fetched {len(sources)} sources from {len(companies)} companies")

    asyncio.run(run_trial(sources, concurrency=args.concurrency))


if __name__ == "__main__":
    main()
