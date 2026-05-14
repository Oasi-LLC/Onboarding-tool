#!/usr/bin/env python3
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.analysis import prepare_canonical_for_analysis, resolve_analysis_window
from src.market_slug import load_market_slug_map
from src.property_config import load_property_inventory


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Compute FLOHOM day-of-week performance by market_slug.")
    p.add_argument("--property", required=True, help="Property id, e.g. flohom")
    p.add_argument("--canonical", default=None, help="Optional canonical.csv path")
    p.add_argument("--output", default=None, help="Optional output csv path")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    property_id = args.property
    inv = load_property_inventory(property_id)
    analysis_window = inv.get("analysis_window") or {}

    canonical_path = Path(args.canonical) if args.canonical else PROJECT_ROOT / "output" / property_id / "ingestion" / "canonical.csv"
    if not canonical_path.exists():
        raise SystemExit(f"Canonical file not found: {canonical_path}")

    cdf = pd.read_csv(canonical_path, usecols=["arrival_date"])
    cdf["arrival_date"] = pd.to_datetime(cdf["arrival_date"], errors="coerce")
    max_arrival = (
        pd.Timestamp(cdf["arrival_date"].max()).normalize()
        if cdf["arrival_date"].notna().any()
        else None
    )
    w_start, w_end = resolve_analysis_window(analysis_window, canonical_max_arrival=max_arrival)

    out_path = Path(args.output) if args.output else PROJECT_ROOT / "outputs" / property_id / "by_day_of_week_by_market.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(canonical_path)
    for c in ("arrival_date", "departure_date", "booking_date"):
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    df = prepare_canonical_for_analysis(df)
    df = df[(df["arrival_date"] >= w_start) & (df["arrival_date"] <= w_end)].copy()

    unit_to_market = load_market_slug_map(property_id=property_id, inventory=inv)
    df["market_slug"] = df["unit_id"].astype(str).map(unit_to_market).fillna("unmapped")

    # Explode to stay-nights by market for day-of-week attribution.
    stay = df[["market_slug", "arrival_date", "departure_date", "revenue", "nights"]].dropna(
        subset=["arrival_date", "departure_date", "revenue", "nights"]
    ).copy()
    stay = stay[stay["departure_date"] > stay["arrival_date"]].copy()
    stay["nights"] = pd.to_numeric(stay["nights"], errors="coerce")
    stay = stay[stay["nights"] > 0].copy()
    stay["rev_per_night"] = pd.to_numeric(stay["revenue"], errors="coerce") / stay["nights"]
    stay["stay_date"] = stay.apply(
        lambda r: pd.date_range(
            start=pd.Timestamp(r["arrival_date"]).normalize(),
            end=(pd.Timestamp(r["departure_date"]).normalize() - pd.Timedelta(days=1)),
            freq="D",
        ),
        axis=1,
    )
    stay = stay.explode("stay_date")
    stay["dow"] = pd.to_datetime(stay["stay_date"], errors="coerce").dt.day_name().str[:3]
    agg = (
        stay.groupby(["market_slug", "dow"], as_index=False)
        .agg(
            revenue=("rev_per_night", "sum"),
            room_nights=("stay_date", "count"),
        )
    )
    agg["adr"] = agg["revenue"] / agg["room_nights"]
    agg.loc[agg["room_nights"] <= 0, "adr"] = pd.NA

    # Occupancy by market × dow using active listing-day capacity in analysis window.
    listing_start_dates = {
        str(r.get("unit_id")): pd.to_datetime(r.get("listing_start_date"), errors="coerce")
        for r in (inv.get("room_types") or [])
        if isinstance(r, dict) and r.get("unit_id")
    }
    listing_unit_counts = inv.get("listing_unit_counts") or {}
    market_to_units: dict[str, list[str]] = {}
    for u, m in unit_to_market.items():
        market_to_units.setdefault(m, []).append(u)
    if "unmapped" in set(df["market_slug"].astype(str).unique()):
        market_to_units["unmapped"] = sorted(set(df.loc[df["market_slug"] == "unmapped", "unit_id"].astype(str)))

    date_spine = pd.date_range(w_start, w_end, freq="D")
    cap_rows = []
    for market_slug, units in market_to_units.items():
        for d in date_spine:
            cap = 0
            for u in units:
                start = listing_start_dates.get(u)
                if pd.isna(start):
                    start = w_start
                if pd.Timestamp(d).normalize() >= pd.Timestamp(start).normalize():
                    cap += int(listing_unit_counts.get(u, 1))
            cap_rows.append({"market_slug": market_slug, "dow": pd.Timestamp(d).day_name()[:3], "available_room_nights": cap})
    cap_df = pd.DataFrame(cap_rows).groupby(["market_slug", "dow"], as_index=False).agg(
        available_room_nights=("available_room_nights", "sum")
    )
    out = agg.merge(cap_df, on=["market_slug", "dow"], how="left")
    out["occupancy"] = (out["room_nights"] / out["available_room_nights"]) * 100.0
    out.loc[out["available_room_nights"] <= 0, "occupancy"] = pd.NA
    out["revpar"] = out["revenue"] / out["available_room_nights"]
    out.loc[out["available_room_nights"] <= 0, "revpar"] = pd.NA
    out = out[["market_slug", "dow", "revenue", "room_nights", "adr", "occupancy", "revpar"]].sort_values(
        ["market_slug", "dow"]
    )
    out.to_csv(out_path, index=False)
    print(f"Wrote {out_path} ({len(out)} rows)")


if __name__ == "__main__":
    main()
