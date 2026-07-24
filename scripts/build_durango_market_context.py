#!/usr/bin/env python3
"""
Build AirDNA market-context CSVs for Adventure Inn Durango (secondary context).

Outputs under output/<property>/benchmark/:
  market_context_monthly.csv
  market_summer_compare.csv
  market_booking_window.csv
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.airdna import load_airdna
from src.property_config import load_property_inventory

MONTH_NAMES = {7: "July", 8: "August", 9: "September"}


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--property", default="adventure_inn_durango")
    p.add_argument("--months", default="7,8,9", help="Comma-separated stay months to highlight.")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    prop_id = args.property
    months = [int(x.strip()) for x in str(args.months).split(",") if x.strip()]
    inv = load_property_inventory(prop_id)
    airdna_cfg = inv.get("airdna") or {}
    data_dir = Path(airdna_cfg.get("data_dir") or f"data/airdna/{prop_id}")
    if not data_dir.is_absolute():
        data_dir = PROJECT_ROOT / data_dir

    out_dir = PROJECT_ROOT / "output" / prop_id / "benchmark"
    out_dir.mkdir(parents=True, exist_ok=True)

    if not data_dir.exists():
        raise SystemExit(f"AirDNA data dir not found: {data_dir}")

    airdna = load_airdna(data_dir)

    # Monthly market context
    frames = []
    if airdna.occupancy is not None:
        frames.append(airdna.occupancy)
    if airdna.adr is not None:
        frames.append(airdna.adr)
    if airdna.revpar is not None:
        frames.append(airdna.revpar)
    if airdna.revenue_avg is not None:
        frames.append(airdna.revenue_avg)

    monthly = None
    for f in frames:
        monthly = f if monthly is None else monthly.merge(f, on=["year", "month"], how="outer")
    if monthly is None or monthly.empty:
        raise SystemExit("No monthly AirDNA metrics loaded.")

    monthly = monthly.sort_values(["year", "month"]).reset_index(drop=True)
    monthly["month_name"] = monthly["month"].map(lambda m: MONTH_NAMES.get(int(m), pd.Timestamp(2000, int(m), 1).strftime("%B")))
    monthly["context_only"] = True
    monthly["filters_note"] = airdna_cfg.get("filters_note") or ""
    monthly.to_csv(out_dir / "market_context_monthly.csv", index=False)

    # Summer YoY market compare + property gap when KPI file exists
    summer_mkt = monthly.loc[monthly["month"].isin(months)].copy()
    prop_kpis_path = PROJECT_ROOT / "output" / prop_id / "analysis" / "summer" / "summer_year_month_kpis.csv"
    if prop_kpis_path.exists():
        prop = pd.read_csv(prop_kpis_path)
        prop = prop.rename(columns={
            "occupancy_pct": "property_occupancy_pct",
            "adr": "property_adr",
            "revpar": "property_revpar",
        })
        keep = ["year", "month", "property_occupancy_pct", "property_adr", "property_revpar"]
        summer = summer_mkt.merge(prop[keep], on=["year", "month"], how="left")
        if "occupancy_pct" in summer.columns:
            summer["occ_gap_ppt"] = (
                pd.to_numeric(summer["property_occupancy_pct"], errors="coerce")
                - pd.to_numeric(summer["occupancy_pct"], errors="coerce")
            ).round(2)
        if "adr" in summer.columns:
            summer["adr_gap"] = (
                pd.to_numeric(summer["property_adr"], errors="coerce")
                - pd.to_numeric(summer["adr"], errors="coerce")
            ).round(2)
        if "revpar" in summer.columns:
            summer["revpar_gap"] = (
                pd.to_numeric(summer["property_revpar"], errors="coerce")
                - pd.to_numeric(summer["revpar"], errors="coerce")
            ).round(2)
    else:
        summer = summer_mkt
    summer.to_csv(out_dir / "market_summer_compare.csv", index=False)

    # Booking-window / pace shape from AirDNA in-advance RevPAR
    if airdna.revpar_in_advance is not None:
        bw = airdna.revpar_in_advance.copy()
        bw["month_name"] = bw["month"].map(lambda m: MONTH_NAMES.get(int(m), str(m)))
        bw["context_only"] = True
        bw.to_csv(out_dir / "market_booking_window.csv", index=False)
    else:
        pd.DataFrame().to_csv(out_dir / "market_booking_window.csv", index=False)

    print(f"Wrote AirDNA market context under {out_dir}")
    print(f"  market_context_monthly.csv: {len(monthly)} rows")
    print(f"  market_summer_compare.csv: {len(summer)} rows")
    if airdna.revpar_in_advance is not None:
        print(f"  market_booking_window.csv: {len(airdna.revpar_in_advance)} rows")


if __name__ == "__main__":
    main()
