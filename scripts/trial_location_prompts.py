"""A/B test: current vs proposed company_locations prompt on real source data.

Connects to production DB, samples sources from multiple companies,
sends same content to LLM with both old and new prompts, compares results.

Usage:
    cd src && python ../scripts/trial_location_prompts.py [--companies N] [--sources-per-company N]
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

# Common country names for detection
COUNTRY_NAMES = {
    "afghanistan",
    "albania",
    "algeria",
    "argentina",
    "armenia",
    "australia",
    "austria",
    "azerbaijan",
    "bahrain",
    "bangladesh",
    "belarus",
    "belgium",
    "bolivia",
    "bosnia",
    "brazil",
    "brunei",
    "bulgaria",
    "cambodia",
    "cameroon",
    "canada",
    "chile",
    "china",
    "colombia",
    "costa rica",
    "croatia",
    "cuba",
    "czech republic",
    "czechia",
    "denmark",
    "dominican republic",
    "ecuador",
    "egypt",
    "el salvador",
    "estonia",
    "ethiopia",
    "finland",
    "france",
    "georgia",
    "germany",
    "ghana",
    "greece",
    "guatemala",
    "honduras",
    "hong kong",
    "hungary",
    "iceland",
    "india",
    "indonesia",
    "iran",
    "iraq",
    "ireland",
    "israel",
    "italy",
    "jamaica",
    "japan",
    "jordan",
    "kazakhstan",
    "kenya",
    "kuwait",
    "latvia",
    "lebanon",
    "libya",
    "lithuania",
    "luxembourg",
    "malaysia",
    "mexico",
    "mongolia",
    "morocco",
    "myanmar",
    "nepal",
    "netherlands",
    "new zealand",
    "nicaragua",
    "nigeria",
    "north korea",
    "north macedonia",
    "norway",
    "oman",
    "pakistan",
    "panama",
    "paraguay",
    "peru",
    "philippines",
    "poland",
    "portugal",
    "qatar",
    "romania",
    "russia",
    "saudi arabia",
    "senegal",
    "serbia",
    "singapore",
    "slovakia",
    "slovenia",
    "south africa",
    "south korea",
    "spain",
    "sri lanka",
    "sudan",
    "sweden",
    "switzerland",
    "syria",
    "taiwan",
    "tanzania",
    "thailand",
    "tunisia",
    "turkey",
    "turkiye",
    "uae",
    "uganda",
    "uk",
    "ukraine",
    "united arab emirates",
    "united kingdom",
    "united states",
    "uruguay",
    "usa",
    "uzbekistan",
    "venezuela",
    "vietnam",
    "yemen",
    "zambia",
    "zimbabwe",
}

# Regions/continents that should NOT appear in city or country
REGIONS = {
    "north america",
    "south america",
    "latin america",
    "central america",
    "europe",
    "asia",
    "africa",
    "oceania",
    "middle east",
    "asia pacific",
    "asia-pacific",
    "apac",
    "emea",
    "americas",
    "nordic",
    "scandinavia",
    "southeast asia",
    "east asia",
    "western europe",
    "eastern europe",
    "sub-saharan africa",
    "mena",
}

# ── Hallucination guard (from schema_extractor.py) ─────────────────────────
HALLUCINATION_GUARD = (
    "CRITICAL CONSTRAINT: You are a text extraction tool, NOT a knowledge base.\n"
    "- ONLY extract information that is EXPLICITLY STATED in the provided text below.\n"
    "- If a field's information is not in the text, you MUST return null — do NOT guess, "
    "infer from general knowledge, or fill in plausible-sounding values.\n"
    "- It is MUCH better to return null than to fabricate data."
)


def build_system_prompt_old(source_type: str = "company documentation") -> str:
    """Current (old) prompt — matches production exactly."""
    prompt_hint = (
        "Extract each company LOCATION as a separate entity:\n"
        "- Headquarters, manufacturing plants, factories, production sites\n"
        "- Sales offices, service centers, branch offices, R&D centers\n"
        '- Look in "About Us", "Contact", "Locations", footer sections\n'
        "- Include the site type (headquarters, manufacturing, sales, service, R&D, warehouse)\n"
        "- If only a country is mentioned without a city, still extract it"
    )
    field_specs = (
        '- "city" (text): City name\n'
        '- "country" (text): Country name [REQUIRED]\n'
        '- "site_type" (text): headquarters, manufacturing, sales, service, R&D, warehouse, office'
    )
    return _build_entity_prompt(prompt_hint, field_specs, source_type)


def build_system_prompt_new(source_type: str = "company documentation") -> str:
    """Proposed (new) prompt B — field rules, tested winner."""
    prompt_hint = (
        "Extract each company LOCATION as a separate entity:\n"
        "- Headquarters, manufacturing plants, factories, production sites\n"
        "- Sales offices, service centers, branch offices, R&D centers\n"
        '- Look in "About Us", "Contact", "Locations", footer sections\n'
        "- Include the site type (headquarters, manufacturing, sales, service, R&D, warehouse)\n"
        "- If only a country is mentioned without a city, still extract it with city as null\n"
        "\n"
        "FIELD PLACEMENT RULES:\n"
        "- city: MUST be a specific municipality or town (e.g. Detroit, Shanghai, Praha). "
        "NEVER put a country name or region here.\n"
        "- country: MUST be a sovereign nation (e.g. USA, China, Czech Republic). "
        "Infer the country from context when possible (e.g. .de domain → Germany, "
        "phone +420 → Czech Republic, address mentions a state/province).\n"
        "- If only a country or region is mentioned with no specific city, "
        "set city to null and put the country in the country field.\n"
        "- SKIP entries that are only a continent or region name "
        '(e.g. "North America", "Europe", "Asia Pacific") with no country or city.'
    )
    field_specs = (
        '- "city" (text): Municipality/town name ONLY — never a country, region, or continent. Null if unknown.\n'
        '- "country" (text): Sovereign nation name (e.g. USA, Germany, Czech Republic). Infer from context if possible.\n'
        '- "site_type" (text): headquarters, manufacturing, sales, service, R&D, warehouse, office'
    )
    return _build_entity_prompt(prompt_hint, field_specs, source_type)


def _build_entity_prompt(prompt_hint: str, field_specs: str, source_type: str) -> str:
    """Build entity list extraction prompt (mirrors v2 format from schema_extractor.py)."""
    return f"""You are extracting Company facility and office locations from {source_type}.
{HALLUCINATION_GUARD}
For each company_location found, extract:
{field_specs}

