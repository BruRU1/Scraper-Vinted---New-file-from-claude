"""
sold_checker.py

Shared logic for visiting an individual Vinted listing page directly and
determining what actually happened to it, rather than just inferring
"likely sold" from it disappearing off category/search pages.

For a given listing URL, visiting the page tells us one of three things:

  1. SOLD    - the page loads and shows Vinted's "Sold" badge
               (<div class="web_ui__Cell__body">Sold</div>).
               We also try to read the sold price from the page.
               status becomes "confirmed_sold".
  2. ACTIVE  - the page loads fine and there's no Sold badge - the listing
               was just buried past the pages we scrape (e.g. past page 10),
               not actually gone. We refresh its data and reactivate it.
               status becomes "active".
  3. DELETED - the page 404s / errors entirely. Genuinely removed, but we
               can't confirm *why* (could be sold without the badge, could
               be taken down, could be blocked from us).
               status becomes "deleted" - distinct from the old
               "likely_sold_or_removed" guess, since this one was actually
               visited and confirmed unreachable, not just inferred.

This module doesn't run on its own - check_listing_page() is imported and
driven by check_batch.py, which is run as one leg of a GitHub Actions
matrix job (see split_batches.py -> check_batch.py -> merge_batches.py).
"""

import re

USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

# The exact markup confirmed by checking real sold listings:
#   <div class="web_ui__Cell__body">Sold</div>
# Matched on class + exact text, not class alone, since that class name
# is reused elsewhere on the page for unrelated cell content.
SOLD_BADGE_SELECTOR = "div.web_ui__Cell__body"

# Price text confirmed on sold pages:
#   <p class="web_ui__Text__text web_ui__Text__subtitle web_ui__Text__left">£4.00</p>
PRICE_SELECTOR = "p.web_ui__Text__subtitle"
PRICE_PATTERN = re.compile(r"[\d,]+\.?\d*")


def check_listing_page(page, url):
    """
    Visits a single listing URL and returns one of:
      ("sold", price_str_or_None)
      ("active", None)
      ("gone", None)
    """
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=20000)

        if response is not None and response.status == 404:
            return "gone", None

        page.wait_for_timeout(1500)

        # Look for any Cell body element whose text is exactly "Sold".
        cell_bodies = page.query_selector_all(SOLD_BADGE_SELECTOR)
        for el in cell_bodies:
            text = (el.inner_text() or "").strip()
            if text.lower() == "sold":
                sold_price = None
                price_els = page.query_selector_all(PRICE_SELECTOR)
                for price_el in price_els:
                    price_text = (price_el.inner_text() or "").strip()
                    match = PRICE_PATTERN.search(price_text)
                    if match:
                        sold_price = match.group().replace(",", "")
                        break
                return "sold", sold_price

        return "active", None

    except Exception as e:
        print(f"    ! error visiting {url}: {e}")
        return "gone", None
