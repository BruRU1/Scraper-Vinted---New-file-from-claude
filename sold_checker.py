"""
sold_checker.py

Shared logic for visiting an individual Vinted listing page directly and
determining what actually happened to it, rather than just inferring
"likely sold" from it disappearing off category/search pages.

For a given listing URL, visiting the page tells us one of four things:

  1. SOLD    - the page loads and shows Vinted's "Sold" badge
               (<div class="web_ui__Cell__body">Sold</div>).
               We also try to read the sold price from the page.
               status becomes "confirmed_sold".
  2. ACTIVE  - the page loads fine and there's no Sold badge - the listing
               was just buried past the pages we scrape (e.g. past page 10),
               not actually gone. We refresh its data and reactivate it.
               status becomes "active".
  3. GONE    - the page actually 404s. Genuinely removed, but we can't
               confirm *why* (could be sold without the badge, could be
               taken down, could be blocked from us).
               status becomes "deleted" - distinct from the old
               "likely_sold_or_removed" guess, since this one was actually
               visited and confirmed unreachable, not just inferred.
  4. ERROR   - anything else went wrong (network hiccup, a timeout,
               Vinted being slow, an unexpected page structure). This is
               NOT the same as a confirmed 404 - we genuinely don't know
               what happened, so check_batch.py leaves the listing exactly
               as it was (still "likely_sold_or_removed") instead of
               finalizing it as "deleted". A transient failure used to get
               permanently misclassified as deleted here, with no retry -
               this makes it safe to just try again next run instead.

This module doesn't run on its own - check_listing_page() is imported and
driven by check_batch.py, which is run as one leg of a GitHub Actions
matrix job (see split_batches.py -> check_batch.py -> merge_batches.py).
"""

import re

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError

# Resource types this check never needs - it only ever reads text (the
# "Sold" badge and a price), never looks at the rendered page visually.
# Blocking these cuts how much each visit actually has to download,
# which is most of why a single check can take as long as it does.
# scraper.py's own page (a different page/context entirely) doesn't use
# this - it genuinely needs the full page for extract.js.
BLOCKED_RESOURCE_TYPES = {"image", "media", "font", "stylesheet"}


def block_unneeded_resources(page):
    """Call once, right after creating a page that will only be used for
    check_listing_page() - never on scraper.py's own page."""
    page.route(
        "**/*",
        lambda route: route.abort()
        if route.request.resource_type in BLOCKED_RESOURCE_TYPES
        else route.continue_(),
    )

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
      ("error", None)
    """
    try:
        response = page.goto(url, wait_until="domcontentloaded", timeout=20000)

        if response is not None and response.status == 404:
            return "gone", None

        # Wait only as long as actually needed for a Cell body element to
        # show up (same 1.5s ceiling as before), instead of always
        # sleeping the full 1.5s even when the page already has what we
        # need. Finding none within that time isn't an error - it just
        # means this page genuinely has no Sold badge, same as today.
        try:
            page.wait_for_selector(SOLD_BADGE_SELECTOR, timeout=1500)
        except PlaywrightTimeoutError:
            pass

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
        # Anything other than a confirmed 404 above (a timeout, a network
        # blip, Vinted being slow) - we genuinely don't know what
        # happened, so this must NOT be treated the same as a confirmed
        # "gone". See the "ERROR" case in this module's docstring.
        print(f"    ! error visiting {url}: {e}")
        return "error", None
