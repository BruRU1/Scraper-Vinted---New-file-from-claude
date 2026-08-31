"""
merge_batches.py

Run AFTER all parallel check_batch.py matrix jobs finish. Reads every
data/batches/results_*.csv file, and applies those updates back onto the
main data/listings.csv, matched by listing_id.

Run manually:  python merge_batches.py
"""

import csv
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
BATCHES_DIR = DATA_DIR / "batches"
LISTINGS_PATH = DATA_DIR / "listings.csv"

LISTINGS_COLUMNS = [
    "listing_id",
    "url",
    "platform",
    "category",
    "title",
    "current_price",
    "original_price",
    "price_drops",
    "last_price_change",
    "currency",
    "brand",
    "size",
    "condition",
    "imageUrl",
    "first_seen",
    "last_seen",
    "status",
    "date_disappeared",
    "consecutive_misses",
    "sold_price",
    "sold_confirmed_at",
]


def main():
    with open(LISTINGS_PATH, "r", newline="", encoding="utf-8") as f:
        listings = list(csv.DictReader(f))

    listings_by_id = {row["listing_id"]: row for row in listings}

    result_files = sorted(BATCHES_DIR.glob("results_*.csv"))
    print(f"Found {len(result_files)} result files to merge.")

    total_applied = 0
    for result_file in result_files:
        with open(result_file, "r", newline="", encoding="utf-8") as f:
            results = list(csv.DictReader(f))

        for result in results:
            listing_id = result["listing_id"]
            target = listings_by_id.get(listing_id)
            if target is None:
                continue

            target["status"] = result["status"]

            if result["status"] == "confirmed_sold":
                target["sold_price"] = result["sold_price"]
                target["sold_confirmed_at"] = result["sold_confirmed_at"]

            elif result["status"] == "active":
                target["consecutive_misses"] = result["consecutive_misses"]
                target["date_disappeared"] = ""
                target["sold_price"] = ""
                target["sold_confirmed_at"] = ""
                if result.get("last_seen"):
                    target["last_seen"] = result["last_seen"]

            elif result["status"] == "deleted":
                target["date_disappeared"] = result["date_disappeared"]

            total_applied += 1

        print(f"  {result_file.name}: {len(results)} results applied")

    with open(LISTINGS_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LISTINGS_COLUMNS)
        writer.writeheader()
        for row in listings_by_id.values():
            writer.writerow(row)

    print(f"\nDone. Applied {total_applied} updates to {LISTINGS_PATH}")


if __name__ == "__main__":
    main()
