"""
resale_score.py

Turns the group-level analytics from analyze.py into a ranked shortlist of
currently ACTIVE listings worth buying to resell: priced well below what
comparable items actually go for, in a group that also historically sells
quickly (so the money isn't tied up for months waiting for a buyer).

The benchmark a listing is compared against - "what does this actually
sell for" - is chosen per group, in priority order:

  1. SOLD PRICE: the average of real confirmed_sold prices for the same
     (brand, category, condition, size) group (analyze.py's
     sold_benchmark_lookup - combines still-live confirmed_sold rows and
     everything archive_resolved.py has already archived). This is what
     items in this exact group actually sold for, not what someone is
     hoping to get - used whenever at least MIN_SOLD_FOR_TRUST sales
     exist.
  2. ASKING PRICE fallback: when sold data for the group is weak (fewer
     than MIN_SOLD_FOR_TRUST confirmed sales) but there's a reliable
     asking-price average from other active listings (analyze.py's
     group_avg_lookup, gated by MIN_SAMPLE_SIZE there), use that instead.
  3. If sold data is weak AND there's no reliable asking average either,
     still use whatever sold data exists (even a single sale) rather than
     skip the listing outright - a real sale beats no signal at all.

Every row's benchmark_source ("sold"/"asking") and sold_sample_size are
written to the output so a thin/unreliable estimate is visible, not
hidden - and resale_score itself is scaled down for lower-confidence rows
(see CONFIDENCE_WEIGHT) so well-supported deals rank above shakier ones
at the same discount %.

A listing only makes the list if:
  - a benchmark price is available at all (see above)
  - it's from a genuine Vinted category browse, not a "brand_*"
    keyword-search category - those catch real items but also unrelated
    junk that happens to mention the brand name, which is too risky when
    the output is meant to guide an actual purchase (deals.csv can afford
    to be softer about this; this can't)
  - the listing price is at least MIN_PRICE_GBP - very cheap listings are
    usually "open to offers" placeholders, not real buyable prices
  - the discount is at least DEAL_THRESHOLD_PCT below the benchmark
  - the absolute estimated profit is at least MIN_PROFIT_GBP - a 20%
    discount on a #2 keyring is still only 40p, not worth listing/shipping

resale_score ranks the shortlist: mostly how underpriced the item is vs.
its benchmark, with a smaller boost for being in a group that sells fast
historically, scaled down for lower-confidence benchmarks. It is a
relative score for sorting, not a currency amount.

Caveats worth knowing about:
  - No fees, shipping cost, or your own time is factored into profit -
    as a seller there are no Vinted fees, only the buyer incurs them, and
    those aren't relevant to this dashboard's own purchase decision.
  - A "sold"-sourced benchmark with a small sold_sample_size is still a
    real sale price, just a less certain average - check confidence/
    sold_sample_size before trusting a big number off 1-2 sales.
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
    compute_sold_benchmark_lookup,
    load_archived_sold_prices,
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

# How many confirmed sales a group needs before its sold-price average is
# trusted over an asking-price fallback. Below this, sold data is "weak" -
# fall back to the asking-price average when one's available (see
# compute_resale_opportunities).
MIN_SOLD_FOR_TRUST = 5

# Above this many confirmed sales, a sold-based benchmark is "high"
# confidence rather than just "medium" - see classify_confidence().
MIN_SOLD_FOR_HIGH_CONFIDENCE = 10

# Multiplies resale_score so a well-supported deal outranks an equally
# discounted one resting on thinner evidence, instead of both looking
# identical just because the discount % happens to match.
CONFIDENCE_WEIGHT = {"high": 1.0, "medium": 0.85, "low": 0.65}

RESALE_COLUMNS = [
    "listing_id",
    "url",
    "title",
    "brand",
    "category",
    "condition",
    "size",
    "current_price",
    "benchmark_price",
    "estimated_profit",
    "pct_below_avg",
    "benchmark_source",
    "sold_sample_size",
    "confidence",
    "asking_avg_price",
    "group_avg_days_to_disappear",
    "group_sample_size",
    "resale_score",
]


def classify_confidence(benchmark_source, sold_sample_size):
    """How much to trust this row's benchmark_price. A sold-sourced
    benchmark earns "high"/"medium" based on how many real sales back it;
    an asking-price fallback (or a sold benchmark too thin to pass
    MIN_SOLD_FOR_TRUST) is always "low" - it's either not a proven sale
    price at all, or is one resting on very few sales."""
    if benchmark_source == "sold" and sold_sample_size >= MIN_SOLD_FOR_HIGH_CONFIDENCE:
        return "high"
    if benchmark_source == "sold" and sold_sample_size >= MIN_SOLD_FOR_TRUST:
        return "medium"
    return "low"


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


def compute_resale_opportunities(listings, group_stats, group_avg_lookup, sold_benchmark_lookup):
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

        sold_avg, sold_n = sold_benchmark_lookup.get(key, (None, 0))
        asking_avg = group_avg_lookup.get(key)

        # Sold price is the primary benchmark whenever there's enough of
        # it to trust; an asking-price average only steps in when sold
        # data is weak (or uses the weak sold data anyway if that's all
        # there is) - see this module's docstring for the full priority
        # order.
        if sold_avg is not None and sold_n >= MIN_SOLD_FOR_TRUST:
            benchmark_price, benchmark_source = sold_avg, "sold"
        elif asking_avg is not None and asking_avg > 0:
            benchmark_price, benchmark_source = asking_avg, "asking"
        elif sold_avg is not None:
            benchmark_price, benchmark_source = sold_avg, "sold"
        else:
            continue  # no usable benchmark at all for this group

        price = parse_price(row.get("current_price"))
        if price is None or price < MIN_PRICE_GBP:
            continue

        pct_below = (benchmark_price - price) / benchmark_price
        if pct_below < DEAL_THRESHOLD_PCT:
            continue

        profit = round(benchmark_price - price, 2)
        if profit < MIN_PROFIT_GBP:
            continue

        confidence = classify_confidence(benchmark_source, sold_n)
        speed_score = sell_speed.get(key, 0.5)
        # Weighted toward how underpriced the item is, with a smaller
        # boost for the group historically selling fast - rewards items
        # that are both cheap AND likely to flip quickly. Scaled down for
        # lower-confidence benchmarks so a well-supported deal outranks
        # an equally-discounted but shakier one.
        resale_score = round(
            (pct_below * 0.7 + speed_score * 0.3) * 100 * CONFIDENCE_WEIGHT[confidence], 1
        )

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
                "benchmark_price": benchmark_price,
                "estimated_profit": profit,
                "pct_below_avg": round(pct_below * 100, 1),
                "benchmark_source": benchmark_source,
                "sold_sample_size": sold_n,
                "confidence": confidence,
                "asking_avg_price": asking_avg if asking_avg else "",
                "group_avg_days_to_disappear": (
                    group_stat["avg_days_to_disappear"] if group_stat else ""
                ),
                "group_sample_size": group_stat["sample_size"] if group_stat else "",
                "resale_score": resale_score,
            }
        )

    opportunities.sort(key=lambda o: o["resale_score"], reverse=True)
    return opportunities


def main(listings=None, group_stats=None, group_avg_lookup=None, sold_benchmark_lookup=None):
    """Pass pre-fetched/pre-computed `listings`/`group_stats`/
    `group_avg_lookup`/`sold_benchmark_lookup` (e.g. from
    analyze.analyze()'s return value, via run_analytics.py) to reuse
    analyze.py's already-fetched data instead of independently
    re-fetching the whole table and recomputing the same groups from
    scratch."""
    if listings is None:
        listings = load_listings()
    if group_stats is None or group_avg_lookup is None or sold_benchmark_lookup is None:
        groups = build_groups(listings)
        archived_sold_prices = load_archived_sold_prices()
        sold_benchmark_lookup = compute_sold_benchmark_lookup(groups, archived_sold_prices)
        group_stats, group_avg_lookup = compute_group_stats(
            groups, datetime.now(timezone.utc), sold_benchmark_lookup=sold_benchmark_lookup
        )

    opportunities = compute_resale_opportunities(
        listings, group_stats, group_avg_lookup, sold_benchmark_lookup
    )

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
