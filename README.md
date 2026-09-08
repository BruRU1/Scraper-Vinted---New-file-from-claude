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
3. Set up the database (see below) - the scraper's actual listing data
   lives in Postgres now, not in a CSV in this repo.
4. Go to the **Actions** tab → you should see "Vinted Scraper" listed.
   Click into it → **Run workflow** to trigger it manually and confirm it
   works, rather than waiting up to 6 hours for the first scheduled run.

## Database setup (one-time)

The full listing history (every listing ever seen, and its price
history) is stored in a Postgres database instead of a CSV file in this
repo - CSVs committed to git don't scale forever, and GitHub hard-rejects
any pushed file over 100MB. [Supabase](https://supabase.com) gives you a
free Postgres database with no server to manage.

1. Create a free account at [supabase.com](https://supabase.com) and a
   new project.
2. In the project, go to **Project Settings → Database → Connection
   string**, and copy the **URI** (starts with `postgresql://...`).
3. In this GitHub repo, go to **Settings → Secrets and variables →
   Actions → New repository secret**. Name it `DATABASE_URL`, paste the
   connection string as the value, and save.
4. Go to the **Actions** tab → **Migrate to database** → **Run workflow**.
   Run this exactly once - it moves everything currently in
   `data/listings.csv`, `data/listings_archive.csv`, and
   `data/price_history.csv` into the database, creating the tables for
   you. It's safe to re-run if it fails partway through.

After that, every scraper run reads and writes the database directly -
`data/listings.csv` and friends are no longer touched by any script, and
can be deleted from the repo once you've confirmed everything looks
right (they're kept as a historical snapshot in the meantime). The
dashboard and `data/group_stats.csv`/`data/deals.csv`/
`data/resale_opportunities.csv` are unaffected - those stay small and
keep living in git as before.

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
