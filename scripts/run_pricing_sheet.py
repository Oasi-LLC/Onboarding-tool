from __future__ import annotations

import argparse
from datetime import date, datetime, timedelta
from pathlib import Path
import sys

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.property_config import load_property_inventory


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a daily pricing sheet from the pricing matrix and property event config."
    )
    parser.add_argument(
        "--property",
        dest="property_id",
        required=True,
        help="Property ID (used to locate config and output/<property_id>/pricing).",
    )
    parser.add_argument(
        "--matrix-file",
        dest="matrix_file",
        default="pricing_matrix_draft.csv",
        help="CSV under output/<property>/pricing/ (default: per-listing pricing_matrix_draft.csv; "
        "use pricing_matrix_group_draft.csv for listing-group columns).",
    )
    parser.add_argument(
        "--start-date",
        dest="start_date",
        required=True,
        help="Start date for the sheet (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--end-date",
        dest="end_date",
        required=True,
        help="End date for the sheet (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--year",
        dest="year",
        type=int,
        default=None,
        help="Deprecated: ignored when pricing.events_* blocks exist. If set and no events_* keys, "
        "load only pricing.events_<year>.",
    )
    return parser.parse_args()


def _daterange(start: date, end: date):
    current = start
    while current <= end:
        yield current
        current += timedelta(days=1)


def _event_lists_from_pricing_cfg(pricing_cfg: dict, year_fallback: int | None) -> list[dict]:
    """Collect event dicts from all pricing.events_* keys (e.g. events_2026, events_2027)."""
    out: list[dict] = []
    keys = sorted(
        k for k in pricing_cfg.keys() if isinstance(k, str) and k.startswith("events_")
    )
    for k in keys:
        ev = pricing_cfg.get(k)
        if isinstance(ev, list):
            for item in ev:
                if isinstance(item, dict):
                    out.append(item)
    if not out and year_fallback is not None:
        ev = pricing_cfg.get(f"events_{year_fallback}")
        if isinstance(ev, list):
            for item in ev:
                if isinstance(item, dict):
                    out.append(item)
    return out


def main() -> None:
    args = parse_args()
    prop_id = args.property_id
    start = datetime.strptime(args.start_date, "%Y-%m-%d").date()
    end = datetime.strptime(args.end_date, "%Y-%m-%d").date()
    if end < start:
        raise SystemExit("end-date must be on or after start-date")

    root = PROJECT_ROOT
    pricing_matrix_path = root / "output" / prop_id / "pricing" / str(args.matrix_file).strip()
    if not pricing_matrix_path.exists():
        raise SystemExit(f"Pricing matrix not found: {pricing_matrix_path}. Run run_pricing_matrix.py first.")

    print(f"Property: {prop_id}")
    print(f"Using pricing matrix: {pricing_matrix_path}")
    print(f"Date range: {start} to {end}")

    matrix = pd.read_csv(pricing_matrix_path)

    # Load property config for events and unit_ids
    inventory = load_property_inventory(prop_id)
    pricing_cfg = inventory.get("pricing") or {}
    events = _event_lists_from_pricing_cfg(pricing_cfg, args.year)

    # Build a simple event lookup by date
    event_by_date: dict[date, dict] = {}
    for evt in events:
        try:
            s = datetime.strptime(str(evt.get("start_date")), "%Y-%m-%d").date()
            e = datetime.strptime(str(evt.get("end_date")), "%Y-%m-%d").date()
        except Exception:
            continue
        name = evt.get("name", "")
        mult = float(evt.get("multiplier", 1.0))
        current = s
        while current <= e:
            # If multiple events overlap, we could combine; for now, keep the highest multiplier
            existing = event_by_date.get(current)
            if existing is None or mult > existing.get("multiplier", 1.0):
                event_by_date[current] = {"name": name, "multiplier": mult}
            current += timedelta(days=1)

    # Columns in matrix: either unit_id (per-listing) or listing_group (group-level),
    # plus month_index and Monday..Sunday.
    id_col = "unit_id" if "unit_id" in matrix.columns else "listing_group"
    unit_ids = matrix[id_col].unique().tolist()
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    rows = []
    for d in _daterange(start, end):
        dow_name = d.strftime("%A")  # Monday..Sunday
        if dow_name not in days:
            continue
        month_index = d.month

        # Base row
        row: dict[str, object] = {
            "date": d.isoformat(),
            "day_of_week": dow_name,
        }

        # Look up base rates from matrix for each listing
        for unit in unit_ids:
            match = matrix[
                (matrix[id_col] == unit)
                & (matrix["month_index"] == month_index)
            ]
            if match.empty:
                rate = None
            else:
                rate = match.iloc[0].get(dow_name)
            row[unit] = rate

        # Apply event multiplier if any
        evt = event_by_date.get(d)
        if evt:
            m = evt.get("multiplier", 1.0) or 1.0
            for unit in unit_ids:
                if row[unit] is not None:
                    try:
                        row[unit] = round(float(row[unit]) * float(m))
                    except (TypeError, ValueError):
                        pass
            row["notes"] = evt.get("name", "")
        else:
            row["notes"] = ""

        rows.append(row)

    sheet = pd.DataFrame(rows)

    # Write sheet to output/<property>/pricing/pricing_sheet_<start>_<end>.csv
    out_dir = pricing_matrix_path.parent
    out_dir.mkdir(parents=True, exist_ok=True)
    group_tag = "_group" if "listing_group" in matrix.columns else ""
    out_path = out_dir / f"pricing_sheet{group_tag}_{start.isoformat()}_{end.isoformat()}.csv"
    sheet.to_csv(out_path, index=False)

    print(f"Wrote pricing sheet: {out_path} ({len(sheet)} rows)")


if __name__ == "__main__":
    main()

