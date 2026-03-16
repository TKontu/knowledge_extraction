"""Iterative prompt testing for company_locations extraction.

Tests multiple prompt variants on the same source pages and prints
the full LLM response for inspection. Designed for rapid iteration.

Usage:
    cd src && python ../scripts/trial_location_prompts_v2.py
"""

import asyncio
import json
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


# ── Prompt variants to test ────────────────────────────────────────────────


def _build_entity_prompt(prompt_hint: str, field_specs: str) -> str:
    return f"""You are extracting Company facility and office locations from company documentation.
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
The "quote" must be a VERBATIM excerpt copied directly from the source text, NOT a restatement of your extracted value."""


PROMPTS = {
    "A_current": _build_entity_prompt(
        prompt_hint=(
            "Extract each company LOCATION as a separate entity:\n"
            "- Headquarters, manufacturing plants, factories, production sites\n"
            "- Sales offices, service centers, branch offices, R&D centers\n"
            '- Look in "About Us", "Contact", "Locations", footer sections\n'
            "- Include the site type (headquarters, manufacturing, sales, service, R&D, warehouse)\n"
            "- If only a country is mentioned without a city, still extract it"
        ),
        field_specs=(
            '- "city" (text): City name\n'
            '- "country" (text): Country name [REQUIRED]\n'
            '- "site_type" (text): headquarters, manufacturing, sales, service, R&D, warehouse, office'
        ),
    ),
    "B_field_rules": _build_entity_prompt(
        prompt_hint=(
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
        ),
        field_specs=(
            '- "city" (text): Municipality/town name ONLY — never a country, region, or continent. Null if unknown.\n'
            '- "country" (text): Sovereign nation name (e.g. USA, Germany, Czech Republic). Infer from context if possible.\n'
            '- "site_type" (text): headquarters, manufacturing, sales, service, R&D, warehouse, office'
        ),
    ),
    "C_examples": _build_entity_prompt(
        prompt_hint=(
            "Extract each company LOCATION as a separate entity:\n"
            "- Headquarters, manufacturing plants, factories, production sites\n"
            "- Sales offices, service centers, branch offices, R&D centers\n"
            '- Look in "About Us", "Contact", "Locations", footer sections\n'
            "- Include the site type (headquarters, manufacturing, sales, service, R&D, warehouse)\n"
            "- If only a country is mentioned without a city, still extract it with city as null\n"
            "\n"
            "FIELD PLACEMENT RULES:\n"
            "- city = specific municipality/town ONLY. country = sovereign nation ONLY.\n"
            "- NEVER put a country name in the city field.\n"
            "- Infer the country from clues: domain (.de→Germany), phone prefix (+420→Czech Republic), "
            "addresses, postal codes.\n"
            "- SKIP pure continent/region mentions (North America, Europe, Asia Pacific) — "
            "these are NOT locations.\n"
            "\n"
            "EXAMPLES:\n"
            '- Address "Žižkova 771, 394 68 Žirovnice" → city="Žirovnice", country="Czech Republic"\n'
            '- "Anton-Gmeinder-Str. 9-19, 74821 Mosbach, Germany" → city="Mosbach", country="Germany"\n'
            '- "our India operations" (no city given) → city=null, country="India"\n'
            '- "Sales office North America" → SKIP (region, not a location)\n'
            '- "9445 195th Street, Surrey BC V4N 4G3, Canada" → city="Surrey", country="Canada"'
        ),
        field_specs=(
            '- "city" (text): Municipality/town name ONLY — never a country, region, or continent. Null if unknown.\n'
            '- "country" (text): Sovereign nation name (e.g. USA, Germany, Czech Republic). Infer from context if possible.\n'
            '- "site_type" (text): headquarters, manufacturing, sales, service, R&D, warehouse, office'
        ),
    ),
}


def build_user_prompt(content: str, source_context: str) -> str:
    return f"""Company: {source_context}

Extract company_locations information from ONLY the content below:

---
{content[:CONTENT_LIMIT]}
---"""


async def extract(
    client: AsyncOpenAI, content: str, company: str, prompt: str
) -> list[dict]:
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
        return data.get("company_locations", [])
    except Exception as e:
        return [{"_error": str(e)}]


