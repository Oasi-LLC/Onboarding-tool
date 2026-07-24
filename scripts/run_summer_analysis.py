#!/usr/bin/env python3
"""
Build summer (Jul/Aug) deep-dive CSVs for a property.

Usage (from project root):
  python scripts/run_summer_analysis.py --property adventure_inn_durango
"""

import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.property_config import load_property_inventory
from src.summer_analysis import build_summer_tables, write_summer_tables


def main() -> None:
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
        print("Usage: python scripts/run_summer_analysis.py --property <property_id>")
        sys.exit(1)

    inv = load_property_inventory(property_id)
    if canonical_path is None:
        canonical_path = PROJECT_ROOT / "output" / property_id / "ingestion" / "canonical.csv"
    if output_dir is None:
        output_dir = PROJECT_ROOT / "output" / property_id / "analysis" / "summer"

    if not canonical_path.exists():
        print(f"Error: canonical not found: {canonical_path}")
        sys.exit(1)

    summer_cfg = inv.get("summer") or {}
    months = summer_cfg.get("months") or [7, 8]
    months = [int(m) for m in months]
    pace_month = int(summer_cfg.get("pace_asof_month", 7))
    pace_day = int(summer_cfg.get("pace_asof_day", 23))
    anchor_year = int(summer_cfg.get("anchor_year", 2025))
    capacity_schedule = inv.get("capacity_schedule") or []
    room_count = int(inv.get("room_count") or 27)

    df = pd.read_csv(canonical_path)
    for col in ("arrival_date", "departure_date", "booking_date"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")

    print(f"Property: {inv.get('property_name', property_id)}")
    print(f"Summer months: {months}; pace as-of {pace_month}/{pace_day}; anchor year {anchor_year}")
    tables = build_summer_tables(
        df,
        summer_months=months,
        pace_asof_month=pace_month,
        pace_asof_day=pace_day,
        anchor_year=anchor_year,
        capacity_schedule=capacity_schedule,
        room_count=room_count,
    )
    write_summer_tables(tables, output_dir)
    for name, frame in tables.items():
        print(f"  Wrote {output_dir / (name + '.csv')} ({len(frame)} rows)")
    print("Done.")


if __name__ == "__main__":
    main()
