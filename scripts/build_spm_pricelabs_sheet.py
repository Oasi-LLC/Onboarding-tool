"""Build PriceLabs override CSV for Spoon Mountain from the daily pricing sheet.

Source: data/SPM/2027pricingfull.csv
Output: output/spoon_mountain/pricing/pricelabs_override_sheet_<start>_to_<end>.csv

One rate column applies to all listings (Shaka/Chisum/Kingfisher).
PMS: ownerrez
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_SOURCE = PROJECT_ROOT / "data" / "SPM" / "2027pricingfull.csv"
DEFAULT_OUT_DIR = PROJECT_ROOT / "output" / "spoon_mountain" / "pricing"

RATE_COLUMN = "Shaka/Chisum/Kingfisher"

# PriceLabs Listing Id -> Listing Name (display; import keys on Listing Id)
LISTING_MAP: dict[str, tuple[str, str]] = {
    "Chisum": ("278915", "Chisum"),
    "Kingfisher": ("303587", "Kingfisher"),
    "Shaka": ("303588", "Shaka"),
}

PMS_NAME = "ownerrez"
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
    p.add_argument("--output", default=None, help="Optional output path.")
    p.add_argument(
        "--min-stay",
        type=int,
        default=DEFAULT_MIN_STAY,
        help=f"Default minimum stay when not in source (default {DEFAULT_MIN_STAY}).",
    )
    p.add_argument(
        "--blank-reason",
        action="store_true",
        help="Leave Reason empty even when Notes is set.",
    )
    return p.parse_args()


def _format_date(d: datetime) -> str:
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


def _find_rate_column(fieldnames: list[str]) -> str:
    if RATE_COLUMN in fieldnames:
        return RATE_COLUMN
    for col in fieldnames:
        if col and "shaka" in col.lower() and "kingfisher" in col.lower():
            return col
    raise ValueError(f"Rate column not found. Expected '{RATE_COLUMN}'. Got: {fieldnames}")


def main() -> None:
    args = _parse_args()
    source = Path(args.source).resolve()
    if not source.exists():
        raise SystemExit(f"Source file not found: {source}")

    with open(source, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = list(reader.fieldnames or [])
        rows_in = list(reader)

    rate_col = _find_rate_column(fieldnames)

    out_rows: list[list[object]] = []
    parsed_dates: list[datetime] = []

    for row in rows_in:
        date_raw = (row.get("Date") or "").strip()
        if not date_raw:
            continue
        d = _parse_date(date_raw)
        parsed_dates.append(d)
        date_str = _format_date(d)

        notes = (row.get("Notes") or row.get("NOTES") or "").strip()
        reason = "" if args.blank_reason else notes

        price = _clean_int(row.get(rate_col, ""))
        if price is None:
            continue

        for _unit, (listing_id, listing_name) in LISTING_MAP.items():
            out_rows.append(
                [
                    listing_id,
                    listing_name,
                    PMS_NAME,
                    reason,
                    date_str,
                    date_str,
                    price,
                    CURRENCY,
                    PRICE_TYPE,
                    args.min_stay,
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
        out_path = (
            DEFAULT_OUT_DIR
            / f"pricelabs_override_sheet_{start.isoformat()}_to_{end.isoformat()}.csv"
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)

    with open(out_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        writer.writerow(OUTPUT_COLUMNS)
        writer.writerows(out_rows)

    n_days = len(parsed_dates)
    n_listings = len(LISTING_MAP)
    print(
        f"Wrote PriceLabs override sheet: {out_path} "
        f"({len(out_rows)} rows = {n_days} days × {n_listings} listings, PMS={PMS_NAME})"
    )


if __name__ == "__main__":
    main()
