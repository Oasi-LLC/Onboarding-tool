from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

# Ensure project root is on sys.path so `src` can be imported when running as a script
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.pricing_matrix import (
    DEFAULT_TIER_MONTH_BLEND_WEIGHT,
    apply_within_tier_median_rates,
    build_pricing_matrix,
    build_tiered_pricing_matrix,
    coerce_month_score_by_index_config,
    listing_factors_from_price_hierarchy,
    write_tiered_pricing_xlsx,
)
from src.property_config import load_property_inventory

_DAY_COLS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _coerce_month_matrix_multipliers(raw: object) -> dict[int, float]:
    """Parse pricing.month_matrix_multipliers: {1..12 -> positive float}."""
    if not raw or not isinstance(raw, dict):
        return {}
    out: dict[int, float] = {}
    for k, v in raw.items():
        try:
            mi = int(k)
            mult = float(v)
            if 1 <= mi <= 12 and mult > 0:
                out[mi] = mult
        except (TypeError, ValueError):
            continue
    return out


def apply_month_matrix_multipliers(df: pd.DataFrame, mult_by_month: dict[int, float]) -> pd.DataFrame:
    """Scale weekday price columns for selected month_index rows (e.g. ease December only)."""
    if not mult_by_month or df.empty or "month_index" not in df.columns:
        return df
    out = df.copy()
    day_cols = [c for c in _DAY_COLS if c in out.columns]
    if not day_cols:
        return out
    for mi, mult in mult_by_month.items():
        mask = out["month_index"] == mi
        if not mask.any():
            continue
        for c in day_cols:
            out.loc[mask, c] = (pd.to_numeric(out.loc[mask, c], errors="coerce") * mult).round(0)
    return out


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


