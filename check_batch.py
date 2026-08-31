"""
check_batch.py

Checks a single batch of listings (produced by split_batches.py) and
writes the results to its own results CSV. Designed to be run as one leg
of a GitHub Actions matrix job, so N batches run in parallel.

Takes the batch number as a command-line argument:

    python check_batch.py 0
    python check_batch.py 1
    ... etc

Reads:   data/batches/batch_{N}.csv        (listing_id, url pairs to check)
Writes:  data/batches/results_{N}.csv      (full updated row data for each)
"""

import csv
import sys
from datetime import datetime, timezone
from pathlib import Path

from sold_checker import LISTINGS_COLUMNS, check_listing_page, USER_AGENT
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
BATCHES_DIR = DATA_DIR / "batches"
LISTINGS_PATH = DATA_DIR / "listings.csv"

RESULT_COLUMNS = ["listing_id", "status", "sold_price", "sold_confirmed_at",
                  "consecutive_misses", "date_disappeared", "last_seen"]


def main():
    if len(sys.argv) < 2:
        print("Usage: python check_batch.py <batch_number>")
        sys.exit(1)

    batch_num = sys.argv[1]
    batch_path = BATCHES_DIR / f"batch_{batch_num}.csv"
    results_path = BATCHES_DIR / f"results_{batch_num}.csv"

    with open(batch_path, "r", newline="", encoding="utf-8") as f:
        batch = list(csv.DictReader(f))

    print(f"Batch {batch_num}: {len(batch)} listings to check.")

    results = []

    if batch:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(user_agent=USER_AGENT)
            page = context.new_page()

            for row in batch:
                url = row["url"]
                listing_id = row["listing_id"]
                print(f"  [{batch_num}] Checking {listing_id} -> {url}")

                outcome, sold_price = check_listing_page(page, url)
                now_iso = datetime.now(timezone.utc).isoformat()

                if outcome == "sold":
                    results.append({
                        "listing_id": listing_id,
                        "status": "confirmed_sold",
                        "sold_price": sold_price or "",
                        "sold_confirmed_at": now_iso,
                        "consecutive_misses": "",
                        "date_disappeared": "",
                        "last_seen": "",
                    })
                    print(f"    -> SOLD (price: {sold_price})")

                elif outcome == "active":
                    results.append({
                        "listing_id": listing_id,
                        "status": "active",
                        "sold_price": "",
                        "sold_confirmed_at": "",
                        "consecutive_misses": "0",
                        "date_disappeared": "",
                        "last_seen": now_iso,
                    })
                    print(f"    -> still ACTIVE (was buried, not gone)")

                else:  # gone
                    results.append({
                        "listing_id": listing_id,
                        "status": "deleted",
                        "sold_price": "",
                        "sold_confirmed_at": "",
                        "consecutive_misses": "",
                        "date_disappeared": now_iso,
                        "last_seen": "",
                    })
                    print(f"    -> DELETED (404/error, unconfirmed)")

                import random
                import time
                time.sleep(random.uniform(2, 5))

            browser.close()

    with open(results_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RESULT_COLUMNS)
        writer.writeheader()
        for row in results:
            writer.writerow(row)

    print(f"Batch {batch_num} done. Wrote {len(results)} results to {results_path}")


if __name__ == "__main__":
    main()
