"""
check_sold_backlog.py

ONE-TIME cleanup script. Visits every listing currently marked
"likely_sold_or_removed" in data/listings.csv and confirms what actually
happened to each one (sold / still active / genuinely gone), using the
shared logic in sold_checker.py.

Run this once to clean up your existing backlog. After that, use
check_sold_new.py after each scrape to keep checking only newly-flagged
listings going forward, rather than re-running this on the whole backlog
every time.

Run manually:  python check_sold_backlog.py
"""

from sold_checker import load_listings, save_listings, process_listings


def main():
    listings = load_listings()
    listings_by_id = {row["listing_id"]: row for row in listings}

    backlog = [row for row in listings if row.get("status") == "likely_sold_or_removed"]

    print(f"Loaded {len(listings)} total listings.")
    print(f"Found {len(backlog)} listings marked likely_sold_or_removed to check.\n")

    if not backlog:
        print("Nothing to check.")
        return

    counts = process_listings(backlog, listings_by_id)

    save_listings(list(listings_by_id.values()))

    print(f"\nDone. Checked {counts['checked']} listings.")
    print(f"  Confirmed sold:        {counts['sold']}")
    print(f"  Reactivated (was active, buried): {counts['reactivated']}")
    print(f"  Gone, unconfirmed (404/error):    {counts['gone_unconfirmed']}")


if __name__ == "__main__":
    main()
