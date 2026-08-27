# Vinted Scraper

Automated version of the Resale Data Collector Chrome extension. Runs
headless via Playwright, on a schedule, with no browser or manual click
needed — GitHub Actions triggers it every 6 hours.

## What's in this folder

- `categories.json` — the 7 categories and page count to scrape. Edit this
  to add/remove categories or change how many pages per category.
- `extract.js` — the actual data-extraction logic, ported directly from the
  extension's `selectors.js` + `content.js`. Same selectors, same fallback
  behaviour, same Vinted title-attribute parser. If Vinted changes their
  page structure and scraping breaks, this is the file to fix — same as
  before, only selectors.js needed editing.
- `scraper.py` — loads the config, opens each category/page URL with
  Playwright, runs `extract.js` on the page, and appends results to
  `data/resale_data.csv`.
- `requirements.txt` — Python dependencies (just Playwright).
- `.github/workflows/scrape.yml` — the automation. Tells GitHub to run
  `scraper.py` every 6 hours and commit the updated CSV back to the repo.
- `data/resale_data.csv` — the growing dataset. Created automatically on
  first run.

## Setup (one-time)

1. Push this folder to a GitHub repo (via GitHub Desktop: Commit → Push).
2. Go to the repo on github.com → **Settings → Actions → General** →
   under "Workflow permissions" select **"Read and write permissions"**.
   This lets the workflow commit the scraped CSV back to the repo — without
   this step, the scrape will run but the commit-and-push step will fail.
3. Go to the **Actions** tab → you should see "Vinted Scraper" listed.
   Click into it → **Run workflow** to trigger it manually and confirm it
   works, rather than waiting up to 6 hours for the first scheduled run.

## Running it locally (optional, for testing)

```
pip install -r requirements.txt
playwright install chromium
python scraper.py
```

## Adjusting frequency

Edit the cron line in `.github/workflows/scrape.yml`:
```
- cron: "0 */6 * * *"   # every 6 hours
```

## Notes

- URLs are built from `categories.json`'s base path + page number only —
  the `search_id`/`time` params from browser-copied URLs are session-
  specific and deliberately dropped; the category path is permanent.
- A random 2-6 second delay runs between each page load to avoid hammering
  Vinted and reduce block/rate-limit risk.
- If a run finds 0 listings across the board, it's almost always a
  selector change on Vinted's side — check `extract.js` against the live
  page structure (inspect element, as described in the original
  selectors.js comments) and update the selector arrays.
