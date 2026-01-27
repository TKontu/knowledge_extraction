#!/usr/bin/env python3
"""Batch crawl all companies from input/companies.txt"""

import os
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

# Configuration
API_BASE_URL = os.getenv("API_BASE_URL", "http://192.168.0.136:8742")
API_KEY = os.getenv("API_KEY", "thisismyapikey3215215632")
INPUT_FILE = Path(__file__).parent.parent / "input" / "companies.txt"


def extract_company_name(url: str) -> str:
    """Extract company name from URL."""
    # Clean URL
    url = url.strip()
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # Parse domain
    try:
        domain = urlparse(url).netloc
        # Remove www. and .com/.co.uk etc
        name = domain.replace("www.", "").split(".")[0]
        return name.title()
    except:
        return url.split("/")[0][:50]


def main():
    """Main execution."""
    print(f"🚀 Starting batch crawl from {INPUT_FILE}")

    # Read companies
    if not INPUT_FILE.exists():
        print(f"❌ File not found: {INPUT_FILE}")
        sys.exit(1)

    with open(INPUT_FILE) as f:
        urls = [line.strip() for line in f if line.strip() and not line.startswith("#")]

    print(f"📋 Found {len(urls)} companies")

    # Create project
    headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}

    project_data = {
        "name": "Industrial Drivetrain Companies - Full Batch",
        "description": f"Complete crawl of {len(urls)} industrial drivetrain component manufacturers and suppliers",
        "source_config": {"type": "web", "group_by": "company"},
        "extraction_schema": {
            "categories": [
                "manufacturing",
                "services",
                "company_info",
                "products",
                "specifications",
            ],
            "entity_types": [
                "company",
                "site_location",
                "product",
                "service",
                "specification",
                "certification",
            ],
        },
    }

    print("📦 Creating project...")
    resp = requests.post(
        f"{API_BASE_URL}/api/v1/projects", json=project_data, headers=headers
    )

    if resp.status_code == 409:
        # Project exists, find it
        print("   ℹ️  Project already exists, fetching...")
        resp = requests.get(f"{API_BASE_URL}/api/v1/projects", headers=headers)
        projects = resp.json()
        project = next((p for p in projects if p["name"] == project_data["name"]), None)
        if not project:
            print("❌ Could not find existing project")
            sys.exit(1)
        project_id = project["id"]
        print(f"✅ Using existing project: {project['name']} ({project_id})")
    elif resp.status_code not in (200, 201):
        print(f"❌ Failed to create project: {resp.status_code}")
        print(resp.text)
        sys.exit(1)
    else:
        project = resp.json()
        project_id = project["id"]
        print(f"✅ Project created: {project['name']} ({project_id})")

    # Create crawl jobs for each company
    print("\n🕷️  Creating crawl jobs...")
    successful = 0
    failed = []

    for i, url in enumerate(urls, 1):
        company_name = extract_company_name(url)

        # Normalize URL
        if not url.startswith(("http://", "https://")):
            url = "https://" + url

        crawl_data = {
            "url": url,
            "project_id": project_id,
            "company": company_name,
            "max_depth": 3,
            "limit": 50,
            "auto_extract": False,  # We'll extract after crawling
            "profile": "company_info",
        }

        try:
            resp = requests.post(
                f"{API_BASE_URL}/api/v1/crawl",
                json=crawl_data,
                headers=headers,
                timeout=10,
            )

            if resp.status_code in (200, 202):
                successful += 1
                if i % 10 == 0:
                    print(f"   ✅ {i}/{len(urls)} jobs created")
            else:
                failed.append((url, company_name, resp.status_code))
                if len(failed) <= 5:  # Only show first 5 failures in real-time
                    print(f"   ⚠️  Failed: {company_name} ({resp.status_code})")

        except Exception as e:
            failed.append((url, company_name, str(e)))
            if len(failed) <= 5:
                print(f"   ⚠️  Error: {company_name} - {e}")

        # Small delay to avoid overwhelming the API
        if i % 10 == 0:
            time.sleep(0.5)

    print("\n📊 Summary:")
    print(f"   Total companies: {len(urls)}")
    print(f"   Crawl jobs created: {successful}")
    print(f"   Failed: {len(failed)}")

    if failed:
        print("\n⚠️  Failed companies:")
        for url, company, error in failed[:10]:
            print(f"   - {company}: {error}")
        if len(failed) > 10:
            print(f"   ... and {len(failed) - 10} more")

    print("\n✅ Crawl jobs queued successfully!")
    print("\n📍 Monitor progress:")
    print(f"   API: {API_BASE_URL}/docs")
    print(f"   Project ID: {project_id}")
    print("\n⏱️  Estimated completion: 10-26 hours")
    print("   (Depends on site size and complexity)")


if __name__ == "__main__":
    main()
