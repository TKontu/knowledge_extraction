"""A/B test: current vs proposed product prompt for model_number extraction.

Tests all three product groups (gearbox, motor, accessory) with old vs new
prompt_hint that adds model_number guidance.

Usage:
    cd src && python ../scripts/trial_model_number_prompts.py [--companies N] [--sources-per-company N]
"""

import argparse
import asyncio
import json
import random
from collections import defaultdict

import psycopg
from openai import AsyncOpenAI

# ── Config ──────────────────────────────────────────────────────────────────
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

# ── Product group definitions ───────────────────────────────────────────────

PRODUCT_GROUPS = {
    "products_gearbox": {
        "description": "Gearbox product information",
        "fields": [
            '- "product_name" (text): Product name [REQUIRED]',
            '- "series_name" (text): Product series',
            '- "model_number" (text): Model number',
            '- "subcategory" (text): planetary, helical, worm, bevel, cycloidal',
            '- "power_rating_kw" (float): Power rating in kW',
            '- "torque_rating_nm" (float): Torque rating in Nm',
            '- "ratio" (text): Gear ratio',
            '- "efficiency_percent" (float): Efficiency percentage',
        ],
        "old_hint": (
            "Extract GEARBOX products only:\n"
            "- Product names, series, model numbers\n"
            "- Convert HP to kW (multiply by 0.746)\n"
            "- Convert lb-ft to Nm (multiply by 1.356)\n"
            '- Gear ratios like "1:50" or "50:1"'
        ),
        "new_hint": (
            "Extract GEARBOX products only:\n"
            "- Product names, series, model numbers\n"
            "- Convert HP to kW (multiply by 0.746)\n"
            "- Convert lb-ft to Nm (multiply by 1.356)\n"
            '- Gear ratios like "1:50" or "50:1"\n'
            '- model_number: The manufacturer\'s part/model identifier (e.g., "SK 9032.1", "M3BP 315", "RX87").\n'
            "  Look in product tables, datasheets, spec headers. If no distinct model number exists, use null."
        ),
    },
    "products_motor": {
        "description": "Motor product information",
        "fields": [
            '- "product_name" (text): Product name [REQUIRED]',
            '- "series_name" (text): Product series',
            '- "model_number" (text): Model number',
            '- "subcategory" (text): AC, DC, servo, stepper, brushless, induction',
            '- "power_rating_kw" (float): Power rating in kW',
            '- "speed_rating_rpm" (float): Speed rating in RPM',
            '- "voltage" (text): Voltage',
        ],
        "old_hint": (
            "Extract MOTOR products only:\n"
            "- Electric motors, servo motors, stepper motors\n"
            "- Power ratings, speed ratings, voltage"
        ),
        "new_hint": (
            "Extract MOTOR products only:\n"
            "- Electric motors, servo motors, stepper motors\n"
            "- Power ratings, speed ratings, voltage\n"
            '- model_number: The manufacturer\'s part/model identifier (e.g., "SK 9032.1", "M3BP 315", "RX87").\n'
            "  Look in product tables, datasheets, spec headers. If no distinct model number exists, use null."
        ),
    },
    "products_accessory": {
        "description": "Drivetrain accessory products",
        "fields": [
            '- "product_name" (text): Product name [REQUIRED]',
            '- "subcategory" (text): coupling, shaft, bearing, brake, clutch',
            '- "model_number" (text): Model number',
            '- "torque_rating_nm" (float): Torque rating in Nm',
        ],
        "old_hint": (
            "Extract ACCESSORY products:\n"
            "- Couplings, shafts, bearings, brakes, clutches\n"
            "- Pulleys, belts, chains, sprockets"
        ),
        "new_hint": (
            "Extract ACCESSORY products:\n"
            "- Couplings, shafts, bearings, brakes, clutches\n"
            "- Pulleys, belts, chains, sprockets\n"
            '- model_number: The manufacturer\'s part/model identifier (e.g., "SK 9032.1", "M3BP 315", "RX87").\n'
            "  Look in product tables, datasheets, spec headers. If no distinct model number exists, use null."
        ),
    },
}


def build_product_prompt(group_key: str, use_new: bool) -> str:
    """Build entity list extraction prompt for a product group."""
    g = PRODUCT_GROUPS[group_key]
    fields_str = "\n".join(g["fields"])
    hint = g["new_hint"] if use_new else g["old_hint"]
    id_field = "product_name"

    return f"""You are extracting {g["description"]} from company documentation.
{HALLUCINATION_GUARD}
For each product found, extract:
{fields_str}

{hint}

IMPORTANT RULES:
- Extract ONLY from the content provided below. Do NOT use outside knowledge.
- Extract ONLY the most relevant/significant items (max 20 items)
- If no product information found, return an empty list.
- Skip generic navigation/coverage lists, not actual entities.

Output JSON with per-entity confidence and quote:
{{
  "{group_key}": [
    {{<fields>, "_confidence": 0.0-1.0, "_quote": "exact text from source"}},
    ...
  ],
  "has_more": true/false
}}

Set "has_more" to true if there are more entities in the content not yet extracted.

Confidence per entity:
- 0.5-0.7 if sparse detail
- 0.8-1.0 if well-supported with clear evidence

For each entity, include "_quote": a brief verbatim excerpt (15-50 chars) from the source identifying this entity.
The "quote" must be a VERBATIM excerpt copied directly from the source text, NOT a restatement of your extracted value. The quote should show WHERE in the source the information was found."""


