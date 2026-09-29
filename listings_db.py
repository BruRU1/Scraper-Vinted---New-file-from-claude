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


def _rows_to_dicts(db_rows, columns):
    result = []
    for db_row in db_rows:
        row = {col: _stringify(val) for col, val in zip(columns, db_row)}
        row["listing_id"] = int(row["listing_id"])
        row["consecutive_misses"] = int(row["consecutive_misses"] or 0)
        row["price_drops"] = int(row["price_drops"] or 0)
        result.append(row)
    return result


def fetch_all_rows(exclude_columns=None):
    """Every listing as a plain list of dicts, same shape as the old
    csv.DictReader(listings.csv) rows. Pass exclude_columns to skip ones
    the caller never uses (e.g. "imageUrl") - every column fetched is
    Supabase egress, and a table this size adds up fast."""
    exclude = set(exclude_columns or [])
    columns = [c for c in LISTINGS_COLUMNS if c not in exclude]
    db_cols = [db_column(c) for c in columns]
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(f"SELECT {', '.join(db_cols)} FROM listings")
            rows = cur.fetchall()
    return _rows_to_dicts(rows, columns)


def fetch_url_id_map():
    """Lightweight {url: listing_id} for every row, regardless of status -
    just two columns, versus all 20 fetch_all_rows() pulls. Enough to
    recognize "this URL has been seen before" (and reuse its listing_id)
    without paying for the full row of every listing scraper.py has no
    reason to touch."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT url, listing_id FROM listings")
            return {url: int(listing_id) for url, listing_id in cur.fetchall()}


def fetch_rows_by_status(statuses):
    """Full row dicts, same shape as fetch_all_rows(), but only for the
    given statuses."""
    db_cols = [db_column(c) for c in LISTINGS_COLUMNS]
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                f"SELECT {', '.join(db_cols)} FROM listings WHERE status = ANY(%s)",
                (list(statuses),),
            )
            rows = cur.fetchall()
    return _rows_to_dicts(rows, LISTINGS_COLUMNS)


def load_for_scrape():
    """Returns (listings_by_url, url_to_id, next_id) for scraper.py.

    scraper.py only ever reconciles/reactivates listings that are
    currently "active" or "likely_sold_or_removed" - a confirmed_sold or
    deleted row is never modified by it, so there's no reason to pull its
    full ~20-column data (and pay the egress for it) on every single run,
    which is what load_all_listings() used to do for the ENTIRE table.

    url_to_id still covers every URL regardless of status (just 2 columns,
    all rows) purely so a listing that gets relisted on Vinted after
    already being confirmed_sold/deleted reuses its old listing_id instead
    of minting a duplicate one - that gap (scraper.py not recognizing an
    archived listing's URL) is exactly the bug migrate_to_db.py had to
    clean up after the fact; this keeps it from recurring, at a fraction
    of the cost of loading those rows' full data every run.
    """
    url_to_id = fetch_url_id_map()
    rows = fetch_rows_by_status(["active", "likely_sold_or_removed"])
    listings_by_url = {row["url"]: row for row in rows}
    next_id = (max(url_to_id.values()) if url_to_id else 0) + 1
    return listings_by_url, url_to_id, next_id


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


HISTORY_COLUMNS = ["listing_id", "old_price", "new_price", "changed_at"]


def fetch_history_for_listings(listing_ids):
    """price_history rows for the given listing_ids - used by
    archive_resolved.py to pull a resolved listing's price history along
    with it before removing both from the live database."""
    if not listing_ids:
        return []
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT listing_id, old_price, new_price, changed_at "
                "FROM price_history WHERE listing_id = ANY(%s)",
                (list(listing_ids),),
            )
            rows = cur.fetchall()
    return [{col: _stringify(val) for col, val in zip(HISTORY_COLUMNS, row)} for row in rows]


def delete_resolved(listing_ids):
    """Removes the given listing_ids from both price_history and
    listings (price_history first - it has a foreign key onto listings,
    so the reverse order would fail). Only ever called by
    archive_resolved.py, and only after those rows have already been
    written to the archive file - once a row is gone from here, the
    archive file is the only remaining copy. Returns the number of
    listings rows removed."""
    if not listing_ids:
        return 0
    ids = list(listing_ids)
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM price_history WHERE listing_id = ANY(%s)", (ids,))
            cur.execute("DELETE FROM listings WHERE listing_id = ANY(%s)", (ids,))
            deleted = cur.rowcount
        conn.commit()
    return deleted


def vacuum_full(tables):
    """Physically reclaims disk space after deleting rows. A plain DELETE
    only marks rows as removable - Postgres doesn't shrink the file on
    disk (or reduce what Supabase counts against the storage quota)
    until something rewrites the table, which VACUUM FULL does. VACUUM
    can't run inside a transaction, so this uses its own autocommit
    connection rather than get_connection()'s usual "with" block."""
    conn = get_connection()
    conn.autocommit = True
    try:
        with conn.cursor() as cur:
            for table in tables:
                cur.execute(f"VACUUM FULL {table}")
    finally:
        conn.close()


def ensure_price_history_index():
    """This index looked unused (nothing in this codebase ever queries
    price_history BY listing_id directly) and an earlier version of this
    project dropped it on that basis - but it turned out to be load-
    bearing for a completely different reason: price_history.listing_id
    is a foreign key onto listings, and deleting rows from listings (as
    archive_resolved.py does) makes Postgres check, for every row being
    deleted, whether any price_history row still references it. Without
    an index on that column, each check is a full table scan - fine for
    a handful of deletes, catastrophic for hundreds of thousands (this is
    exactly what made the first production run of archive_resolved.py
    time out and fail). CREATE INDEX IF NOT EXISTS is a no-op once it
    already exists, so this is safe to call every run."""
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE INDEX IF NOT EXISTS idx_price_history_listing_id "
                "ON price_history (listing_id)"
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
