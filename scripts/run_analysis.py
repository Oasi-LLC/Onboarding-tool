#!/usr/bin/env python3
"""
Run onboarding analysis from canonical CSV per docs/07-analysis-tables-and-formulas.md.
Uses two full calendar years (current_year - 2, current_year - 1). Writes one CSV per table to output/analysis/.

Usage (run from project root):
  python scripts/run_analysis.py --property <property_id> [--canonical path] [--output-dir path]

Example:
  python scripts/run_analysis.py --property lafave_zion --canonical output/ingestion/canonical.csv --output-dir output/analysis
"""

import sys
from pathlib import Path

import pandas as pd

# Project root = parent of scripts/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.analysis import get_analysis_years, run_analysis
from src.property_config import load_property_inventory

OUTPUT_FILES = [
    "overall_summary.csv",
    "monthly_performance.csv",
    "channel_by_year.csv",
    "channel_summary.csv",
    "channel_by_listing.csv",
    "by_day_of_week.csv",
    "booking_window.csv",
    "adr_by_listing_by_month.csv",
]


def main():
    args = sys.argv[1:]
    property_id = None
    canonical_path = None
    output_dir = None

    i = 0
    while i < len(args):
        if args[i] == "--property" and i + 1 < len(args):
            property_id = args[i + 1]
            i += 2
        elif args[i] == "--canonical" and i + 1 < len(args):
            canonical_path = Path(args[i + 1])
            i += 2
        elif args[i] == "--output-dir" and i + 1 < len(args):
            output_dir = Path(args[i + 1])
            i += 2
        else:
            i += 1

    if not property_id:
        print("Usage: python scripts/run_analysis.py --property <property_id> [--canonical path] [--output-dir path]")
        print("Example: python scripts/run_analysis.py --property lafave_zion")
        sys.exit(1)

    if canonical_path is None:
        canonical_path = PROJECT_ROOT / "output" / property_id / "ingestion" / "canonical.csv"
    if output_dir is None:
        output_dir = PROJECT_ROOT / "output" / property_id / "analysis"

    if not canonical_path.exists():
        print(f"Error: Canonical CSV not found: {canonical_path}")
        print("Run ingestion first: python scripts/run_ingestion.py --property <id> <csv_path>")
        sys.exit(1)

    try:
        inv = load_property_inventory(property_id)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)

    room_count = inv.get("room_count")
    year_1, year_2 = get_analysis_years()
    print(f"Property: {inv.get('property_name', property_id)}")
    print(f"Analysis period: {year_1} and {year_2} (Jan–Dec each)")
    print(f"Room count: {room_count or 'not set (occupancy/RevPAR at property level will be null)'}")
    print(f"Loading: {canonical_path}")

    df = pd.read_csv(canonical_path)
    # Parse date columns so analysis can filter and derive
    for col in ("arrival_date", "departure_date", "booking_date"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    tables = run_analysis(df, room_count=room_count)
    n_rows = len(tables["overall_summary"]) - 1  # exclude PROPERTY row for listing count
    if n_rows == 0:
        print("Warning: No data in analysis period; tables may be empty or only PROPERTY row.")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for name, table_df in tables.items():
        out_file = output_dir / f"{name}.csv"
        table_df.to_csv(out_file, index=False)
        print(f"  Wrote {out_file} ({len(table_df)} rows)")

    print(f"\nDone. All tables written to {output_dir}")


if __name__ == "__main__":
    main()
