"""
db_size_report.py

Read-only diagnostic: prints how much space each table and index is
actually using in the database, plus a breakdown of the listings table
by status. Doesn't change anything - purely for deciding what a pruning
plan should target before touching any data.

Run manually:  DATABASE_URL=postgresql://... python db_size_report.py
Or via the "DB size report" workflow (workflow_dispatch, Actions tab) -
that way the connection string never has to leave the GitHub secret.
"""

from listings_db import get_connection


def fmt_bytes(n):
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def main():
    with get_connection() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_database_size(current_database())")
            db_size = cur.fetchone()[0]
            print(f"Total database size: {fmt_bytes(db_size)}\n")

            print("Per-table size (table + its indexes):")
            cur.execute(
                """
                SELECT
                    relname,
                    pg_total_relation_size(relid) AS total_bytes,
                    pg_relation_size(relid) AS table_bytes,
                    pg_indexes_size(relid) AS indexes_bytes,
                    n_live_tup AS approx_rows
                FROM pg_catalog.pg_stat_user_tables
                ORDER BY pg_total_relation_size(relid) DESC
                """
            )
            for relname, total_b, table_b, idx_b, approx_rows in cur.fetchall():
                print(
                    f"  {relname:<20} total={fmt_bytes(total_b):>10}  "
                    f"table={fmt_bytes(table_b):>10}  indexes={fmt_bytes(idx_b):>10}  "
                    f"~{approx_rows:,} rows"
                )

            print("\nPer-index size:")
            cur.execute(
                """
                SELECT
                    indexrelname,
                    relname,
                    pg_relation_size(indexrelid) AS index_bytes
                FROM pg_catalog.pg_stat_user_indexes
                ORDER BY pg_relation_size(indexrelid) DESC
                """
            )
            for indexrelname, relname, idx_b in cur.fetchall():
                print(f"  {indexrelname:<32} on {relname:<14} {fmt_bytes(idx_b):>10}")

            print("\nlistings row counts by status:")
            cur.execute("SELECT status, count(*) FROM listings GROUP BY status ORDER BY count(*) DESC")
            for status, count in cur.fetchall():
                print(f"  {status:<25} {count:,}")

            cur.execute("SELECT count(*) FROM listings")
            total_listings = cur.fetchone()[0]
            print(f"  {'TOTAL':<25} {total_listings:,}")

            cur.execute("SELECT count(*) FROM price_history")
            history_count = cur.fetchone()[0]
            print(f"\nprice_history rows: {history_count:,}")

            print("\nlikely_sold_or_removed backlog, by age since date_disappeared:")
            cur.execute(
                """
                SELECT
                    CASE
                        WHEN date_disappeared IS NULL THEN 'no date_disappeared'
                        WHEN now() - date_disappeared < interval '7 days' THEN '0-7 days'
                        WHEN now() - date_disappeared < interval '14 days' THEN '7-14 days'
                        WHEN now() - date_disappeared < interval '30 days' THEN '14-30 days'
                        WHEN now() - date_disappeared < interval '45 days' THEN '30-45 days'
                        WHEN now() - date_disappeared < interval '60 days' THEN '45-60 days'
                        WHEN now() - date_disappeared < interval '90 days' THEN '60-90 days'
                        ELSE '90+ days'
                    END AS age_bucket,
                    count(*)
                FROM listings
                WHERE status = 'likely_sold_or_removed'
                GROUP BY 1
                ORDER BY min(now() - date_disappeared)
                """
            )
            for bucket, count in cur.fetchall():
                print(f"  {bucket:<22} {count:,}")


if __name__ == "__main__":
    main()
