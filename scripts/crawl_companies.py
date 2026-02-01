#!/usr/bin/env python3
"""Batch crawl companies from input file with smart crawl support.

Usage:
    python scripts/crawl_companies.py [options]

Environment variables:
    API_BASE_URL    - API endpoint (default: http://192.168.0.136:8742)
    API_KEY         - API authentication key

Options:
    --input FILE        Input file path (default: input/companies.txt)
    --project NAME      Project name (default: auto-generated)
    --template NAME     Template to use (default: drivetrain_company_analysis)
    --max-depth N       Crawl depth 1-10 (default: 5)
    --limit N           Max pages per company 1-1000 (default: 100)
    --no-smart-crawl    Disable smart crawl (use traditional crawl)
    --relevance FLOAT   Relevance threshold 0.0-1.0 (default: 0.4)
    --auto-extract      Enable auto-extraction after crawl
    --resume            Resume from last successful crawl
    --dry-run           Validate URLs without submitting jobs
    --concurrency N     Max concurrent job submissions (default: 5)
    --english-only      Filter non-English content (off by default - LLM translates)
    --focus-terms       Optional terms for URL prioritization in smart crawl
"""

import argparse
import json
import os
import re
import sys
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

import requests

# Configuration defaults
DEFAULT_API_URL = os.getenv("API_BASE_URL", "http://192.168.0.136:8742")
DEFAULT_API_KEY = os.getenv("API_KEY", "thisismyapikey3215215632")
DEFAULT_INPUT = Path(__file__).parent.parent / "input" / "companies.txt"
DEFAULT_TEMPLATE = "drivetrain_company_analysis"
STATE_FILE = Path(__file__).parent.parent / "output" / "crawl_batch_state.json"


@dataclass
class CrawlConfig:
    """Configuration for batch crawl."""

    api_url: str
    api_key: str
    input_file: Path
    project_name: str | None
    template: str
    max_depth: int
    limit: int
    smart_crawl: bool
    relevance_threshold: float
    auto_extract: bool
    resume: bool
    dry_run: bool
    concurrency: int
    focus_terms: list[str] | None
    english_only: bool


def parse_args() -> CrawlConfig:
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(
        description="Batch crawl companies with smart crawl support"
    )
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_INPUT,
        help=f"Input file path (default: {DEFAULT_INPUT})",
    )
    parser.add_argument(
        "--project",
        type=str,
        default=None,
        help="Project name (default: auto-generated with timestamp)",
    )
    parser.add_argument(
        "--template",
        type=str,
        default=DEFAULT_TEMPLATE,
        help=f"Template to use (default: {DEFAULT_TEMPLATE})",
    )
    parser.add_argument(
        "--max-depth",
        type=int,
        default=5,
        choices=range(1, 11),
        metavar="N",
        help="Crawl depth 1-10 (default: 5)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Max pages per company 1-1000 (default: 100)",
    )
    parser.add_argument(
        "--no-smart-crawl",
        action="store_true",
        help="Disable smart crawl (use traditional crawl)",
    )
    parser.add_argument(
        "--relevance",
        type=float,
        default=0.4,
        help="Relevance threshold 0.0-1.0 (default: 0.4)",
    )
    parser.add_argument(
        "--auto-extract",
        action="store_true",
        help="Enable auto-extraction after crawl",
    )
    parser.add_argument(
        "--resume",
        action="store_true",
        help="Resume from last successful crawl",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate URLs without submitting jobs",
    )
    parser.add_argument(
        "--concurrency",
        type=int,
        default=5,
        help="Max concurrent job submissions (default: 5)",
    )
    parser.add_argument(
        "--focus-terms",
        type=str,
        nargs="+",
        default=None,
        help="Focus terms for smart crawl (optional - used for URL prioritization, not strict filtering)",
    )
    parser.add_argument(
        "--english-only",
        action="store_true",
        help="Filter non-English content (WARNING: excludes Spanish, Portuguese, German pages)",
    )

    args = parser.parse_args()

    # Validate limit
    if not 1 <= args.limit <= 1000:
        parser.error("--limit must be between 1 and 1000")

    # Validate relevance
    if not 0.0 <= args.relevance <= 1.0:
        parser.error("--relevance must be between 0.0 and 1.0")

    return CrawlConfig(
        api_url=DEFAULT_API_URL,
        api_key=DEFAULT_API_KEY,
        input_file=args.input,
        project_name=args.project,
        template=args.template,
        max_depth=args.max_depth,
        limit=args.limit,
        smart_crawl=not args.no_smart_crawl,
        relevance_threshold=args.relevance,
        auto_extract=args.auto_extract,
        resume=args.resume,
        dry_run=args.dry_run,
        concurrency=args.concurrency,
        focus_terms=args.focus_terms,
        english_only=args.english_only,
    )


