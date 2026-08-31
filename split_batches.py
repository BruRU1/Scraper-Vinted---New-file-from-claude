"""
split_batches.py

Finds listings newly flagged "likely_sold_or_removed" (within the recency
window, same logic as before), splits them into N batch files, and saves
each batch as its own small CSV under data/batches/.

These batch files are picked up by parallel matrix jobs in the GitHub
Actions workflow, each running check_batch.py on one batch at the same
time - so instead of checking 22,000 listings one after another, N jobs
each check ~22,000/N listings simultaneously, cutting wall-clock time
roughly by a factor of N.

Run manually:  python split_batches.py
"""

import csv
import math
from datetime import datetime, timezone, timedelta
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
LISTINGS_PATH = DATA_DIR / "listings.csv"
BATCHES_DIR = DATA_DIR / "batches"

NUM_BATCHES = 5

# How recently a listing must have been flagged to be picked up this run.
RECENT_WINDOW_MINUTES = 8 * 60  # 8 hours

BATCH_COLUMNS = ["listing_id", "url"]


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def main():
    with open(LISTINGS_PATH, "r", newline="", encoding="utf-8") as f:
        listings = list(csv.DictReader(f))

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=RECENT_WINDOW_MINUTES)

    newly_flagged = []
    for row in listings:
        if row.get("status") != "likely_sold_or_removed":
            continue
        disappeared = parse_dt(row.get("date_disappeared"))
        if disappeared and disappeared >= cutoff:
            newly_flagged.append(row)

    print(f"Loaded {len(listings)} total listings.")
    print(f"Found {len(newly_flagged)} newly-flagged listings to check.")

    BATCHES_DIR.mkdir(exist_ok=True, parents=True)
    # Clear out any old batch files from a previous run.
    for old_file in BATCHES_DIR.glob("batch_*.csv"):
        old_file.unlink()

    if not newly_flagged:
        print("Nothing to check - writing empty batch files.")
        for i in range(NUM_BATCHES):
            with open(BATCHES_DIR / f"batch_{i}.csv", "w", newline="", encoding="utf-8") as f:
                csv.DictWriter(f, fieldnames=BATCH_COLUMNS).writeheader()
        return

    batch_size = math.ceil(len(newly_flagged) / NUM_BATCHES)

    for i in range(NUM_BATCHES):
        start = i * batch_size
        end = start + batch_size
        batch_rows = newly_flagged[start:end]

        with open(BATCHES_DIR / f"batch_{i}.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=BATCH_COLUMNS)
            writer.writeheader()
            for row in batch_rows:
                writer.writerow({"listing_id": row["listing_id"], "url": row["url"]})

        print(f"  batch_{i}.csv: {len(batch_rows)} listings")

    print(f"\nSplit into {NUM_BATCHES} batches under {BATCHES_DIR}/")


if __name__ == "__main__":
    main()
