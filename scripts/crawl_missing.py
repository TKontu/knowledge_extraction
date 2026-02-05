#!/usr/bin/env python3
"""
Crawl missing companies from gap analysis.

Usage:
    python scripts/crawl_missing.py [--dry-run] [--delay SECONDS]
"""

import argparse
import json
import re
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

# Configuration
API_BASE = "http://192.168.0.136:8742"
API_KEY = "thisismyapikey3215215632"
PROJECT_ID = "99a19141-9268-40a8-bc9e-ad1fa12243da"

# Crawl settings
MAX_DEPTH = 5
LIMIT = 100
SMART_CRAWL_ENABLED = False


def extract_company_name(url: str) -> str:
    """Generate a company name from URL."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()
        domain = re.sub(r"^www\.", "", domain)
        parts = domain.split(".")
        name = parts[0] if parts else domain
        # Title case and clean up
        name = name.replace("-", " ").replace("_", " ").title()
        return name
    except Exception:
        return "Unknown"


def crawl_url(url: str, company: str, dry_run: bool = False) -> dict | None:
    """Submit a crawl job for a URL."""
    payload = {
        "url": url,
        "project_id": PROJECT_ID,
        "company": company,
        "max_depth": MAX_DEPTH,
        "limit": LIMIT,
        "smart_crawl_enabled": SMART_CRAWL_ENABLED,
    }

    if dry_run:
        print(f"  [DRY RUN] Would crawl: {url} as '{company}'")
        return {"status": "dry_run", "url": url}

    try:
        response = requests.post(
            f"{API_BASE}/api/v1/crawl",
            headers={
                "X-API-Key": API_KEY,
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
        response.raise_for_status()
        result = response.json()
        print(f"  ✓ Queued: {url} -> job_id: {result.get('job_id', 'unknown')[:8]}...")
        return result
    except requests.RequestException as e:
        print(f"  ✗ Failed: {url} -> {e}")
        return None


def load_missing_urls(filepath: str) -> list[tuple[str, str]]:
    """Load missing URLs from file."""
    urls = []
    with open(filepath) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and line.startswith("http"):
                company = extract_company_name(line)
                urls.append((line, company))
    return urls


def main():
    parser = argparse.ArgumentParser(description="Crawl missing companies")
    parser.add_argument("--dry-run", action="store_true", help="Show what would be crawled without doing it")
    parser.add_argument("--delay", type=float, default=0.5, help="Delay between requests in seconds")
    parser.add_argument("--input", default="output/missing_companies.txt", help="Input file with URLs")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        print(f"Error: Input file not found: {input_path}")
        return 1

    urls = load_missing_urls(input_path)
    print(f"Loaded {len(urls)} URLs from {input_path}")
    print(f"Settings: max_depth={MAX_DEPTH}, limit={LIMIT}, smart_crawl={SMART_CRAWL_ENABLED}")
    print()

    if args.dry_run:
        print("[DRY RUN MODE - No actual crawls will be started]")
        print()

    success = 0
    failed = 0

    for i, (url, company) in enumerate(urls, 1):
        print(f"[{i}/{len(urls)}] {company}")
        result = crawl_url(url, company, dry_run=args.dry_run)
        if result:
            success += 1
        else:
            failed += 1

        if not args.dry_run and i < len(urls):
            time.sleep(args.delay)

    print()
    print(f"Complete: {success} queued, {failed} failed")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    exit(main())
