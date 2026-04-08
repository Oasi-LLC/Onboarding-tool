#!/usr/bin/env python3
"""
Run AirDNA market benchmarking for a property.

Reads analysis outputs (from run_analysis.py) and AirDNA exports, then
produces a benchmark report comparing the property to the local market.

Usage (from project root):
  python scripts/run_market_benchmark.py --property <property_id>

Example:
  python scripts/run_market_benchmark.py --property lafave_zion

Outputs:
  output/<property_id>/benchmark/market_benchmark.csv   — full month-by-month table
  output/<property_id>/benchmark/summary.txt            — human-readable report
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.airdna import (
    load_airdna,
    revpar_gap,
    occupancy_gap,
    bedroom_revenue_comparison,
    percentile_positioning,
    peak_days,
    market_seasonality_index,
)
from src.analysis import get_analysis_years
from src.property_config import load_property_inventory


def resolve_benchmark_years() -> list[int]:
    """Single source of truth for benchmark year window."""
    y1, y2 = get_analysis_years()
    return [y1, y2]


# ── Helpers to extract property metrics from analysis CSVs ──────────────────

def _load_property_monthly(analysis_dir: Path) -> pd.DataFrame:
    """
    Load monthly_performance.csv and return property-level rows with
    columns: year, month, revpar, occupancy_pct.

    Handles two formats:
      - PROPERTY-row format: has unit_id column, filter to unit_id == "PROPERTY"
      - Aggregate format: has year_month column (e.g. "2024-01"), already property-level
    """
    p = analysis_dir / "monthly_performance.csv"
    if not p.exists():
        return pd.DataFrame()
    df = pd.read_csv(p)

    if "unit_id" in df.columns:
        # Multi-unit format — filter to PROPERTY row
        prop = df[df["unit_id"] == "PROPERTY"].copy()
        prop["year"] = pd.to_numeric(prop.get("year", pd.Series(dtype=float)), errors="coerce")
        prop["month"] = pd.to_numeric(prop.get("month", pd.Series(dtype=float)), errors="coerce")
    elif "year_month" in df.columns:
        # Aggregate format — parse year_month into year + month
        prop = df.copy()
        ym = prop["year_month"].astype(str)
        prop["year"] = pd.to_numeric(ym.str[:4], errors="coerce")
        prop["month"] = pd.to_numeric(ym.str[5:7], errors="coerce")
    else:
        return pd.DataFrame()

    prop["revpar"] = pd.to_numeric(prop.get("revpar", pd.Series(dtype=float)), errors="coerce")
    prop["occupancy_pct"] = pd.to_numeric(prop.get("occupancy_pct", pd.Series(dtype=float)), errors="coerce")
    return prop[["year", "month", "revpar", "occupancy_pct"]].dropna(subset=["year", "month"])


def _load_property_revpar_monthly(analysis_dir: Path) -> pd.DataFrame:
    prop = _load_property_monthly(analysis_dir)
    if prop.empty or "revpar" not in prop.columns:
        return pd.DataFrame()
    return prop[["year", "month", "revpar"]].dropna(subset=["revpar"])


def _load_property_occ_monthly(analysis_dir: Path) -> pd.DataFrame:
    prop = _load_property_monthly(analysis_dir)
    if prop.empty or "occupancy_pct" not in prop.columns:
        return pd.DataFrame()
    return prop[["year", "month", "occupancy_pct"]].dropna(subset=["occupancy_pct"])


def _bedroom_map_from_groups(listing_groups: list[dict]) -> dict[str, str]:
    """Map listing_group name → closest BR label (1BR, 2BR, etc.)."""
    mapping = {}
    for g in listing_groups:
        br = g.get("bedrooms")
        name = g.get("name")
        if br and name:
            label = f"{int(br)}BR" if int(br) <= 5 else "6BR"
            mapping[name] = label
    return mapping


def _property_br_revenue(analysis_dir: Path, listing_groups: list[dict]) -> dict[str, float]:
    """
    Average monthly revenue per bedroom category for this property,
    derived from adr_by_listing_by_month.csv + overall_summary.csv.
    Uses overall_summary revenue and night_count to estimate avg monthly revenue.
    """
    overall_p = analysis_dir / "overall_summary.csv"
    if not overall_p.exists():
        return {}
    overall = pd.read_csv(overall_p)
    overall = overall[overall["unit_id"] != "PROPERTY"].copy()
    overall["revenue"] = pd.to_numeric(overall.get("revenue", pd.Series(dtype=float)), errors="coerce")

    # Build unit → group name map
    unit_to_group: dict[str, str] = {}
    for g in listing_groups:
        for uid in g.get("unit_ids") or []:
            unit_to_group[str(uid)] = g.get("name", "")

    # Build group → bedroom label map
    br_map = _bedroom_map_from_groups(listing_groups)

    overall["group"] = overall["unit_id"].map(lambda u: unit_to_group.get(str(u)))
    overall["bedroom"] = overall["group"].map(lambda g: br_map.get(g) if g else None)
    overall = overall.dropna(subset=["bedroom"])

    # 24 months of data → divide by 24 for average monthly revenue per unit
    group_rev = overall.groupby("bedroom")["revenue"].sum()
    group_count = overall.groupby("bedroom")["unit_id"].count()
    # per-unit per-month average
    result = {}
    for br in group_rev.index:
        avg_monthly = group_rev[br] / group_count[br] / 24
        result[br] = round(avg_monthly, 2)
    return result


def _property_avg_monthly_revenue(analysis_dir: Path) -> float:
    """Overall average monthly revenue per listing (all units, 24 months)."""
    overall_p = analysis_dir / "overall_summary.csv"
    if not overall_p.exists():
        return float("nan")
    df = pd.read_csv(overall_p)
    prop_row = df[df["unit_id"] == "PROPERTY"]
    if prop_row.empty:
        return float("nan")
    total_rev = pd.to_numeric(prop_row["revenue"].iloc[0], errors="coerce")
    # Estimate unit count
    n_units = len(df) - 1
    if n_units <= 0:
        return float("nan")
    return float(total_rev / n_units / 24)


# ── Report formatting ────────────────────────────────────────────────────────

def _fmt_pct(v) -> str:
    try:
        return f"{float(v):+.1f}%"
    except Exception:
        return "n/a"


def _fmt_dollar(v) -> str:
    try:
        return f"${float(v):,.0f}"
    except Exception:
        return "n/a"


def _section(title: str, width: int = 72) -> str:
    return f"\n{'─' * width}\n  {title}\n{'─' * width}"


def build_report(
    prop_id: str,
    years: list[int],
    revpar_df: pd.DataFrame,
    occ_df: pd.DataFrame,
    br_df: pd.DataFrame,
    pct_info: dict,
    top_days: pd.DataFrame,
    seasonality_df: pd.DataFrame,
) -> str:
    lines = []
    lines.append(f"\nAirDNA Market Benchmark Report — {prop_id}")
    lines.append(f"Analysis years: {', '.join(str(y) for y in years)}\n")

    # 1. RevPAR gap
    lines.append(_section("1. RevPAR Gap (Property vs. Market)"))
    if not revpar_df.empty:
        avg_gap = revpar_df["gap"].mean()
        avg_gap_pct = revpar_df["gap_pct"].mean()
        lines.append(f"  Average gap over period: {_fmt_dollar(avg_gap)} ({avg_gap_pct:+.1f}%)")
        lines.append(f"  {'Year':<6}{'Month':<8}{'Market RevPAR':>14}{'Property RevPAR':>16}{'Gap':>10}{'Gap %':>8}")
        for _, r in revpar_df.iterrows():
            lines.append(
                f"  {int(r['year']):<6}{int(r['month']):<8}"
                f"{_fmt_dollar(r['market_revpar']):>14}"
                f"{_fmt_dollar(r['property_revpar']):>16}"
                f"{_fmt_dollar(r['gap']):>10}"
                f"{r['gap_pct']:>+7.1f}%"
            )
    else:
        lines.append("  No RevPAR data available.")

    # 2. Occupancy gap
    lines.append(_section("2. Occupancy Divergence (Property vs. Market)"))
    if not occ_df.empty:
        avg_gap_ppt = occ_df["gap_ppt"].mean()
        lines.append(f"  Average occupancy gap over period: {avg_gap_ppt:+.1f} ppt")
        lines.append(f"  {'Year':<6}{'Month':<8}{'Market Occ%':>12}{'Property Occ%':>14}{'Gap (ppt)':>10}")
        for _, r in occ_df.iterrows():
            lines.append(
                f"  {int(r['year']):<6}{int(r['month']):<8}"
                f"{r['market_occ']:>11.1f}%"
                f"{r['property_occ']:>13.1f}%"
                f"{r['gap_ppt']:>+9.1f}"
            )
    else:
        lines.append("  No occupancy data available.")

    # 3. Bedroom revenue comparison
    lines.append(_section("3. Bedroom-Level Revenue Comparison (avg monthly per unit)"))
    if not br_df.empty:
        lines.append(f"  {'Bedroom':<10}{'Market Avg':>12}{'Property Avg':>14}{'Gap':>10}{'Gap %':>8}")
        for _, r in br_df.iterrows():
            lines.append(
                f"  {r['bedroom']:<10}"
                f"{_fmt_dollar(r['market_avg_monthly']):>12}"
                f"{_fmt_dollar(r['property_avg_monthly']):>14}"
                f"{_fmt_dollar(r['gap']):>10}"
                f"{r['gap_pct']:>+7.1f}%"
            )
    else:
        lines.append("  No bedroom revenue data available.")

    # 4. Percentile positioning
    lines.append(_section("4. Revenue Percentile Positioning"))
    if pct_info:
        lines.append(f"  Property avg monthly revenue per unit: {_fmt_dollar(pct_info.get('property_avg'))}")
        lines.append(f"  Market 25th pct: {_fmt_dollar(pct_info.get('p25'))}")
        lines.append(f"  Market 50th pct: {_fmt_dollar(pct_info.get('p50'))}")
        lines.append(f"  Market 75th pct: {_fmt_dollar(pct_info.get('p75'))}")
        lines.append(f"  Market 90th pct: {_fmt_dollar(pct_info.get('p90'))}")
        lines.append(f"\n  → Property estimated band: {pct_info.get('estimated_band', 'n/a')}")
    else:
        lines.append("  No percentile data available.")

    # 5. Peak days
    lines.append(_section("5. Top Market RevPAR Days (Peak Demand Targets)"))
    if not top_days.empty:
        lines.append(f"  {'Date':<14}{'Market RevPAR':>14}")
        for _, r in top_days.iterrows():
            lines.append(f"  {str(r['Date'].date()):<14}{_fmt_dollar(r['RevPAR']):>14}")
    else:
        lines.append("  No peak-day data available.")

    # 6. Market seasonality index
    lines.append(_section("6. Market Seasonality Index (RevPAR vs. Annual Average)"))
    if not seasonality_df.empty:
        lines.append(f"  {'Month':<8}{'Market RevPAR':>14}{'Seasonality Index':>18}")
        for _, r in seasonality_df.iterrows():
            lines.append(
                f"  {int(r['month']):<8}"
                f"{_fmt_dollar(r['market_revpar_avg']):>14}"
                f"{r['seasonality_index']:>17.1f}%"
            )
    else:
        lines.append("  No seasonality data available.")

    lines.append("\n")
    return "\n".join(lines)


# ── Main ─────────────────────────────────────────────────────────────────────

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AirDNA market benchmarking report.")
    parser.add_argument("--property", dest="property_id", required=True)
    parser.add_argument("--analysis-dir", dest="analysis_dir", default=None)
    parser.add_argument("--airdna-dir", dest="airdna_dir", default=None)
    parser.add_argument("--output-dir", dest="output_dir", default=None)
    parser.add_argument("--top-peak-days", dest="top_n", type=int, default=20)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    prop_id = args.property_id
    root = Path(".").resolve()

    analysis_dir = (
        Path(args.analysis_dir).resolve()
        if args.analysis_dir
        else root / "output" / prop_id / "analysis"
    )
    output_dir = (
        Path(args.output_dir).resolve()
        if args.output_dir
        else root / "output" / prop_id / "benchmark"
    )

    if not analysis_dir.exists():
        raise SystemExit(f"Analysis directory not found: {analysis_dir}")

    # Load property config
    inventory = load_property_inventory(prop_id)
    mkt_cfg = inventory.get("market_data") or {}
    listing_groups = inventory.get("listing_groups") or []

    # Resolve AirDNA data directory
    if args.airdna_dir:
        airdna_dir = Path(args.airdna_dir).resolve()
    elif mkt_cfg.get("airdna_dir"):
        airdna_dir = (root / mkt_cfg["airdna_dir"]).resolve()
    else:
        airdna_dir = root / "data" / "airdna" / prop_id

    if not airdna_dir.exists():
        raise SystemExit(f"AirDNA data directory not found: {airdna_dir}")

    # Which years to compare
    gap_threshold = float(mkt_cfg.get("gap_threshold", 0.05))
    target_percentile = mkt_cfg.get("target_percentile", "75%")

    print(f"Property  : {inventory.get('property_name', prop_id)}")
    print(f"Analysis  : {analysis_dir}")
    print(f"AirDNA    : {airdna_dir}")

    # Load AirDNA data
    airdna = load_airdna(airdna_dir)

    # Use the same canonical analysis window as the main pipeline.
    years = resolve_benchmark_years()
    avail_years: list[int] = []
    if airdna.revpar is not None:
        avail_years = sorted(airdna.revpar["year"].dropna().astype(int).unique().tolist())
    if not avail_years:
        raise SystemExit("No market RevPAR data loaded — cannot benchmark analysis years.")
    missing_market_years = [y for y in years if y not in avail_years]
    if missing_market_years:
        print(
            "Warning: AirDNA data missing analysis year(s): "
            + ", ".join(str(y) for y in missing_market_years)
            + ". Benchmark outputs may be partial."
        )

    print(f"Benchmark years: {years}\n")

    # Pull property metrics from analysis outputs
    revpar_monthly = _load_property_revpar_monthly(analysis_dir)
    occ_monthly = _load_property_occ_monthly(analysis_dir)
    prop_br_revenue = _property_br_revenue(analysis_dir, listing_groups)
    prop_avg_rev = _property_avg_monthly_revenue(analysis_dir)

    # Run comparisons
    revpar_df = revpar_gap(airdna, revpar_monthly, years)
    occ_df = occupancy_gap(airdna, occ_monthly, years)
    br_df = bedroom_revenue_comparison(airdna, prop_br_revenue, years)
    pct_info = percentile_positioning(airdna, prop_avg_rev, years)
    top_days_df = peak_days(airdna, top_n=args.top_n, year_filter=years)
    seas_df = market_seasonality_index(airdna, years)

    # Build and print report
    report = build_report(
        prop_id, years,
        revpar_df, occ_df, br_df, pct_info, top_days_df, seas_df,
    )
    print(report)

    # Write outputs
    output_dir.mkdir(parents=True, exist_ok=True)

    summary_path = output_dir / "summary.txt"
    summary_path.write_text(report, encoding="utf-8")
    print(f"Wrote report : {summary_path}")

    if not revpar_df.empty:
        revpar_df.to_csv(output_dir / "revpar_gap.csv", index=False)
    if not occ_df.empty:
        occ_df.to_csv(output_dir / "occupancy_gap.csv", index=False)
    if not br_df.empty:
        br_df.to_csv(output_dir / "bedroom_revenue.csv", index=False)
    if not top_days_df.empty:
        top_days_df.to_csv(output_dir / "peak_days.csv", index=False)
    if not seas_df.empty:
        seas_df.to_csv(output_dir / "seasonality_index.csv", index=False)

    print(f"Wrote CSVs   : {output_dir}/")


if __name__ == "__main__":
    main()
