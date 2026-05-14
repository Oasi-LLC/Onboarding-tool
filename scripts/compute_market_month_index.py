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
    p = argparse.ArgumentParser(description="Compute FLOHOM monthly performance by market_slug.")
    p.add_argument("--property", required=True, help="Property id, e.g. flohom")
    p.add_argument("--canonical", default=None, help="Optional canonical.csv path")
    p.add_argument("--output", default=None, help="Optional output csv path")
    return p.parse_args()


def _active_room_nights_for_month(
    listings_in_market: list[str],
    listing_start_dates: dict[str, pd.Timestamp],
    listing_unit_counts: dict[str, int],
    year: int,
    month: int,
) -> int:
    m_start = pd.Timestamp(year=year, month=month, day=1)
    m_end = m_start + pd.offsets.MonthEnd(0)
    total = 0
    for unit in listings_in_market:
        start = listing_start_dates.get(unit)
        if start is None:
            start = m_start
        effective_start = max(m_start, start)
        if effective_start > m_end:
            continue
        days = int((m_end - effective_start).days + 1)
        total += int(listing_unit_counts.get(unit, 1)) * days
    return int(total)


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

    out_path = Path(args.output) if args.output else PROJECT_ROOT / "outputs" / property_id / "monthly_performance_by_market.csv"
    out_path.parent.mkdir(parents=True, exist_ok=True)

    df = pd.read_csv(canonical_path)
    for c in ("arrival_date", "departure_date", "booking_date"):
        if c in df.columns:
            df[c] = pd.to_datetime(df[c], errors="coerce")
    df = prepare_canonical_for_analysis(df)
    df = df[(df["arrival_date"] >= w_start) & (df["arrival_date"] <= w_end)].copy()

    unit_to_market = load_market_slug_map(property_id=property_id, inventory=inv)
    df["market_slug"] = df["unit_id"].astype(str).map(unit_to_market).fillna("unmapped")

    listing_start_dates = {
        str(r.get("unit_id")): pd.to_datetime(r.get("listing_start_date"), errors="coerce")
        for r in (inv.get("room_types") or [])
        if isinstance(r, dict) and r.get("unit_id")
    }
    listing_unit_counts = inv.get("listing_unit_counts") or {}

    df["year"] = df["arrival_date"].dt.year
    df["month"] = df["arrival_date"].dt.month
    grp = (
        df.groupby(["market_slug", "year", "month"], as_index=False)
        .agg(
            revenue=("revenue", "sum"),
            room_nights=("nights", "sum"),
        )
    )
    grp["adr"] = grp["revenue"] / grp["room_nights"]
    grp.loc[grp["room_nights"] <= 0, "adr"] = pd.NA

    market_to_listings: dict[str, list[str]] = {}
    for unit, market in unit_to_market.items():
        market_to_listings.setdefault(market, []).append(unit)
    if "unmapped" in set(df["market_slug"].astype(str).unique()):
        unmapped_units = sorted(set(df.loc[df["market_slug"] == "unmapped", "unit_id"].astype(str)))
        market_to_listings["unmapped"] = unmapped_units

    avail = []
    for r in grp.itertuples(index=False):
        avail.append(
            _active_room_nights_for_month(
                listings_in_market=market_to_listings.get(str(r.market_slug), []),
                listing_start_dates=listing_start_dates,
                listing_unit_counts=listing_unit_counts,
                year=int(r.year),
                month=int(r.month),
            )
        )
    grp["available_room_nights"] = avail
    grp["occupancy"] = (grp["room_nights"] / grp["available_room_nights"]) * 100.0
    grp.loc[grp["available_room_nights"] <= 0, "occupancy"] = pd.NA
    grp["revpar"] = grp["revenue"] / grp["available_room_nights"]
    grp.loc[grp["available_room_nights"] <= 0, "revpar"] = pd.NA

    # Market-local month_index (month climatology / annual mean climatology)
    month_clim = (
        grp.groupby(["market_slug", "month"], as_index=False)
        .agg(month_revpar=("revpar", "mean"))
    )
    market_mean = (
        month_clim.groupby("market_slug", as_index=False)
        .agg(annual_mean_revpar=("month_revpar", "mean"), distinct_months=("month", "nunique"))
    )
    month_clim = month_clim.merge(market_mean, on="market_slug", how="left")
    month_clim["month_index_market"] = month_clim["month_revpar"] / month_clim["annual_mean_revpar"]

    # Portfolio fallback month index (used for sparse markets)
    pf_month = grp.groupby("month", as_index=False).agg(revpar=("revpar", "mean"))
    pf_mean = float(pf_month["revpar"].mean()) if len(pf_month) else 1.0
    pf_month["month_index_portfolio"] = pf_month["revpar"] / pf_mean if pf_mean else 1.0

    out = grp.merge(
        month_clim[["market_slug", "month", "month_index_market", "distinct_months"]],
        on=["market_slug", "month"],
        how="left",
    ).merge(
        pf_month[["month", "month_index_portfolio"]],
        on="month",
        how="left",
    )
    sparse = out["distinct_months"].fillna(0).astype(int) < 6
    out["month_index"] = out["month_index_market"]
    out.loc[sparse, "month_index"] = out.loc[sparse, "month_index_portfolio"]
    out["market_index_source"] = "market"
    out.loc[sparse, "market_index_source"] = "portfolio_fallback"

    out = out[
        [
            "market_slug",
            "month",
            "year",
            "revenue",
            "room_nights",
            "adr",
            "occupancy",
            "revpar",
            "month_index",
            "market_index_source",
        ]
    ].sort_values(["market_slug", "year", "month"])
    out.to_csv(out_path, index=False)
    print(f"Wrote {out_path} ({len(out)} rows)")


if __name__ == "__main__":
    main()
