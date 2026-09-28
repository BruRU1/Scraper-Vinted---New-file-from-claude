"""
archive_resolved.py

Moves fully-resolved listings (status confirmed_sold or deleted -
nothing left to ever check or reconcile again; see
listings_db.load_for_scrape(), which already skips them for exactly this
reason) out of the live database and into a compressed archive file in
this repo, along with their price_history rows.

Nothing is deleted from existence. A resolved listing moves from
"expensive to keep indexed in the live database" to "a row in a gzipped
CSV sitting in git" - the archive file only ever grows: each run adds
whatever's newly resolved since the last run on top of what's already
there, so nothing already archived is ever lost, overwritten, or
re-fetched.

Why this exists: Supabase's free tier caps total database size at
500MB, and the live `listings` table was carrying ~300k fully-resolved
rows it never needed to touch again, most of the size problem. This is
meant to run every scheduled pipeline run from now on (not just once),
so the database stays lean permanently instead of drifting back toward
the limit.

After writing the archive and deleting the rows from the live tables,
also runs VACUUM FULL - a plain DELETE only marks rows as removable, it
doesn't actually shrink the database on disk (or reduce what counts
against Supabase's storage quota) until something rewrites the table,
which is what VACUUM FULL does.

Output files (gzip-compressed CSV):
  data/listings_resolved_archive.csv.gz
  data/price_history_archive.csv.gz

Run manually:  DATABASE_URL=postgresql://... python archive_resolved.py
Runs automatically as a step in the "merge-and-analyze" job in
.github/workflows/scrape.yml, after run_analytics.py (so this run's own
stats still see the full data one last time before it's archived) and
before the final commit (so the updated archive files get committed
along with everything else that run produces).
"""

import csv
import gzip
from pathlib import Path

import listings_db

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
LISTINGS_ARCHIVE_PATH = DATA_DIR / "listings_resolved_archive.csv.gz"
HISTORY_ARCHIVE_PATH = DATA_DIR / "price_history_archive.csv.gz"

RESOLVED_STATUSES = ("confirmed_sold", "deleted")


def load_existing(path):
    if not path.exists():
        return []
    with gzip.open(path, "rt", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def write_all(path, columns, rows):
    DATA_DIR.mkdir(exist_ok=True, parents=True)
    with gzip.open(path, "wt", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def main():
    listings_db.drop_unused_price_history_index()

    resolved = listings_db.fetch_rows_by_status(RESOLVED_STATUSES)
    print(f"Found {len(resolved)} resolved (confirmed_sold/deleted) listings in the live database.")

    if not resolved:
        print("Nothing to archive.")
        return

    resolved_ids = [row["listing_id"] for row in resolved]
    history = listings_db.fetch_history_for_listings(resolved_ids)
    print(f"Found {len(history)} matching price_history rows.")

    existing_listings = load_existing(LISTINGS_ARCHIVE_PATH)
    existing_history = load_existing(HISTORY_ARCHIVE_PATH)
    print(
        f"Archive already holds {len(existing_listings)} listings and "
        f"{len(existing_history)} price_history rows from previous runs."
    )

    write_all(LISTINGS_ARCHIVE_PATH, listings_db.LISTINGS_COLUMNS, existing_listings + resolved)
    write_all(HISTORY_ARCHIVE_PATH, listings_db.HISTORY_COLUMNS, existing_history + history)
    print(
        f"Archive now holds {len(existing_listings) + len(resolved)} listings and "
        f"{len(existing_history) + len(history)} price_history rows."
    )

    deleted_count = listings_db.delete_resolved(resolved_ids)
    print(f"Removed {deleted_count} listings (and their price_history rows) from the live database.")

    if deleted_count:
        # A plain DELETE doesn't actually shrink the database on disk -
        # this is the step that does. Cheap and fast even on a table
        # this size (a couple of seconds in testing), so just always run
        # it after removing anything.
        print("Reclaiming disk space (VACUUM FULL)...")
        listings_db.vacuum_full(["listings", "price_history"])
        print("Done.")


if __name__ == "__main__":
    main()
