"""
split_batches.py

Selects listings still marked "likely_sold_or_removed" and splits them
into N batch files for parallel checking.

Always takes the OLDEST unconfirmed listings first (by date_disappeared),
up to TOTAL_CAP_PER_RUN for this run. This treats every likely-gone
listing the same way, whether it was flagged this run or months ago -
there's no separate "backlog" concept, just one queue, oldest first. That
guarantees steady forward progress through the whole backlog every run,
regardless of how much or little was newly flagged, and nothing can
silently fall out of scope waiting for a narrower time window.

Run manually:  python split_batches.py
(In the automated workflow, this runs right after scraper.py, against
whatever scraper.py just committed to data/listings.csv.)
"""

import csv
import math
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
LISTINGS_PATH = DATA_DIR / "listings.csv"
BATCHES_DIR = DATA_DIR / "batches"

NUM_BATCHES = 18

# Total listings to process in one run, across all batches combined.
# Keep this in line with what NUM_BATCHES x MAX_PER_BATCH in
# check_batch.py can comfortably handle within the job timeout.
TOTAL_CAP_PER_RUN = 18000

BATCH_COLUMNS = ["listing_id", "url"]


def main():
    with open(LISTINGS_PATH, "r", newline="", encoding="utf-8") as f:
        listings = list(csv.DictReader(f))

    unconfirmed = [row for row in listings if row.get("status") == "likely_sold_or_removed"]

    print(f"Loaded {len(listings)} total listings.")
    print(f"Found {len(unconfirmed)} listings still marked likely_sold_or_removed.")

    # Oldest-flagged first (blank date_disappeared sorts last, not first,
    # so it doesn't jump the queue ahead of dated ones).
    unconfirmed.sort(key=lambda r: r.get("date_disappeared") or "9999")

    to_check = unconfirmed[:TOTAL_CAP_PER_RUN]
    print(f"Processing {len(to_check)} this run (oldest-flagged first, "
          f"cap {TOTAL_CAP_PER_RUN}).")
    if len(unconfirmed) > TOTAL_CAP_PER_RUN:
        print(f"  {len(unconfirmed) - TOTAL_CAP_PER_RUN} remain for future runs.")

    BATCHES_DIR.mkdir(exist_ok=True, parents=True)
    for old_file in BATCHES_DIR.glob("batch_*.csv"):
        old_file.unlink()

    if not to_check:
        print("Nothing to check - writing empty batch files.")
        for i in range(NUM_BATCHES):
            with open(BATCHES_DIR / f"batch_{i}.csv", "w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=BATCH_COLUMNS).writeheader()
        return

    batch_size = math.ceil(len(to_check) / NUM_BATCHES)

    for i in range(NUM_BATCHES):
        start = i * batch_size
        end = start + batch_size
        batch_rows = to_check[start:end]

        with open(BATCHES_DIR / f"batch_{i}.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=BATCH_COLUMNS)
            writer.writeheader()
            for row in batch_rows:
                writer.writerow({"listing_id": row["listing_id"], "url": row["url"]})

        print(f"  batch_{i}.csv: {len(batch_rows)} listings")

    print(f"\nSplit into {NUM_BATCHES} batches under {BATCHES_DIR}/")


if __name__ == "__main__":
    main()
