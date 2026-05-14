from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

# Ensure project root is on sys.path so `src` can be imported when running as a script
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.pricing_matrix import (  # noqa: E402
    build_pricing_matrix,
    coerce_month_score_by_index_config,
    listing_factors_from_price_hierarchy,
)
from src.property_config import load_property_inventory  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a per-listing pricing matrix by rescaling model outputs "
        "to match owner-provided High/Shoulder/Low weekday rates per listing_group."
    )
    parser.add_argument(
        "--property",
        dest="property_id",
        required=True,
        help="Property ID (used to locate output/<property_id>/analysis).",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    prop_id = args.property_id

    root = Path(".").resolve()
    analysis_dir = root / "output" / prop_id / "analysis"
    pricing_dir = root / "output" / prop_id / "pricing"
    pricing_dir.mkdir(parents=True, exist_ok=True)

    if not analysis_dir.exists():
        raise SystemExit(f"Analysis directory not found: {analysis_dir}")

    inventory = load_property_inventory(prop_id)
    pricing_cfg = inventory.get("pricing") or {}
    dow_hierarchy = pricing_cfg.get("dow_hierarchy")
    month_score_by_index = coerce_month_score_by_index_config(pricing_cfg.get("month_score_by_index"))
    listing_factor_override = listing_factors_from_price_hierarchy(
        pricing_cfg.get("listing_price_hierarchy")
    )

    listing_groups = inventory.get("listing_groups") or []
    if not listing_groups:
        raise SystemExit("No listing_groups found in property config. Cannot map unit_id -> group.")

    # unit_id -> listing_group
    unit_to_group: dict[str, str] = {}
    for g in listing_groups:
        gname = g.get("name")
        if not gname:
            continue
        for u in g.get("unit_ids") or []:
            unit_to_group[str(u)] = gname

    # Owner-provided season/day rates per listing_group (ints; commas removed)
    owner_rates: dict[str, dict[str, list[int]]] = {
        "High": {
            "House (6BR/4BA)": [1451, 1451, 1494, 1667, 1667, 1753, 1537],
            "Suite BIG (1BR/1BA)": [511, 511, 527, 592, 592, 623, 543],
            "Suite SMALL (1BR/1BA)": [430, 430, 444, 502, 502, 531, 458],
            "Deluxe Villa (2BR/1BA)": [583, 583, 603, 681, 681, 720, 622],
            "Premium Villa (2BR/2BA)": [861, 861, 888, 996, 996, 1050, 915],
            "Villa Game (2BR/2BA)": [774, 774, 799, 896, 896, 944, 823],
            "J.MT. Villa (3BR/2BA)": [987, 987, 1017, 1135, 1135, 1193, 1047],
            "Premier Villa (3BR/3BA)": [1035, 1035, 1066, 1190, 1190, 1251, 1097],
        },
        "Shoulder": {
            "House (6BR/4BA)": [1280, 1280, 1325, 1505, 1505, 1595, 1370],
            "Suite BIG (1BR/1BA)": [402, 402, 417, 477, 477, 508, 432],
            "Suite SMALL (1BR/1BA)": [348, 348, 363, 420, 420, 448, 377],
            "Deluxe Villa (2BR/1BA)": [465, 465, 484, 561, 561, 599, 504],
            "Premium Villa (2BR/2BA)": [701, 701, 732, 853, 853, 913, 762],
            "Villa Game (2BR/2BA)": [644, 644, 668, 764, 764, 813, 692],
            "J.MT. Villa (3BR/2BA)": [836, 836, 866, 985, 985, 1045, 896],
            "Premier Villa (3BR/3BA)": [871, 871, 902, 1025, 1025, 1087, 933],
        },
        "Low": {
            "House (6BR/4BA)": [790, 760, 825, 940, 965, 1030, 850],
            "Suite BIG (1BR/1BA)": [210, 200, 220, 250, 255, 275, 225],
            "Suite SMALL (1BR/1BA)": [175, 170, 185, 210, 215, 230, 190],
            "Deluxe Villa (2BR/1BA)": [265, 255, 275, 315, 325, 345, 285],
            "Premium Villa (2BR/2BA)": [315, 305, 330, 375, 385, 410, 340],
            "Villa Game (2BR/2BA)": [380, 365, 395, 450, 465, 495, 410],
            "J.MT. Villa (3BR/2BA)": [450, 435, 475, 535, 550, 585, 485],
            "Premier Villa (3BR/3BA)": [465, 450, 490, 555, 570, 610, 505],
        },
    }

    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    season_months: dict[str, list[int]] = {"High": [4, 5, 6, 9, 10], "Shoulder": [3, 7, 8, 11], "Low": [1, 2, 12]}

    # Generate model listing-level matrix first (this preserves individual differences within group)
    model_matrix = build_pricing_matrix(
        analysis_dir,
        dow_hierarchy=dow_hierarchy,
        month_score_by_index=month_score_by_index,
        listing_factor_by_unit=listing_factor_override,
    )

    if "unit_id" not in model_matrix.columns or "month_index" not in model_matrix.columns:
        raise SystemExit("Unexpected matrix format from build_pricing_matrix (expected unit_id and month_index).")

    # Map each unit_id to its listing_group
    model_matrix = model_matrix.copy()
    model_matrix["listing_group"] = model_matrix["unit_id"].astype(str).map(unit_to_group)
    # Drop any model rows for unit_ids that aren't assigned to a listing_group.
    # This prevents generating prices for units that "don't exist anymore".
    unmapped = int(model_matrix["listing_group"].isna().sum())
    if unmapped:
        print(f"Warning: dropping {unmapped} model rows with unmapped unit_id -> listing_group.")
        model_matrix = model_matrix.loc[model_matrix["listing_group"].notna()].copy()

    # Revenue weights per unit_id (2-year revenue totals)
    overall = pd.read_csv(analysis_dir / "overall_summary.csv")
    weights = (
        overall.loc[overall["unit_id"] != "PROPERTY", ["unit_id", "revenue"]]
        .rename(columns={"revenue": "weight"})
        .copy()
    )
    weights["weight"] = pd.to_numeric(weights["weight"], errors="coerce").fillna(0.0)
    model_matrix = model_matrix.merge(weights, on="unit_id", how="left")
    model_matrix["weight"] = model_matrix["weight"].fillna(0.0)

    # Apply owner group weekday levels ONLY (absolute),
    # but keep each listing's within-group month/day relative performance from the model.
    #
    # For each (group, month_index, weekday):
    #   mult(group, month, weekday) = owner_group_weekday_rate(season, group, weekday) / model_group_weighted_avg_rate
    # Then:
    #   final_rate(listing, month, weekday) = model_rate(listing, month, weekday) * mult
    #
    # This ensures the group level matches your owner base table for that season,
    # while each listing still moves relative to other listings in its group for that specific month & weekday.
    out = model_matrix.copy()
    for season in ["High", "Shoulder", "Low"]:
        for mi in season_months[season]:
            month_mask = out["month_index"] == int(mi)
            for group in out.loc[month_mask, "listing_group"].dropna().unique().tolist():
                group = str(group)
                if group not in owner_rates[season]:
                    continue

                idx = month_mask & (out["listing_group"] == group)
                idx_list = out.index[idx].tolist()
                if not idx_list:
                    continue

                # Model group average per weekday for THIS month/day.
                # We only use the model here to keep within-group month/day relative performance.
                for d, owner_rate in zip(weekdays, owner_rates[season][group]):
                    s = pd.to_numeric(out.loc[idx_list, d], errors="coerce")
                    mask = s.notna()
                    if not mask.any():
                        mult = 1.0
                    else:
                        model_avg = float(s.loc[mask].mean())
                        if model_avg == 0 or pd.isna(model_avg):
                            mult = 1.0
                        else:
                            mult = float(owner_rate) / float(model_avg)

                    out.loc[idx_list, d] = s * mult

    # Final rounding to whole dollars
    out[weekdays] = out[weekdays].round(0)

    # Prepare output: listing-level matrix
    final = out[["unit_id", "month_index"] + weekdays].copy()

    # Backup current pricing_matrix_draft.csv if it's group-based
    out_path = pricing_dir / "pricing_matrix_draft.csv"
    if out_path.exists():
        backup = pricing_dir / "pricing_matrix_draft_backup_before_owner_rescale.csv"
        out_path.replace(backup)

    final.to_csv(out_path, index=False)
    print(f"Wrote listing-level owner-rescaled pricing matrix: {out_path} ({len(final)} rows)")


if __name__ == "__main__":
    main()

