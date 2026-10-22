"""
validate_resale_score.py

Checks whether resale_score.py's scoring actually predicts good resale
outcomes, using the hundreds of thousands of listings already archived
as confirmed_sold or deleted (see archive_resolved.py) as ground truth -
something there simply wasn't enough history to do until now.

For every archived listing, retrospectively recomputes what
resale_score.py would have flagged it as: the same formula, the same
eligibility filters (real category, minimum price, minimum discount,
minimum profit), using group stats computed from the archive itself.
That's the best stand-in available for "what a typical price was" -
nothing recorded the live group average at the exact moment each listing
was actually seen, so this uses each group's average price across its
whole archived history instead. Worth knowing before trusting the
output: that's a rougher substitute than what resale_score.py uses live
(current active-listing prices), so treat this as directionally
informative, not exact.

Then buckets the eligible listings by resale_score, using the exact same
tier boundaries the dashboard shows (Excellent 75+, Good 60-74, Fair
below 60 - see docs/index.html's scoreTier), and reports per tier:
  - how many listings landed in it
  - what fraction actually ended up confirmed_sold (vs just deleted,
    i.e. presumably never sold)
  - the median days from first seen to being confirmed sold, among the
    ones that did sell

Also reports the same two numbers for the general eligible population
(same category/price filters, but not flagged as a deal at all) as a
baseline - if resale_score is adding real value, every tier above it
should sell more often and/or faster than that baseline, and Excellent
should beat Good which should beat Fair.

Run manually: python validate_resale_score.py
Reads data/listings_resolved_archive.csv.gz directly - no database
connection needed or listings touched, this is purely retrospective.
"""

import csv
import gzip
from pathlib import Path
from statistics import mean, median

from analyze import (
    DATA_DIR,
    MIN_SAMPLE_SIZE,
    canonical_size,
    normalize,
    parse_dt,
    parse_price,
    passes_price_floor,
)
from resale_score import (
    DEAL_THRESHOLD_PCT,
    MIN_PRICE_GBP,
    MIN_PROFIT_GBP,
    compute_sell_speed_scores,
)

ARCHIVE_PATH = DATA_DIR / "listings_resolved_archive.csv.gz"

TIERS = [
    ("Excellent (75+)", 75, 1000),
    ("Good (60-74)", 60, 75),
    ("Fair (<60)", 0, 60),
]


