from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

# Ensure project root is on sys.path so `src` can be imported when running as a script
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.pricing_matrix import build_pricing_matrix
from src.property_config import load_property_inventory


def write_pricing_outputs(
    pricing_dir: Path,
    listing_pricing_df: pd.DataFrame,
    group_pricing_df: pd.DataFrame,
) -> None:
    listing_out_path = pricing_dir / "pricing_matrix_draft.csv"
    listing_pricing_df.to_csv(listing_out_path, index=False)
    print(f"Wrote pricing matrix (per listing): {listing_out_path} ({len(listing_pricing_df)} rows)")
    if not group_pricing_df.empty:
        group_out_path = pricing_dir / "pricing_matrix_group_draft.csv"
        group_pricing_df.to_csv(group_out_path, index=False)
        print(f"Wrote pricing matrix (per group): {group_out_path} ({len(group_pricing_df)} rows)")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build draft pricing matrix from analysis outputs for a property."
    )
    parser.add_argument(
        "--property",
        dest="property_id",
        required=True,
        help="Property ID (used to locate output/<property_id>/analysis).",
    )
    parser.add_argument(
        "--analysis-dir",
        dest="analysis_dir",
        default=None,
        help="Optional override for analysis directory (defaults to output/<property_id>/analysis).",
    )
    parser.add_argument(
        "--output-dir",
        dest="output_dir",
        default=None,
        help="Optional override for pricing output directory (defaults to output/<property_id>/pricing).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    prop_id = args.property_id

    root = Path(".").resolve()
    analysis_dir = (
        Path(args.analysis_dir).resolve()
        if args.analysis_dir
        else root / "output" / prop_id / "analysis"
    )
    pricing_dir = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else root / "output" / prop_id / "pricing"
    )

    if not analysis_dir.exists():
        raise SystemExit(f"Analysis directory not found: {analysis_dir}")

    print(f"Property: {prop_id}")
    print(f"Using analysis tables from: {analysis_dir}")

    # Load property config to pick up pricing settings (e.g. DOW hierarchy)
    inventory = load_property_inventory(prop_id)
    pricing_cfg = inventory.get("pricing") or {}
    dow_hierarchy = pricing_cfg.get("dow_hierarchy")
    listing_groups = inventory.get("listing_groups") or []

    listing_pricing_df = build_pricing_matrix(analysis_dir, dow_hierarchy=dow_hierarchy)
    group_pricing_df = pd.DataFrame()

    # If listing groups are defined, aggregate matrix to group level
    if listing_groups:
        overall = pd.read_csv(analysis_dir / "overall_summary.csv")
        weights = (
            overall.loc[overall["unit_id"] != "PROPERTY", ["unit_id", "revenue"]]
            .rename(columns={"revenue": "weight"})
            .copy()
        )
        weights["weight"] = pd.to_numeric(weights["weight"], errors="coerce").fillna(0.0)

        # unit_id -> group metadata
        unit_to_group: dict[str, dict] = {}
        for g in listing_groups:
            gname = g.get("name")
            if not gname:
                continue
            for u in g.get("unit_ids") or []:
                unit_to_group[str(u)] = {
                    "listing_group": gname,
                }

        df = listing_pricing_df.copy()
        df["listing_group"] = df["unit_id"].map(lambda u: unit_to_group.get(str(u), {}).get("listing_group"))
        df = df.merge(weights, on="unit_id", how="left")
        df["weight"] = df["weight"].fillna(0.0)

        day_cols = [c for c in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"] if c in df.columns]
        if not day_cols:
            raise SystemExit("Expected weekday columns missing from pricing matrix output.")

        # Drop listings not assigned to a group (if any)
        df = df.loc[df["listing_group"].notna()].copy()

        def _wavg(series: pd.Series, w: pd.Series) -> float:
            s = pd.to_numeric(series, errors="coerce")
            ww = pd.to_numeric(w, errors="coerce").fillna(0.0)
            mask = s.notna()
            s = s.loc[mask]
            ww = ww.loc[mask]
            if len(s) == 0:
                return float("nan")
            if ww.sum() <= 0:
                return float(s.mean())
            return float((s * ww).sum() / ww.sum())

        grouped_rows = []
        for (gname, m), grp in df.groupby(["listing_group", "month_index"], as_index=False):
            out = {
                "listing_group": gname,
                "month_index": int(m),
            }
            for c in day_cols:
                out[c] = round(_wavg(grp[c], grp["weight"]))
            grouped_rows.append(out)

        group_pricing_df = pd.DataFrame(grouped_rows).sort_values(["listing_group", "month_index"]).reset_index(drop=True)

    pricing_dir.mkdir(parents=True, exist_ok=True)
    write_pricing_outputs(pricing_dir, listing_pricing_df, group_pricing_df)


if __name__ == "__main__":
    main()

