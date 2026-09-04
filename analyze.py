"""
analyze.py

Reads data/listings.csv and produces market analytics:

  - Average/median price per (brand, category, condition, size) group
  - Typical "days listed" per group, as a demand proxy
  - A flagged list of currently ACTIVE listings priced meaningfully below
    their group's average - i.e. "deals"

Outputs two files into data/:

  data/group_stats.csv   - one row per (brand, category, condition, size)
                            group: avg price, median price, sample size,
                            avg days listed, avg days to disappear
  data/deals.csv          - one row per active listing flagged as
                            underpriced relative to its group

Groups with fewer than MIN_SAMPLE_SIZE listings are excluded from
group_stats.csv and never used to flag deals - too few comparable
listings makes the average unreliable/noisy rather than meaningful.

Run manually:  python analyze.py
(Not scheduled by GitHub Actions - run this locally or add a separate
 workflow step/job later once you're ready to automate it too.)
"""

import csv
from datetime import datetime, timezone
from pathlib import Path
from statistics import mean, median

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
LISTINGS_PATH = DATA_DIR / "listings.csv"
GROUP_STATS_PATH = DATA_DIR / "group_stats.csv"
DEALS_PATH = DATA_DIR / "deals.csv"

# A group (brand+category+condition+size) needs at least this many
# listings before its average is considered reliable enough to use.
MIN_SAMPLE_SIZE = 5

# How far below the group average a listing's price needs to be
# (as a fraction, e.g. 0.20 = 20% below) to get flagged as a deal.
DEAL_THRESHOLD_PCT = 0.20

# Categories named "brand_*" in categories.json are plain keyword searches
# (search_text=Supreme, search_text=Palace, etc.), which match ANY listing
# whose title/description mentions that word - not just real clothing from
# that brand. In practice this pulls in things like novels with "Palace"
# in the title, or household junk tagged "Supreme" by mistake, usually
# priced far below what a genuine item would go for. Real Vinted category
# browses (jeans, outerwear, etc.) don't have this problem, since Vinted's
# own category filter already did that work - so the floor only applies
# to "brand_*" categories.
BRAND_SEARCH_MIN_PRICE = 3.0

GROUP_STATS_COLUMNS = [
    "brand",
    "category",
    "condition",
    "size",
    "sample_size",
    "avg_price",
    "median_price",
    "min_price",
    "max_price",
    "avg_days_listed",       # for still-active listings: now - first_seen
    "avg_days_to_disappear",  # for confirmed_sold/deleted/likely_sold_or_removed
                              # listings: (sold/disappeared time) - first_seen
]

DEALS_COLUMNS = [
    "listing_id",
    "url",
    "title",
    "brand",
    "category",
    "condition",
    "size",
    "current_price",
    "group_avg_price",
    "pct_below_avg",
    "days_listed",
]


def parse_price(value):
    try:
        return float(str(value).replace(",", "").strip())
    except (ValueError, TypeError):
        return None


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def normalize(value):
    """Lowercase + strip so 'Nike', 'nike ', 'NIKE' all group together.
    Blank/missing values get grouped as 'unknown' rather than silently
    dropped, so partial listings still contribute to some group."""
    value = (value or "").strip().lower()
    return value if value else "unknown"


def passes_price_floor(category, price):
    """False for a "brand_*" (keyword-search) category priced below the
    floor - almost certainly not a genuine item of that brand, just
    something that happened to mention the word. Leaves real category
    browses and anything without a parseable price untouched."""
    if price is None or not category.startswith("brand_"):
        return True
    return price >= BRAND_SEARCH_MIN_PRICE


