from __future__ import annotations

import argparse
import sys
from dataclasses import dataclass
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Optional

import pandas as pd
import yaml

# Ensure project root is on sys.path so `src` can be imported when running as a script
PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.property_config import load_property_inventory  # noqa: E402


@dataclass(frozen=True)
class PriceLabsRow:
    listing_id: str
    listing_name: str
    pms_name: str
    reason: str
    start_date: str
    end_date: str
    price: int
    currency: str
    price_type: str
    minimum_stay: int
    minimum_price: str
    minimum_price_type: str
    maximum_price: str
    maximum_price_type: str


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create PriceLabs CSV from pricing matrix.")
    parser.add_argument("--property", dest="property_id", required=True, help="Property ID.")
    parser.add_argument("--start-date", dest="start_date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--end-date", dest="end_date", required=True, help="YYYY-MM-DD")
    parser.add_argument("--year", dest="year", type=int, default=2026, help="Event year (default 2026).")
    parser.add_argument(
        "--output",
        dest="output_path",
        default=None,
        help="Optional output path (defaults to output/<property>/pricing/pricelabs_sheet_<start>_<end>.csv).",
    )
    return parser.parse_args()


def _format_date_for_pricelabs(d: date) -> str:
    # User requested ISO format for PriceLabs import.
    return d.isoformat()


def _extract_listing_title(listing_name: str) -> str:
    # Input looks like: "LaFave: ...--Mystery Premium Villa (2BR/2BA)"
    if "--" in listing_name:
        return listing_name.split("--", 1)[1].strip()
    return listing_name.strip()


def _detect_group_from_title(title: str) -> Optional[str]:
    # Match based on the exact group naming we use in YAML.
    # title examples:
    # - "Sinawava Suite BIG (1BR/1BA)"
    # - "Premium Villa (2BR/2BA)" won't appear standalone; we match keywords.
    if "House (6BR/4BA)" in title:
        return "House (6BR/4BA)"
    if title.startswith("J.MT. Villa"):
        return "J.MT. Villa (3BR/2BA)"
    if "Suite BIG" in title:
        return "Suite BIG (1BR/1BA)"
    if "Suite SMALL" in title:
        return "Suite SMALL (1BR/1BA)"
    if "Deluxe Villa" in title:
        return "Deluxe Villa (2BR/1BA)"
    if "Villa Game" in title:
        return "Villa Game (2BR/2BA)"
    if "Premium Villa" in title:
        return "Premium Villa (2BR/2BA)"
    if "Premier Villa" in title:
        return "Premier Villa (3BR/3BA)"
    return None


def _extract_key_from_title(title: str, group: str) -> Optional[str]:
    # Key is the human name part before the group descriptor.
    # Examples:
    # - "Mystery Premium Villa (2BR/2BA)" -> "Mystery"
    # - "Big Springs Premium Villa (2BR/2BA)" -> "Big Springs"
    # - "Mount Kinesava Deluxe Villa (2BR/1BA)" -> "Mount Kinesava"
    if group == "House (6BR/4BA)" or group == "J.MT. Villa (3BR/2BA)":
        return None

    if group == "Suite BIG (1BR/1BA)":
        return title.split(" Suite BIG", 1)[0].strip()
    if group == "Suite SMALL (1BR/1BA)":
        return title.split(" Suite SMALL", 1)[0].strip()
    if group == "Deluxe Villa (2BR/1BA)":
        return title.split(" Deluxe Villa", 1)[0].strip()
    if group == "Villa Game (2BR/2BA)":
        return title.split(" Villa Game", 1)[0].strip()
    if group == "Premium Villa (2BR/2BA)":
        return title.split(" Premium Villa", 1)[0].strip()
    if group == "Premier Villa (3BR/3BA)":
        return title.split(" Premier Villa", 1)[0].strip()
    return None


def _unit_name_from_unit_id(unit_id: str) -> str:
    # "201 LaFave South: Checkerboard Mesa" -> "Checkerboard Mesa"
    if ":" in unit_id:
        return unit_id.split(":", 1)[1].strip()
    return unit_id.strip()


