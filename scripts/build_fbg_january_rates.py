#!/usr/bin/env python3
"""
Build FBG January rates from stay-night history (DOW hierarchy enforced).

Optional manager anchors (--manager-anchors):
  - haus: Mon–Wed 209, Fri–Sat 359
  - elite: Fri–Sat 729

Outputs:
  output/fbg/pricing/january_by_listing.csv
  output/fbg/pricing/january_by_group.csv
  output/fbg/pricing/january_build_diagnostics.csv

Usage:
  python scripts/build_fbg_january_rates.py
  python scripts/build_fbg_january_rates.py --property fbg
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.analysis import prepare_canonical_for_analysis
from src.fbg_january_rates import (
    _tier_maps,
    apply_group_rates_to_listings,
    apply_january_hierarchies,
    audit_listing_dow_hierarchy,
    audit_listing_tier_hierarchy,
    build_january_listing_rates,
    build_recommended_january_rates,
    combine_listings_to_groups,
    finalize_group_january_rates,
)
from src.property_config import load_property_inventory

import pandas as pd


def main() -> None:
    p = argparse.ArgumentParser(description="Build FBG January pricing from stay-night ADR.")
    p.add_argument("--property", default="fbg")
    p.add_argument("--canonical", default=None)
    p.add_argument("--output-dir", default=None)
    p.add_argument(
        "--manager-anchors",
        action="store_true",
        help="Apply manager January anchors for haus and elite (default: data-only).",
    )
    p.add_argument(
        "--recommended-grid",
        action="store_true",
        help="Use data-informed recommended January base grid, then DOW + listing tier hierarchy.",
    )
    args = p.parse_args()

    inv = load_property_inventory(args.property)
    out_dir = Path(args.output_dir or PROJECT_ROOT / "output" / args.property / "pricing")
    out_dir.mkdir(parents=True, exist_ok=True)

    canonical_path = Path(
        args.canonical or PROJECT_ROOT / "output" / args.property / "ingestion" / "canonical.csv"
    )
    canonical = prepare_canonical_for_analysis(pd.read_csv(canonical_path))

    pricing_cfg = inv.get("pricing") or {}
    dow_h = pricing_cfg.get("dow_hierarchy") or []
    listing_h = pricing_cfg.get("listing_price_hierarchy") or {}

    if args.recommended_grid:
        group_median_df, group_df = build_recommended_january_rates(listing_h, dow_h)
        _, group_units = _tier_maps(listing_h)
        listing_rows = []
        for gid, uids in group_units.items():
            for uid in uids:
                listing_rows.append({"group_id": gid, "unit_id": uid, "month_index": 1})
        listing_df = pd.DataFrame(listing_rows)
        listing_df["source"] = "recommended_grid_base"
        listing_uniform_df = apply_group_rates_to_listings(listing_df, group_df)
        listing_uniform_df["source"] = "recommended_grid_enforced"
        diag = pd.DataFrame()
    else:
        listing_df, diag = build_january_listing_rates(
            canonical,
            pricing_cfg=pricing_cfg,
            analysis_window=inv.get("analysis_window"),
            use_manager_anchors=args.manager_anchors,
        )
        group_median_df = combine_listings_to_groups(listing_df, dow_hierarchy=None)
        group_df = finalize_group_january_rates(group_median_df, listing_h, dow_h)
        listing_uniform_df = apply_group_rates_to_listings(listing_df, group_df)
    dow_audit = audit_listing_dow_hierarchy(listing_uniform_df, dow_h)
    tier_audit = audit_listing_tier_hierarchy(listing_uniform_df, listing_h)

    listing_raw_path = out_dir / "january_by_listing_raw.csv"
    listing_path = out_dir / "january_by_listing.csv"
    group_median_path = out_dir / "january_by_group_median.csv"
    group_path = out_dir / "january_by_group.csv"
    diag_path = out_dir / "january_build_diagnostics.csv"
    dow_hierarchy_path = out_dir / "january_dow_hierarchy_audit.csv"
    tier_hierarchy_path = out_dir / "january_listing_tier_hierarchy_audit.csv"
    listing_df.to_csv(listing_raw_path, index=False)
    listing_uniform_df.to_csv(listing_path, index=False)
    group_median_df.to_csv(group_median_path, index=False)
    group_df.to_csv(group_path, index=False)
    diag.to_csv(diag_path, index=False)
    dow_audit.to_csv(dow_hierarchy_path, index=False)
    tier_audit.to_csv(tier_hierarchy_path, index=False)

    print(f"Wrote {listing_raw_path} ({len(listing_df)} listings, pre-group)")
    print(f"Wrote {listing_path} ({len(listing_uniform_df)} listings, group-uniform rates)")
    print(f"Wrote {group_median_path} (pre-hierarchy medians)")
    print(f"Wrote {group_path} ({len(group_df)} groups, tier + DOW enforced)")
    print(f"Wrote {diag_path}")
    print(f"Wrote {dow_hierarchy_path}")
    print(f"Wrote {tier_hierarchy_path}")
    dow_failed = dow_audit.loc[~dow_audit["hierarchy_ok"]]
    if dow_failed.empty:
        print("\nDOW hierarchy: all listings PASS")
    else:
        print(f"\nDOW hierarchy: {len(dow_failed)} listing(s) FAIL")
        print(dow_failed.to_string(index=False))
    if tier_audit.empty:
        print("Listing tier hierarchy: all groups PASS (all DOWs)")
    else:
        print(f"Listing tier hierarchy: {len(tier_audit)} violation(s)")
        print(tier_audit.to_string(index=False))
    print("\n=== January by group (median within tier) ===")
    print(group_df.to_string(index=False))
    print("\n=== January by listing (group-uniform rates) ===")
    show = listing_uniform_df.drop(columns=["source"], errors="ignore")
    print(show.to_string(index=False))


if __name__ == "__main__":
    main()
