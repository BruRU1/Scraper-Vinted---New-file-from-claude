"""
sold_checker.py

Shared logic for visiting an individual Vinted listing page directly and
determining what actually happened to it, rather than just inferring
"likely sold" from it disappearing off category/search pages.

For a given listing URL, visiting the page tells us one of three things:

  1. SOLD    - the page loads and shows Vinted's "Sold" badge
               (<div class="web_ui__Cell__body">Sold</div>).
               We also try to read the sold price from the page.
  2. ACTIVE  - the page loads fine and there's no Sold badge - the listing
               was just buried past the pages we scrape (e.g. past page 10),
               not actually gone. We refresh its data and reactivate it.
  3. GONE    - the page 404s / errors entirely. Genuinely removed, but we
               can't confirm *why* (could be sold without the badge, could
               be taken down, could be blocked from us) - stays as
               likely_sold_or_removed, just now more confidently "not
               reachable" rather than "not seen in a scrape".

This module doesn't run on its own - it's imported by:
  - check_sold_backlog.py   (one-time pass over the existing backlog)
  - check_sold_new.py       (ongoing - only listings newly flagged this run)
"""

import csv
import re
from datetime import datetime, timezone
from pathlib import Path

from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent
EXTRACT_JS_PATH = ROOT / "extract.js"
DATA_DIR = ROOT / "data"
LISTINGS_PATH = DATA_DIR / "listings.csv"

LISTINGS_COLUMNS = [
    "listing_id",
    "url",
    "platform",
    "category",
    "title",
    "current_price",
    "original_price",
    "price_drops",
    "last_price_change",
    "currency",
    "brand",
    "size",
    "condition",
    "imageUrl",
    "first_seen",
    "last_seen",
    "status",
    "date_disappeared",
    "consecutive_misses",
    "sold_price",
    "sold_confirmed_at",
]

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


def load_listings():
    with open(LISTINGS_PATH, "r", newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        return list(reader)


def save_listings(listings):
    with open(LISTINGS_PATH, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=LISTINGS_COLUMNS)
        writer.writeheader()
        for row in listings:
            writer.writerow(row)


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


def process_listings(listing_rows, listings_by_id):
    """
    Visits each listing in listing_rows (a list of row dicts referencing
    listings_by_id), updates listings_by_id in place, and returns counts.
    """
    counts = {"sold": 0, "reactivated": 0, "gone_unconfirmed": 0, "checked": 0}

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()

        for row in listing_rows:
            url = row.get("url")
            listing_id = row.get("listing_id")
            if not url or not listing_id:
                continue

            print(f"  Checking listing {listing_id} -> {url}")
            outcome, sold_price = check_listing_page(page, url)
            counts["checked"] += 1
            now_iso = datetime.now(timezone.utc).isoformat()

            target = listings_by_id.get(listing_id)
            if target is None:
                continue

            if outcome == "sold":
                target["status"] = "confirmed_sold"
                target["sold_price"] = sold_price or ""
                target["sold_confirmed_at"] = now_iso
                counts["sold"] += 1
                print(f"    -> SOLD (price: {sold_price})")

            elif outcome == "active":
                target["status"] = "active"
                target["consecutive_misses"] = 0
                target["date_disappeared"] = ""
                target["last_seen"] = now_iso
                counts["reactivated"] += 1
                print(f"    -> still ACTIVE (was buried, not gone)")

            else:  # gone
                # Stays likely_sold_or_removed - genuinely unreachable but
                # we can't confirm it was actually sold.
                counts["gone_unconfirmed"] += 1
                print(f"    -> GONE (404/error, unconfirmed)")

            import random
            import time
            time.sleep(random.uniform(2, 5))

        browser.close()

    return counts
