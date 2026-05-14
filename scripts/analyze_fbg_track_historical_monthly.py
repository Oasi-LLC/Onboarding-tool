#!/usr/bin/env python3
"""
Build a month-of-year hierarchy from the Track-style historical FBG dashboard CSV.

Uses the same cancel rules as ingestion (reservation_status_is_excluded), arrival-month
attribution, and the same 50/50 revenue + RevPAR rank -> performance_score_1_10 as
build_monthly_performance_combined (12-unit Basse-1 availability proxy).

Default input: data/FBG/Dashboard revenue reporting _ Oasi 2026 - Historical FBG (until 12_31_24).csv

Usage (from repo root):
  python scripts/analyze_fbg_track_historical_monthly.py
  python scripts/analyze_fbg_track_historical_monthly.py --csv path/to/file.csv --output out.csv
  python scripts/analyze_fbg_track_historical_monthly.py --property-filter basse1 --no-pre-2025-cut
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.analysis import build_monthly_performance_combined, prepare_canonical_for_analysis
from src.parser import reservation_status_is_excluded

DEFAULT_HISTORICAL_CSV = (
    PROJECT_ROOT
    / "data"
    / "FBG"
    / "Dashboard revenue reporting _ Oasi 2026 - Historical FBG (until 12_31_24).csv"
)
DEFAULT_OUTPUT = PROJECT_ROOT / "output" / "fbg" / "analysis" / "historical_track_month_hierarchy.csv"


def _load_track_dashboard_to_canonical(
    path: Path,
    *,
    pre_2025_cut: bool,
    property_filter: str,
) -> pd.DataFrame:
    raw = pd.read_csv(path)
    if "Check in Date" not in raw.columns or "REVENUE" not in raw.columns:
        raise ValueError("Expected Track dashboard columns: Check in Date, REVENUE, Status, Property, Nights")
    raw = raw.copy()
    raw["arrival_date"] = pd.to_datetime(raw["Check in Date"], errors="coerce")
    raw = raw.loc[raw["arrival_date"].notna()].copy()
    if pre_2025_cut:
        raw = raw.loc[raw["arrival_date"] < pd.Timestamp("2025-01-01")].copy()

    if property_filter == "basse1":
        raw = raw.loc[raw["Property"].astype(str).isin(["OLD CB", "Onera Fredericksburg"])].copy()
    elif property_filter == "onera_only":
        raw = raw.loc[raw["Property"].astype(str) == "Onera Fredericksburg"].copy()
    elif property_filter == "all":
        pass
    else:
        raise ValueError("property_filter must be basse1 | onera_only | all")

    if "Status" in raw.columns:
        raw = raw.loc[~raw["Status"].astype(str).map(reservation_status_is_excluded)].copy()

    raw["revenue"] = pd.to_numeric(raw["REVENUE"], errors="coerce")
    raw = raw.loc[raw["revenue"].notna() & (raw["revenue"] > 0)].copy()
    raw["nights"] = pd.to_numeric(raw.get("Nights"), errors="coerce")
    res_col = "Reservation Number" if "Reservation Number" in raw.columns else None
    if not res_col:
        raw["reservation_id"] = [f"hist-{i}" for i in range(len(raw))]
    else:
        raw["reservation_id"] = raw[res_col].astype(str).str.strip()

    listing = raw.get("Listing Name", pd.Series([""] * len(raw))).astype(str).str.strip()
    listing = listing.replace("", "UNKNOWN_LISTING")

    out = pd.DataFrame(
        {
            "arrival_date": raw["arrival_date"],
            "departure_date": pd.to_datetime(raw.get("Check out Date"), errors="coerce"),
            "nights": raw["nights"],
            "revenue": raw["revenue"],
            "unit_id": listing,
            "guest_name": "",
            "channel": "HistoricalTrack",
            "reservation_id": raw["reservation_id"],
        }
    )
    out = out.dropna(subset=["arrival_date"])
    inferred = (out["departure_date"] - out["arrival_date"]).dt.days
    out["nights"] = out["nights"].where(out["nights"].notna() & (out["nights"] > 0), inferred)
    out = out.loc[out["nights"].notna() & (out["nights"] > 0)].copy()
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="FBG historical Track month hierarchy (Basse-1 proxy).")
    p.add_argument(
        "--csv",
        type=Path,
        default=DEFAULT_HISTORICAL_CSV,
        help="Path to Track-style dashboard CSV",
    )
    p.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Where to write the hierarchy CSV",
    )
    p.add_argument(
        "--property-filter",
        choices=("basse1", "onera_only", "all"),
        default="basse1",
        help="Which Property column values to keep (basse1 = OLD CB + Onera Fredericksburg)",
    )
    p.add_argument(
        "--no-pre-2025-cut",
        action="store_true",
        help="If set, keep all arrival dates in the file (default: arrival < 2025-01-01)",
    )
    p.add_argument(
        "--basse1-units",
        type=int,
        default=12,
        help="Physical units for RevPAR denominator (default 12 = Basse 1 era)",
    )
    args = p.parse_args()

    if not args.csv.exists():
        print(f"Error: file not found: {args.csv}", file=sys.stderr)
        sys.exit(1)

    raw_canon = _load_track_dashboard_to_canonical(
        args.csv,
        pre_2025_cut=not args.no_pre_2025_cut,
        property_filter=args.property_filter,
    )
    df = prepare_canonical_for_analysis(raw_canon)

    # Single pool SKU so availability = basse1_units * days in month (matches prior ad-hoc analysis).
    pool = "HISTORICAL_POOL"
    listing_start_dates = {pool: "2000-01-01"}
    listing_unit_counts = {pool: int(args.basse1_units)}
    room_count = int(args.basse1_units)

    combined = build_monthly_performance_combined(
        df,
        room_count=room_count,
        listing_start_dates=listing_start_dates,
        listing_unit_counts=listing_unit_counts,
    )

    args.output.parent.mkdir(parents=True, exist_ok=True)
    combined.to_csv(args.output, index=False)

    ordered = combined.sort_values(["performance_score_1_10", "month_index"], ascending=[True, True])
    print(f"Wrote {args.output} ({len(combined)} month rows)")
    print(f"Raw reservation rows after filters: {len(df)}")
    print("\nWorst → best (by performance_score_1_10, then calendar month):")
    for _, r in ordered.iterrows():
        print(
            f"  {int(r['month_index']):2d} {r['month_name']:<9} score={int(r['performance_score_1_10'])} "
            f"rev=${r['revenue']:,.0f} revpar={r['revpar']} years={int(r.get('avg_basis_year_count', 0) or 0)}"
        )


if __name__ == "__main__":
    main()
