"""
scraper.py

Automated Vinted listing collector. Reuses the exact extraction logic from
the original Chrome extension (selectors.js + content.js), ported into
extract.js and run in-page via Playwright's page.evaluate().

Reads its target list from categories.json, visits N pages per category,
and appends structured rows to data/resale_data.csv.

Run manually:      python scraper.py
Run automatically:  triggered on a schedule by .github/workflows/scrape.yml
"""

import json
import csv
import os
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "categories.json"
EXTRACT_JS_PATH = ROOT / "extract.js"
DATA_DIR = ROOT / "data"
CSV_PATH = DATA_DIR / "resale_data.csv"

CSV_COLUMNS = [
    "platform",
    "category",
    "title",
    "price",
    "currency",
    "brand",
    "size",
    "condition",
    "url",
    "imageUrl",
    "pageNumber",
    "dateCollected",
]

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def load_config():
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return json.load(f)


def load_extract_script():
    with open(EXTRACT_JS_PATH, "r", encoding="utf-8") as f:
        return f.read()


def build_url(base_url, path, page_number):
    # Deliberately drop session-specific params (search_id, time) —
    # they belong to a browsing session and aren't needed. Category
    # path + page number is stable and sufficient.
    if page_number <= 1:
        return f"{base_url}{path}"
    return f"{base_url}{path}?page={page_number}"


def ensure_csv_header():
    DATA_DIR.mkdir(exist_ok=True)
    if not CSV_PATH.exists():
        with open(CSV_PATH, "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()


def append_rows(rows):
    if not rows:
        return
    with open(CSV_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
        for row in rows:
            writer.writerow(row)


def scrape():
    config = load_config()
    extract_script = load_extract_script()
    base_url = config["base_url"]
    pages_per_category = config.get("pages_per_category", 10)
    categories = config["categories"]

    ensure_csv_header()

    total_saved = 0
    total_errors = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()

        for category in categories:
            name = category["name"]
            path = category["path"]

            for page_number in range(1, pages_per_category + 1):
                url = build_url(base_url, path, page_number)
                print(f"[{name}] page {page_number} -> {url}")

                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=30000)
                    # Let dynamic content settle — Vinted's grid loads via JS.
                    page.wait_for_timeout(2000)

                    result = page.evaluate(extract_script)

                    if not result.get("success"):
                        print(f"  ! {result.get('error')}")
                        total_errors += 1
                        continue

                    listings = result.get("listings", [])
                    now = datetime.now(timezone.utc).isoformat()

                    rows = []
                    for listing in listings:
                        rows.append(
                            {
                                "platform": "Vinted",
                                "category": name,
                                "title": listing.get("title", ""),
                                "price": listing.get("price", ""),
                                "currency": listing.get("currency", ""),
                                "brand": listing.get("brand", ""),
                                "size": listing.get("size", ""),
                                "condition": listing.get("condition", ""),
                                "url": listing.get("url", ""),
                                "imageUrl": listing.get("imageUrl", ""),
                                "pageNumber": page_number,
                                "dateCollected": now,
                            }
                        )

                    append_rows(rows)
                    total_saved += len(rows)
                    print(f"  ✓ {len(rows)} listings saved")

                except Exception as e:
                    print(f"  ! error on {url}: {e}")
                    total_errors += 1

                # Random delay between requests — avoids hammering Vinted
                # and reduces the chance of getting rate-limited/blocked.
                time.sleep(random.uniform(2, 6))

        browser.close()

    print(f"\nDone. {total_saved} listings saved this run, {total_errors} errors.")


if __name__ == "__main__":
    scrape()
