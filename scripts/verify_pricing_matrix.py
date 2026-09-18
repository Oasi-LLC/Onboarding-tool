#!/usr/bin/env python3
"""
Verify a proposed pricing matrix against stay-night historical ADR.

Expands reservations to occupied nights, builds min/median/max ADR by
listing × month × day-of-week, then compares proposed rates and audits
DOW / listing / month hierarchy and weekend spread shape.

Usage:
  python scripts/verify_pricing_matrix.py --property fbg \\
    --matrix data/FBG/fbg_pricing_matrix_proposed.csv
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.analysis import prepare_canonical_for_analysis
from src.pricing_verify import run_pricing_verification
from src.property_config import load_property_inventory

OUTPUT_FILES = [
    "historical_adr_by_listing_month_dow.csv",
    "historical_adr_by_listing_month.csv",
    "historical_adr_by_listing_dow.csv",
    "rate_comparison.csv",
    "spread_by_month.csv",
    "spread_uniformity.csv",
    "spread_vs_history.csv",
    "dow_hierarchy_violations.csv",
    "listing_hierarchy_violations.csv",
    "month_hierarchy_violations.csv",
    "verification_summary.csv",
]


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Verify proposed pricing matrix vs stay-night ADR history.")
    p.add_argument("--property", required=True, help="Property id (e.g. fbg).")
    p.add_argument("--matrix", required=True, help="Path to wide pricing matrix CSV.")
    p.add_argument(
        "--canonical",
        default=None,
        help="Canonical CSV (default: output/<property>/ingestion/canonical.csv).",
    )
    p.add_argument(
        "--output-dir",
        default=None,
        help="Output directory (default: output/<property>/pricing/verification).",
    )
    return p.parse_args()


def main() -> None:
    args = parse_args()
    inv = load_property_inventory(args.property)
    canonical_path = Path(args.canonical or PROJECT_ROOT / "output" / args.property / "ingestion" / "canonical.csv")
    matrix_path = Path(args.matrix)
    out_dir = Path(args.output_dir or PROJECT_ROOT / "output" / args.property / "pricing" / "verification")
    out_dir.mkdir(parents=True, exist_ok=True)

    canonical = pd.read_csv(canonical_path)
    canonical = prepare_canonical_for_analysis(canonical)
    matrix = pd.read_csv(matrix_path)

    results = run_pricing_verification(
        canonical,
        matrix,
        pricing_cfg=inv.get("pricing") or {},
        analysis_window=inv.get("analysis_window"),
    )

    file_map = {
        "historical_adr_by_listing_month_dow.csv": "by_listing_month_dow",
        "historical_adr_by_listing_month.csv": "by_listing_month",
        "historical_adr_by_listing_dow.csv": "by_listing_dow",
        "rate_comparison.csv": "rate_comparison",
        "spread_by_month.csv": "spread_by_month",
        "spread_uniformity.csv": "spread_uniformity",
        "spread_vs_history.csv": "spread_vs_history",
        "dow_hierarchy_violations.csv": "dow_hierarchy_violations",
        "listing_hierarchy_violations.csv": "listing_hierarchy_violations",
        "month_hierarchy_violations.csv": "month_hierarchy_violations",
        "verification_summary.csv": "summary",
    }
    for fname, key in file_map.items():
        df = results.get(key, pd.DataFrame())
        df.to_csv(out_dir / fname, index=False)

    summary = results["summary"]
    print(f"Wrote verification outputs to {out_dir}")
    print("\nSummary:")
    for _, r in summary.iterrows():
        print(f"  {r['metric']}: {r['value']}")

    cmp_df = results["rate_comparison"]
    if not cmp_df.empty:
        flagged = cmp_df.loc[cmp_df["cell_flag"] != "ok"]
        print(f"\nFlagged cells (non-ok): {len(flagged)} / {len(cmp_df)}")
        if not flagged.empty:
            top = (
                flagged.groupby(["unit_id", "cell_flag"])
                .size()
                .reset_index(name="count")
                .sort_values("count", ascending=False)
                .head(15)
            )
            print(top.to_string(index=False))

    spread_u = results["spread_uniformity"]
    flat = spread_u.loc[spread_u["status"] == "warn"] if not spread_u.empty else pd.DataFrame()
    if not flat.empty:
        print(f"\nListings with flat Sat/Mon spread across months ({len(flat)}):")
        print(flat[["unit_id", "cv_pct", "min_ratio", "max_ratio"]].to_string(index=False))

    spread_hist = results.get("spread_vs_history", pd.DataFrame())
    bad_spread = spread_hist.loc[spread_hist["status"].isin(["warn", "fail"])] if not spread_hist.empty else pd.DataFrame()
    if not bad_spread.empty:
        print(f"\nSat/Mon spread vs history mismatches ({len(bad_spread)} unit-months):")
        print(
            bad_spread.sort_values("spread_delta_pct", key=abs, ascending=False)
            .head(20)[["unit_id", "month_name", "proposed_sat_mon_spread_pct", "hist_sat_mon_spread_pct", "spread_delta_pct", "status"]]
            .to_string(index=False)
        )

    for label, key in [
        ("DOW hierarchy", "dow_hierarchy_violations"),
        ("Listing hierarchy", "listing_hierarchy_violations"),
        ("Month hierarchy", "month_hierarchy_violations"),
    ]:
        v = results[key]
        if not v.empty:
            print(f"\n{label} violations: {len(v)}")
            print(v.head(10).to_string(index=False))


if __name__ == "__main__":
    main()