def build_user_prompt(content: str, source_context: str, group_key: str) -> str:
    return f"""Company: {source_context}

Extract {group_key} information from ONLY the content below:

---
{content[:CONTENT_LIMIT]}
---"""


def is_real_model_number(val: str) -> bool:
    """Heuristic: a real model number has digits or specific patterns, not just a product name."""
    if not val:
        return False
    val = val.strip()
    # Must contain at least one digit or be very short (likely a code)
    has_digit = any(c.isdigit() for c in val)
    has_special = any(c in val for c in "-./")
    # Reject if it's just a generic word
    if val.lower() in {
        "n/a",
        "null",
        "none",
        "unknown",
        "various",
        "standard",
        "custom",
    }:
        return False
    return has_digit or (has_special and len(val) < 30)


async def extract_products(
    client: AsyncOpenAI,
    content: str,
    source_context: str,
    group_key: str,
    system_prompt: str,
) -> list[dict]:
    """Call LLM and parse product entities."""
    user_prompt = build_user_prompt(content, source_context, group_key)
    try:
        response = await client.chat.completions.create(
            model=LLM_MODEL,
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt},
            ],
            response_format={"type": "json_object"},
            temperature=0.1,
            max_tokens=4096,
        )
        text = response.choices[0].message.content
        data = json.loads(text)
        return data.get(group_key, [])
    except Exception as e:
        print(f"  ERROR: {e}")
        return []


def score_products(products: list[dict]) -> dict:
    """Score product extractions for model_number quality."""
    total = len(products)
    if total == 0:
        return {
            "total": 0,
            "model_filled": 0,
            "model_real": 0,
            "model_fake": 0,
            "model_empty": 0,
            "samples_filled": [],
            "samples_empty": [],
        }

    model_filled = 0
    model_real = 0
    model_fake = 0
    model_empty = 0
    samples_filled = []
    samples_empty = []

    for p in products:
        mn = (p.get("model_number") or "").strip()
        pn = (p.get("product_name") or "").strip()

        if not mn:
            model_empty += 1
            if pn:
                samples_empty.append(pn[:40])
        elif is_real_model_number(mn):
            model_real += 1
            model_filled += 1
            samples_filled.append(f"{pn[:25]}→{mn[:25]}")
        else:
            # Filled but looks like a name, not a model number
            model_fake += 1
            model_filled += 1
            samples_filled.append(f"{pn[:25]}→{mn[:25]}?")

    return {
        "total": total,
        "model_filled": model_filled,
        "model_real": model_real,
        "model_fake": model_fake,
        "model_empty": model_empty,
        "samples_filled": samples_filled[:5],
        "samples_empty": samples_empty[:3],
    }


def fetch_sources(
    n_companies: int,
    sources_per_company: int,
    specific_companies: list[str] | None = None,
) -> list[tuple[str, str, str]]:
    """Fetch sources likely to contain product data."""
    conn = psycopg.connect(DB_DSN)
    cur = conn.cursor()

    if specific_companies:
        companies = specific_companies
    else:
        # Get companies with product extractions
        cur.execute(
            """
            SELECT DISTINCT source_group
            FROM extractions
            WHERE project_id = %s
              AND extraction_type IN ('products_gearbox', 'products_motor', 'products_accessory')
            ORDER BY source_group
            """,
            (PROJECT_ID,),
        )
        all_companies = [r[0] for r in cur.fetchall()]
        must_include = {"Sew-eurodrive", "Siemens", "Nord", "Flender", "Bonfiglioli"}
        chosen = [c for c in all_companies if c in must_include]
        remaining = [c for c in all_companies if c not in must_include]
        random.seed(42)
        extra = random.sample(remaining, min(n_companies - len(chosen), len(remaining)))
        companies = chosen + extra
        companies = companies[:n_companies]

    results = []
    for company in companies:
        cur.execute(
            """
            SELECT s.source_group, s.uri,
                   COALESCE(s.cleaned_content, s.content) as content
            FROM sources s
            JOIN extractions e ON e.source_id = s.id
            WHERE s.project_id = %s
              AND s.source_group = %s
              AND e.extraction_type IN ('products_gearbox', 'products_motor', 'products_accessory')
              AND s.content IS NOT NULL
              AND length(s.content) > 500
            GROUP BY s.id, s.source_group, s.uri, s.cleaned_content, s.content
            ORDER BY random()
            LIMIT %s
            """,
            (PROJECT_ID, company, sources_per_company),
        )
        for row in cur.fetchall():
            results.append(row)

    conn.close()
    return results