# PriceLabs listing mapping (listing_id, full listing name including prefix).
PRICE_LABS_LISTINGS: list[tuple[str, str]] = [
    ("4140___8128", "LaFave: Luxury Rentals at Zion--Mystery Premium Villa (2BR/2BA)"),
    ("4140___8114", "LaFave: Luxury Rentals at Zion--J.MT. Villa (3BR/2BA)"),
    ("4140___8115", "LaFave: Luxury Rentals at Zion--House (6BR/4BA)"),
    ("4140___8117", "LaFave: Luxury Rentals at Zion--Sinawava Suite BIG (1BR/1BA)"),
    ("4140___8118", "LaFave: Luxury Rentals at Zion--Sentinel Suite BIG (1BR/1BA)"),
    ("4140___8119", "LaFave: Luxury Rentals at Zion--Sundial Suite BIG (1BR/1BA)"),
    ("4140___8120", "LaFave: Luxury Rentals at Zion--Watchman Suite SMALL (1BR/1BA)"),
    ("4140___8121", "LaFave: Luxury Rentals at Zion--Zion Suite SMALL (1BR/1BA)"),
    ("4140___8122", "LaFave: Luxury Rentals at Zion--Emerald Villa Game (2BR/2BA)"),
    ("4140___8123", "LaFave: Luxury Rentals at Zion--Subway Villa Game (2BR/2BA)"),
    ("4140___8124", "LaFave: Luxury Rentals at Zion--Meridian Villa Game (2BR/2BA)"),
    ("4140___8125", "LaFave: Luxury Rentals at Zion--Checkerboard Premium Villa (2BR/2BA)"),
    ("4140___8126", "LaFave: Luxury Rentals at Zion--Echo Canyon Premium Villa (2BR/2BA)"),
    ("4140___8127", "LaFave: Luxury Rentals at Zion--Mountain Premium Villa (2BR/2BA)"),
    ("4140___8129", "LaFave: Luxury Rentals at Zion--Big Springs Premium Villa (2BR/2BA)"),
    ("4140___8130", "LaFave: Luxury Rentals at Zion--East Temple Premium Villa (2BR/2BA)"),
    ("4140___8131", "LaFave: Luxury Rentals at Zion--Lava Villa Game (2BR/2BA)"),
    ("4140___8132", "LaFave: Luxury Rentals at Zion--Phantom Premium Villa (2BR/2BA)"),
    ("4140___8133", "LaFave: Luxury Rentals at Zion--Pine Premium Villa (2BR/2BA)"),
    ("4140___8134", "LaFave: Luxury Rentals at Zion--Hidden Deluxe Villa (2BR/1BA)"),
    ("4140___8135", "LaFave: Luxury Rentals at Zion--Kolob Deluxe Villa (2BR/1BA)"),
    ("4140___8136", "LaFave: Luxury Rentals at Zion--Northgate Deluxe Villa (2BR/1BA)"),
    ("4140___8137", "LaFave: Luxury Rentals at Zion--Orderville Deluxe Villa (2BR/1BA)"),
    ("4140___8138", "LaFave: Luxury Rentals at Zion--Kayenta Deluxe Villa (2BR/1BA)"),
    ("4140___8139", "LaFave: Luxury Rentals at Zion--Mount Kinesava Deluxe Villa (2BR/1BA)"),
    ("4140___8140", "LaFave: Luxury Rentals at Zion--Weeping Rock Deluxe Villa (2BR/1BA)"),
    ("4140___8141", "LaFave: Luxury Rentals at Zion--Angels Premier Villa (3BR/3BA)"),
    ("4140___8142", "LaFave: Luxury Rentals at Zion--Narrows Premier Villa (3BR/3BA)"),
    ("4140___8143", "LaFave: Luxury Rentals at Zion--Cathedral Premier Villa (3BR/3BA)"),
    ("4140___8144", "LaFave: Luxury Rentals at Zion--Virgin Premier Villa (3BR/3BA)"),
]