def normalize_url(url: str) -> str | None:
    """Normalize and validate URL. Returns None if invalid."""
    url = url.strip()
    if not url:
        return None

    # Skip comments
    if url.startswith("#"):
        return None

    # Fix common protocol typos
    url = re.sub(r"^nttps://", "https://", url)
    url = re.sub(r"^htps://", "https://", url)
    url = re.sub(r"^https:/([^/])", r"https://\1", url)  # https:/domain -> https://domain

    # Add protocol if missing
    if not url.startswith(("http://", "https://")):
        url = "https://" + url

    # Remove trailing punctuation that shouldn't be there
    url = re.sub(r"[,;]+$", "", url)

    # Parse and validate
    try:
        parsed = urlparse(url)
        if not parsed.netloc:
            return None
        # Basic domain validation
        if "." not in parsed.netloc:
            return None
        return url
    except Exception:
        return None


def extract_company_name(url: str) -> str:
    """Extract company name from URL domain."""
    try:
        parsed = urlparse(url)
        domain = parsed.netloc.lower()

        # Remove www. and common TLDs
        domain = re.sub(r"^www\.", "", domain)

        # Get the main domain part (before first dot)
        name = domain.split(".")[0]

        # Clean up and title case
        name = re.sub(r"[-_]", " ", name)
        return name.title()
    except Exception:
        return url[:50]


def load_urls(input_file: Path) -> list[tuple[str, str]]:
    """Load and validate URLs from input file. Returns list of (url, company_name)."""
    if not input_file.exists():
        print(f"Error: Input file not found: {input_file}")
        sys.exit(1)

    urls = []
    invalid = []

    with open(input_file) as f:
        for line_num, line in enumerate(f, 1):
            line = line.strip()

            # Skip empty lines and comments
            if not line or line.startswith("#"):
                continue

            normalized = normalize_url(line)
            if normalized:
                company = extract_company_name(normalized)
                urls.append((normalized, company))
            else:
                invalid.append((line_num, line))

    if invalid:
        print(f"Warning: {len(invalid)} invalid URLs skipped:")
        for line_num, line in invalid[:5]:
            print(f"  Line {line_num}: {line[:60]}...")
        if len(invalid) > 5:
            print(f"  ... and {len(invalid) - 5} more")
        print()

    return urls


def load_state() -> dict:
    """Load batch state from file."""
    if STATE_FILE.exists():
        with open(STATE_FILE) as f:
            return json.load(f)
    return {"completed": [], "failed": [], "project_id": None}


def save_state(state: dict) -> None:
    """Save batch state to file."""
    STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(STATE_FILE, "w") as f:
        json.dump(state, f, indent=2)


def get_or_create_project(
    config: CrawlConfig, headers: dict, url_count: int
) -> str | None:
    """Get existing or create new project. Returns project_id."""
    # Generate project name if not specified
    if config.project_name:
        project_name = config.project_name
    else:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M")
        project_name = f"Drivetrain Companies Batch {timestamp}"

    # Try to find existing project
    resp = requests.get(f"{config.api_url}/api/v1/projects", headers=headers)
    if resp.status_code == 200:
        projects = resp.json()
        existing = next((p for p in projects if p["name"] == project_name), None)
        if existing:
            print(f"Using existing project: {project_name}")
            return existing["id"]

    # Create new project from template
    print(f"Creating project from template '{config.template}'...")

    create_data = {
        "template": config.template,
        "name": project_name,
        "description": f"Batch crawl of {url_count} industrial drivetrain companies",
    }

    resp = requests.post(
        f"{config.api_url}/api/v1/projects/from-template",
        json=create_data,
        headers=headers,
    )

    if resp.status_code in (200, 201):
        project = resp.json()
        print(f"Created project: {project_name} ({project['id']})")
        return project["id"]

    # Fallback: create without template
    print(f"Template creation failed ({resp.status_code}), creating basic project...")

    create_data = {
        "name": project_name,
        "description": f"Batch crawl of {url_count} industrial drivetrain companies",
    }

    resp = requests.post(
        f"{config.api_url}/api/v1/projects",
        json=create_data,
        headers=headers,
    )

    if resp.status_code in (200, 201):
        project = resp.json()
        print(f"Created project: {project_name} ({project['id']})")
        return project["id"]

    print(f"Failed to create project: {resp.status_code}")
    print(resp.text[:500])
    return None


