#!/usr/bin/env python3
"""
Build FBG 12-month group pricing matrix from the January anchor + historical uplifts.

Outputs:
  output/fbg/pricing/pricing_matrix_by_group.csv       — final (DOW + tier enforced)
  output/fbg/pricing/pricing_matrix_by_group_raw.csv   — pre-hierarchy projection
  output/fbg/pricing/month_uplift_summary.csv          — level + spread uplift vs January

Usage:
  python scripts/build_fbg_seasonal_matrix.py
  python scripts/build_fbg_seasonal_matrix.py --property fbg
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.fbg_seasonal_matrix import (
    MONTH_HIERARCHY_ORDER,
    build_seasonal_matrix_from_january_anchor,
    load_historical_dow_spreads,
)
from src.property_config import load_property_inventory


def main() -> None:
    p = argparse.ArgumentParser(description="Build FBG seasonal matrix from January anchor.")
    p.add_argument("--property", default="fbg")
    p.add_argument("--output-dir", default=None)
    p.add_argument(
        "--hist-spreads",
        default=None,
        help="Path to dow_spread_by_group_month.csv (default: output/<property>/pricing/analysis/...)",
    )
    args = p.parse_args()

    inv = load_property_inventory(args.property)
    out_dir = Path(args.output_dir or PROJECT_ROOT / "output" / args.property / "pricing")
    out_dir.mkdir(parents=True, exist_ok=True)

    hist_path = Path(
        args.hist_spreads
        or PROJECT_ROOT / "output" / args.property / "pricing" / "analysis" / "dow_spread_by_group_month.csv"
    )
    hist = load_historical_dow_spreads(hist_path)

    pricing_cfg = inv.get("pricing") or {}
    raw_df, final_df, uplift_df = build_seasonal_matrix_from_january_anchor(
        pricing_cfg.get("listing_price_hierarchy") or {},
        pricing_cfg.get("dow_hierarchy") or [],
        hist,
    )

    raw_path = out_dir / "pricing_matrix_by_group_raw.csv"
    final_path = out_dir / "pricing_matrix_by_group.csv"
    uplift_path = out_dir / "month_uplift_summary.csv"

    raw_df.to_csv(raw_path, index=False)
    final_df.to_csv(final_path, index=False)
    uplift_df.to_csv(uplift_path, index=False)

    print(f"Wrote {uplift_path}")
    print(f"Wrote {raw_path}")
    print(f"Wrote {final_path} ({len(final_df)} rows)")

    print("\n=== Month uplift vs January (hierarchy order) ===")
    cols = [
        "month_name",
        "month_score",
        "also_applies_to",
        "mtw_uplift_ratio",
        "fri_sat_uplift_ratio",
        "spread_ts_delta_vs_jan_pp",
        "spread_fs_delta_vs_jan_pp",
        "portfolio_mtw",
        "portfolio_fri_sat",
    ]
    print(uplift_df[[c for c in cols if c in uplift_df.columns]].to_string(index=False))

    print("\n=== Sample: haus by month (final) ===")
    haus = final_df.loc[final_df["group_id"] == "haus"].sort_values("month_index")
    print(
        haus[["month_index", "Monday", "Thursday", "Friday"]].to_string(index=False)
    )

    print("\nMonth hierarchy (weak → strong):")
    for m, name, score in MONTH_HIERARCHY_ORDER:
        print(f"  {score:2d}  {name}")


if __name__ == "__main__":
    main()
