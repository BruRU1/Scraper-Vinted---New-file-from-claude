"""
check_batch.py

Checks a single batch of listings (produced by split_batches.py) and
writes the results to its own results CSV. Designed to be run as one leg
of a GitHub Actions matrix job, so N batches run in parallel.

On top of that job-level parallelism, this version also runs multiple
Playwright pages CONCURRENTLY within a single batch job (CONCURRENT_PAGES
of them), instead of checking listings one at a time. Each page works
through its own slice of the batch independently. This multiplies
throughput within each already-parallel batch job, rather than needing
more GitHub Actions jobs (which are capped) to go faster.

Safety measures kept from before:
  - MAX_PER_BATCH caps how many listings this batch will check, even if
    more were assigned to it. Anything over the cap is picked up by a
    future run instead (within the recency window in split_batches.py).
  - Results are written incrementally, not only at the very end, so a
    timeout partway through still keeps whatever progress was made.

Takes the batch number as a command-line argument:

    python check_batch.py 0

Reads:   data/batches/batch_{N}.csv        (listing_id, url pairs to check)
Writes:  data/batches/results_{N}.csv      (full updated row data for each)
"""

import csv
import random
import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

from sold_checker import check_listing_page, USER_AGENT
from playwright.sync_api import sync_playwright

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
BATCHES_DIR = DATA_DIR / "batches"

# Never check more than this many listings in one batch job, even if more
# were assigned. Keeps each job comfortably inside its timeout.
MAX_PER_BATCH = 1000

# How many Playwright pages run concurrently within this one batch job.
# Each page works through its own slice of the batch independently.
# Higher = faster, but more simultaneous requests to Vinted from this one
# job - start conservative and raise it once you've confirmed no
# rate-limiting issues show up.
CONCURRENT_PAGES = 5

# Write results to disk every N listings checked (across all pages
# combined), not just once at the very end.
SAVE_EVERY = 50

RESULT_COLUMNS = ["listing_id", "status", "sold_price", "sold_confirmed_at",
                  "consecutive_misses", "date_disappeared", "last_seen"]

# Shared state across worker threads - protected by results_lock.
results = []
results_lock = threading.Lock()
checked_count = 0


def build_result(listing_id, outcome, sold_price, now_iso):
    if outcome == "sold":
        return {
            "listing_id": listing_id,
            "status": "confirmed_sold",
            "sold_price": sold_price or "",
            "sold_confirmed_at": now_iso,
            "consecutive_misses": "",
            "date_disappeared": "",
            "last_seen": "",
        }
    elif outcome == "active":
        return {
            "listing_id": listing_id,
            "status": "active",
            "sold_price": "",
            "sold_confirmed_at": "",
            "consecutive_misses": "0",
            "date_disappeared": "",
            "last_seen": now_iso,
        }
    else:  # gone
        return {
            "listing_id": listing_id,
            "status": "deleted",
            "sold_price": "",
            "sold_confirmed_at": "",
            "consecutive_misses": "",
            "date_disappeared": now_iso,
            "last_seen": "",
        }


def write_results(results_path):
    with results_lock:
        snapshot = list(results)
    with open(results_path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=RESULT_COLUMNS)
        writer.writeheader()
        for row in snapshot:
            writer.writerow(row)


def worker(worker_id, rows, browser, batch_num, total, results_path):
    global checked_count

    context = browser.new_context(user_agent=USER_AGENT)
    page = context.new_page()

    for row in rows:
        url = row["url"]
        listing_id = row["listing_id"]

        outcome, sold_price = check_listing_page(page, url)
        now_iso = datetime.now(timezone.utc).isoformat()
        result = build_result(listing_id, outcome, sold_price, now_iso)

        with results_lock:
            results.append(result)
            global_checked = len(results)

        print(f"  [{batch_num}/p{worker_id}] ({global_checked}/{total}) "
              f"{listing_id} -> {outcome.upper()}"
              + (f" (£{sold_price})" if sold_price else ""))

        if global_checked % SAVE_EVERY == 0:
            write_results(results_path)
            print(f"    (progress saved: {global_checked} so far)")

        time.sleep(random.uniform(2, 5))

    context.close()


def split_for_workers(items, n):
    """Divide items into n roughly-equal chunks for the worker pages."""
    k, m = divmod(len(items), n)
    return [items[i * k + min(i, m):(i + 1) * k + min(i + 1, m)] for i in range(n)]


def main():
    if len(sys.argv) < 2:
        print("Usage: python check_batch.py <batch_number>")
        sys.exit(1)

    batch_num = sys.argv[1]
    batch_path = BATCHES_DIR / f"batch_{batch_num}.csv"
    results_path = BATCHES_DIR / f"results_{batch_num}.csv"

    with open(batch_path, "r", newline="", encoding="utf-8") as f:
        batch = list(csv.DictReader(f))

    if len(batch) > MAX_PER_BATCH:
        print(f"Batch {batch_num}: {len(batch)} assigned, capping to "
              f"{MAX_PER_BATCH} (remainder will be picked up next run).")
        batch = batch[:MAX_PER_BATCH]
    else:
        print(f"Batch {batch_num}: {len(batch)} listings to check.")

    if not batch:
        write_results(results_path)
        print(f"Batch {batch_num}: nothing to check.")
        return

    chunks = split_for_workers(batch, CONCURRENT_PAGES)
    total = len(batch)

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)

        threads = []
        for worker_id, chunk in enumerate(chunks):
            if not chunk:
                continue
            t = threading.Thread(
                target=worker,
                args=(worker_id, chunk, browser, batch_num, total, results_path),
            )
            t.start()
            threads.append(t)

        for t in threads:
            t.join()

        browser.close()

    write_results(results_path)
    print(f"Batch {batch_num} done. Wrote {len(results)} results to {results_path}")


if __name__ == "__main__":
    main()