def submit_crawl_job(
    config: CrawlConfig,
    headers: dict,
    project_id: str,
    url: str,
    company: str,
) -> tuple[bool, str | None, str | None]:
    """Submit a single crawl job. Returns (success, job_id, error)."""
    crawl_data = {
        "url": url,
        "project_id": project_id,
        "company": company,
        "max_depth": config.max_depth,
        "limit": config.limit,
        "auto_extract": config.auto_extract,
        "prefer_english_only": config.english_only,  # Default False - LLM can translate
        "allow_subdomains": False,
        "ignore_query_parameters": True,
        "smart_crawl_enabled": config.smart_crawl,
    }

    # Add smart crawl parameters (only if provided)
    if config.smart_crawl:
        crawl_data["relevance_threshold"] = config.relevance_threshold
        if config.focus_terms:
            crawl_data["focus_terms"] = config.focus_terms

    try:
        resp = requests.post(
            f"{config.api_url}/api/v1/crawl",
            json=crawl_data,
            headers=headers,
            timeout=30,
        )

        if resp.status_code in (200, 202):
            data = resp.json()
            return True, data.get("job_id"), None

        return False, None, f"HTTP {resp.status_code}: {resp.text[:200]}"

    except requests.Timeout:
        return False, None, "Request timeout"
    except requests.RequestException as e:
        return False, None, str(e)


def main() -> None:
    """Main execution."""
    config = parse_args()

    print("=" * 60)
    print("Industrial Drivetrain Companies - Batch Crawl")
    print("=" * 60)

    # Load URLs
    urls = load_urls(config.input_file)
    print(f"Loaded {len(urls)} valid URLs from {config.input_file}")

    if not urls:
        print("No valid URLs found. Exiting.")
        sys.exit(1)

    # Dry run mode
    if config.dry_run:
        print("\n[DRY RUN] Validated URLs:")
        for url, company in urls[:10]:
            print(f"  {company}: {url}")
        if len(urls) > 10:
            print(f"  ... and {len(urls) - 10} more")
        print("\nNo jobs submitted (dry run mode)")
        return

    # Load state for resume
    state = load_state() if config.resume else {"completed": [], "failed": [], "project_id": None}
    completed_urls = set(state.get("completed", []))

    if config.resume and completed_urls:
        print(f"Resuming: {len(completed_urls)} already completed")

    # Filter out already completed
    pending = [(url, company) for url, company in urls if url not in completed_urls]
    print(f"Pending: {len(pending)} URLs to crawl")

    if not pending:
        print("All URLs already completed!")
        return

    # Setup API
    headers = {"X-API-Key": config.api_key, "Content-Type": "application/json"}

    # Get or create project
    project_id = state.get("project_id")
    if not project_id:
        project_id = get_or_create_project(config, headers, len(urls))
        if not project_id:
            sys.exit(1)
        state["project_id"] = project_id
        save_state(state)

    # Print configuration
    print(f"\nConfiguration:")
    print(f"  Project ID: {project_id}")
    print(f"  Template: {config.template}")
    print(f"  Max depth: {config.max_depth}")
    print(f"  Page limit: {config.limit}")
    print(f"  Smart crawl: {config.smart_crawl}")
    if config.smart_crawl:
        print(f"  Relevance threshold: {config.relevance_threshold}")
        if config.focus_terms:
            print(f"  Focus terms: {', '.join(config.focus_terms)}")
    print(f"  English only: {config.english_only}")
    print(f"  Auto-extract: {config.auto_extract}")
    print()

    # Submit jobs
    print("Submitting crawl jobs...")
    successful = 0
    failed = []
    job_ids = []

    for i, (url, company) in enumerate(pending, 1):
        success, job_id, error = submit_crawl_job(
            config, headers, project_id, url, company
        )

        if success:
            successful += 1
            job_ids.append(job_id)
            state["completed"].append(url)

            if i % 10 == 0 or i == len(pending):
                print(f"  Progress: {i}/{len(pending)} ({successful} ok, {len(failed)} failed)")
                save_state(state)
        else:
            failed.append((url, company, error))
            state["failed"].append({"url": url, "company": company, "error": error})

            if len(failed) <= 3:
                print(f"  Failed: {company} - {error}")

        # Rate limiting between submissions
        if i % config.concurrency == 0:
            time.sleep(0.5)

    # Save final state
    save_state(state)

    # Summary
    print()
    print("=" * 60)
    print("Summary")
    print("=" * 60)
    print(f"  Total URLs: {len(urls)}")
    print(f"  Previously completed: {len(completed_urls)}")
    print(f"  Submitted this run: {successful}")
    print(f"  Failed this run: {len(failed)}")
    print()

    if failed:
        print("Failed URLs:")
        for url, company, error in failed[:10]:
            print(f"  - {company}: {error[:60]}")
        if len(failed) > 10:
            print(f"  ... and {len(failed) - 10} more")
        print()

    print(f"Project ID: {project_id}")
    print(f"State saved to: {STATE_FILE}")
    print()
    print("Monitor progress:")
    print(f"  API docs: {config.api_url}/docs")
    print(f"  Jobs list: GET {config.api_url}/api/v1/jobs?type=crawl&status=running")
    print()

    if config.smart_crawl:
        print("Estimated time: 2-4 hours (smart crawl)")
    else:
        print("Estimated time: 8-16 hours (traditional crawl)")


if __name__ == "__main__":
    main()
