"""
merge_batches.py

Run AFTER all parallel check_batch.py matrix jobs finish. Reads every
data/batches/results_*.csv file and applies those updates onto the
listings table, matched by listing_id.

Unlike the old CSV version, this only writes the handful of columns each
status actually changes, and only for the listing_ids a batch actually
checked - it never has to touch (or even read) the other ~99% of rows
nothing happened to this run.

Run manually:  DATABASE_URL=postgresql://... python merge_batches.py
"""

import csv
from pathlib import Path

import listings_db

ROOT = Path(__file__).parent
DATA_DIR = ROOT / "data"
BATCHES_DIR = DATA_DIR / "batches"


def main():
    result_files = sorted(BATCHES_DIR.glob("results_*.csv"))
    print(f"Found {len(result_files)} result files to merge.")

    results_by_status = {"confirmed_sold": [], "active": [], "deleted": []}
    total_results = 0

    for result_file in result_files:
        with open(result_file, "r", newline="", encoding="utf-8") as f:
            results = list(csv.DictReader(f))

        for result in results:
            status = result.get("status")
            if status in results_by_status:
                results_by_status[status].append(result)

        total_results += len(results)
        print(f"  {result_file.name}: {len(results)} results")

    total_applied = listings_db.apply_batch_results(results_by_status)

    print(f"\nDone. Applied {total_applied} updates to the database "
          f"(of {total_results} results read).")


if __name__ == "__main__":
    main()
