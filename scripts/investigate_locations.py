"""Investigate the locations extraction gap.

Traces the full pipeline: source content → extraction → consolidation → report
for the `locations` field in `company_meta` group.

Answers:
1. Do source pages actually mention locations/cities/countries?
2. What did the LLM return for `locations` in raw extractions?
3. What did the LLM return for `certifications` (same group, working field) for comparison?
4. What does the v2 prompt look like for company_meta?
5. Are there grounding gate drops happening?

Usage:
    cd src && python ../scripts/investigate_locations.py
"""

import json
import re
from pathlib import Path

import psycopg

# -- Config ------------------------------------------------------------------

PROJECT_ID = "99a19141-9268-40a8-bc9e-ad1fa12243da"  # Drivetrain
DB_DSN = "postgresql://scristill:scristill@192.168.0.136:5432/scristill"
SAMPLE_LIMIT = 20  # Number of source groups to sample in detail
OUTPUT_DIR = Path(__file__).parent.parent / "analysis"

# Location-related keywords to search in source content
LOCATION_KEYWORDS = [
    r"\bheadquarter",
    r"\boffice\b",
    r"\bfacility\b",
    r"\bfacilities\b",
    r"\bplant\b",
    r"\bfactory\b",
    r"\bmanufacturing site",
    r"\bservice center",
    r"\bbranch\b",
    r"\blocated in\b",
    r"\bbased in\b",
    r"\boperations in\b",
    r"\bpresence in\b",
]

LOCATION_PATTERN = re.compile("|".join(LOCATION_KEYWORDS), re.IGNORECASE)