def score(locations: list[dict]) -> dict:
    total = len(locations)
    city_has_country = 0
    city_has_region = 0
    country_empty_with_city = 0
    city_filled = 0
    country_filled = 0
    for loc in locations:
        if "_error" in loc:
            continue
        city = (loc.get("city") or "").strip()
        country = (loc.get("country") or "").strip()
        if city:
            city_filled += 1
        if country:
            country_filled += 1
        if city.lower() in COUNTRY_NAMES:
            city_has_country += 1
        if city.lower() in REGIONS:
            city_has_region += 1
        if city and not country:
            country_empty_with_city += 1
    return {
        "total": total,
        "city_has_country": city_has_country,
        "city_has_region": city_has_region,
        "country_empty_with_city": country_empty_with_city,
        "city_filled": city_filled,
        "country_filled": country_filled,
    }


def fmt_loc(loc: dict) -> str:
    city = loc.get("city") or "∅"
    country = loc.get("country") or "∅"
    site = loc.get("site_type") or ""
    flags = []
    if city.lower() in COUNTRY_NAMES:
        flags.append("⚠COUNTRY_IN_CITY")
    if city.lower() in REGIONS:
        flags.append("⚠REGION_IN_CITY")
    if city != "∅" and country == "∅":
        flags.append("⚠NO_COUNTRY")
    flag_str = f" {' '.join(flags)}" if flags else ""
    return f"  {city}, {country} [{site}]{flag_str}"


async def main() -> None:
    client = AsyncOpenAI(base_url=LLM_BASE_URL, api_key="ollama")

    conn = psycopg.connect(DB_DSN)
    cur = conn.cursor()

    # Test sources: mix of known-problematic and high-value pages
    test_pages = [
        ("Wikov", "%oil-and-g%"),
        ("Wikov", "%company-structure%"),
        ("Wikov", "%contacts%group%"),
        ("Zf", "%homepage%"),
        ("Sew Eurodrive PE", "%servomotorreductores%"),
        ("Nord", None),
        ("Flender", "%about%"),
        ("Boschrexroth", "%about%"),
        ("Siemens", None),
        ("Dbsantasalo", None),
    ]

    sources = []
    for company, uri_pattern in test_pages:
        if uri_pattern:
            cur.execute(
                """SELECT source_group, uri, COALESCE(cleaned_content, content)
                   FROM sources
                   WHERE project_id = %s AND source_group = %s
                     AND uri LIKE %s AND content IS NOT NULL AND length(content) > 500
                   LIMIT 1""",
                (PROJECT_ID, company, uri_pattern),
            )
        else:
            cur.execute(
                """SELECT source_group, uri, COALESCE(cleaned_content, content)
                   FROM sources
                   WHERE project_id = %s AND source_group = %s
                     AND status = 'extracted' AND content IS NOT NULL AND length(content) > 1000
                   ORDER BY random() LIMIT 2""",
                (PROJECT_ID, company),
            )
        for row in cur.fetchall():
            sources.append(row)
    conn.close()

    print(f"Testing {len(sources)} sources across {len(PROMPTS)} prompt variants\n")

    # Run all prompts on all sources
    agg = {name: defaultdict(int) for name in PROMPTS}

    for company, url, content in sources:
        print(f"\n{'=' * 100}")
        print(f"  {company} | {url[:90]}")
        print(f"  content: {len(content)} chars")
        print(f"{'=' * 100}")

        for prompt_name, prompt in PROMPTS.items():
            locs = await extract(client, content, company, prompt)
            s = score(locs)
            for k, v in s.items():
                agg[prompt_name][k] += v

            has_issues = (
                s["city_has_country"]
                or s["city_has_region"]
                or s["country_empty_with_city"]
            )
            marker = " ⚠" if has_issues else " ✓"
            print(
                f"\n  [{prompt_name}]{marker}  ({s['total']} locations, "
                f"country_fill={s['country_filled']}/{s['total']})"
            )
            for loc in locs:
                print(fmt_loc(loc))

    # Aggregate
    print(f"\n\n{'=' * 100}")
    print("AGGREGATE COMPARISON")
    print(f"{'=' * 100}")
    print(
        f"  {'Prompt':<20} {'Total':>6} {'City✗Country':>13} {'City✗Region':>12} "
        f"{'No Country':>11} {'Country%':>9}"
    )
    print(f"  {'-' * 75}")
    for name in PROMPTS:
        a = agg[name]
        pct = 100 * a["country_filled"] / a["total"] if a["total"] else 0
        print(
            f"  {name:<20} {a['total']:>6} {a['city_has_country']:>13} {a['city_has_region']:>12} "
            f"{a['country_empty_with_city']:>11} {pct:>8.1f}%"
        )


if __name__ == "__main__":
    asyncio.run(main())
