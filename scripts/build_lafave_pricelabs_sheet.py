"""Build a PriceLabs-format CSV for LaFave from the per-listing pricing sheet.

Source: LAFAVE Analysis - Pricing_sheet_v2_2027_perListing.csv
Output: output/lafave_zion/pricing/pricelabs_sheet_<start>_to_<end>.csv

The source sheet is wide-format (one column per unit_id). Rows 2–3 are group /
multiplier metadata; data rows start at row 4 with M/D/YYYY dates.
"""

from __future__ import annotations

import argparse
import csv
import sys
from datetime import datetime
from pathlib import Path

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_pricelabs_sheet import (  # noqa: E402
    PRICE_LABS_LISTINGS,
    _detect_group_from_title,
    _extract_key_from_title,
    _extract_listing_title,
    _match_unit_id,
)

DEFAULT_SOURCE = PROJECT_ROOT / "LAFAVE Analysis - Pricing_sheet_v2_2027_perListing.csv"
DEFAULT_OUT_DIR = PROJECT_ROOT / "output" / "lafave_zion" / "pricing"

PMS_NAME = "resnexus"
CURRENCY = "USD"
PRICE_TYPE = "fixed"
DEFAULT_MIN_STAY = 2

OUTPUT_COLUMNS = [
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


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--source", default=str(DEFAULT_SOURCE), help="Source pricing CSV.")
    p.add_argument(
        "--output",
        default=None,
        help="Optional output path. Defaults to output/lafave_zion/pricing/pricelabs_sheet_<start>_to_<end>.csv",
    )
    return p.parse_args()


def _format_date(d: datetime) -> str:
    return d.date().isoformat()


def _parse_date(raw: str) -> datetime:
    return datetime.strptime(raw.strip(), "%m/%d/%Y")


def _clean_int(raw: str) -> int | None:
    s = (raw or "").strip().replace(",", "").replace("$", "")
    if not s:
        return None
    try:
        return int(round(float(s)))
    except ValueError:
        return None


def _build_unit_to_listing() -> dict[str, tuple[str, str]]:
    prop_cfg_path = PROJECT_ROOT / "config" / "properties" / "lafave_zion.yaml"
    with open(prop_cfg_path, "r", encoding="utf-8") as f:
        prop_cfg = yaml.safe_load(f) or {}

    group_to_units: dict[str, list[str]] = {}
    for g in prop_cfg.get("listing_groups") or []:
        gname = g.get("name")
        group_to_units.setdefault(gname, []).extend(g.get("unit_ids") or [])

    unit_to_listing: dict[str, tuple[str, str]] = {}
    for listing_id, listing_name in PRICE_LABS_LISTINGS:
        title = _extract_listing_title(listing_name)
        group = _detect_group_from_title(title)
        if not group:
            raise SystemExit(f"Could not detect group from listing_name='{listing_name}'.")
        key = _extract_key_from_title(title, group)
        unit_id = _match_unit_id(group_to_units.get(group, []), group=group, key=key)
        unit_to_listing[unit_id] = (listing_id, listing_name)

    return unit_to_listing


def main() -> None:
    args = _parse_args()
    source = Path(args.source).resolve()
    if not source.exists():
        raise SystemExit(f"Source file not found: {source}")

    unit_to_listing = _build_unit_to_listing()

    with open(source, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.reader(f)
        all_rows = list(reader)

    if len(all_rows) < 4:
        raise SystemExit("Source CSV too short (expected header + metadata + data rows).")

    header = all_rows[0]
    # First column is blank (dates); skip DAY, notes.
    unit_columns: list[tuple[str, int]] = []
    for idx, col in enumerate(header):
        col = (col or "").strip()
        if not col or col.upper() == "DAY" or col.lower() == "notes":
            continue
        if col not in unit_to_listing:
            raise SystemExit(f"No PriceLabs mapping for unit column: {col!r}")
        unit_columns.append((col, idx))

    if len(unit_columns) != len(unit_to_listing):
        mapped = {u for u, _ in unit_columns}
        missing = sorted(set(unit_to_listing) - mapped)
        extra = sorted(mapped - set(unit_to_listing))
        raise SystemExit(
            f"Column count mismatch: {len(unit_columns)} unit columns vs "
            f"{len(unit_to_listing)} listings. missing={missing[:3]} extra={extra[:3]}"
        )

    out_rows: list[list[object]] = []
    parsed_dates: list[datetime] = []

    for row in all_rows[3:]:
        if not row:
            continue
        date_raw = (row[0] or "").strip()
        if not date_raw:
            continue
        d = _parse_date(date_raw)
        parsed_dates.append(d)
        date_str = _format_date(d)

        for unit_id, col_idx in unit_columns:
            listing_id, listing_name = unit_to_listing[unit_id]
            price_val = row[col_idx] if col_idx < len(row) else ""
            price = _clean_int(price_val)
            if price is None:
                continue
            out_rows.append(
                [
                    listing_id,
                    listing_name,
                    PMS_NAME,
                    "",
                    date_str,
                    date_str,
                    price,
                    CURRENCY,
                    PRICE_TYPE,
                    DEFAULT_MIN_STAY,
                    "",
                    "fixed",
                    "",
                    "fixed",
                ]
            )

    if not parsed_dates:
        raise SystemExit("No date rows found in source CSV.")

    start = min(parsed_dates).date()
    end = max(parsed_dates).date()

    if args.output:
        out_path = Path(args.output).resolve()
    else:
        out_path = DEFAULT_OUT_DIR / f"pricelabs_sheet_{start.isoformat()}_to_{end.isoformat()}.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(OUTPUT_COLUMNS)
        writer.writerows(out_rows)

    print(
        f"Wrote PriceLabs sheet: {out_path} "
        f"({len(out_rows)} rows, {len(parsed_dates)} days, {len(unit_columns)} listings)"
    )


if __name__ == "__main__":
    main()
