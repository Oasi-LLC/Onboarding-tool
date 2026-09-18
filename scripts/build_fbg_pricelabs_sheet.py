"""Build PriceLabs override CSV for FBG from the daily tier-group pricing sheet.

Source: data/FBG/Pricing2026&27_FBG - Pricing_2027_Basse1&2_final.csv
Output: output/fbg/pricing/pricelabs_override_sheet_<start>_to_<end>.csv

Each PriceLabs listing maps to a tier group column (haus, king_room, …). Daily
group rates from the source sheet are applied per listing. LOS comes from the
source row. Reason is always left empty.

Listing Id / Name / tier group are defined explicitly below (no pet-friendly SKUs).
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
    / "FBG"
    / "Pricing2026&27_FBG - Pricing_2027_Basse1&2_final.csv"
)
DEFAULT_OUT_DIR = PROJECT_ROOT / "output" / "fbg" / "pricing"

# (Listing Id, Listing Name, tier group column in source sheet)
LISTING_MAP: list[tuple[str, str, str]] = [
    # haus
    ("203812___490007", "Onera Fredericksburg Cedar Haus", "haus"),
    ("203812___490024", "Onera Fredericksburg Juniper Haus", "haus"),
    ("203812___528745", "Onera Fredericksburg Pecan Haus", "haus"),
    # king_room
    (
        "203812___643775",
        "Onera Fredericksburg Great Lodge : King Room -- 6 units",
        "king_room",
    ),
    (
        "203812___657152",
        "Onera Fredericksburg Great Lodge : King Room (Accessible) -- 2 units",
        "king_room",
    ),
    # entry
    ("203812___643760", "Onera Fredericksburg Post Oak", "entry"),
    (
        "203812___655826",
        "Onera Fredericksburg Cypress Lodge | Sleeps 2 -- 2 units",
        "entry",
    ),
    # soft
    ("203812___634080", "Onera Fredericksburg Bluebonnet", "soft"),
    ("203812___364779", "Onera Fredericksburg Lantana Dome", "soft"),
    ("203812___364780", "Onera Fredericksburg Sage Safari", "soft"),
    ("203812___362535", "Onera Fredericksburg Cocoon", "soft"),
    ("203812___364782", "Onera Fredericksburg Buckeye Bungalow", "soft"),
    # core
    ("203812___643772", "Onera Fredericksburg Spiral", "core"),
    (
        "203812___643762",
        "Onera Fredericksburg Cypress Lodge | Sleeps 4 -- 2 units",
        "core",
    ),
    ("203812___643764", "Onera Fredericksburg Quonset -- 2 units", "core"),
    ("203812___643766", "Onera Fredericksburg Winecup -- 2 units", "core"),
    # signature
    (
        "203812___655827",
        "Onera Fredericksburg Cypress Lodge | Sleeps 6 -- 2 units",
        "signature",
    ),
    # hard
    ("203812___364778", "Onera Fredericksburg Live Oak Lodge", "hard"),
    ("203812___364776", "Onera Fredericksburg Spyglass", "hard"),
    ("203812___364773", "Onera Fredericksburg Walnut House", "hard"),
    # elite
    ("203812___364781", "Onera Fredericksburg Monarch", "elite"),
    ("203812___643773", "Onera Fredericksburg Monolith -- 2 units", "elite"),
    ("203812___643771", "Onera Fredericksburg Diamond -- 3 units", "elite"),
    # buyout
    ("203812___643757", "Onera Fredericksburg Great Lodge", "buyout"),
]

GROUP_COLUMNS = [
    "haus",
    "king_room",
    "entry",
    "soft",
    "core",
    "signature",
    "hard",
    "elite",
    "buyout",
]

PMS_NAME = "cloudbeds"
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


def main() -> None:
    args = _parse_args()
    source = Path(args.source).resolve()
    if not source.exists():
        raise SystemExit(f"Source file not found: {source}")

    listings = LISTING_MAP

    with open(source, "r", encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        rows_in = list(reader)

    out_rows: list[list[object]] = []
    parsed_dates: list[datetime] = []

    for row in rows_in:
        date_raw = (row.get("Date") or "").strip()
        if not date_raw:
            continue
        d = _parse_date(date_raw)
        parsed_dates.append(d)
        date_str = _format_date(d)

        los_raw = row.get("LOS", "")
        min_stay = _clean_int(str(los_raw)) if los_raw is not None else None
        if min_stay is None:
            min_stay = DEFAULT_MIN_STAY

        for listing_id, listing_name, group_id in listings:
            price = _clean_int(row.get(group_id, ""))
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

    n_days = len(parsed_dates)
    n_listings = len(listings)
    print(
        f"Wrote PriceLabs override sheet: {out_path} "
        f"({len(out_rows)} rows = {n_days} days × {n_listings} listings, PMS={PMS_NAME})"
    )


if __name__ == "__main__":
    main()
