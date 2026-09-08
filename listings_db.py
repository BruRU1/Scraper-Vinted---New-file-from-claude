"""
listings_db.py

Shared Postgres access for listing data. data/listings.csv and
data/price_history.csv used to be committed to git in full on every run,
which is the whole reason this exists: a full-file rewrite-and-commit
doesn't scale forever, and GitHub hard-rejects any pushed file over
100MB (listings.csv kept creeping back up toward that line even with
archiving). A real database has no such ceiling, and only ever writes
what actually changed.

Every function here trades in the exact same "dict of strings" shape
csv.DictReader/DictWriter used to produce, so the scraping,
reconciliation, and analytics logic elsewhere didn't need to change -
only the load/save boundary did. Blank/missing values are always "" in
memory (never None, never NaN), matching how an empty CSV cell used to
read back; this module is the only place that translates between that
and SQL NULL.

Needs the DATABASE_URL environment variable set to a Postgres connection
string - in Supabase: Project Settings -> Database -> Connection string
-> URI. In GitHub Actions this comes from the DATABASE_URL repository
secret (set at the job level in scrape.yml); locally, export it yourself
before running any script that imports this module.

Run schema.sql once (or let migrate_to_db.py do it for you) before using
any of this - every function assumes the listings/price_history tables
already exist.
"""

import os
from datetime import date, datetime

import psycopg2
import psycopg2.extras

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

# In-memory dict key -> DB column name, only where they differ. Postgres
# folds unquoted identifiers to lowercase, so the camelCase "imageUrl"
# used everywhere else in the codebase is stored as snake_case in the DB.
_COLUMN_OVERRIDES = {"imageUrl": "image_url"}


def db_column(key):
    return _COLUMN_OVERRIDES.get(key, key)


def get_connection():
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL is not set - point it at your Supabase Postgres "
            "connection string (Project Settings -> Database -> Connection "
            "string -> URI)."
        )
    return psycopg2.connect(url)


def _stringify(value):
    """Matches what csv.DictReader used to hand back: SQL NULL becomes
    "", everything else becomes a plain string."""
    if value is None:
        return ""
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    return str(value)


def to_sql(value):
    """The inverse direction: "" becomes NULL for the DB; everything else
    passes through as text and lets Postgres cast it to the target
    column's real type (numeric, timestamptz, etc)."""
    if value is None or value == "":
        return None
    return value


def fetch_all_rows():
    """Every listing as a plain list of dicts, same shape as the old
    csv.DictReader(listings.csv) rows."""
    db_cols = [db_column(c) for c in LISTINGS_COLUMNS]
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {', '.join(db_cols)} FROM listings")
            rows = cur.fetchall()

    result = []
    for db_row in rows:
        row = {col: _stringify(val) for col, val in zip(LISTINGS_COLUMNS, db_row)}
        row["listing_id"] = int(row["listing_id"])
        row["consecutive_misses"] = int(row["consecutive_misses"] or 0)
        row["price_drops"] = int(row["price_drops"] or 0)
        result.append(row)
    return result


def load_all_listings():
    """Returns (listings_by_url dict, next_id int) - exactly what
    scraper.py's old CSV-backed load_listings() returned."""
    listings_by_url = {}
    max_id = 0
    for row in fetch_all_rows():
        listings_by_url[row["url"]] = row
        max_id = max(max_id, row["listing_id"])
    return listings_by_url, max_id + 1


def save_all_listings(listings_by_url):
    """Upserts every row in listings_by_url. Matches the old
    save_listings()'s "rewrite everything" semantics 1:1 (safe, simple,
    proven), just against the DB instead of a full CSV rewrite - a run
    that touches most active listings still only ever transmits ~20
    columns x N rows to Postgres, never a 90MB file to git."""
    rows = list(listings_by_url.values())
    if not rows:
        return

    db_cols = [db_column(c) for c in LISTINGS_COLUMNS]
    update_clause = ", ".join(f"{c} = EXCLUDED.{c}" for c in db_cols if c != "listing_id")
    sql = (
        f"INSERT INTO listings ({', '.join(db_cols)}) VALUES %s "
        f"ON CONFLICT (listing_id) DO UPDATE SET {update_clause}"
    )
    values = [tuple(to_sql(row.get(c)) for c in LISTINGS_COLUMNS) for row in rows]

    with get_connection() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(cur, sql, values, page_size=1000)
        conn.commit()