def load_archive():
    if not ARCHIVE_PATH.exists():
        raise SystemExit(f"{ARCHIVE_PATH} not found - nothing archived yet to validate against.")
    with gzip.open(ARCHIVE_PATH, "rt", newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def group_key(row):
    return (
        normalize(row.get("brand")),
        normalize(row.get("category")),
        normalize(row.get("condition")),
        normalize(canonical_size(row.get("size"))),
    )


def days_to_resolve(row):
    """Days from first_seen to whatever resolved it - sale confirmation
    for confirmed_sold, last-seen-before-removal for deleted. None if the
    needed timestamps aren't both parseable."""
    first_seen = parse_dt(row.get("first_seen"))
    if first_seen is None:
        return None
    if row.get("status") == "confirmed_sold":
        resolved = parse_dt(row.get("sold_confirmed_at"))
    else:
        resolved = parse_dt(row.get("date_disappeared"))
    if resolved is None:
        return None
    return (resolved - first_seen).days


def build_group_stats(eligible_rows):
    """Same shape/logic as analyze.compute_group_stats, but computed from
    this archive snapshot instead of a live database fetch - avg_price is
    the group's mean archived price (not "current active asking price",
    since nothing here is active), avg_days_to_disappear is the mean of
    each row's own days_to_resolve()."""
    groups = {}
    for row in eligible_rows:
        groups.setdefault(group_key(row), []).append(row)

    stats_rows = []
    group_avg_lookup = {}

    for key, rows in groups.items():
        brand, category, condition, size = key
        prices = [parse_price(r.get("current_price")) for r in rows]
        prices = [p for p in prices if p is not None]
        if len(prices) < MIN_SAMPLE_SIZE:
            continue

        avg_price = round(mean(prices), 2)
        group_avg_lookup[key] = avg_price

        disappear_values = [d for d in (days_to_resolve(r) for r in rows) if d is not None]

        stats_rows.append(
            {
                "brand": brand,
                "category": category,
                "condition": condition,
                "size": size,
                "sample_size": len(prices),
                "avg_days_to_disappear": (
                    round(mean(disappear_values), 1) if disappear_values else ""
                ),
            }
        )

    return stats_rows, group_avg_lookup


def tier_for(score):
    for name, low, high in TIERS:
        if low <= score < high:
            return name
    return TIERS[-1][0]


def summarize(label, rows):
    if not rows:
        print(f"  {label:<20} (no listings)")
        return
    sold = [r for r in rows if r["status"] == "confirmed_sold"]
    sold_rate = 100 * len(sold) / len(rows)
    sold_days = [d for d in (days_to_resolve(r) for r in sold) if d is not None]
    days_str = f"{median(sold_days):.0f} days" if sold_days else "n/a"
    print(f"  {label:<20} {len(rows):>7,} listings   {sold_rate:5.1f}% confirmed sold   median {days_str} to sell")


def main():
    print("Loading archive...")
    archive = load_archive()
    print(f"Loaded {len(archive):,} archived (confirmed_sold/deleted) listings.")

    eligible = [
        r for r in archive
        if passes_price_floor(normalize(r.get("category")), parse_price(r.get("current_price")))
        and not normalize(r.get("category")).startswith("brand_")
        and (parse_price(r.get("current_price")) or 0) >= MIN_PRICE_GBP
    ]
    print(f"{len(eligible):,} are in real categories at >= £{MIN_PRICE_GBP:.2f} "
          f"(resale_score.py's basic eligibility bar).\n")

    group_stats, group_avg_lookup = build_group_stats(eligible)
    sell_speed = compute_sell_speed_scores(group_stats)
    print(f"Computed trustworthy group stats for {len(group_stats):,} groups "
          f"(min sample size {MIN_SAMPLE_SIZE}).\n")

    flagged_by_tier = {name: [] for name, _, _ in TIERS}

    for row in eligible:
        key = group_key(row)
        group_avg = group_avg_lookup.get(key)
        if group_avg is None or group_avg <= 0:
            continue

        price = parse_price(row.get("current_price"))
        pct_below = (group_avg - price) / group_avg
        if pct_below < DEAL_THRESHOLD_PCT:
            continue

        profit = group_avg - price
        if profit < MIN_PROFIT_GBP:
            continue

        speed_score = sell_speed.get(key, 0.5)
        resale_score = round((pct_below * 0.7 + speed_score * 0.3) * 100, 1)

        flagged_by_tier[tier_for(resale_score)].append(row)

    total_flagged = sum(len(rows) for rows in flagged_by_tier.values())
    print(f"{total_flagged:,} of those {len(eligible):,} would have been flagged as resale "
          f"opportunities ({int(DEAL_THRESHOLD_PCT*100)}%+ below group average, "
          f"min profit £{MIN_PROFIT_GBP:.2f}).\n")

    print("Baseline (not flagged as a deal at all):")
    summarize("All eligible", eligible)

    print("\nFlagged resale opportunities, by resale_score tier:")
    for name, _, _ in TIERS:
        summarize(name, flagged_by_tier[name])

    print(
        "\nIf resale_score is working, Excellent should out-sell Good, Good should "
        "out-sell Fair, and all three should beat the baseline above - both in "
        "sold-rate and in days-to-sell. If the tiers look flat or out of order, "
        "the scoring formula isn't adding predictive value yet and is worth "
        "revisiting before relying on it for real purchases."
    )


if __name__ == "__main__":
    main()