def main():
    OUTPUT_DIR.mkdir(exist_ok=True)
    conn = psycopg.connect(DB_DSN)

    report = {}

    # =========================================================================
    # 1. EXTRACTION LAYER: What did the LLM return for company_meta?
    # =========================================================================
    print("=" * 70)
    print("1. RAW EXTRACTIONS — company_meta field analysis")
    print("=" * 70)

    with conn.cursor() as cur:
        # Total company_meta extractions
        cur.execute(
            """
            SELECT count(*), data_version
            FROM extractions
            WHERE project_id = %s AND extraction_type = 'company_meta'
            GROUP BY data_version
            ORDER BY data_version
        """,
            (PROJECT_ID,),
        )
        versions = cur.fetchall()
        print(f"\nExtractions by data_version: {versions}")
        report["extraction_counts_by_version"] = [
            {"version": v, "count": c} for c, v in versions
        ]

        # Analyze locations field in v2 extractions
        cur.execute(
            """
            SELECT
                e.id,
                e.source_id,
                e.source_group,
                e.data,
                e.confidence,
                e.chunk_index,
                e.data_version
            FROM extractions e
            WHERE e.project_id = %s
              AND e.extraction_type = 'company_meta'
              AND e.data_version = 2
            LIMIT 500
        """,
            (PROJECT_ID,),
        )
        rows = cur.fetchall()

        locations_empty = 0
        locations_has_items = 0
        locations_missing = 0
        locations_has_value = 0
        certs_empty = 0
        certs_has_items = 0
        certs_missing = 0
        sample_locations_data = []
        sample_certs_data = []

        for eid, sid, sg, data, conf, chunk_idx, dv in rows:
            # --- locations ---
            loc_field = data.get("locations")
            if loc_field is None:
                locations_missing += 1
            elif isinstance(loc_field, dict):
                items = loc_field.get("items", [])
                if items:
                    locations_has_items += 1
                    if len(sample_locations_data) < 10:
                        sample_locations_data.append(
                            {
                                "extraction_id": str(eid),
                                "source_group": sg,
                                "items": items,
                            }
                        )
                else:
                    # Check if it has "value" key instead (v1 style in v2 wrapper)
                    val = loc_field.get("value")
                    if val and (isinstance(val, list) and len(val) > 0):
                        locations_has_value += 1
                    else:
                        locations_empty += 1
            else:
                # Unexpected format
                if len(sample_locations_data) < 10:
                    sample_locations_data.append(
                        {
                            "extraction_id": str(eid),
                            "raw_type": type(loc_field).__name__,
                            "raw_value": str(loc_field)[:200],
                        }
                    )

            # --- certifications (for comparison) ---
            cert_field = data.get("certifications")
            if cert_field is None:
                certs_missing += 1
            elif isinstance(cert_field, dict):
                items = cert_field.get("items", [])
                if items:
                    certs_has_items += 1
                    if len(sample_certs_data) < 5:
                        sample_certs_data.append(
                            {
                                "extraction_id": str(eid),
                                "source_group": sg,
                                "items": items[:5],
                            }
                        )
                else:
                    certs_empty += 1

        total_v2 = len(rows)
        print(f"\nTotal v2 extractions sampled: {total_v2}")
        print("\n--- locations field ---")
        print(f"  missing (no key):      {locations_missing}")
        print(f"  empty items []:        {locations_empty}")
        print(f"  has items:             {locations_has_items}")
        print(f"  has value (not items): {locations_has_value}")
        print("\n--- certifications field (comparison) ---")
        print(f"  missing (no key):      {certs_missing}")
        print(f"  empty items []:        {certs_empty}")
        print(f"  has items:             {certs_has_items}")

        report["v2_sample_size"] = total_v2
        report["locations_stats"] = {
            "missing": locations_missing,
            "empty_items": locations_empty,
            "has_items": locations_has_items,
            "has_value_not_items": locations_has_value,
        }
        report["certifications_stats"] = {
            "missing": certs_missing,
            "empty_items": certs_empty,
            "has_items": certs_has_items,
        }

        if sample_locations_data:
            print("\n  Sample locations with items:")
            for s in sample_locations_data[:3]:
                print(f"    {json.dumps(s, indent=2)[:300]}")
        else:
            print("\n  NO locations items found in any extraction!")

    # =========================================================================
    # 2. FULL COUNT — all company_meta extractions
    # =========================================================================
    print("\n" + "=" * 70)
    print("2. FULL COUNT — locations across ALL company_meta extractions")
    print("=" * 70)

    with conn.cursor() as cur:
        # Use SQL JSON to check locations field across all extractions
        cur.execute(
            """
            SELECT
                count(*) AS total,
                count(*) FILTER (WHERE data ? 'locations') AS has_locations_key,
                count(*) FILTER (
                    WHERE data->'locations'->'items' IS NOT NULL
                      AND jsonb_array_length(data->'locations'->'items') > 0
                ) AS has_location_items,
                count(*) FILTER (
                    WHERE data ? 'certifications'
                ) AS has_certs_key,
                count(*) FILTER (
                    WHERE data->'certifications'->'items' IS NOT NULL
                      AND jsonb_array_length(data->'certifications'->'items') > 0
                ) AS has_cert_items
            FROM extractions
            WHERE project_id = %s
              AND extraction_type = 'company_meta'
              AND data_version = 2
        """,
            (PROJECT_ID,),
        )
        row = cur.fetchone()
        total, has_loc_key, has_loc_items, has_cert_key, has_cert_items = row

        print(f"\nTotal v2 company_meta extractions: {total}")
        print(
            f"  Has 'locations' key:     {has_loc_key} ({100 * has_loc_key / max(total, 1):.1f}%)"
        )
        print(
            f"  Has location items > 0:  {has_loc_items} ({100 * has_loc_items / max(total, 1):.1f}%)"
        )
        print(f"  Has 'certifications' key: {has_cert_key}")
        print(
            f"  Has cert items > 0:      {has_cert_items} ({100 * has_cert_items / max(total, 1):.1f}%)"
        )

        report["full_count"] = {
            "total_v2_company_meta": total,
            "has_locations_key": has_loc_key,
            "has_location_items": has_loc_items,
            "has_certifications_key": has_cert_key,
            "has_cert_items": has_cert_items,
        }

        # Also check v1 extractions
        cur.execute(
            """
            SELECT
                count(*) AS total,
                count(*) FILTER (
                    WHERE data ? 'locations'
                      AND data->>'locations' != 'null'
                      AND data->>'locations' != '[]'
                      AND data->>'locations' != ''
                ) AS has_locations_data
            FROM extractions
            WHERE project_id = %s
              AND extraction_type = 'company_meta'
              AND (data_version = 1 OR data_version IS NULL)
        """,
            (PROJECT_ID,),
        )
        v1_row = cur.fetchone()
        print(
            f"\nv1 company_meta extractions: {v1_row[0]}, with locations data: {v1_row[1]}"
        )
        report["v1_count"] = {"total": v1_row[0], "has_locations_data": v1_row[1]}

    # =========================================================================
    # 3. WHAT DOES THE LLM ACTUALLY RETURN? — Raw JSON inspection
    # =========================================================================
    print("\n" + "=" * 70)
    print("3. RAW LLM OUTPUT SAMPLES — full data column for company_meta")
    print("=" * 70)

    with conn.cursor() as cur:
        # Get a few full extraction data blobs to see exactly what the LLM returned
        cur.execute(
            """
            SELECT e.id, e.source_group, e.data, e.confidence, e.chunk_index,
                   s.uri, s.title
            FROM extractions e
            JOIN sources s ON s.id = e.source_id
            WHERE e.project_id = %s
              AND e.extraction_type = 'company_meta'
              AND e.data_version = 2
            ORDER BY random()
            LIMIT 10
        """,
            (PROJECT_ID,),
        )
        samples = cur.fetchall()

        raw_samples = []
        for eid, sg, data, conf, chunk, uri, title in samples:
            sample = {
                "extraction_id": str(eid),
                "source_group": sg,
                "uri": uri,
                "title": title,
                "confidence": conf,
                "chunk_index": chunk,
                "locations_field": data.get("locations"),
                "certifications_field": data.get("certifications"),
                "all_keys": list(data.keys()),
            }
            raw_samples.append(sample)
            print(f"\n  [{sg}] {uri}")
            print(
                f"    locations: {json.dumps(data.get('locations'), default=str)[:200]}"
            )
            print(
                f"    certifications: {json.dumps(data.get('certifications'), default=str)[:200]}"
            )

        report["raw_samples"] = raw_samples

    # =========================================================================
    # 4. SOURCE CONTENT ANALYSIS — Do pages mention locations?
    # =========================================================================
    print("\n" + "=" * 70)
    print("4. SOURCE CONTENT — Do pages actually mention locations?")
    print("=" * 70)

    with conn.cursor() as cur:
        # Pick source groups that have company_meta extractions
        cur.execute(
            """
            SELECT DISTINCT e.source_group
            FROM extractions e
            WHERE e.project_id = %s AND e.extraction_type = 'company_meta'
            ORDER BY e.source_group
            LIMIT %s
        """,
            (PROJECT_ID, SAMPLE_LIMIT),
        )
        source_groups = [r[0] for r in cur.fetchall()]

        groups_with_location_text = 0
        groups_total = 0
        location_evidence = []

        for sg in source_groups:
            cur.execute(
                """
                SELECT s.id, s.uri, s.title,
                       COALESCE(s.cleaned_content, s.content) AS content
                FROM sources s
                WHERE s.project_id = %s AND s.source_group = %s
                  AND COALESCE(s.cleaned_content, s.content) IS NOT NULL
                LIMIT 5
            """,
                (PROJECT_ID, sg),
            )
            sources = cur.fetchall()

            group_has_location = False
            group_matches = []

            for sid, uri, title, content in sources:
                if not content:
                    continue
                matches = LOCATION_PATTERN.findall(content)
                if matches:
                    group_has_location = True
                    # Extract context around matches
                    contexts = []
                    for m in re.finditer(LOCATION_PATTERN, content):
                        start = max(0, m.start() - 80)
                        end = min(len(content), m.end() + 80)
                        snippet = content[start:end].replace("\n", " ").strip()
                        contexts.append(snippet)
                    group_matches.append(
                        {
                            "uri": uri,
                            "title": title,
                            "match_count": len(matches),
                            "sample_contexts": contexts[:3],
                            "content_length": len(content),
                        }
                    )

            groups_total += 1
            if group_has_location:
                groups_with_location_text += 1
                location_evidence.append(
                    {
                        "source_group": sg,
                        "sources_with_locations": len(group_matches),
                        "samples": group_matches[:2],
                    }
                )

        print(f"\nSource groups analyzed: {groups_total}")
        print(
            f"Groups with location keywords in content: {groups_with_location_text} ({100 * groups_with_location_text / max(groups_total, 1):.0f}%)"
        )

        print("\nSample evidence (first 5 groups):")
        for ev in location_evidence[:5]:
            print(
                f"\n  [{ev['source_group']}] — {ev['sources_with_locations']} sources with location text"
            )
            for src in ev["samples"][:2]:
                print(f"    {src['uri']}")
                for ctx in src["sample_contexts"][:2]:
                    print(f'      → "{ctx[:120]}..."')

        report["source_content_analysis"] = {
            "groups_analyzed": groups_total,
            "groups_with_location_keywords": groups_with_location_text,
            "percentage": round(
                100 * groups_with_location_text / max(groups_total, 1), 1
            ),
            "evidence_samples": location_evidence[:10],
        }

    # =========================================================================
    # 5. SIDE-BY-SIDE: Source content vs extraction for specific groups
    # =========================================================================
    print("\n" + "=" * 70)
    print("5. SIDE-BY-SIDE — Source mentions locations, but extraction is empty")
    print("=" * 70)

    side_by_side = []
    with conn.cursor() as cur:
        for ev in location_evidence[:8]:
            sg = ev["source_group"]

            # Get all company_meta extractions for this group
            cur.execute(
                """
                SELECT e.data, e.chunk_index, e.confidence, s.uri
                FROM extractions e
                JOIN sources s ON s.id = e.source_id
                WHERE e.project_id = %s
                  AND e.extraction_type = 'company_meta'
                  AND e.source_group = %s
                  AND e.data_version = 2
                ORDER BY e.chunk_index
            """,
                (PROJECT_ID, sg),
            )
            extractions = cur.fetchall()

            loc_items_found = 0
            cert_items_found = 0
            extraction_details = []

            for data, chunk, conf, uri in extractions:
                loc = data.get("locations", {})
                cert = data.get("certifications", {})
                loc_items = loc.get("items", []) if isinstance(loc, dict) else []
                cert_items = cert.get("items", []) if isinstance(cert, dict) else []
                loc_items_found += len(loc_items)
                cert_items_found += len(cert_items)
                extraction_details.append(
                    {
                        "uri": uri,
                        "chunk": chunk,
                        "confidence": conf,
                        "locations": loc,
                        "certifications_count": len(cert_items),
                    }
                )

            entry = {
                "source_group": sg,
                "total_extractions": len(extractions),
                "total_location_items": loc_items_found,
                "total_cert_items": cert_items_found,
                "source_evidence": ev["samples"][:2],
                "extraction_samples": extraction_details[:3],
            }
            side_by_side.append(entry)

            print(f"\n  [{sg}]")
            print(
                f"    Extractions: {len(extractions)}, Location items: {loc_items_found}, Cert items: {cert_items_found}"
            )
            print(
                f"    Source mentions locations: YES ({ev['sources_with_locations']} pages)"
            )
            if extraction_details:
                print(
                    f"    Sample extraction locations field: {json.dumps(extraction_details[0]['locations'], default=str)[:200]}"
                )

    report["side_by_side"] = side_by_side

    # =========================================================================
    # 6. PROMPT RECONSTRUCTION — What does the LLM see?
    # =========================================================================
    print("\n" + "=" * 70)
    print("6. PROMPT ANALYSIS — How locations is described to the LLM")
    print("=" * 70)

    # Reconstruct what the LLM sees for company_meta
    field_spec_locations = (
        '- "locations" (list): List of {city, country, site_type} objects'
    )
    field_spec_certs = '- "certifications" (list): ISO certifications, industry standards, safety certifications'

    prompt_hint = """Extract:
- Certifications: ISO 9001, ISO 14001, ATEX, UL, CE, etc.
- Locations: manufacturing plants, headquarters, sales offices, service centers"""

    v2_output_format = """Output JSON with per-field structure. Each field has its own value, confidence, and quote:
{
  "fields": {
    "certifications": {"value": <extracted_value>, "confidence": 0.0-1.0, "quote": "exact text from source"},
    ...
  }
}"""

    print(f"\nField spec for locations:     {field_spec_locations}")
    print(f"Field spec for certifications: {field_spec_certs}")
    print(f"\nPrompt hint:\n  {prompt_hint}")
    print("\nv2 output format tells LLM to return:")
    print('  {"value": <extracted_value>, "confidence": 0.0-1.0, "quote": "..."}')
    print(
        "\n⚠️  KEY ISSUE: v2 format asks for 'value' key, but list fields are stored as 'items'."
    )
    print('   The LLM sees: return {"value": [...]} for a list field')
    print('   But the pipeline expects: {"items": [{"value": ..., "grounding": ...}]}')
    print(
        "\n⚠️  DESCRIPTION ISSUE: 'List of {city, country, site_type} objects' tells the LLM"
    )
    print(
        "   to return complex objects, but the v2 per-field format only has one 'value' slot."
    )
    print(
        '   The LLM may not know how to encode a list of dicts into {"value": [...], "quote": "..."}'
    )

    report["prompt_analysis"] = {
        "locations_field_spec": field_spec_locations,
        "certifications_field_spec": field_spec_certs,
        "prompt_hint": prompt_hint,
        "v2_output_format_issue": (
            'v2 format asks LLM for {"value": ..., "confidence": ..., "quote": ...} '
            'per field. For list fields, the LLM must return {"value": [...list items...]}. '
            "Complex objects like {city, country, site_type} may confuse the LLM about "
            "how to structure the response. Certifications (simple strings) work; "
            "locations (complex objects) may fail."
        ),
    }

    # =========================================================================
    # 7. CHECK: What does the LLM actually return in the raw data column?
    # =========================================================================
    print("\n" + "=" * 70)
    print("7. DEEP DIVE — Exact JSON structure of locations in extractions")
    print("=" * 70)

    with conn.cursor() as cur:
        # Get diverse sample of locations field values
        cur.execute(
            """
            SELECT DISTINCT ON (data->'locations')
                   data->'locations' AS loc_json,
                   data->>'_meta' AS meta
            FROM extractions
            WHERE project_id = %s
              AND extraction_type = 'company_meta'
              AND data_version = 2
              AND data ? 'locations'
            LIMIT 20
        """,
            (PROJECT_ID,),
        )
        distinct_formats = cur.fetchall()

        print(f"\nDistinct locations field formats found: {len(distinct_formats)}")
        format_samples = []
        for loc_json, meta in distinct_formats:
            fmt = json.dumps(loc_json, default=str)[:300]
            print(f"  {fmt}")
            format_samples.append(fmt)

        report["distinct_locations_formats"] = format_samples

    # =========================================================================
    # 8. CONSOLIDATED DATA — What ended up in consolidated_extractions?
    # =========================================================================
    print("\n" + "=" * 70)
    print("8. CONSOLIDATED DATA — company_meta in consolidated_extractions")
    print("=" * 70)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT c.source_group, c.data, c.source_count, c.grounded_count
            FROM consolidated_extractions c
            WHERE c.project_id = %s AND c.extraction_type = 'company_meta'
            LIMIT 10
        """,
            (PROJECT_ID,),
        )
        consolidated = cur.fetchall()

        print(f"\nConsolidated company_meta records: {len(consolidated)}")
        consolidated_samples = []
        for sg, data, sc, gc in consolidated:
            loc_data = data.get("locations")
            cert_data = data.get("certifications")
            sample = {
                "source_group": sg,
                "source_count": sc,
                "grounded_count": gc,
                "locations": loc_data,
                "certifications": cert_data,
            }
            consolidated_samples.append(sample)
            print(f"\n  [{sg}] sources={sc} grounded={gc}")
            print(f"    locations:      {json.dumps(loc_data, default=str)[:200]}")
            print(f"    certifications: {json.dumps(cert_data, default=str)[:200]}")

        report["consolidated_samples"] = consolidated_samples

    # =========================================================================
    # 9. V1 vs V2 comparison — Did v1 extractions capture locations?
    # =========================================================================
    print("\n" + "=" * 70)
    print("9. V1 vs V2 — Did older v1 extractions capture locations?")
    print("=" * 70)

    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT e.data, e.source_group, s.uri
            FROM extractions e
            JOIN sources s ON s.id = e.source_id
            WHERE e.project_id = %s
              AND e.extraction_type = 'company_meta'
              AND (e.data_version = 1 OR e.data_version IS NULL)
            ORDER BY random()
            LIMIT 10
        """,
            (PROJECT_ID,),
        )
        v1_samples = cur.fetchall()

        print(f"\nv1 samples found: {len(v1_samples)}")
        v1_data = []
        for data, sg, uri in v1_samples:
            loc = data.get("locations")
            cert = data.get("certifications")
            entry = {
                "source_group": sg,
                "uri": uri,
                "locations": loc,
                "certifications": cert,
                "all_keys": list(data.keys()),
            }
            v1_data.append(entry)
            print(f"\n  [{sg}] {uri}")
            print(f"    locations: {json.dumps(loc, default=str)[:200]}")
            print(f"    certifications: {json.dumps(cert, default=str)[:200]}")

        report["v1_samples"] = v1_data

    # =========================================================================
    # 10. SUMMARY & HYPOTHESIS
    # =========================================================================
    print("\n" + "=" * 70)
    print("10. SUMMARY")
    print("=" * 70)

    print(f"""
FINDINGS:
  - v2 company_meta extractions: {report["full_count"]["total_v2_company_meta"]}
  - Extractions with locations items > 0: {report["full_count"]["has_location_items"]}
  - Extractions with cert items > 0: {report["full_count"]["has_cert_items"]}
  - Source pages with location keywords: {report["source_content_analysis"]["percentage"]}%

HYPOTHESES:
  1. DESCRIPTION COMPLEXITY: "List of {{city, country, site_type}} objects" asks for
     structured objects, but the v2 prompt format only shows a flat
     {{"value": ..., "confidence": ..., "quote": ...}} structure. The LLM may not
     know how to return a list of complex objects in that format.

  2. FIELD VS ENTITY: Locations are really an entity list (each with city, country,
     site_type), but defined as a simple list field. The entity_list extraction
     path has proper per-item handling; list fields in non-entity groups don't.

  3. GROUNDING GATE: Even if some items were extracted, list items use "required"
     grounding (text field_type default). Complex dict items like
     {{"city": "X", "country": "Y"}} can't match source text verbatim → all dropped.

  4. CERTIFICATIONS WORK because they're simple strings ("ISO 9001") that match
     source text verbatim. Locations as complex objects can't.

RECOMMENDED INVESTIGATION:
  - Check the distinct formats above to confirm what the LLM actually returns
  - If LLM returns empty items: prompt/description issue
  - If LLM returns items but they're dropped: grounding gate issue
  - Compare with how certifications (simple strings) flow through successfully
""")

    # Write full report
    output_file = OUTPUT_DIR / "locations_investigation.json"
    with open(output_file, "w") as f:
        json.dump(report, f, indent=2, default=str)
    print(f"\nFull report written to: {output_file}")

    conn.close()


if __name__ == "__main__":
    main()
