"""A/B test: current vs proposed services prompt for service_types extraction.

Tests 4 prompt variants on real source pages where provides_services=True
but service_types is empty in production.

Usage:
    cd src && python ../scripts/trial_service_types_prompts.py
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


# ── Prompt variants ─────────────────────────────────────────────────────────


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


PROMPTS = {
    "A_current": _build_prompt(
        prompt_hint=(
            "Look for SERVICE offerings:\n"
            "- Repair services, maintenance programs, overhaul\n"
            "- Service centers, field service teams (on-site service at customer locations)\n"
            "- Spare parts supply, technical support\n"
            "- Field service = technicians travel to customer site"
        ),
        service_types_desc=(
            "Types: repair, maintenance, refurbishment, installation, commissioning, field service"
        ),
    ),
    "B_desc": _build_prompt(
        prompt_hint=(
            "Look for SERVICE offerings:\n"
            "- Repair services, maintenance programs, overhaul\n"
            "- Service centers, field service teams (on-site service at customer locations)\n"
            "- Spare parts supply, technical support\n"
            "- Field service = technicians travel to customer site"
        ),
        service_types_desc=(
            "List of specific service types offered. Examples: repair, maintenance, "
            "refurbishment, installation, commissioning, field service, overhaul, "
            "spare parts, technical support, inspection, testing. "
            "Extract the actual terms used on the page, in any language."
        ),
    ),
    "C_hint": _build_prompt(
        prompt_hint=(
            "Look for SERVICE offerings:\n"
            "- Repair services, maintenance programs, overhaul\n"
            "- Service centers, field service teams (on-site service at customer locations)\n"
            "- Spare parts supply, technical support\n"
            "- Field service = technicians travel to customer site\n"
            "When provides_services is true, also populate the service_types list with "
            "the specific services mentioned (e.g. repair, maintenance, overhaul, "
            "installation, spare parts, inspection, field service). "
            "Extract the actual terms from the text, not just the boolean flags."
        ),
        service_types_desc=(
            "Types: repair, maintenance, refurbishment, installation, commissioning, field service"
        ),
    ),
    "D_both": _build_prompt(
        prompt_hint=(
            "Look for SERVICE offerings:\n"
            "- Repair services, maintenance programs, overhaul\n"
            "- Service centers, field service teams (on-site service at customer locations)\n"
            "- Spare parts supply, technical support\n"
            "- Field service = technicians travel to customer site\n"
            "When provides_services is true, also populate the service_types list with "
            "the specific services mentioned (e.g. repair, maintenance, overhaul, "
            "installation, spare parts, inspection, field service). "
            "Extract the actual terms from the text, not just the boolean flags."
        ),
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
        fields = data.get("fields", data)
        return fields
    except Exception as e:
        return {"_error": str(e)}


def get_val(field_data):
    """Extract value from v2 field structure or plain value."""
    if isinstance(field_data, dict):
        return field_data.get("value")
    return field_data


def score(fields: dict) -> dict:
    if "_error" in fields:
        return {"provides_services": False, "service_types": [], "n_types": 0}
    ps = get_val(fields.get("provides_services"))
    st = get_val(fields.get("service_types"))
    if not isinstance(st, list):
        st = []
    # Flatten: items may be dicts with 'value' or plain strings
    flat = []
    for item in st:
        if isinstance(item, dict):
            flat.append(str(item.get("value", item)))
        else:
            flat.append(str(item))
    return {
        "provides_services": bool(ps),
        "service_types": flat,
        "n_types": len(flat),
    }


def fetch_test_sources() -> list[tuple[str, str, str]]:
    """Fetch sources from gap companies where services were detected + good companies for control."""
    conn = psycopg.connect(DB_DSN)
    cur = conn.cursor()

    # Gap companies: provides_services=True in consolidated but no service_types
    # Pick ones with enough service-relevant content
    gap_companies = [
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
    ]

    # Good companies for control (have service_types in production)
    good_companies = [
        "Allisontransmission",
        "Dbsantasalo",
        "Amigaeng",
        "Abb",
    ]

    sources = []

    for company in gap_companies + good_companies:
        # Get pages where the raw extraction had provides_services=True
        cur.execute(
            """
            SELECT DISTINCT s.source_group, s.uri, COALESCE(s.cleaned_content, s.content)
            FROM sources s
            JOIN extractions e ON e.source_id = s.id
            WHERE s.project_id = %(pid)s
              AND s.source_group = %(sg)s
              AND e.extraction_type = 'services'
              AND e.data IS NOT NULL
              AND s.content IS NOT NULL
              AND length(s.content) > 500
            ORDER BY s.uri
            LIMIT 4
            """,
            {"pid": PROJECT_ID, "sg": company},
        )
        for row in cur.fetchall():
            sources.append(row)

    # Also grab pages with service keywords for gap companies that may not have extractions
    for company in gap_companies:
        cur.execute(
            """
            SELECT source_group, uri, COALESCE(cleaned_content, content)
            FROM sources
            WHERE project_id = %(pid)s AND source_group = %(sg)s
              AND status = 'extracted'
              AND (content ILIKE '%%repair%%' OR content ILIKE '%%maintenance%%'
                   OR content ILIKE '%%service%%center%%' OR content ILIKE '%%field service%%'
                   OR content ILIKE '%%mantenimiento%%' OR content ILIKE '%%reparación%%'
                   OR content ILIKE '%%manutenção%%')
              AND content IS NOT NULL AND length(content) > 500
            ORDER BY random()
            LIMIT 2
            """,
            {"pid": PROJECT_ID, "sg": company},
        )
        for row in cur.fetchall():
            # Deduplicate by URL
            if row[1] not in {s[1] for s in sources}:
                sources.append(row)

    conn.close()
    return sources


async def main() -> None:
    client = AsyncOpenAI(base_url=LLM_BASE_URL, api_key="ollama")
    sources = fetch_test_sources()

    print(f"Testing {len(sources)} sources across {len(PROMPTS)} prompt variants\n")

    # Track per-prompt aggregates
    agg = {name: defaultdict(int) for name in PROMPTS}
    # Track gap vs good separately
    gap_agg = {name: defaultdict(int) for name in PROMPTS}
    good_companies = {"Allisontransmission", "Dbsantasalo", "Amigaeng", "Abb"}

    for company, url, content in sources:
        is_good = company in good_companies
        tag = "GOOD" if is_good else "GAP"

        print(f"\n{'=' * 100}")
        print(f"  [{tag}] {company} | {url[:85]}")
        print(f"{'=' * 100}")

        for prompt_name, prompt in PROMPTS.items():
            fields = await extract_services(client, content, company, prompt)
            s = score(fields)

            target = agg[prompt_name]
            target["sources"] += 1
            target["svc_true"] += int(s["provides_services"])
            target["has_types"] += int(s["n_types"] > 0)
            target["total_types"] += s["n_types"]
            target["svc_true_no_types"] += int(
                s["provides_services"] and s["n_types"] == 0
            )

            if not is_good:
                gt = gap_agg[prompt_name]
                gt["sources"] += 1
                gt["svc_true"] += int(s["provides_services"])
                gt["has_types"] += int(s["n_types"] > 0)
                gt["total_types"] += s["n_types"]
                gt["svc_true_no_types"] += int(
                    s["provides_services"] and s["n_types"] == 0
                )

            marker = (
                "✓" if s["n_types"] > 0 else ("⚠" if s["provides_services"] else "·")
            )
            types_str = (
                ", ".join(s["service_types"][:5]) if s["service_types"] else "[]"
            )
            print(
                f"  [{prompt_name}] {marker}  svc={s['provides_services']}, "
                f"types({s['n_types']}): {types_str}"
            )

    # ── Aggregate ──
    print(f"\n\n{'=' * 100}")
    print("AGGREGATE — ALL SOURCES")
    print(f"{'=' * 100}")
    print(
        f"  {'Prompt':<15} {'Sources':>8} {'SvcTrue':>8} {'HasTypes':>9} "
        f"{'AvgTypes':>9} {'SvcNoTypes':>11} {'TypeRate':>9}"
    )
    print(f"  {'-' * 72}")
    for name in PROMPTS:
        a = agg[name]
        avg_types = a["total_types"] / a["sources"] if a["sources"] else 0
        type_rate = 100 * a["has_types"] / a["svc_true"] if a["svc_true"] else 0
        print(
            f"  {name:<15} {a['sources']:>8} {a['svc_true']:>8} {a['has_types']:>9} "
            f"{avg_types:>9.1f} {a['svc_true_no_types']:>11} {type_rate:>8.1f}%"
        )

    print(f"\n{'=' * 100}")
    print("AGGREGATE — GAP COMPANIES ONLY (the ones we're trying to fix)")
    print(f"{'=' * 100}")
    print(
        f"  {'Prompt':<15} {'Sources':>8} {'SvcTrue':>8} {'HasTypes':>9} "
        f"{'AvgTypes':>9} {'SvcNoTypes':>11} {'TypeRate':>9}"
    )
    print(f"  {'-' * 72}")
    for name in PROMPTS:
        a = gap_agg[name]
        avg_types = a["total_types"] / a["sources"] if a["sources"] else 0
        type_rate = 100 * a["has_types"] / a["svc_true"] if a["svc_true"] else 0
        print(
            f"  {name:<15} {a['sources']:>8} {a['svc_true']:>8} {a['has_types']:>9} "
            f"{avg_types:>9.1f} {a['svc_true_no_types']:>11} {type_rate:>8.1f}%"
        )

    print(f"\n{'=' * 100}")
    print("VERDICT")
    print(f"{'=' * 100}")
    # Compare: which prompt has highest TypeRate for gap companies?
    best = max(
        PROMPTS.keys(),
        key=lambda n: (
            100 * gap_agg[n]["has_types"] / gap_agg[n]["svc_true"]
            if gap_agg[n]["svc_true"]
            else 0
        ),
    )
    baseline_rate = (
        100 * gap_agg["A_current"]["has_types"] / gap_agg["A_current"]["svc_true"]
        if gap_agg["A_current"]["svc_true"]
        else 0
    )
    best_rate = (
        100 * gap_agg[best]["has_types"] / gap_agg[best]["svc_true"]
        if gap_agg[best]["svc_true"]
        else 0
    )
    print(f"  Baseline (A_current) gap TypeRate: {baseline_rate:.1f}%")
    print(f"  Best variant ({best}) gap TypeRate: {best_rate:.1f}%")
    if best_rate > baseline_rate + 5:
        print(f"  → {best} is significantly better. Deploy it.")
    elif best_rate > baseline_rate:
        print(f"  → {best} is slightly better. Consider deploying.")
    else:
        print("  → No significant improvement. May be a data availability issue.")


if __name__ == "__main__":
    asyncio.run(main())