def append_history(rows):
    """Insert-only - a price change is a fact about the past, never
    updated or replaced."""
    if not rows:
        return
    values = [
        (int(r["listing_id"]), to_sql(r["old_price"]), to_sql(r["new_price"]), to_sql(r["changed_at"]))
        for r in rows
    ]
    with get_connection() as conn:
        with conn.cursor() as cur:
            psycopg2.extras.execute_values(
                cur,
                "INSERT INTO price_history (listing_id, old_price, new_price, changed_at) VALUES %s",
                values,
                page_size=1000,
            )
        conn.commit()


def fetch_unconfirmed(order_by_oldest=True):
    """(listing_id, url) pairs still marked likely_sold_or_removed, for
    split_batches.py. Sorting happens in Postgres instead of Python -
    blank date_disappeared sorts last (NULLS LAST), same as before, so it
    can't jump the queue ahead of dated ones."""
    order = "ORDER BY date_disappeared ASC NULLS LAST" if order_by_oldest else ""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT listing_id, url FROM listings "
                f"WHERE status = 'likely_sold_or_removed' {order}"
            )
            return cur.fetchall()


def count_all_listings():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM listings")
            return cur.fetchone()[0]


def apply_batch_results(results_by_status):
    """Applies check_batch.py results onto the listings table. Only
    updates the handful of columns each status actually changes (mirrors
    exactly what the old CSV-based merge_batches.py did per status
    branch) and only for the rows a batch actually checked - unlike the
    old full-file rewrite, a run that resolves 13,000 listings out of
    300,000 now only ever touches those 13,000 rows.

    results_by_status: {"confirmed_sold": [...], "active": [...],
    "deleted": [...]}, each a list of the raw result dicts from
    check_batch.py's results_N.csv (same keys check_batch.py writes:
    listing_id, sold_price, sold_confirmed_at, consecutive_misses,
    date_disappeared, last_seen).
    """
    total = 0
    with get_connection() as conn:
        with conn.cursor() as cur:
            sold = results_by_status.get("confirmed_sold", [])
            if sold:
                values = [
                    (int(r["listing_id"]), to_sql(r["sold_price"]), to_sql(r["sold_confirmed_at"]))
                    for r in sold
                ]
                psycopg2.extras.execute_values(
                    cur,
                    "UPDATE listings SET status = 'confirmed_sold', "
                    "sold_price = data.sold_price::numeric, "
                    "sold_confirmed_at = data.sold_confirmed_at::timestamptz, image_url = '' "
                    "FROM (VALUES %s) AS data (listing_id, sold_price, sold_confirmed_at) "
                    "WHERE listings.listing_id = data.listing_id::bigint",
                    values,
                    page_size=1000,
                )
                total += len(sold)

            active = results_by_status.get("active", [])
            if active:
                values = [(int(r["listing_id"]), to_sql(r["consecutive_misses"]), to_sql(r["last_seen"])) for r in active]
                psycopg2.extras.execute_values(
                    cur,
                    "UPDATE listings SET status = 'active', "
                    "consecutive_misses = data.consecutive_misses::integer, "
                    "date_disappeared = NULL, sold_price = NULL, sold_confirmed_at = NULL, "
                    "last_seen = COALESCE(data.last_seen::timestamptz, listings.last_seen) "
                    "FROM (VALUES %s) AS data (listing_id, consecutive_misses, last_seen) "
                    "WHERE listings.listing_id = data.listing_id::bigint",
                    values,
                    page_size=1000,
                )
                total += len(active)

            deleted = results_by_status.get("deleted", [])
            if deleted:
                values = [(int(r["listing_id"]), to_sql(r["date_disappeared"])) for r in deleted]
                psycopg2.extras.execute_values(
                    cur,
                    "UPDATE listings SET status = 'deleted', "
                    "date_disappeared = data.date_disappeared::timestamptz, image_url = '' "
                    "FROM (VALUES %s) AS data (listing_id, date_disappeared) "
                    "WHERE listings.listing_id = data.listing_id::bigint",
                    values,
                    page_size=1000,
                )
                total += len(deleted)
        conn.commit()
    return total
