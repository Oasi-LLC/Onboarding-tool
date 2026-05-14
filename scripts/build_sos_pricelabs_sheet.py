"""Build a PriceLabs-format CSV for SOS from the manual pricing sheet.

Source: SOS 2026 Pricing Sheet - Mar 27 - Jan 28 ( SOS & M&M) (1).csv
Output: output/sos/pricing/pricelabs_sheet_<start>_to_<end>.csv

The source sheet is wide-format (one column per SOS listing group: 11BR / 12BR /
23BR / Maya / Mod). This script explodes it into the long PriceLabs upload
format (one row per listing per date), keeping the Reason column blank.
"""

from __future__ import annotations

import argparse
import csv
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent

DEFAULT_SOURCE = (
    PROJECT_ROOT
    / "SOS 2026 Pricing Sheet - Mar 27 - Jan 28 ( SOS & M&M) (1).csv"
)
DEFAULT_OUT_DIR = PROJECT_ROOT / "output" / "sos" / "pricing"

# Mapping from the source CSV column header -> (Guesty Listing Id, Listing Name)
# IDs/names taken from the existing
# output/sos/pricing/pricelabs_override_sheet_sos1_mayamod1_2027-03-01_to_2028-01-09.csv
LISTING_MAP: dict[str, tuple[str, str]] = {
    "11BR": (
        "686424c7c8d43b001321df29",
        "11BR Designer Villa! Poolside Dntwn PS! Sleeps 30",
    ),
    "12BR": (
        "68642480da9649000fc63fc5",
        "12BR/Sleeps 30! Designer Poolside Adventure Dwtn!",
    ),
    "23BR": (
        "686424d94cdb20000e10b8db",
        "23BR Designer Concept Buyout! True PS Experience!",
    ),
    "Maya": ("6901008185d27c0022d3ba14", "Maya -- Maya Hotel Retreat"),
    "Mod": ("6901037df868fc002a10e0d7", "Mod -- The Mod Hotel"),
}

PMS_NAME = "guesty"
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
        help="Optional output path. Defaults to output/sos/pricing/pricelabs_sheet_<start>_to_<end>.csv",
    )
    return p.parse_args()


def _format_date(d: datetime) -> str:
    # PriceLabs accepts M/D/YY (matches existing SOS override sheet style).
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
        rows_in = list(reader)
        fieldnames = reader.fieldnames or []

    missing = [c for c in LISTING_MAP if c not in fieldnames]
    if missing:
        raise SystemExit(f"Source CSV missing listing columns: {missing}")

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
