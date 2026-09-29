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

Processes in chunks (CHUNK_SIZE listings at a time), committing and
pushing each chunk's archive-file update to git BEFORE moving on to the
next chunk's database delete. This means a failure partway through can
only ever put one chunk's worth of already-deleted rows at risk of not
having reached git yet - never anything from a chunk that already
completed, and never the whole run's worth of newly-resolved listings.

After writing the archive and deleting the rows from the live tables,
also runs VACUUM FULL - a plain DELETE only marks rows as removable, it
doesn't actually shrink the database on disk (or reduce what counts
against Supabase's storage quota) until something rewrites the table,
which is what VACUUM FULL does.

Why this exists: Supabase's free tier caps total database size at
500MB, and the live `listings` table was carrying hundreds of thousands
of fully-resolved rows it never needed to touch again - most of the size
problem. This is meant to run every scheduled pipeline run from now on
(not just once), so the database stays lean permanently instead of
drifting back toward the limit.

Output files (gzip-compressed CSV):
  data/listings_resolved_archive.csv.gz
  data/price_history_archive.csv.gz

Run manually:  DATABASE_URL=postgresql://... python archive_resolved.py
Runs automatically as a step in the "merge-and-analyze" job in
.github/workflows/scrape.yml, after run_analytics.py (so this run's own
stats still see the full data one last time before it's archived) and
before that job's own final commit (which only ever has group_stats.csv/
deals.csv/resale_opportunities.csv left to add, since this script
already committed its own files chunk by chunk).
"""

import csv
import gzip
import subprocess
from pathlib import Path

import listings_db

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
LISTINGS_ARCHIVE_PATH = DATA_DIR / "listings_resolved_archive.csv.gz"
HISTORY_ARCHIVE_PATH = DATA_DIR / "price_history_archive.csv.gz"

RESOLVED_STATUSES = ("confirmed_sold", "deleted")

# How many listings to archive-and-delete per chunk. Keeps each database
# delete small (fast, low lock contention) and bounds how much could ever
# be at risk if a run fails partway through to just this many rows.
CHUNK_SIZE = 10000


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


def git_commit_and_push(message):
    """Commits and pushes just the archive files. A no-op (nothing
    staged) if this chunk didn't actually change either file."""
    subprocess.run(["git", "config", "user.name", "vinted-scraper-bot"], check=True)
    subprocess.run(["git", "config", "user.email", "actions@users.noreply.github.com"], check=True)
    subprocess.run(["git", "add", str(LISTINGS_ARCHIVE_PATH), str(HISTORY_ARCHIVE_PATH)], check=True)
    nothing_staged = subprocess.run(["git", "diff", "--cached", "--quiet"]).returncode == 0
    if nothing_staged:
        return
    subprocess.run(["git", "commit", "-m", message], check=True)
    subprocess.run(["git", "pull", "--rebase", "origin", "main"], check=True)
    subprocess.run(["git", "push", "origin", "HEAD:main"], check=True)


def main():
    listings_db.ensure_price_history_index()

    resolved = listings_db.fetch_rows_by_status(RESOLVED_STATUSES)
    print(f"Found {len(resolved)} resolved (confirmed_sold/deleted) listings in the live database.")

    if not resolved:
        print("Nothing to archive.")
        return

    archived_listings = load_existing(LISTINGS_ARCHIVE_PATH)
    archived_history = load_existing(HISTORY_ARCHIVE_PATH)
    print(
        f"Archive already holds {len(archived_listings)} listings and "
        f"{len(archived_history)} price_history rows from previous runs."
    )

    total_deleted = 0
    num_chunks = (len(resolved) + CHUNK_SIZE - 1) // CHUNK_SIZE

    for i in range(0, len(resolved), CHUNK_SIZE):
        chunk = resolved[i:i + CHUNK_SIZE]
        chunk_ids = [row["listing_id"] for row in chunk]
        chunk_history = listings_db.fetch_history_for_listings(chunk_ids)

        archived_listings.extend(chunk)
        archived_history.extend(chunk_history)
        write_all(LISTINGS_ARCHIVE_PATH, listings_db.LISTINGS_COLUMNS, archived_listings)
        write_all(HISTORY_ARCHIVE_PATH, listings_db.HISTORY_COLUMNS, archived_history)

        # Commit BEFORE deleting - if the delete or anything after it
        # fails, this chunk's rows are already safely archived in git,
        # never only-deleted-and-not-yet-archived.
        chunk_num = i // CHUNK_SIZE + 1
        git_commit_and_push(f"Archive chunk {chunk_num}/{num_chunks} of resolved listings")

        deleted = listings_db.delete_resolved(chunk_ids)
        total_deleted += deleted
        print(f"  chunk {chunk_num}/{num_chunks}: archived and removed {deleted} listings "
              f"({total_deleted}/{len(resolved)} so far)")

    print(
        f"Archive now holds {len(archived_listings)} listings and "
        f"{len(archived_history)} price_history rows."
    )
    print(f"Removed {total_deleted} listings (and their price_history rows) from the live database.")

    if total_deleted:
        print("Reclaiming disk space (VACUUM FULL)...")
        listings_db.vacuum_full(["listings", "price_history"])
        print("Done.")


if __name__ == "__main__":
    main()
