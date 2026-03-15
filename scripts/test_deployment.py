#!/usr/bin/env python3
"""Test deployment with scrapethissite.com crawl."""

import os
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

# Load .env file if it exists
env_file = Path(__file__).parent.parent / ".env"
if env_file.exists():
    with open(env_file) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                key, value = line.split("=", 1)
                os.environ.setdefault(key.strip(), value.strip())

# Configuration
API_BASE_URL = os.getenv("API_BASE_URL", "http://192.168.0.136:8742")
API_KEY = os.getenv("API_KEY", "thisismyapikey3215215632")

# Test parameters
TEST_URL = "https://www.scrapethissite.com/pages/"
TEST_COMPANY = "ScrapeThisSite"
MAX_DEPTH = 5
PAGE_LIMIT = 50


def test_health():
    """Test if the API is reachable."""
    print("🏥 Testing API health...")
    try:
        resp = requests.get(f"{API_BASE_URL}/health", timeout=5)
        if resp.status_code == 200:
            print(f"   ✅ API is healthy: {resp.json()}")
            return True
        else:
            print(f"   ⚠️  API returned {resp.status_code}")
            return False
    except Exception as e:
        print(f"   ❌ Cannot reach API: {e}")
        return False


def create_project():
    """Create a test project using company_analysis template."""
    print("\n📦 Creating test project from template...")
    headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}

    project_name = f"ScrapeThisSite Test - {datetime.now().strftime('%Y%m%d-%H%M%S')}"

    project_data = {
        "template": "company_analysis",
        "name": project_name,
        "description": "Test crawl of scrapethissite.com to validate deployment",
    }

    try:
        resp = requests.post(
            f"{API_BASE_URL}/api/v1/projects/from-template",
            json=project_data,
            headers=headers,
            timeout=10,
        )

        if resp.status_code == 201:
            project = resp.json()
            project_id = project["id"]
            print(f"   ✅ Project created: {project['name']}")
            print(f"   📍 Project ID: {project_id}")
            return project_id
        else:
            print(f"   ❌ Failed to create project: {resp.status_code}")
            print(f"   Response: {resp.text}")
            return None

    except Exception as e:
        print(f"   ❌ Error creating project: {e}")
        return None


def create_crawl_job(project_id: str):
    """Create a crawl job."""
    print("\n🕷️  Creating crawl job...")
    headers = {"X-API-Key": API_KEY, "Content-Type": "application/json"}

    crawl_data = {
        "url": TEST_URL,
        "project_id": project_id,
        "company": TEST_COMPANY,
        "max_depth": MAX_DEPTH,
        "limit": PAGE_LIMIT,
        "auto_extract": True,  # Auto-extract after crawling
        "profile": None,
    }

    print(f"   URL: {TEST_URL}")
    print(f"   Max Depth: {MAX_DEPTH}")
    print(f"   Page Limit: {PAGE_LIMIT}")

    try:
        resp = requests.post(
            f"{API_BASE_URL}/api/v1/crawl", json=crawl_data, headers=headers, timeout=10
        )

        if resp.status_code == 202:
            job = resp.json()
            job_id = job["job_id"]
            print("   ✅ Crawl job created!")
            print(f"   📍 Job ID: {job_id}")
            print(f"   Status: {job['status']}")
            return job_id
        else:
            print(f"   ❌ Failed to create crawl: {resp.status_code}")
            print(f"   Response: {resp.text}")
            return None

    except Exception as e:
        print(f"   ❌ Error creating crawl: {e}")
        return None


def monitor_crawl(job_id: str, max_checks: int = 20, interval: int = 30):
    """Monitor crawl progress."""
    print("\n👀 Monitoring crawl progress...")
    print(f"   (Checking every {interval}s, max {max_checks} checks)")
    headers = {"X-API-Key": API_KEY}

    for i in range(max_checks):
        try:
            resp = requests.get(
                f"{API_BASE_URL}/api/v1/crawl/{job_id}", headers=headers, timeout=10
            )

            if resp.status_code == 200:
                status = resp.json()

                # Print progress
                pages_completed = status.get("pages_completed", 0)
                pages_total = status.get("pages_total", "?")
                job_status = status.get("status", "unknown")

                print(f"\n   [{i + 1}/{max_checks}] Status: {job_status}")
                print(f"   Pages: {pages_completed}/{pages_total}")

                if status.get("sources_created"):
                    print(f"   Sources created: {status['sources_created']}")

                # Check if completed
                if job_status in ("completed", "failed"):
                    if job_status == "completed":
                        print("\n   ✅ Crawl completed successfully!")
                        print("   📊 Final stats:")
                        print(f"      - Pages crawled: {pages_completed}")
                        print(
                            f"      - Sources created: {status.get('sources_created', 'N/A')}"
                        )
                    else:
                        print("\n   ❌ Crawl failed!")
                        if status.get("error"):
                            print(f"   Error: {status['error']}")
                    return status

                # Wait before next check
                if i < max_checks - 1:
                    time.sleep(interval)
            else:
                print(f"   ⚠️  Failed to get status: {resp.status_code}")

        except Exception as e:
            print(f"   ⚠️  Error checking status: {e}")

    print(f"\n   ⏱️  Monitoring stopped after {max_checks} checks")
    print("   Job is still running - check manually at:")
    print(f"   {API_BASE_URL}/api/v1/crawl/{job_id}")
    return None


def main():
    """Main execution."""
    print("=" * 60)
    print("🚀 DEPLOYMENT TEST - ScrapethisSite.com")
    print("=" * 60)
    print(f"API Base URL: {API_BASE_URL}")
    print(f"Test URL: {TEST_URL}")
    print(f"Depth: {MAX_DEPTH}, Pages: {PAGE_LIMIT}")
    print("=" * 60)

    # Step 1: Health check
    if not test_health():
        print("\n❌ API is not reachable. Please check:")
        print("   1. Is the deployment running in Portainer?")
        print("   2. Is the API_BASE_URL correct?")
        print("   3. Are the ports accessible?")
        sys.exit(1)

    # Step 2: Create project
    project_id = create_project()
    if not project_id:
        print("\n❌ Failed to create project. Check API_KEY and logs.")
        sys.exit(1)

    # Step 3: Create crawl job
    job_id = create_crawl_job(project_id)
    if not job_id:
        print("\n❌ Failed to create crawl job. Check logs.")
        sys.exit(1)

    # Step 4: Monitor progress
    print("\n" + "=" * 60)
    result = monitor_crawl(job_id, max_checks=20, interval=30)

    # Summary
    print("\n" + "=" * 60)
    print("📋 TEST SUMMARY")
    print("=" * 60)
    print(f"Project ID: {project_id}")
    print(f"Job ID: {job_id}")
    print("\n🌐 View in API:")
    print(f"   Docs: {API_BASE_URL}/docs")
    print(f"   Project: {API_BASE_URL}/api/v1/projects/{project_id}")
    print(f"   Job Status: {API_BASE_URL}/api/v1/crawl/{job_id}")

    if result and result.get("status") == "completed":
        print("\n✅ DEPLOYMENT TEST PASSED!")
    else:
        print("\n⚠️  Test incomplete - check logs for details")

    print("=" * 60)


if __name__ == "__main__":
    main()
