"""
archive_resolved.py

Keeps data/listings.csv from growing without bound. GitHub hard-rejects
any pushed file over 100MB, and listings.csv crossed that line - which
broke every scheduled run until this existed. It does two things:

  1. Any listing whose status is "confirmed_sold" or "deleted" - i.e.
     genuinely resolved, nothing left to ever check or reconcile again -
     is moved out of listings.csv into data/listings_archive.csv (same
     columns, appended, never rewritten). Nothing is deleted or lost:
     for trend analysis across weeks/months, read listings.csv and
     listings_archive.csv together (or just concatenate them) to get
     the full history. This just keeps the "live" working file - the
     one every run reads and rewrites in full - from carrying rows
     nothing needs to touch again.

  2. For any row that's no longer "active" (likely_sold_or_removed,
     confirmed_sold, deleted), imageUrl is cleared. A thumbnail link for
     something that's not buyable any more (or not even confirmed to
     still exist) isn't useful, and imageUrl is one of the largest
     columns in the file (Vinted's CDN links are long) - blanking it
     saves roughly 100+ bytes/row, which adds up fast at 200k+ rows.
     Every other column (price, brand, category, dates, etc) is kept
     exactly as-is, so no analytics/trend data is lost - only the image
     link for listings nobody can click through and buy any more.

Run manually:  python archive_resolved.py
(In the automated workflow this runs twice: at the end of the "scrape"
job, right before its commit, and at the end of "merge-and-analyze",
right after analyze.py and before its commit. It must run AFTER
analyze.py there, since analyze.py still needs to see confirmed_sold
listings for its own stats before they get archived away.)
"""

import csv
from pathlib import Path

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
LISTINGS_PATH = DATA_DIR / "listings.csv"
ARCHIVE_PATH = DATA_DIR / "listings_archive.csv"

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

RESOLVED_STATUSES = {"confirmed_sold", "deleted"}


def main():
    if not LISTINGS_PATH.exists():
        print(f"No {LISTINGS_PATH} found - nothing to do.")
        return

    with open(LISTINGS_PATH, "r", newline="", encoding="utf-8") as f:
        listings = list(csv.DictReader(f))

    keep_rows = []
    archive_rows = []
    blanked_images = 0

    for row in listings:
        if row.get("status") in RESOLVED_STATUSES:
            archive_rows.append(row)
            continue

        if row.get("status") != "active" and row.get("imageUrl"):
            row["imageUrl"] = ""
            blanked_images += 1

        keep_rows.append(row)

    # Always make sure the archive file exists, even with 0 rows to add
    # this run - the workflow's `git add data/listings_archive.csv` fails
    # outright ("pathspec did not match any files") if the path doesn't
    # exist at all yet.
    if not ARCHIVE_PATH.exists():
        with open(ARCHIVE_PATH, "w", newline="", encoding="utf-8") as f:
            csv.DictWriter(f, fieldnames=LISTINGS_COLUMNS).writeheader()

    if archive_rows:
        with open(ARCHIVE_PATH, "a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=LISTINGS_COLUMNS)
            for row in archive_rows:
                writer.writerow(row)

    with open(LISTINGS_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LISTINGS_COLUMNS)
        writer.writeheader()
        for row in keep_rows:
            writer.writerow(row)

    print(f"Loaded {len(listings)} listings.")
    print(f"Archived {len(archive_rows)} confirmed_sold/deleted listings to {ARCHIVE_PATH}.")
    print(f"Cleared imageUrl on {blanked_images} non-active listings.")
    print(f"{len(keep_rows)} listings remain in {LISTINGS_PATH}.")


if __name__ == "__main__":
    main()