def load_listings():
    with open(LISTINGS_PATH, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def build_groups(listings):
    """Returns dict keyed by (brand, category, condition, size) -> list of rows."""
    groups = {}
    for row in listings:
        key = (
            normalize(row.get("brand")),
            normalize(row.get("category")),
            normalize(row.get("condition")),
            normalize(row.get("size")),
        )
        groups.setdefault(key, []).append(row)
    return groups


def compute_group_stats(groups, now):
    stats_rows = []
    # group_key -> avg_price, for use when flagging deals
    group_avg_lookup = {}

    for key, rows in groups.items():
        brand, category, condition, size = key

        # Drop likely-junk keyword-search matches (see passes_price_floor)
        # before computing anything, so they can't skew this group's
        # average/median/days stats either.
        rows = [
            r for r in rows
            if passes_price_floor(category, parse_price(r.get("current_price")))
        ]

        # Only currently-active listings represent real, current asking
        # prices. Including confirmed_sold/deleted rows here would mix in
        # stale prices from listings no longer actually available, which
        # gets worse over time as more listings get confirmed gone.
        active_rows = [r for r in rows if r.get("status") == "active"]
        prices = [parse_price(r.get("current_price")) for r in active_rows]
        prices = [p for p in prices if p is not None]

        if len(prices) < MIN_SAMPLE_SIZE:
            continue  # not enough data to trust an average for this group

        days_listed_values = []
        days_to_disappear_values = []

        for r in rows:
            first_seen = parse_dt(r.get("first_seen"))
            if first_seen is None:
                continue

            status = r.get("status")
            if status == "active":
                days_listed_values.append((now - first_seen).days)
            elif status == "confirmed_sold":
                sold_at = parse_dt(r.get("sold_confirmed_at"))
                if sold_at:
                    days_to_disappear_values.append((sold_at - first_seen).days)
            elif status in ("deleted", "likely_sold_or_removed"):
                disappeared = parse_dt(r.get("date_disappeared"))
                if disappeared:
                    days_to_disappear_values.append((disappeared - first_seen).days)

        avg_price = round(mean(prices), 2)
        group_avg_lookup[key] = avg_price

        stats_rows.append(
            {
                "brand": brand,
                "category": category,
                "condition": condition,
                "size": size,
                "sample_size": len(prices),
                "avg_price": avg_price,
                "median_price": round(median(prices), 2),
                "min_price": round(min(prices), 2),
                "max_price": round(max(prices), 2),
                "avg_days_listed": (
                    round(mean(days_listed_values), 1) if days_listed_values else ""
                ),
                "avg_days_to_disappear": (
                    round(mean(days_to_disappear_values), 1)
                    if days_to_disappear_values
                    else ""
                ),
            }
        )

    stats_rows.sort(key=lambda r: r["sample_size"], reverse=True)
    return stats_rows, group_avg_lookup


def find_deals(listings, group_avg_lookup, now):
    deals = []

    for row in listings:
        if row.get("status") != "active":
            continue

        key = (
            normalize(row.get("brand")),
            normalize(row.get("category")),
            normalize(row.get("condition")),
            normalize(row.get("size")),
        )
        group_avg = group_avg_lookup.get(key)
        if group_avg is None:
            continue  # group didn't meet MIN_SAMPLE_SIZE, skip

        price = parse_price(row.get("current_price"))
        if price is None or group_avg <= 0:
            continue

        if not passes_price_floor(key[1], price):
            continue  # likely a keyword-search junk match, not a real deal

        pct_below = (group_avg - price) / group_avg
        if pct_below < DEAL_THRESHOLD_PCT:
            continue

        first_seen = parse_dt(row.get("first_seen"))
        days_listed = (now - first_seen).days if first_seen else ""

        deals.append(
            {
                "listing_id": row.get("listing_id"),
                "url": row.get("url"),
                "title": row.get("title"),
                "brand": row.get("brand"),
                "category": row.get("category"),
                "condition": row.get("condition"),
                "size": row.get("size"),
                "current_price": price,
                "group_avg_price": group_avg,
                "pct_below_avg": round(pct_below * 100, 1),
                "days_listed": days_listed,
            }
        )

    deals.sort(key=lambda d: d["pct_below_avg"], reverse=True)
    return deals


def write_csv(path, columns, rows):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=columns)
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def analyze():
    if not LISTINGS_PATH.exists():
        print(f"No listings.csv found at {LISTINGS_PATH}")
        return

    listings = load_listings()
    now = datetime.now(timezone.utc)

    groups = build_groups(listings)
    group_stats, group_avg_lookup = compute_group_stats(groups, now)
    deals = find_deals(listings, group_avg_lookup, now)

    write_csv(GROUP_STATS_PATH, GROUP_STATS_COLUMNS, group_stats)
    write_csv(DEALS_PATH, DEALS_COLUMNS, deals)

    print(f"Loaded {len(listings)} listings.")
    print(f"Computed stats for {len(group_stats)} groups (min sample size {MIN_SAMPLE_SIZE}).")
    print(f"Flagged {len(deals)} active listings as deals ({int(DEAL_THRESHOLD_PCT*100)}%+ below group average).")
    print(f"Wrote {GROUP_STATS_PATH} and {DEALS_PATH}.")


if __name__ == "__main__":
    analyze()
