"""
split_batches.py

Reads data/newly_flagged.csv - the EXACT list of listings scraper.py just
flagged "likely_sold_or_removed" during this run (written by scraper.py
itself at the end of its run) - and splits them into N batch files for
parallel checking.

This deliberately does NOT scan the full listings.csv or use any time
window. It only ever processes what was newly flagged in the run that
just happened. The historical backlog (everything flagged in past runs)
is left untouched - that's a separate problem to tackle later, not
something this script does automatically.

Run manually:  python split_batches.py
(In the automated workflow, this runs right after scraper.py, reading
the file scraper.py just wrote.)
"""

import csv
import math
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
NEWLY_FLAGGED_PATH = DATA_DIR / "newly_flagged.csv"
BATCHES_DIR = DATA_DIR / "batches"

NUM_BATCHES = 18

BATCH_COLUMNS = ["listing_id", "url"]


def main():
    if not NEWLY_FLAGGED_PATH.exists():
        print(f"No {NEWLY_FLAGGED_PATH} found - did scraper.py run first?")
        newly_flagged = []
    else:
        with open(NEWLY_FLAGGED_PATH, "r", newline="", encoding="utf-8") as f:
            newly_flagged = list(csv.DictReader(f))

    print(f"Found {len(newly_flagged)} listings newly flagged this run.")

    BATCHES_DIR.mkdir(exist_ok=True, parents=True)
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
