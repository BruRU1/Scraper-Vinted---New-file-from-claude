"""
migrate_to_db.py

One-time move of everything currently in data/listings.csv,
data/listings_archive.csv, and data/price_history.csv into the Postgres
database that replaces them. Safe to run more than once - it upserts
listings by listing_id and only inserts price_history rows it hasn't
seen before, so re-running after a partial failure just picks up where
it left off rather than duplicating anything.

Creates the listings/price_history tables first if they don't exist yet
(see schema.sql) - you don't need to run that file separately.

Needs DATABASE_URL set (see listings_db.py's docstring). In GitHub
Actions this comes from the DATABASE_URL repository secret; the
"Migrate to database" workflow (workflow_dispatch, run once from the
Actions tab) does this for you against the real data on main - you
shouldn't need to run this yourself unless you're doing something by
hand.

Run manually:  DATABASE_URL=postgresql://... python migrate_to_db.py
"""

import csv
from pathlib import Path

import psycopg2.extras

from listings_db import LISTINGS_COLUMNS, get_connection, db_column, to_sql

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
LISTINGS_PATH = DATA_DIR / "listings.csv"
ARCHIVE_PATH = DATA_DIR / "listings_archive.csv"
HISTORY_PATH = DATA_DIR / "price_history.csv"

SCHEMA_PATH = ROOT / "schema.sql"


def ensure_schema(conn):
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        with conn.cursor() as cur:
            cur.execute(f.read())
    conn.commit()
    print("Schema ready (tables created if they didn't already exist).")


def load_csv_rows(path):
    if not path.exists():
        return []
    with open(path, "r", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


# Preference order when the same URL somehow shows up under more than
# one listing_id (see the "same URL, two IDs" comment below) - an active
# listing is the current truth about what's really on Vinted right now,
# so it always wins over a stale resolved row for the same URL.
_STATUS_PRIORITY = {"active": 3, "likely_sold_or_removed": 2, "confirmed_sold": 1, "deleted": 0}


def migrate_listings(conn):
    rows = load_csv_rows(LISTINGS_PATH) + load_csv_rows(ARCHIVE_PATH)
    print(f"Loaded {len(rows)} listings from listings.csv + listings_archive.csv.")
    if not rows:
        return set()

    seen_ids = set()
    deduped = []
    for row in rows:
        listing_id = row["listing_id"]
        if listing_id in seen_ids:
            continue  # shouldn't happen (the two files are disjoint by construction), but be safe
        seen_ids.add(listing_id)
        deduped.append(row)

    # Same URL, two different listing_ids: happens when a listing was
    # archived as resolved (moved out of listings.csv into
    # listings_archive.csv), then later relisted under the same URL -
    # scraper.py only ever checked listings.csv for matches, never the
    # archive, so it couldn't see the old row and created a new one.
    # listings.url has a UNIQUE constraint, so only one can be kept; the
    # currently-active row (or, failing that, whichever status is
    # "freshest") is the real current truth about that listing. This bug
    # can't recur once everything lives in one table with nothing
    # archived out of scraper.py's view.
    by_url = {}
    dropped_ids = set()
    for row in deduped:
        existing = by_url.get(row["url"])
        if existing is None:
            by_url[row["url"]] = row
            continue
        a_priority = (_STATUS_PRIORITY.get(row["status"], -1), row.get("first_seen", ""))
        b_priority = (_STATUS_PRIORITY.get(existing["status"], -1), existing.get("first_seen", ""))
        winner, loser = (row, existing) if a_priority > b_priority else (existing, row)
        by_url[row["url"]] = winner
        dropped_ids.add(loser["listing_id"])
        print(f"  URL collision: keeping listing_id {winner['listing_id']} "
              f"({winner['status']}) over {loser['listing_id']} ({loser['status']}) "
              f"for {row['url']}")

    if len(by_url) != len(deduped):
        print(f"Resolved {len(deduped) - len(by_url)} URL collision(s); "
              f"{len(by_url)} unique listings remain.")
    deduped = list(by_url.values())

    db_cols = [db_column(c) for c in LISTINGS_COLUMNS]
    update_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in db_cols if c != "listing_id")
    sql = (
        f"INSERT INTO listings ({', '.join(db_cols)}) VALUES %s "
        f"ON CONFLICT (listing_id) DO UPDATE SET {update_clause}"
    )
    values = [tuple(to_sql(row.get(c, "")) for c in LISTINGS_COLUMNS) for row in deduped]

    with conn.cursor() as cur:
        psycopg2.extras.execute_values(cur, sql, values, page_size=1000)
    conn.commit()
    print(f"Upserted {len(deduped)} rows into listings.")
    return dropped_ids


def migrate_history(conn, dropped_ids=frozenset()):
    rows = load_csv_rows(HISTORY_PATH)
    print(f"Loaded {len(rows)} price_history rows.")
    if not rows:
        return

    with conn.cursor() as cur:
        cur.execute("SELECT count(*) FROM price_history")
        already = cur.fetchone()[0]
    if already:
        print(f"price_history already has {already} rows - skipping "
              f"(assumed already migrated; delete the table's rows first "
              f"if you really want to re-import).")
        return

    # Skip history rows belonging to a listing_id dropped as a URL
    # collision loser (see migrate_listings) - it no longer exists in
    # listings, and price_history.listing_id is a foreign key onto it.
    if dropped_ids:
        before = len(rows)
        rows = [r for r in rows if r["listing_id"] not in dropped_ids]
        if len(rows) != before:
            print(f"Skipped {before - len(rows)} price_history rows for "
                  f"listing_ids dropped as URL collisions.")

    values = [
        (int(r["listing_id"]), to_sql(r["old_price"]), to_sql(r["new_price"]), to_sql(r["changed_at"]))
        for r in rows
    ]
    with conn.cursor() as cur:
        psycopg2.extras.execute_values(
            cur,
            "INSERT INTO price_history (listing_id, old_price, new_price, changed_at) VALUES %s",
            values,
            page_size=1000,
        )
    conn.commit()
    print(f"Inserted {len(values)} rows into price_history.")


def main():
    conn = get_connection()
    try:
        ensure_schema(conn)
        dropped_ids = migrate_listings(conn)
        migrate_history(conn, dropped_ids)
    finally:
        conn.close()
    print("\nDone. data/listings.csv, data/listings_archive.csv, and "
          "data/price_history.csv are no longer read by any script - they "
          "can stay in the repo as a historical snapshot, or you can "
          "delete them once you've confirmed the dashboard/queries look right.")


if __name__ == "__main__":
    main()
