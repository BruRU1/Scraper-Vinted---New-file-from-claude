"""
run_analytics.py

Runs analyze.py and resale_score.py back-to-back against a SINGLE shared
fetch of the listings table, instead of each independently re-fetching
the whole (large, ever-growing) table from Supabase within the same few
minutes. Egress from Supabase scales directly with how much data crosses
the wire - fetching the entire table twice in one workflow run was pure
waste, and doing that every ~6 hours as the table grew past 700k rows is
what pushed this project over its free-tier egress limit.

Run manually:  DATABASE_URL=postgresql://... python run_analytics.py
Runs automatically as the "Run analytics" step in
.github/workflows/scrape.yml, replacing what used to be two separate
steps (analyze.py, then resale_score.py).
"""

import analyze
import resale_score


def main():
    listings, group_stats, group_avg_lookup = analyze.analyze()
    resale_score.main(listings=listings, group_stats=group_stats, group_avg_lookup=group_avg_lookup)


if __name__ == "__main__":
    main()
