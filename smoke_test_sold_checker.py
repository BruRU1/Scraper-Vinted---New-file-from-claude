"""
smoke_test_sold_checker.py

One-off, read-only validation for the speed/correctness changes to
sold_checker.py (adaptive wait instead of a flat sleep, resource
blocking, separating "confirmed gone" from "error"). Visits a small,
fixed set of REAL listing URLs with known expected outcomes - a few
already confirmed_sold, a few confirmed deleted, a few currently active -
and reports what the updated check_listing_page() says, plus how long
each visit took.

Doesn't touch the database, doesn't write any batch/results files,
changes nothing. Only exists to look at real output before trusting
this logic inside the actual pipeline (check_batch.py).

Run manually:  python smoke_test_sold_checker.py
Or via the "Smoke test sold checker" workflow (workflow_dispatch) - no
DATABASE_URL needed at all, since this never touches the database.
"""

import time

from playwright.sync_api import sync_playwright

from sold_checker import USER_AGENT, block_unneeded_resources, check_listing_page

# A handful of real URLs with a known expected outcome, pulled from the
# archive (confirmed_sold/deleted) and the last run's active listings -
# not a rigorous test suite, just enough to eyeball that classification
# still behaves the same and see the real timing.
TEST_CASES = [
    ("expected sold", "https://www.vinted.co.uk/items/9963714078-superdry-top?referrer=catalog"),
    ("expected sold", "https://www.vinted.co.uk/items/9882950312-ladies-long-sleeved-patterned-blouse-ms-size-12?referrer=catalog"),
    ("expected sold", "https://www.vinted.co.uk/items/9957518531-carhartt-mens-sage-green-shirt-size-s?referrer=catalog"),
    ("expected gone (deleted)", "https://www.vinted.co.uk/items/9806685426-mens-hoodie?referrer=catalog"),
    ("expected gone (deleted)", "https://www.vinted.co.uk/items/9832127607-supreme-wallet?referrer=catalog"),
    ("expected active", "https://www.vinted.co.uk/items/10521869735-mens-adidas-shorts?referrer=catalog"),
    ("expected active", "https://www.vinted.co.uk/items/10515990337-nike-slides-10?referrer=catalog"),
    ("expected active", "https://www.vinted.co.uk/items/10527730955-nike-sliders?referrer=catalog"),
]


def main():
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(user_agent=USER_AGENT)
        page = context.new_page()
        block_unneeded_resources(page)

        print(f"{'expectation':<24} {'outcome':<10} {'price':<8} {'seconds':<8} url")
        print("-" * 100)
        for expectation, url in TEST_CASES:
            start = time.monotonic()
            outcome, sold_price = check_listing_page(page, url)
            elapsed = time.monotonic() - start
            match = "" if outcome in expectation else "  <-- unexpected, check this one"
            print(f"{expectation:<24} {outcome:<10} {sold_price or '-':<8} {elapsed:<8.2f} {url}{match}")

        context.close()
        browser.close()


if __name__ == "__main__":
    main()
