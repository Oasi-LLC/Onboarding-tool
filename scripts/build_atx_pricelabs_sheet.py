"""Build PriceLabs override CSV for ATX from the manual pricing sheet.

Source: data/ATX/Atx Pricing Sheet 2025 - 2027 - Sheet19.csv
Output: output/atx/pricing/pricelabs_override_sheet_<start>_to_<end>.csv

Listing mapping (Hostaway):
  320203  Sunstrip
  511788  MLVRN (Loft Only)     -> Malvern (4BR) column
  389561  MLVRN (Loft + Airstream) -> Malvern (5BR) column
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_SOURCE = (
    PROJECT_ROOT / "data" / "ATX" / "Atx Pricing Sheet 2025 - 2027 - Sheet19.csv"
)
DEFAULT_OUT_DIR = PROJECT_ROOT / "output" / "atx" / "pricing"

# Source sheet column -> (PriceLabs Listing Id, Listing Name)
LISTING_MAP: dict[str, tuple[str, str]] = {
    "Sunstrip": (
        "320203",
        "Sunstrip -- Best Location! 6 beds! Patio+Fire Pit+Game Room",
    ),
    "Malvern (4BR)": (
        "511788",
        "MLVRN (Loft Only) -- Hot Tub! Heated Pool! BBQ! Loft in the Trees",
    ),
    "Malvern (5BR)": (
        "389561",
        "MLVRN (Loft + Airstream) -- Hot Tub! Heated Pool! BBQ! Loft in Trees+Airstream",
    ),
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
        help="Optional output path.",
    )
    p.add_argument(
        "--blank-reason",
        action="store_true",
        help="Leave Reason empty even when NOTES is set (default: use NOTES as Reason).",
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


def main() -> None:
    args = _parse_args()
    source = Path(args.source).resolve()
    if not source.exists():
        raise SystemExit(f"Source file not found: {source}")

    with open(source, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        fieldnames = reader.fieldnames or []
        rows_in = list(reader)

    missing = [c for c in LISTING_MAP if c not in fieldnames]
    if missing:
        raise SystemExit(f"Source CSV missing columns: {missing}. Found: {fieldnames}")

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

        notes = (row.get("NOTES") or "").strip()
        reason = "" if args.blank_reason else notes

        for src_col, (listing_id, listing_name) in LISTING_MAP.items():
            price = _clean_int(row.get(src_col, ""))
            if price is None:
                continue
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
                    min_stay,
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

    print(
        f"Wrote PriceLabs override sheet: {out_path} "
        f"({len(out_rows)} rows = {len(parsed_dates)} days × {len(LISTING_MAP)} listings)"
    )


if __name__ == "__main__":
    main()