async def run_trial(
    sources: list[tuple[str, str, str]],
    concurrency: int = 5,
) -> None:
    """Run A/B extraction for each product group on all sources."""
    client = AsyncOpenAI(base_url=LLM_BASE_URL, api_key="ollama")
    sem = asyncio.Semaphore(concurrency)

    for group_key in PRODUCT_GROUPS:
        print(f"\n{'=' * 100}")
        print(f"PRODUCT GROUP: {group_key}")
        print(f"{'=' * 100}")

        prompt_old = build_product_prompt(group_key, use_new=False)
        prompt_new = build_product_prompt(group_key, use_new=True)

        agg_old = defaultdict(int)
        agg_new = defaultdict(int)
        per_source = []

        async def process_source(sg: str, url: str, content: str) -> None:
            async with sem:
                old_prods = await extract_products(
                    client, content, sg, group_key, prompt_old
                )
                new_prods = await extract_products(
                    client, content, sg, group_key, prompt_new
                )

                old_score = score_products(old_prods)
                new_score = score_products(new_prods)

                per_source.append(
                    {
                        "company": sg,
                        "url": url[:80],
                        "old": old_score,
                        "new": new_score,
                    }
                )

                for key in [
                    "total",
                    "model_filled",
                    "model_real",
                    "model_fake",
                    "model_empty",
                ]:
                    agg_old[key] += old_score[key]
                    agg_new[key] += new_score[key]

        print(f"\nProcessing {len(sources)} sources for {group_key}...")
        tasks = [process_source(sg, url, content) for sg, url, content in sources]
        await asyncio.gather(*tasks)

        # Per-source diffs (only interesting)
        print("\n  PER-SOURCE (showing diffs where model_number changed)")
        print(f"  {'-' * 90}")
        for r in sorted(per_source, key=lambda x: x["company"]):
            old, new = r["old"], r["new"]
            if old["total"] == 0 and new["total"] == 0:
                continue
            if (
                old["model_filled"] == new["model_filled"]
                and old["model_real"] == new["model_real"]
            ):
                continue
            delta_real = new["model_real"] - old["model_real"]
            sign = "+" if delta_real > 0 else ""
            print(
                f"  {r['company']:<25} | OLD: {old['model_real']}/{old['total']} real model#  "
                f"NEW: {new['model_real']}/{new['total']} ({sign}{delta_real})"
            )
            if new["samples_filled"]:
                print(f"    NEW samples: {new['samples_filled']}")

        # Aggregate
        print(f"\n  AGGREGATE: {group_key}")
        print(f"  {'Metric':<30} {'OLD':>8} {'NEW':>8} {'Delta':>8}")
        print(f"  {'-' * 60}")
        for label, key in [
            ("Total products", "total"),
            ("Model# filled", "model_filled"),
            ("Model# real (has digits)", "model_real"),
            ("Model# fake (no digits)", "model_fake"),
            ("Model# empty", "model_empty"),
        ]:
            delta = agg_new[key] - agg_old[key]
            sign = "+" if delta > 0 else ""
            print(f"  {label:<30} {agg_old[key]:>8} {agg_new[key]:>8} {sign}{delta:>7}")

        if agg_old["total"] > 0:
            old_rate = 100 * agg_old["model_real"] / agg_old["total"]
        else:
            old_rate = 0
        if agg_new["total"] > 0:
            new_rate = 100 * agg_new["model_real"] / agg_new["total"]
        else:
            new_rate = 0
        print(
            f"\n  Model# fill rate (real): OLD {old_rate:5.1f}%  →  NEW {new_rate:5.1f}%"
        )


def main() -> None:
    parser = argparse.ArgumentParser(
        description="A/B test product model_number prompts"
    )
    parser.add_argument(
        "--companies", type=int, default=15, help="Number of companies to test"
    )
    parser.add_argument(
        "--sources-per-company", type=int, default=3, help="Sources per company"
    )
    parser.add_argument(
        "--company", action="append", help="Specific company name (repeatable)"
    )
    parser.add_argument(
        "--concurrency", type=int, default=5, help="Max concurrent LLM calls"
    )
    args = parser.parse_args()

    print("=" * 100)
    print("MODEL NUMBER PROMPT A/B TRIAL")
    print(f"  Model: {LLM_MODEL}")
    print(f"  Companies: {args.companies}, Sources/company: {args.sources_per_company}")
    print("=" * 100)

    sources = fetch_sources(
        args.companies,
        args.sources_per_company,
        specific_companies=args.company,
    )
    print(
        f"Fetched {len(sources)} sources from {len(set(s[0] for s in sources))} companies"
    )

    asyncio.run(run_trial(sources, concurrency=args.concurrency))


if __name__ == "__main__":
    main()
