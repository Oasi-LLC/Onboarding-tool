#!/usr/bin/env python3
"""
Sync a property's raw booking data from the master Google Sheet into the
local cache (data/.cache/sheets/), replacing the manual "download the tab
as CSV and drop it into data/<PROPERTY>/" step.

This does NOT run ingestion — it only refreshes the cache. run_ingestion.py
picks the cache up automatically for properties with a google_sheets
data_source (see README: Google Sheets data source).

Usage (run from project root):
  python scripts/sync_sheets.py --property <property_id>
  python scripts/sync_sheets.py --all
  python scripts/sync_sheets.py --property <property_id> --force   # ignore daily staleness check

Example:
  python scripts/sync_sheets.py --property lafave_zion
"""

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src import sheets_sync

# Properties currently wired to the master Google Sheet. Keep in sync with
# which config/properties/<id>.yaml files declare a google_sheets data_source.
ALL_SHEET_PROPERTIES = ["lafave_zion", "flohom", "atx", "wmb", "fbg", "spoon_mountain"]


def _sync_one(property_id: str, *, data_root: str, force: bool) -> bool:
    if not force:
        stale, reason = sheets_sync.needs_sync(property_id, data_root=data_root)
        if not stale:
            print(f"{property_id}: skipped — {reason} (use --force to re-sync anyway)")
            return True
    try:
        results = sheets_sync.sync_property(property_id, data_root=data_root)
    except Exception as exc:
        print(f"{property_id}: ERROR — {exc}", file=sys.stderr)
        return False
    for result in results:
        print(f"{property_id}: synced '{result.tab}' -> {result.cache_path} ({result.row_count} rows)")
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--property", help="Property id to sync (e.g. lafave_zion)")
    parser.add_argument("--all", action="store_true", help="Sync every property with a google_sheets data_source")
    parser.add_argument("--data-root", default=str(PROJECT_ROOT / "data"), help="Data root directory (default: data)")
    parser.add_argument("--force", action="store_true", help="Sync even if already synced today")
    args = parser.parse_args()

    if not args.property and not args.all:
        parser.print_help()
        return 2

    property_ids = ALL_SHEET_PROPERTIES if args.all else [args.property]
    ok = True
    for property_id in property_ids:
        ok = _sync_one(property_id, data_root=args.data_root, force=args.force) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
