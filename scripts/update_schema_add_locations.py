"""Add company_locations entity list group to the drivetrain project schema.

This script:
1. Fetches the current extraction_schema from the live API
2. Validates company_locations doesn't already exist
3. Removes the 'locations' field from company_meta (if present)
4. Appends the company_locations entity list group
5. PUTs the updated schema back with force=true
6. Verifies the update succeeded

Usage:
    python scripts/update_schema_add_locations.py [--dry-run]
"""

import argparse
import json
import sys

import httpx

API_BASE = "http://192.168.0.136:8742/api/v1"
PROJECT_ID = "99a19141-9268-40a8-bc9e-ad1fa12243da"

COMPANY_LOCATIONS_GROUP = {
    "name": "company_locations",
    "description": "Company facility and office locations",
    "is_entity_list": True,
    "prompt_hint": (
        "Extract each company LOCATION as a separate entity:\n"
        "- Headquarters, manufacturing plants, factories, production sites\n"
        "- Sales offices, service centers, branch offices, R&D centers\n"
        '- Look in "About Us", "Contact", "Locations", footer sections\n'
        "- Include the site type (headquarters, manufacturing, sales, service, R&D, warehouse)\n"
        "- If only a country is mentioned without a city, still extract it\n"
    ),
    "fields": [
        {
            "name": "city",
            "field_type": "text",
            "required": False,
            "description": "City name",
        },
        {
            "name": "country",
            "field_type": "text",
            "required": True,
            "default": "",
            "description": "Country name",
        },
        {
            "name": "site_type",
            "field_type": "text",
            "required": False,
            "description": "headquarters, manufacturing, sales, service, R&D, warehouse, office",
        },
    ],
}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Add company_locations to drivetrain schema"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Show changes without applying"
    )
    args = parser.parse_args()

    client = httpx.Client(
        timeout=30,
        headers={"X-API-Key": "thisismyapikey3215215632"},
    )

    # Step 1: Fetch current project
    print(f"[1/5] Fetching project {PROJECT_ID}...")
    resp = client.get(f"{API_BASE}/projects/{PROJECT_ID}")
    if resp.status_code != 200:
        print(f"FATAL: GET project failed: {resp.status_code} {resp.text}")
        sys.exit(1)

    project = resp.json()
    schema = project.get("extraction_schema")
    if not schema:
        print("FATAL: Project has no extraction_schema")
        sys.exit(1)

    field_groups = schema.get("field_groups", [])
    group_names = [g["name"] for g in field_groups]
    print(f"  Current field groups ({len(field_groups)}): {group_names}")

    # Step 2: Check if company_locations already exists
    print("[2/5] Checking for existing company_locations...")
    if "company_locations" in group_names:
        print("  ALREADY EXISTS — nothing to do. Exiting.")
        sys.exit(0)
    print("  Not found — will add.")

    # Step 3: Remove 'locations' field from company_meta
    print("[3/5] Cleaning up company_meta...")
    for group in field_groups:
        if group["name"] == "company_meta":
            original_fields = [f["name"] for f in group.get("fields", [])]
            group["fields"] = [f for f in group["fields"] if f["name"] != "locations"]
            new_fields = [f["name"] for f in group["fields"]]
            if len(original_fields) != len(new_fields):
                removed = set(original_fields) - set(new_fields)
                print(f"  Removed field(s) from company_meta: {removed}")
                # Update description/prompt_hint since locations is gone
                group["description"] = "Certifications"
                group["prompt_hint"] = (
                    "Extract:\n- Certifications: ISO 9001, ISO 14001, ATEX, UL, CE, etc.\n"
                )
            else:
                print("  'locations' field not in company_meta (already clean)")
            break

    # Step 4: Append company_locations group
    print("[4/5] Appending company_locations entity list group...")
    field_groups.append(COMPANY_LOCATIONS_GROUP)
    schema["field_groups"] = field_groups
    new_names = [g["name"] for g in field_groups]
    print(f"  Updated field groups ({len(field_groups)}): {new_names}")

    if args.dry_run:
        print("\n=== DRY RUN — would PUT this schema ===")
        print(json.dumps({"extraction_schema": schema}, indent=2)[:2000])
        print("... (truncated)")
        print("\nRe-run without --dry-run to apply.")
        sys.exit(0)

    # Step 5: PUT updated schema
    print("[5/5] PUTting updated schema (force=true)...")
    payload = {"extraction_schema": schema}
    resp = client.put(
        f"{API_BASE}/projects/{PROJECT_ID}",
        params={"force": "true"},
        json=payload,
    )
    if resp.status_code != 200:
        print(f"FATAL: PUT failed: {resp.status_code}")
        print(resp.text)
        sys.exit(1)

    result = resp.json()
    result_groups = result.get("extraction_schema", {}).get("field_groups", [])
    result_names = [g["name"] for g in result_groups]
    print(f"  Success! Field groups ({len(result_groups)}): {result_names}")

    # Verify
    if "company_locations" in result_names:
        print("\n✓ VERIFIED: company_locations is now in the live schema.")
    else:
        print("\n✗ VERIFICATION FAILED: company_locations not found in response!")
        sys.exit(1)


if __name__ == "__main__":
    main()
