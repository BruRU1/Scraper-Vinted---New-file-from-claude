"""
resale_score.py

Turns the group-level analytics from analyze.py into a ranked shortlist of
currently ACTIVE listings worth buying to resell: priced well below what
comparable items go for, in a group that also historically sells quickly
(so the money isn't tied up for months waiting for a buyer).

A listing only makes the list if:
  - its group has enough sample size to trust the average (same rule
    analyze.py uses for deals.csv)
  - it's from a genuine Vinted category browse, not a "brand_*"
    keyword-search category - those catch real items but also unrelated
    junk that happens to mention the brand name, which is too risky when
    the output is meant to guide an actual purchase (deals.csv can afford
    to be softer about this; this can't)
  - the listing price is at least MIN_PRICE_GBP - very cheap listings are
    usually "open to offers" placeholders, not real buyable prices
  - the discount is at least DEAL_THRESHOLD_PCT below the group average
  - the absolute estimated profit is at least MIN_PROFIT_GBP - a 20%
    discount on a #2 keyring is still only 40p, not worth listing/shipping

resale_score ranks the shortlist: mostly how underpriced the item is,
with a smaller boost for being in a group that sells fast historically.
It is a relative score for sorting, not a currency amount.

Caveats worth knowing about (kept intentionally simple for v1):
  - group_avg_price is what similar items are listed FOR, not proven sale
    prices - Vinted listings often sell for less than asking after offers.
  - no shipping cost, listing fees, or your own time is factored in.
  - "sells fast" is measured per (brand, category, condition, size) group,
    using whatever mix of confirmed_sold/deleted/likely_sold_or_removed
    history that group has - a thin history makes it a rougher estimate.

Must run after analyze.py, since it reuses analyze.py's grouping logic
against the same database rows analyze.py just read. Output:
data/resale_opportunities.csv (small and derived, still committed to
git), sorted by resale_score descending.

Run manually:  DATABASE_URL=postgresql://... python resale_score.py
"""

from datetime import datetime, timezone

from analyze import (
    DATA_DIR,
    build_groups,
    canonical_size,
    compute_group_stats,
    load_listings,
    normalize,
    parse_price,
    write_csv,
)

RESALE_PATH = DATA_DIR / "resale_opportunities.csv"

# How far below the group average a listing needs to be to count at all -
# same bar deals.csv uses, so "resale opportunity" and "deal" mean the
# same threshold, just with resale-specific extra filters on top.
DEAL_THRESHOLD_PCT = 0.20

# Minimum estimated profit in GBP (group_avg_price - current_price).
# Filters out items that clear the percentage bar but are too cheap in
# absolute terms to be worth the hassle of reselling.
MIN_PROFIT_GBP = 5.0

# Minimum listing price in GBP, regardless of implied discount/profit.
# Vinted sellers commonly list at £1-2 as an "open to offers" placeholder,
# or as part of a bundle-only deal - not a price you can actually buy at.
# A "90% below average" listing at that price is usually this, not a real
# opportunity, and shipping alone would exceed the item's value anyway.
MIN_PRICE_GBP = 4.0

RESALE_COLUMNS = [
    "listing_id",
    "url",
    "title",
    "brand",
    "category",
    "condition",
    "size",
    "current_price",
    "group_avg_price",
    "estimated_profit",
    "pct_below_avg",
    "group_avg_days_to_disappear",
    "group_sample_size",
    "resale_score",
]


def parse_dt(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def compute_sell_speed_scores(group_stats):
    """Maps each group key -> a 0-1 "sells fast" score, relative to every
    other group that has a known avg_days_to_disappear (fastest-selling
    group scores near 1.0, slowest near 0.0). Groups with no disappear
    history at all get a neutral 0.5 instead of being penalised for data
    they simply don't have yet."""
    known = [row for row in group_stats if row["avg_days_to_disappear"] != ""]
    if not known:
        return {}

    days_values = [float(row["avg_days_to_disappear"]) for row in known]
    fastest, slowest = min(days_values), max(days_values)
    spread = (slowest - fastest) or 1.0

    scores = {}
    for row in known:
        key = (row["brand"], row["category"], row["condition"], row["size"])
        days = float(row["avg_days_to_disappear"])
        scores[key] = 1 - ((days - fastest) / spread)
    return scores


def compute_resale_opportunities(listings, group_stats, group_avg_lookup):
    sell_speed = compute_sell_speed_scores(group_stats)
    stats_by_key = {
        (row["brand"], row["category"], row["condition"], row["size"]): row
        for row in group_stats
    }
    opportunities = []

    for row in listings:
        if row.get("status") != "active":
            continue

        key = (
            normalize(row.get("brand")),
            normalize(row.get("category")),
            normalize(row.get("condition")),
            normalize(canonical_size(row.get("size"))),
        )
        if key[1].startswith("brand_"):
            # "brand_*" categories are keyword searches (search_text=Supreme
            # etc.), not Vinted's own curated category pages - they catch
            # real items but also unrelated junk that happens to mention
            # the word (stickers, keyrings, novels...) at a price nowhere
            # near what the group's real items go for, which inflates the
            # group average and makes ordinary junk look like a huge deal.
            # deals.csv tolerates that with a soft price floor; a tool
            # meant to spend real money on a purchase can't risk it, so it
            # only draws from genuine category browses.
            continue

        group_avg = group_avg_lookup.get(key)
        if group_avg is None or group_avg <= 0:
            continue  # group didn't meet MIN_SAMPLE_SIZE, skip

        price = parse_price(row.get("current_price"))
        if price is None or price < MIN_PRICE_GBP:
            continue

        pct_below = (group_avg - price) / group_avg
        if pct_below < DEAL_THRESHOLD_PCT:
            continue

        profit = round(group_avg - price, 2)
        if profit < MIN_PROFIT_GBP:
            continue

        speed_score = sell_speed.get(key, 0.5)
        # Weighted toward how underpriced the item is, with a smaller
        # boost for the group historically selling fast - rewards items
        # that are both cheap AND likely to flip quickly.
        resale_score = round((pct_below * 0.7 + speed_score * 0.3) * 100, 1)

        group_stat = stats_by_key.get(key)

        opportunities.append(
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
                "estimated_profit": profit,
                "pct_below_avg": round(pct_below * 100, 1),
                "group_avg_days_to_disappear": (
                    group_stat["avg_days_to_disappear"] if group_stat else ""
                ),
                "group_sample_size": group_stat["sample_size"] if group_stat else "",
                "resale_score": resale_score,
            }
        )

    opportunities.sort(key=lambda o: o["resale_score"], reverse=True)
    return opportunities


def main():
    listings = load_listings()
    now = datetime.now(timezone.utc)

    groups = build_groups(listings)
    group_stats, group_avg_lookup = compute_group_stats(groups, now)
    opportunities = compute_resale_opportunities(listings, group_stats, group_avg_lookup)

    write_csv(RESALE_PATH, RESALE_COLUMNS, opportunities)

    print(f"Loaded {len(listings)} listings.")
    print(
        f"Flagged {len(opportunities)} resale opportunities "
        f"({int(DEAL_THRESHOLD_PCT*100)}%+ below group average, "
        f"min profit £{MIN_PROFIT_GBP:.2f}, min price £{MIN_PRICE_GBP:.2f})."
    )
    print(f"Wrote {RESALE_PATH}.")


if __name__ == "__main__":
    main()