def _match_unit_id(
    unit_ids: list[str],
    group: str,
    key: Optional[str],
) -> str:
    # If key is None, map by group membership only (House / J.MT.)
    if key is None:
        if len(unit_ids) != 1:
            raise SystemExit(f"Ambiguous mapping for group={group} (expected 1 unit, got {len(unit_ids)}).")
        return unit_ids[0]

    key_l = key.lower()
    candidates: list[str] = []
    for u in unit_ids:
        name = _unit_name_from_unit_id(u).lower()
        if key_l in name:
            candidates.append(u)

    if len(candidates) == 1:
        return candidates[0]

    # If multiple candidates match, prefer startswith (more exact), otherwise first.
    starts = [u for u in candidates if _unit_name_from_unit_id(u).lower().startswith(key_l)]
    if len(starts) == 1:
        return starts[0]
    if len(candidates) > 0:
        return candidates[0]

    raise SystemExit(f"Could not map key='{key}' to any unit in group '{group}'.")


def main() -> None:
    args = parse_args()
    prop_id = args.property_id
    start = datetime.strptime(args.start_date, "%Y-%m-%d").date()
    end = datetime.strptime(args.end_date, "%Y-%m-%d").date()
    if end < start:
        raise SystemExit("end-date must be on or after start-date")

    root = Path(".").resolve()
    pricing_matrix_path = root / "output" / prop_id / "pricing" / "pricing_matrix_draft.csv"
    if not pricing_matrix_path.exists():
        raise SystemExit(f"Missing pricing matrix: {pricing_matrix_path}")

    pricing_matrix = pd.read_csv(pricing_matrix_path)

    # Load property config (events + listing_groups)
    inventory = load_property_inventory(prop_id)
    pricing_cfg = inventory.get("pricing") or {}
    events_key = f"events_{args.year}"
    events = pricing_cfg.get(events_key, [])

    with open(Path(root) / "config" / "properties" / f"{prop_id}.yaml", "r", encoding="utf-8") as f:
        prop_cfg = yaml.safe_load(f) or {}

    listing_groups = prop_cfg.get("listing_groups") or []
    unit_to_group: dict[str, str] = {}
    group_to_units: dict[str, list[str]] = {}
    for g in listing_groups:
        gname = g.get("name")
        for u in g.get("unit_ids") or []:
            unit_to_group[u] = gname
            group_to_units.setdefault(gname, []).append(u)

    # Map each PriceLabs listing to a unit_id in our pricing matrix.
    listing_id_to_unit_id: dict[str, str] = {}
    for listing_id, listing_name in PRICE_LABS_LISTINGS:
        title = _extract_listing_title(listing_name)
        group = _detect_group_from_title(title)
        if not group:
            raise SystemExit(f"Could not detect group from listing_name='{listing_name}' (title='{title}').")
        key = _extract_key_from_title(title, group)
        candidate_units = group_to_units.get(group, [])
        unit_id = _match_unit_id(candidate_units, group=group, key=key)
        listing_id_to_unit_id[listing_id] = unit_id

    if len(listing_id_to_unit_id) != len(PRICE_LABS_LISTINGS):
        raise SystemExit("Listing mapping incomplete (check group/key matching).")

    # Ensure all mapped unit_ids exist in the pricing matrix
    id_col = "unit_id" if "unit_id" in pricing_matrix.columns else "listing_group"
    if id_col != "unit_id":
        raise SystemExit("PriceLabs sheet expects per-listing pricing_matrix_draft.csv with unit_id rows.")

    matrix_units = set(pricing_matrix["unit_id"].unique().tolist())
    unmapped = [uid for uid in listing_id_to_unit_id.values() if uid not in matrix_units]
    if unmapped:
        raise SystemExit(f"Mapped unit_ids missing from pricing matrix: {unmapped[:5]}{'...' if len(unmapped)>5 else ''}")

    # Build event lookup by date (highest multiplier wins on overlap)
    event_by_date: dict[date, dict[str, object]] = {}
    for evt in events:
        try:
            s = datetime.strptime(str(evt.get("start_date")), "%Y-%m-%d").date()
            e = datetime.strptime(str(evt.get("end_date")), "%Y-%m-%d").date()
        except Exception:
            continue
        mult = float(evt.get("multiplier", 1.0) or 1.0)
        name = evt.get("name", "")
        cur = s
        while cur <= e:
            existing = event_by_date.get(cur)
            if existing is None or mult > float(existing.get("multiplier", 1.0)):
                event_by_date[cur] = {"name": name, "multiplier": mult}
            cur += timedelta(days=1)

    # Generate PriceLabs rows (daily; PriceLabs can ingest per-day fixed prices)
    weekdays = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    mapped_unit_ids = [listing_id_to_unit_id[lid] for lid, _ in PRICE_LABS_LISTINGS]

    # Speed up lookups: (unit_id, month_index) -> weekday rate columns
    if not set(weekdays).issubset(set(pricing_matrix.columns)):
        missing = [c for c in weekdays if c not in pricing_matrix.columns]
        raise SystemExit(f"pricing_matrix_draft.csv missing weekday columns: {missing}")

    pm_indexed = pricing_matrix.set_index(["unit_id", "month_index"])[weekdays]

    rows: list[list[object]] = []
    for d in (start + timedelta(days=i) for i in range((end - start).days + 1)):
        dow_name = d.strftime("%A")
        if dow_name not in weekdays:
            continue
        month_index = d.month
        evt = event_by_date.get(d)
        event_mult = float(evt.get("multiplier", 1.0) or 1.0) if evt else 1.0
        # User requested Reason always blank (even on event days).
        reason = ""

        start_str = _format_date_for_pricelabs(d)
        end_str = start_str

        # Minimum stay rule (corrected):
        # - Through end of April 2026: 1-night min ONLY for 1BR/1BA and 2BR/1BA units; otherwise 2-night min.
        # - Starting May 1, 2026: 2-night min for all units.
        is_through_apr = d <= date(2026, 4, 30)

        for (listing_id, listing_name), unit_id in zip(PRICE_LABS_LISTINGS, mapped_unit_ids):
            group = unit_to_group.get(unit_id, "")
            is_1br_1ba = "Suite BIG (1BR/1BA)" in group or "Suite SMALL (1BR/1BA)" in group
            is_2br_1ba = "Deluxe Villa (2BR/1BA)" in group
            min_stay = 1 if (is_through_apr and (is_1br_1ba or is_2br_1ba)) else 2

            try:
                base_rate = pm_indexed.at[(unit_id, month_index), dow_name]
            except KeyError:
                price = ""
            else:
                if pd.isna(base_rate) or base_rate is None:
                    price = ""
                else:
                    price = int(round(float(base_rate) * event_mult))

            rows.append(
                [
                    listing_id,
                    listing_name,
                    prop_cfg.get("pms_id", "resnexus") if prop_cfg else "resnexus",
                    reason,
                    start_str,
                    end_str,
                    price,
                    "USD",
                    "fixed",
                    min_stay,
                    "",  # minimum price (left blank)
                    "fixed",
                    "",  # maximum price (left blank)
                    "fixed",
                ]
            )

    cols = [
        "Listing Id",
        "Listing Name",
        "PMS Name",
        "Reason",
        "Start Date",
        "End Date",
        "Price",
        "Currency",
        "Price Type",
        "Minimum Stay",
        "Minimum Price",
        "Minimum Price Type",
        "Maximum Price",
        "Maximum Price Type",
    ]
    sheet = pd.DataFrame(rows, columns=cols)

    if args.output_path:
        out_path = Path(args.output_path).resolve()
    else:
        out_path = root / "output" / prop_id / "pricing" / f"pricelabs_sheet_{args.start_date}_to_{args.end_date}.csv"

    out_path.parent.mkdir(parents=True, exist_ok=True)
    sheet.to_csv(out_path, index=False)
    print(f"Wrote PriceLabs sheet: {out_path} ({len(sheet)} rows)")


if __name__ == "__main__":
    main()

