"""
check_sold_new.py

Run this AFTER scraper.py on every scheduled run. Instead of re-checking
the whole backlog every time, it only visits listings that flipped to
"likely_sold_or_removed" very recently (within RECENT_WINDOW_MINUTES of
now) - i.e. the ones scraper.py just flagged in this run.

This keeps the ongoing cost small and constant per run, rather than
growing as your backlog of likely_sold_or_removed listings grows over
time.

Run manually:      python check_sold_new.py
Run automatically:  add as a step in .github/workflows/scrape.yml, after
                     "Run scraper" and before the commit step.
"""

from datetime import datetime, timezone, timedelta

from sold_checker import load_listings, save_listings, process_listings

# How recently a listing must have been flagged likely_sold_or_removed to
# be picked up by this run. Should comfortably cover the gap between
# scheduled scrapes (6 hours) plus some buffer for GitHub Actions delays.
RECENT_WINDOW_MINUTES = 8 * 60  # 8 hours

# Safety valve: never check more than this many listings in a single run,
# even if more are newly-flagged. Anything over the cap just gets picked
# up on a later run instead (it stays within the recency window for a
# while), so nothing is skipped forever - it just spreads out.
MAX_PER_RUN = 3000


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def main():
    listings = load_listings()
    listings_by_id = {row["listing_id"]: row for row in listings}

    now = datetime.now(timezone.utc)
    cutoff = now - timedelta(minutes=RECENT_WINDOW_MINUTES)

    newly_flagged = []
    for row in listings:
        if row.get("status") != "likely_sold_or_removed":
            continue
        disappeared = parse_dt(row.get("date_disappeared"))
        if disappeared and disappeared >= cutoff:
            newly_flagged.append(row)

    print(f"Loaded {len(listings)} total listings.")
    print(f"Found {len(newly_flagged)} newly-flagged listings "
          f"(flagged within the last {RECENT_WINDOW_MINUTES // 60} hours).")

    if not newly_flagged:
        print("Nothing new to check.")
        return

    # Oldest-flagged first, so nothing waits indefinitely if we're over cap.
    newly_flagged.sort(key=lambda r: r.get("date_disappeared") or "")

    if len(newly_flagged) > MAX_PER_RUN:
        print(f"Capping this run to {MAX_PER_RUN} (oldest-flagged first); "
              f"the rest will be picked up on a later run.\n")
        newly_flagged = newly_flagged[:MAX_PER_RUN]
    else:
        print()

    counts = process_listings(newly_flagged, listings_by_id)

    save_listings(list(listings_by_id.values()))

    print(f"\nDone. Checked {counts['checked']} listings.")
    print(f"  Confirmed sold:        {counts['sold']}")
    print(f"  Reactivated (was active, buried): {counts['reactivated']}")
    print(f"  Gone, unconfirmed (404/error):    {counts['gone_unconfirmed']}")


if __name__ == "__main__":
    main()
