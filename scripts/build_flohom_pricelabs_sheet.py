"""Build a PriceLabs-format CSV for FLOHOM from the manual pricing sheet.

Source: data/FLOHOM/Flohom Baltimore Pricing - PricingSheet_2027_FLo1-16.csv
Output: output/flohom/pricing/pricelabs_sheet_<start>_to_<end>.csv

The source sheet is wide-format (one column per FLOHOM listing). This script
explodes it into the long PriceLabs upload format (one row per listing per
date), keeping the Reason column blank as requested.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_SOURCE = (
    PROJECT_ROOT
    / "data"
    / "FLOHOM"
    / "Flohom Baltimore Pricing - PricingSheet_2027_FLo1-16.csv"
)
DEFAULT_OUT_DIR = PROJECT_ROOT / "output" / "flohom" / "pricing"

# Mapping from the source CSV column header -> (Hostaway Listing Id, full Listing Name)
# IDs and names provided by the user.
LISTING_MAP: dict[str, tuple[str, str]] = {
    "Flohom 1": ("146908", "FLOHOM 01 -- FLOHOM 01 | Inner Harbor, Baltimore"),
    "Flohom 2": ("146944", "FLOHOM 02 -- FLOHOM 02 | Inner Harbor - Canton, Baltimore"),
    "Flohom 3": ("146946", "FLOHOM 03 -- FLOHOM 03 | Magothy River, Annapolis"),
    "Flohom 4": ("260001", "FLOHOM 04 -- FLOHOM 04 | National Harbor"),
    "Flohom 5": ("278502", "FLOHOM 05 -- FLOHOM 05 | Inner Harbor, Baltimore"),
    "Flohom 6": ("345316", "FLOHOM 06 -- FLOHOM 06 | National Harbor"),
    "Flohom 7": ("366176", "FLOHOM 07 -- FLOHOM 07 | Inner Harbor - Canton, Baltimore"),
    "Flohom 8": ("376368", "FLOHOM 08 -- FLOHOM 08 | National Harbor"),
    "Flohom 9": ("383097", "FLOHOM 09 -- FLOHOM 09 | Baltimore Peninsula"),
    "Flohom 10": ("404358", "FLOHOM 10 -- FLOHOM 10 | Virginia Beach"),
    "Flohom 11": ("427313", "FLOHOM 11 -- FLOHOM 11 | Virginia Beach"),
    "Flohom 12": ("422608", "FLOHOM 12 -- FLOHOM 12 | Inner Harbor - Fells Point, Baltimore"),
    "Flohom 13": ("485833", "FLOHOM 13 -- FLOHOM 13 | Inner Harbor, Baltimore"),
    "Flohom 14": ("407612", "FLOHOM 14 -- FLOHOM 14 | Pasadena, Chesapeake Bay"),
    "Flohom 15": ("467334", "FLOHOM 15 -- FLOHOM 15 | North Myrtle Beach"),
    "Flohom 16": ("510832", "FLOHOM 16 | Liberty Landing Marina"),
}

PMS_NAME = "hostaway"
CURRENCY = "USD"
PRICE_TYPE = "fixed"

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
        help="Optional output path. Defaults to output/flohom/pricing/pricelabs_sheet_<start>_to_<end>.csv",
    )
    return p.parse_args()


def _format_date(d: datetime) -> str:
    # PriceLabs accepts M/D/YY (matches existing WMB / SOS sheets in this repo).
    return f"{d.month}/{d.day}/{d.year % 100}"


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


def main() -> None:
    args = _parse_args()
    source = Path(args.source).resolve()
    if not source.exists():
        raise SystemExit(f"Source file not found: {source}")

    with open(source, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        # The header in the file uses 'Flohom N' for listing columns, plus Date/Day/Comments/LOS.
        rows_in = list(reader)

    # Sanity: ensure all expected listing columns are present
    missing = [c for c in LISTING_MAP if c not in (reader.fieldnames or [])]
    if missing:
        raise SystemExit(f"Source CSV missing columns: {missing}")

    out_rows: list[list[object]] = []
    parsed_dates: list[datetime] = []

    for row in rows_in:
        date_raw = (row.get("Date") or "").strip()
        if not date_raw:
            continue
        d = _parse_date(date_raw)
        parsed_dates.append(d)
        date_str = _format_date(d)

        los_raw = (row.get("LOS") or "").strip()
        try:
            min_stay = int(float(los_raw)) if los_raw else 2
        except ValueError:
            min_stay = 2

        for src_col, (listing_id, listing_name) in LISTING_MAP.items():
            price = _clean_int(row.get(src_col, ""))
            if price is None:
                continue
            out_rows.append(
                [
                    listing_id,
                    listing_name,
                    PMS_NAME,
                    "",  # Reason (intentionally blank)
                    date_str,
                    date_str,
                    price,
                    CURRENCY,
                    PRICE_TYPE,
                    min_stay,
                    "",  # Minimum Price
                    "fixed",
                    "",  # Maximum Price
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
        out_path = (
            DEFAULT_OUT_DIR
            / f"pricelabs_sheet_{start.isoformat()}_to_{end.isoformat()}.csv"
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(OUTPUT_COLUMNS)
        writer.writerows(out_rows)

    print(
        f"Wrote PriceLabs sheet: {out_path} "
        f"({len(out_rows)} rows, {len(parsed_dates)} days, {len(LISTING_MAP)} listings)"
    )


if __name__ == "__main__":
    main()
