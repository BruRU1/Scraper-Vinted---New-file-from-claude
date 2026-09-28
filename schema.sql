-- schema.sql
--
-- Creates the two tables that replace data/listings.csv and
-- data/price_history.csv. migrate_to_db.py runs this automatically the
-- first time it's used, so you don't need to run this file by hand -
-- it's kept here mainly as documentation of the schema, and as a manual
-- fallback (paste into Supabase's SQL Editor and run) if you ever need
-- to recreate the tables from scratch. Every statement is safe to
-- re-run.

CREATE TABLE IF NOT EXISTS listings (
    listing_id          BIGINT PRIMARY KEY,
    url                 TEXT NOT NULL UNIQUE,
    platform            TEXT,
    category            TEXT,
    title               TEXT,
    current_price       NUMERIC,
    original_price      NUMERIC,
    price_drops         INTEGER NOT NULL DEFAULT 0,
    last_price_change   TIMESTAMPTZ,
    currency            TEXT,
    brand               TEXT,
    size                TEXT,
    condition           TEXT,
    image_url            TEXT,
    first_seen          TIMESTAMPTZ,
    last_seen           TIMESTAMPTZ,
    status              TEXT,
    date_disappeared    TIMESTAMPTZ,
    consecutive_misses  INTEGER NOT NULL DEFAULT 0,
    sold_price          NUMERIC,
    sold_confirmed_at   TIMESTAMPTZ
);

-- split_batches.py filters/sorts by these; merge-and-analyze's queries
-- group by everything else, but that's a full-table scan either way at
-- this data size (fine on Supabase's free tier).
CREATE INDEX IF NOT EXISTS idx_listings_status ON listings (status);
CREATE INDEX IF NOT EXISTS idx_listings_date_disappeared ON listings (date_disappeared);

CREATE TABLE IF NOT EXISTS price_history (
    id          BIGSERIAL PRIMARY KEY,
    listing_id  BIGINT NOT NULL REFERENCES listings (listing_id),
    old_price   NUMERIC,
    new_price   NUMERIC,
    changed_at  TIMESTAMPTZ
);

-- No index on price_history.listing_id - nothing in this codebase ever
-- looks price history up that way (it's write-only from the scraper's
-- side; archive_resolved.py is the only reader, and it filters by a
-- batch of IDs via = ANY(), which doesn't benefit from a btree here at
-- this table's size). archive_resolved.py drops this index automatically
-- if it finds one from before this was noticed - see
-- listings_db.drop_unused_price_history_index().
