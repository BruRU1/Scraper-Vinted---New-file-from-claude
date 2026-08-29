"""
scraper.py

Automated Vinted listing collector. Reuses the exact extraction logic from
the original Chrome extension (selectors.js + content.js), ported into
extract.js and run in-page via Playwright's page.evaluate().

Reads its target list from categories.json, visits N pages per category,
and reconciles results into two persistent files:

  data/listings.csv       - one row per unique listing (current state),
                             keyed by a stable internal listing_id. Includes
                             at-a-glance price-drop summary columns
                             (original_price, price_drops, last_price_change)
                             so you can see the drop picture without opening
                             the second file.

  data/price_history.csv  - the detailed log: one row per price change ever
                             recorded, for deeper analysis later (timing of
                             drops, sell-through modelling, etc).

Listings are matched across runs by URL. New URLs get the next sequential
listing_id. Existing listings have their current data refreshed; if the
price changed, listings.csv's summary columns are updated AND a row is
appended to price_history.csv.

A listing that isn't seen for 2 consecutive runs is marked
"likely_sold_or_removed" (never "sold" - we can't know that for certain).
If it reappears later (Vinted allows relisting), it's reactivated under
the SAME listing_id rather than getting a new one.

Run manually:      python scraper.py
Run automatically:  triggered on a schedule by .github/workflows/scrape.yml
"""

import json
import csv
import random
import time
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent
CONFIG_PATH = ROOT / "categories.json"
EXTRACT_JS_PATH = ROOT / "extract.js"
DATA_DIR = ROOT / "data"
LISTINGS_PATH = DATA_DIR / "listings.csv"
HISTORY_PATH = DATA_DIR / "price_history.csv"

# Number of consecutive runs a previously-active listing must be absent
# from before we flag it as likely sold/removed. Keeps a single missed
# appearance (e.g. pagination shuffled by new listings) from being a
# false positive.
MISSING_RUNS_THRESHOLD = 2

LISTINGS_COLUMNS = [
    "listing_id",
    "url",
    "platform",
    "category",
    "title",
    "current_price",
    "original_price",       # price the listing was first scraped at
    "price_drops",           # count of recorded price changes
    "last_price_change",     # timestamp of most recent change, else ""
    "currency",
    "brand",
    "size",
    "condition",
    "imageUrl",
    "first_seen",
    "last_seen",
    "status",              # "active" | "likely_sold_or_removed"
    "date_disappeared",    # set when status flips to likely_sold_or_removed; cleared if it reappears
    "consecutive_misses",
]