def run_tiered_mode(
    prop_id: str,
    pricing_cfg: dict,
    pricing_dir: Path,
) -> None:
    """
    Build and write a tiered pricing matrix (anchor × season × DOW × premium).
    Produces both a CSV summary and a formatted XLSX workbook.
    """
    print(f"Pricing mode: tiered  (anchor = ${pricing_cfg.get('anchor_rate', 300):.0f})")

    matrix_df = build_tiered_pricing_matrix(pricing_cfg)

    # ── CSV: one row per group × month ────────────────────────────────────────
    csv_path = pricing_dir / "pricing_matrix_tiered.csv"
    matrix_df.to_csv(csv_path, index=False)
    print(f"Wrote tiered matrix CSV: {csv_path} ({len(matrix_df)} rows)")

    # ── XLSX: formatted workbook with Assumptions + per-group matrix sheets ───
    xlsx_path = pricing_dir / f"{prop_id}_pricing_matrix.xlsx"
    write_tiered_pricing_xlsx(matrix_df, pricing_cfg, xlsx_path)
    print(f"Wrote tiered matrix XLSX: {xlsx_path}")


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

    print(f"Property: {prop_id}")

    # Load property config to pick up pricing settings
    inventory = load_property_inventory(prop_id)
    pricing_cfg = inventory.get("pricing") or {}
    pricing_mode = str(pricing_cfg.get("mode", "score")).lower()

    pricing_dir.mkdir(parents=True, exist_ok=True)

    # ── TIERED MODE ───────────────────────────────────────────────────────────
    # Uses anchor × (season_index / low_index) × (dow_index / d1_index) × premium.
    # No analysis CSVs required — all inputs come from the property YAML.
    if pricing_mode == "tiered":
        run_tiered_mode(prop_id, pricing_cfg, pricing_dir)
        return

    # ── SCORE MODE (default) ──────────────────────────────────────────────────
    # Derives multipliers from analysis output scores (dow_score_1_10, etc.).
    # Requires analysis CSVs to be present.
    if not analysis_dir.exists():
        raise SystemExit(f"Analysis directory not found: {analysis_dir}")

    print(f"Pricing mode: score")
    print(f"Using analysis tables from: {analysis_dir}")

    dow_hierarchy = pricing_cfg.get("dow_hierarchy")
    month_score_by_index = coerce_month_score_by_index_config(pricing_cfg.get("month_score_by_index"))
    listing_groups = inventory.get("listing_groups") or []

    if month_score_by_index:
        print("Using pricing.month_score_by_index overrides for month strength (score mode).")

    listing_factor_override = listing_factors_from_price_hierarchy(
        pricing_cfg.get("listing_price_hierarchy")
    )
    if listing_factor_override:
        print(
            "Using pricing.listing_price_hierarchy for listing strength "
            f"({len(listing_factor_override)} unit_ids)."
        )

    tier_month_blend_weight = pricing_cfg.get("tier_month_blend_weight")
    if tier_month_blend_weight is not None:
        try:
            tier_month_blend_weight = float(tier_month_blend_weight)
        except (TypeError, ValueError):
            tier_month_blend_weight = None
    if tier_month_blend_weight is not None:
        print(f"Using pricing.tier_month_blend_weight override: {tier_month_blend_weight}")
    else:
        print(f"Using default tier_month_blend_weight: {DEFAULT_TIER_MONTH_BLEND_WEIGHT}")

    listing_pricing_df = build_pricing_matrix(
        analysis_dir,
        dow_hierarchy=dow_hierarchy,
        month_score_by_index=month_score_by_index,
        listing_factor_by_unit=listing_factor_override,
        tier_month_blend_weight=tier_month_blend_weight,
    )
    if pricing_cfg.get("listing_price_hierarchy"):
        listing_pricing_df = apply_within_tier_median_rates(
            listing_pricing_df,
            pricing_cfg["listing_price_hierarchy"],
        )
        print(
            "Applied within-tier median rates from pricing.listing_price_hierarchy "
            "(each tier shares one dollar value per month × weekday)."
        )

    month_mult = _coerce_month_matrix_multipliers(pricing_cfg.get("month_matrix_multipliers"))
    if month_mult:
        listing_pricing_df = apply_month_matrix_multipliers(listing_pricing_df, month_mult)
        print(f"Applied pricing.month_matrix_multipliers: {month_mult}")

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

        unit_to_group: dict[str, dict] = {}
        for g in listing_groups:
            gname = g.get("name")
            if not gname:
                continue
            for u in g.get("unit_ids") or []:
                unit_to_group[str(u)] = {"listing_group": gname}

        df = listing_pricing_df.copy()
        df["listing_group"] = df["unit_id"].map(
            lambda u: unit_to_group.get(str(u), {}).get("listing_group")
        )
        df = df.merge(weights, on="unit_id", how="left")
        df["weight"] = df["weight"].fillna(0.0)

        day_cols = [
            c for c in ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
            if c in df.columns
        ]
        if not day_cols:
            raise SystemExit("Expected weekday columns missing from pricing matrix output.")

        df = df.loc[df["listing_group"].notna()].copy()

        def _wavg(series: pd.Series, w: pd.Series) -> float:
            s = pd.to_numeric(series, errors="coerce")
            ww = pd.to_numeric(w, errors="coerce").fillna(0.0)
            mask = s.notna()
            s, ww = s.loc[mask], ww.loc[mask]
            if len(s) == 0:
                return float("nan")
            if ww.sum() <= 0:
                return float(s.mean())
            return float((s * ww).sum() / ww.sum())

        grouped_rows = []
        for (gname, m), grp in df.groupby(["listing_group", "month_index"], as_index=False):
            out = {"listing_group": gname, "month_index": int(m)}
            for c in day_cols:
                out[c] = round(_wavg(grp[c], grp["weight"]))
            grouped_rows.append(out)

        group_pricing_df = (
            pd.DataFrame(grouped_rows)
            .sort_values(["listing_group", "month_index"])
            .reset_index(drop=True)
        )

    write_pricing_outputs(pricing_dir, listing_pricing_df, group_pricing_df)


if __name__ == "__main__":
    main()