{prompt_hint}

IMPORTANT RULES:
- Extract ONLY from the content provided below. Do NOT use outside knowledge.
- Extract ONLY the most relevant/significant items (max 20 items)
- If no company_location information found, return an empty list.
- Skip generic navigation/coverage lists, not actual entities.

Output JSON with per-entity confidence and quote:
{{
  "company_locations": [
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


def build_user_prompt(content: str, source_context: str) -> str:
    """Build user prompt (mirrors schema_extractor._build_user_prompt)."""
    return f"""Company: {source_context}

Extract company_locations information from ONLY the content below:

---
{content[:CONTENT_LIMIT]}
---"""


def score_result(locations: list[dict]) -> dict:
    """Score a set of extracted locations for quality issues."""
    total = len(locations)
    if total == 0:
        return {
            "total": 0,
            "city_has_country": 0,
            "city_has_region": 0,
            "country_empty_with_city": 0,
            "city_filled": 0,
            "country_filled": 0,
            "city_empty": 0,
            "both_empty": 0,
            "site_type_filled": 0,
            "bad_cities": [],
            "good_locations": [],
        }

    city_has_country = 0
    city_has_region = 0
    country_empty = 0
    city_filled = 0
    country_filled = 0
    city_empty = 0
    both_empty = 0
    site_type_filled = 0
    bad_cities = []
    good_locations = []

    for loc in locations:
        city = (loc.get("city") or "").strip()
        country = (loc.get("country") or "").strip()
        site_type = (loc.get("site_type") or "").strip()

        city_lower = city.lower()
        country_lower = country.lower()

        if city:
            city_filled += 1
        else:
            city_empty += 1

        if country:
            country_filled += 1

        if not city and not country:
            both_empty += 1

        if site_type:
            site_type_filled += 1

        # Check: country name in city field
        if city_lower in COUNTRY_NAMES:
            city_has_country += 1
            bad_cities.append(
                {"city": city, "country": country, "issue": "country_in_city"}
            )

        # Check: region/continent in city field
        if city_lower in REGIONS:
            city_has_region += 1
            bad_cities.append(
                {"city": city, "country": country, "issue": "region_in_city"}
            )

        # Check: region/continent in country field
        if country_lower in REGIONS:
            bad_cities.append(
                {"city": city, "country": country, "issue": "region_in_country"}
            )

        # Check: city filled but country empty
        if city and not country:
            country_empty += 1

        # Good location
        if (
            city
            and country
            and city_lower not in COUNTRY_NAMES
            and city_lower not in REGIONS
        ):
            good_locations.append(f"{city}, {country}")

    return {
        "total": total,
        "city_has_country": city_has_country,
        "city_has_region": city_has_region,
        "country_empty_with_city": country_empty,
        "city_filled": city_filled,
        "country_filled": country_filled,
        "city_empty": city_empty,
        "both_empty": both_empty,
        "site_type_filled": site_type_filled,
        "bad_cities": bad_cities,
        "good_locations": good_locations[:5],
    }


async def extract_locations(
    client: AsyncOpenAI,
    content: str,
    source_context: str,
    system_prompt: str,
) -> list[dict]:
    """Call LLM and parse location entities."""
    user_prompt = build_user_prompt(content, source_context)
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
        return data.get("company_locations", [])
    except Exception as e:
        print(f"  ERROR: {e}")
        return []


def fetch_sources(
    n_companies: int,
    sources_per_company: int,
    specific_companies: list[str] | None = None,
) -> list[tuple[str, str, str]]:
    """Fetch real source content from DB. Returns [(source_group, url, content), ...]."""
    conn = psycopg.connect(DB_DSN)
    cur = conn.cursor()

    if specific_companies:
        companies = specific_companies
    else:
        # Get companies that had location extractions
        cur.execute(
            """
            SELECT DISTINCT source_group
            FROM sources
            WHERE project_id = %s
              AND status = 'extracted'
              AND content IS NOT NULL
              AND length(content) > 500
            ORDER BY source_group
            """,
            (PROJECT_ID,),
        )
        all_companies = [r[0] for r in cur.fetchall()]
        # Sample diverse set — include known-problematic ones
        must_include = {"Wikov", "Sew-eurodrive", "Nord", "Siemens", "Zf"}
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
            SELECT source_group, uri,
                   COALESCE(cleaned_content, content) as content
            FROM sources
            WHERE project_id = %s
              AND source_group = %s
              AND status = 'extracted'
              AND content IS NOT NULL
              AND length(content) > 500
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
    """Run A/B extraction on all sources and print comparison."""
    client = AsyncOpenAI(base_url=LLM_BASE_URL, api_key="ollama")

    prompt_old = build_system_prompt_old()
    prompt_new = build_system_prompt_new()

    # Aggregate scores
    agg_old = defaultdict(int)
    agg_new = defaultdict(int)
    per_source_results = []

    sem = asyncio.Semaphore(concurrency)

    async def process_source(source_group: str, url: str, content: str) -> None:
        async with sem:
            # Run both prompts on same content
            old_locs = await extract_locations(
                client, content, source_group, prompt_old
            )
            new_locs = await extract_locations(
                client, content, source_group, prompt_new
            )

            old_score = score_result(old_locs)
            new_score = score_result(new_locs)

            per_source_results.append(
                {
                    "company": source_group,
                    "url": url[:80],
                    "old": old_score,
                    "new": new_score,
                }
            )

            # Aggregate
            for key in [
                "total",
                "city_has_country",
                "city_has_region",
                "country_empty_with_city",
                "city_filled",
                "country_filled",
                "city_empty",
                "both_empty",
                "site_type_filled",
            ]:
                agg_old[key] += old_score[key]
                agg_new[key] += new_score[key]

    print(f"\nProcessing {len(sources)} sources...")
    tasks = [process_source(sg, url, content) for sg, url, content in sources]
    await asyncio.gather(*tasks)

    # ── Print per-source diffs (only interesting ones) ──
    print("\n" + "=" * 100)
    print("PER-SOURCE DIFFERENCES (showing only sources with quality issues)")
    print("=" * 100)

    for r in sorted(per_source_results, key=lambda x: x["company"]):
        old, new = r["old"], r["new"]
        has_issues = (
            old["city_has_country"] > 0
            or new["city_has_country"] > 0
            or old["city_has_region"] > 0
            or new["city_has_region"] > 0
            or old["country_empty_with_city"] > 0
            or new["country_empty_with_city"] > 0
        )
        if not has_issues and old["total"] == 0 and new["total"] == 0:
            continue

        print(f"\n  {r['company']} | {r['url']}")
        print(
            f"    OLD: {old['total']} locs, city_has_country={old['city_has_country']}, "
            f"country_empty={old['country_empty_with_city']}, "
            f"country_filled={old['country_filled']}/{old['total']}"
        )
        print(
            f"    NEW: {new['total']} locs, city_has_country={new['city_has_country']}, "
            f"country_empty={new['country_empty_with_city']}, "
            f"country_filled={new['country_filled']}/{new['total']}"
        )

        if old["bad_cities"]:
            print(f"    OLD bad: {old['bad_cities']}")
        if new["bad_cities"]:
            print(f"    NEW bad: {new['bad_cities']}")
        if new["good_locations"]:
            print(f"    NEW good: {new['good_locations']}")

    # ── Aggregate comparison ──
    print("\n" + "=" * 100)
    print("AGGREGATE COMPARISON")
    print("=" * 100)
    print(f"  Sources tested: {len(per_source_results)}")
    print()
    print(f"  {'Metric':<35} {'OLD':>8} {'NEW':>8} {'Delta':>8} {'Direction':<12}")
    print(f"  {'-' * 75}")

    metrics = [
        ("Total locations extracted", "total", "neutral"),
        ("City has country name", "city_has_country", "lower_better"),
        ("City has region/continent", "city_has_region", "lower_better"),
        ("Country empty (city filled)", "country_empty_with_city", "lower_better"),
        ("City filled", "city_filled", "higher_better"),
        ("Country filled", "country_filled", "higher_better"),
        ("City empty (country-only)", "city_empty", "neutral"),
        ("Both empty", "both_empty", "lower_better"),
        ("Site type filled", "site_type_filled", "higher_better"),
    ]

    for label, key, direction in metrics:
        old_val = agg_old[key]
        new_val = agg_new[key]
        delta = new_val - old_val
        if direction == "lower_better":
            arrow = "✓ BETTER" if delta < 0 else ("✗ WORSE" if delta > 0 else "  same")
        elif direction == "higher_better":
            arrow = "✓ BETTER" if delta > 0 else ("✗ WORSE" if delta < 0 else "  same")
        else:
            arrow = ""
        sign = "+" if delta > 0 else ""
        print(f"  {label:<35} {old_val:>8} {new_val:>8} {sign}{delta:>7} {arrow}")

    # Rates
    print()
    if agg_old["total"] > 0:
        old_country_rate = 100 * agg_old["country_filled"] / agg_old["total"]
        old_bad_rate = 100 * agg_old["city_has_country"] / agg_old["total"]
    else:
        old_country_rate = old_bad_rate = 0
    if agg_new["total"] > 0:
        new_country_rate = 100 * agg_new["country_filled"] / agg_new["total"]
        new_bad_rate = 100 * agg_new["city_has_country"] / agg_new["total"]
    else:
        new_country_rate = new_bad_rate = 0

    print(
        f"  Country fill rate:       OLD {old_country_rate:5.1f}%  →  NEW {new_country_rate:5.1f}%"
    )
    print(
        f"  Country-in-city rate:    OLD {old_bad_rate:5.1f}%  →  NEW {new_bad_rate:5.1f}%"
    )

    print("\n" + "=" * 100)
    print("VERDICT")
    print("=" * 100)
    improvements = 0
    regressions = 0
    for _, key, direction in metrics:
        delta = agg_new[key] - agg_old[key]
        if direction == "lower_better" and delta < 0:
            improvements += 1
        elif direction == "lower_better" and delta > 0:
            regressions += 1
        elif direction == "higher_better" and delta > 0:
            improvements += 1
        elif direction == "higher_better" and delta < 0:
            regressions += 1
    print(f"  Improvements: {improvements}  |  Regressions: {regressions}")
    if regressions == 0 and improvements > 0:
        print("  → NEW prompt is strictly better. Safe to deploy.")
    elif regressions > 0 and improvements > regressions:
        print(
            "  → NEW prompt is net-better but has some regressions. Review per-source diffs."
        )
    elif regressions > 0:
        print("  → NEW prompt has regressions. Investigate before deploying.")
    else:
        print("  → No significant difference detected. May need more data.")


def main() -> None:
    parser = argparse.ArgumentParser(description="A/B test location extraction prompts")
    parser.add_argument(
        "--companies", type=int, default=20, help="Number of companies to test"
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
    print("LOCATION PROMPT A/B TRIAL")
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