HISTORY_COLUMNS = [
    "listing_id",
    "old_price",
    "new_price",
    "changed_at",
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
    if page_number <= 1:
        return f"{base_url}{path}"
    separator = "&" if "?" in path else "?"
    return f"{base_url}{path}{separator}page={page_number}"


def ensure_data_files():
    DATA_DIR.mkdir(exist_ok=True)
    if not LISTINGS_PATH.exists():
        with open(LISTINGS_PATH, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=LISTINGS_COLUMNS).writeheader()
    if not HISTORY_PATH.exists():
        with open(HISTORY_PATH, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=HISTORY_COLUMNS).writeheader()


def load_listings():
    """Returns (listings_by_url dict, next_id int)."""
    listings_by_url = {}
    max_id = 0
    with open(LISTINGS_PATH, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        for row in reader:
            row["listing_id"] = int(row["listing_id"])
            row["consecutive_misses"] = int(row["consecutive_misses"] or 0)
            row["price_drops"] = int(row["price_drops"] or 0)
            listings_by_url[row["url"]] = row
            max_id = max(max_id, row["listing_id"])
    return listings_by_url, max_id + 1


def save_listings(listings_by_url):
    rows = sorted(listings_by_url.values(), key=lambda r: r["listing_id"])
    with open(LISTINGS_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LISTINGS_COLUMNS)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def append_history(rows):
    if not rows:
        return
    with open(HISTORY_PATH, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=HISTORY_COLUMNS)
        for row in rows:
            writer.writerow(row)


def parse_price(value):
    """Best-effort numeric parse so price comparisons aren't string-based
    (avoids false "changes" from formatting differences like '25' vs '25.0')."""
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def reconcile(listings_by_url, next_id, scraped_listing, category_name, now_iso):
    """
    Update listings_by_url in place for a single freshly-scraped item.
    Returns (next_id, history_row_or_None, url).
    """
    url = scraped_listing.get("url", "")
    if not url:
        return next_id, None, None

    price = scraped_listing.get("price", "")
    existing = listings_by_url.get(url)
    history_row = None

    if existing is None:
        # Brand new listing.
        listing_id = next_id
        next_id += 1
        listings_by_url[url] = {
            "listing_id": listing_id,
            "url": url,
            "platform": "Vinted",
            "category": category_name,
            "title": scraped_listing.get("title", ""),
            "current_price": price,
            "original_price": price,
            "price_drops": 0,
            "last_price_change": "",
            "currency": scraped_listing.get("currency", ""),
            "brand": scraped_listing.get("brand", ""),
            "size": scraped_listing.get("size", ""),
            "condition": scraped_listing.get("condition", ""),
            "imageUrl": scraped_listing.get("imageUrl", ""),
            "first_seen": now_iso,
            "last_seen": now_iso,
            "status": "active",
            "date_disappeared": "",
            "consecutive_misses": 0,
        }
    else:
        # Known listing (whether it was active or previously flagged as
        # likely sold/removed - if we're seeing it again, it's back).
        old_price_num = parse_price(existing.get("current_price"))
        new_price_num = parse_price(price)

        if (
            old_price_num is not None
            and new_price_num is not None
            and old_price_num != new_price_num
        ):
            history_row = {
                "listing_id": existing["listing_id"],
                "old_price": existing["current_price"],
                "new_price": price,
                "changed_at": now_iso,
            }
            existing["current_price"] = price
            existing["price_drops"] = existing.get("price_drops", 0) + 1
            existing["last_price_change"] = now_iso

        # Refresh current data in case title/brand/condition/etc were
        # edited by the seller, or were blank before and are now filled.
        existing["title"] = scraped_listing.get("title", "") or existing["title"]
        existing["brand"] = scraped_listing.get("brand", "") or existing["brand"]
        existing["size"] = scraped_listing.get("size", "") or existing["size"]
        existing["condition"] = scraped_listing.get("condition", "") or existing["condition"]
        existing["imageUrl"] = scraped_listing.get("imageUrl", "") or existing["imageUrl"]
        existing["category"] = category_name or existing["category"]
        existing["last_seen"] = now_iso
        existing["status"] = "active"
        existing["date_disappeared"] = ""
        existing["consecutive_misses"] = 0

    return next_id, history_row, url


def mark_missing_listings(listings_by_url, seen_urls_this_run, now_iso):
    """Any listing still marked active that wasn't seen this run gets its
    miss counter bumped; past the threshold it's flagged as likely gone."""
    for url, row in listings_by_url.items():
        if url in seen_urls_this_run:
            continue
        if row["status"] != "active":
            continue

        row["consecutive_misses"] += 1
        if row["consecutive_misses"] >= MISSING_RUNS_THRESHOLD:
            row["status"] = "likely_sold_or_removed"
            row["date_disappeared"] = now_iso


def scrape():
    config = load_config()
    extract_script = load_extract_script()
    base_url = config["base_url"]
    pages_per_category = config.get("pages_per_category", 10)
    categories = config["categories"]

    ensure_data_files()
    listings_by_url, next_id = load_listings()

    seen_urls_this_run = set()
    history_rows = []
    total_new = 0
    total_updated = 0
    total_price_changes = 0
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
                    page.wait_for_timeout(2000)

                    result = page.evaluate(extract_script)

                    if not result.get("success"):
                        print(f"  ! {result.get('error')}")
                        total_errors += 1
                        continue

                    listings = result.get("listings", [])
                    now = datetime.now(timezone.utc).isoformat()

                    for listing in listings:
                        was_new = listing.get("url", "") not in listings_by_url
                        next_id, history_row, seen_url = reconcile(
                            listings_by_url, next_id, listing, name, now
                        )
                        if seen_url:
                            seen_urls_this_run.add(seen_url)
                            if was_new:
                                total_new += 1
                            else:
                                total_updated += 1
                        if history_row:
                            history_rows.append(history_row)
                            total_price_changes += 1

                    print(f"  \u2713 {len(listings)} listings processed")

                except Exception as e:
                    print(f"  ! error on {url}: {e}")
                    total_errors += 1

                time.sleep(random.uniform(2, 6))

        browser.close()

    run_time = datetime.now(timezone.utc).isoformat()
    mark_missing_listings(listings_by_url, seen_urls_this_run, run_time)

    save_listings(listings_by_url)
    append_history(history_rows)

    print(
        f"\nDone. {total_new} new listings, {total_updated} existing listings "
        f"refreshed, {total_price_changes} price changes recorded, "
        f"{total_errors} errors."
    )


if __name__ == "__main__":
    scrape()
