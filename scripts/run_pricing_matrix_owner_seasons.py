from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

# Ensure project root is on sys.path so `src` can be imported when running as a script
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.property_config import load_property_inventory  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate a pricing matrix using owner-provided season rates."
    )
    parser.add_argument(
        "--property",
        dest="property_id",
        required=True,
        help="Property ID (used to locate output/<property_id>/pricing).",
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
    pricing_dir = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else root / "output" / prop_id / "pricing"
    )

    # Keep ordering as you provided (top-to-bottom).
    groups = [
        "House (6BR/4BA)",
        "Suite BIG (1BR/1BA)",
        "Suite SMALL (1BR/1BA)",
        "Deluxe Villa (2BR/1BA)",
        "Premium Villa (2BR/2BA)",
        "Villa Game (2BR/2BA)",
        "J.MT. Villa (3BR/2BA)",
        "Premier Villa (3BR/3BA)",
    ]

    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    # Season -> group -> weekday rates (owner-provided).
    owner_rates = {
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

    season_months = {
        "High": [4, 5, 6, 9, 10],
        "Shoulder": [3, 7, 8, 11],
        "Low": [1, 2, 12],
    }

    rows: list[dict[str, object]] = []
    for season in ["High", "Low", "Shoulder"]:
        # Month order should be numerical so downstream logic is consistent.
        for month_index in season_months[season]:
            for group in groups:
                rates = owner_rates[season][group]
                row: dict[str, object] = {"listing_group": group, "month_index": int(month_index)}
                for day, rate in zip(weekdays, rates):
                    row[day] = int(rate)
                rows.append(row)

    pricing_df = pd.DataFrame(rows).sort_values(["listing_group", "month_index"]).reset_index(drop=True)

    pricing_dir.mkdir(parents=True, exist_ok=True)
    out_path = pricing_dir / "pricing_matrix_group_draft.csv"
    pricing_df.to_csv(out_path, index=False)

    # Quick sanity check: ensure all weekdays are present.
    missing = [c for c in weekdays if c not in pricing_df.columns]
    if missing:
        raise SystemExit(f"Owner pricing matrix missing columns: {missing}")

    print(f"Wrote owner-based GROUP pricing matrix: {out_path} ({len(pricing_df)} rows)")

    # Optional: keep config load here (not used) in case you later want to key off config.
    load_property_inventory(prop_id)


if __name__ == "__main__":
    main()

