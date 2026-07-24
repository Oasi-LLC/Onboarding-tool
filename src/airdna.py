"""
src/airdna.py
=============
Load and process AirDNA market-data exports for a property, then produce
benchmark comparisons against the property's own analysis outputs.

Supported file pattern → metric
────────────────────────────────
  occupancy.csv              Market monthly occupancy rate (%)
  revenue_average.csv        Market average revenue per listing per month ($)
  revenue_by_bedroom.csv     Market monthly revenue by bedroom count ($)
  revenue_by_percentile.csv  Market revenue percentiles: 25/50/75/90 ($)
  revpar.csv                 Market average monthly RevPAR ($)
  revpar_peak_days.csv       Daily RevPAR across the market (high-res, for
                             identifying peak-day benchmarks)

All monthly files cover 2023-03 → 2026-02 (36 rows).
revpar_peak_days.csv is daily (2024-02-01 → present).
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Optional

import pandas as pd


# ── File-name → loader config ────────────────────────────────────────────────

_FILE_CONFIG: dict[str, dict] = {
    "occupancy": {
        "filename": "occupancy.csv",
        "value_col": "Occupancy",
        "metric": "occupancy_pct",
        "scale": 1.0,          # already in pct
    },
    "revenue_average": {
        "filename": "revenue_average.csv",
        "value_col": "Revenue",
        "metric": "revenue_avg",
        "scale": 1.0,
    },
    "revpar": {
        "filename": "revpar.csv",
        "value_col": "Average RevPAR",
        "metric": "revpar",
        "scale": 1.0,
    },
    "adr": {
        "filename": "adr.csv",
        "value_col": "Daily Rate",
        "metric": "adr",
        "scale": 1.0,
    },
}

# Multi-value files handled separately
_BEDROOM_FILE = "revenue_by_bedroom.csv"
_PERCENTILE_FILE = "revenue_by_percentile.csv"
_PEAK_DAYS_FILE = "revpar_peak_days.csv"

# Bedroom columns in the export → normalised label
_BEDROOM_COLS = {
    "1 bedroom": "1BR",
    "2 bedroom": "2BR",
    "3 bedroom": "3BR",
    "4 bedroom": "4BR",
    "5 bedroom": "5BR",
    "6+ bedroom": "6BR",
}


# ── Low-level loaders ────────────────────────────────────────────────────────

def _read_csv(path: Path) -> pd.DataFrame:
    """Read AirDNA CSV, strip BOM, parse Date column."""
    df = pd.read_csv(path, encoding="utf-8-sig")
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"])
    return df


def _monthly_df(df: pd.DataFrame, value_col: str, metric_name: str) -> pd.DataFrame:
    """Return tidy month-level DataFrame with columns [year, month, <metric_name>]."""
    out = df[["Date", value_col]].copy()
    out[metric_name] = pd.to_numeric(out[value_col], errors="coerce")
    out["year"] = out["Date"].dt.year
    out["month"] = out["Date"].dt.month
    return out[["year", "month", metric_name]].dropna(subset=[metric_name])


# ── Public: load all AirDNA data for a property ─────────────────────────────

class AirDNAData:
    """
    Container for all AirDNA market data for one property.

    Attributes (all DataFrames have year + month index columns):
        occupancy        : market occupancy % per month
        revenue_avg      : market average revenue per listing per month
        revpar           : market average RevPAR per month
        revenue_by_bedroom: market revenue per month per BR category (wide)
        revenue_by_percentile: market revenue per month per percentile (wide)
        peak_days        : daily market RevPAR (long format, year/month/date/revpar)
    """

    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.occupancy: Optional[pd.DataFrame] = None
        self.revenue_avg: Optional[pd.DataFrame] = None
        self.revpar: Optional[pd.DataFrame] = None
        self.adr: Optional[pd.DataFrame] = None
        self.revenue_by_bedroom: Optional[pd.DataFrame] = None
        self.revenue_by_percentile: Optional[pd.DataFrame] = None
        self.peak_days: Optional[pd.DataFrame] = None
        self.revpar_in_advance: Optional[pd.DataFrame] = None

    def load(self) -> "AirDNAData":
        d = self.data_dir

        # Single-value monthly files
        for key, cfg in _FILE_CONFIG.items():
            p = d / cfg["filename"]
            if p.exists():
                raw = _read_csv(p)
                df = _monthly_df(raw, cfg["value_col"], cfg["metric"])
                attr = "revenue_avg" if key == "revenue_average" else key
                setattr(self, attr, df)

        # revenue_by_bedroom
        p = d / _BEDROOM_FILE
        if p.exists():
            raw = _read_csv(p)
            cols = {k: v for k, v in _BEDROOM_COLS.items() if k in raw.columns}
            df = raw[["Date"] + list(cols.keys())].copy()
            df = df.rename(columns=cols)
            df["year"] = df["Date"].dt.year
            df["month"] = df["Date"].dt.month
            br_cols = list(cols.values())
            for c in br_cols:
                df[c] = pd.to_numeric(df[c], errors="coerce")
            self.revenue_by_bedroom = df[["year", "month"] + br_cols].dropna(how="all", subset=br_cols)

        # revenue_by_percentile
        p = d / _PERCENTILE_FILE
        if p.exists():
            raw = _read_csv(p)
            pct_cols = [c for c in ["25%", "50%", "75%", "90%"] if c in raw.columns]
            df = raw[["Date"] + pct_cols].copy()
            df["year"] = df["Date"].dt.year
            df["month"] = df["Date"].dt.month
            for c in pct_cols:
                df[c] = pd.to_numeric(df[c], errors="coerce")
            self.revenue_by_percentile = df[["year", "month"] + pct_cols].dropna(how="all", subset=pct_cols)

        # peak days (daily)
        p = d / _PEAK_DAYS_FILE
        if p.exists():
            raw = _read_csv(p)
            df = raw[["Date", "RevPAR"]].copy()
            df["RevPAR"] = pd.to_numeric(df["RevPAR"], errors="coerce")
            df["year"] = df["Date"].dt.year
            df["month"] = df["Date"].dt.month
            self.peak_days = df[["Date", "year", "month", "RevPAR"]].dropna(subset=["RevPAR"])

        # Booking-window shape: RevPAR by lead band (AirDNA "in advance")
        p = d / "revpar_in_advance.csv"
        if p.exists():
            raw = _read_csv(p)
            band_cols = [c for c in ["0-6", "7-14", "15-30", "31-60", "61-90", "91+"] if c in raw.columns]
            if band_cols:
                long = raw.melt(
                    id_vars=["Date"],
                    value_vars=band_cols,
                    var_name="lead_band",
                    value_name="market_revpar",
                )
                long["market_revpar"] = pd.to_numeric(long["market_revpar"], errors="coerce")
                long["year"] = long["Date"].dt.year
                long["month"] = long["Date"].dt.month
                self.revpar_in_advance = long[["year", "month", "lead_band", "market_revpar"]].dropna(
                    subset=["market_revpar"]
                )

        return self


def load_airdna(data_dir: str | Path) -> AirDNAData:
    """Load all available AirDNA files from *data_dir* and return an AirDNAData object."""
    return AirDNAData(Path(data_dir)).load()


# ── Benchmark comparisons ────────────────────────────────────────────────────

def _filter_years(df: pd.DataFrame, years: list[int]) -> pd.DataFrame:
    return df[df["year"].isin(years)].copy()


def revpar_gap(
    airdna: AirDNAData,
    property_revpar_by_month: pd.DataFrame,
    years: list[int],
) -> pd.DataFrame:
    """
    Compare property RevPAR to market RevPAR month-by-month.

    property_revpar_by_month must have columns: year, month, revpar
    (sourced from monthly_performance.csv, PROPERTY row).

    Returns DataFrame with columns:
        year, month, market_revpar, property_revpar, gap, gap_pct
    """
    if airdna.revpar is None:
        return pd.DataFrame()

    mkt = _filter_years(airdna.revpar, years).rename(columns={"revpar": "market_revpar"})
    prop = _filter_years(property_revpar_by_month, years).rename(columns={"revpar": "property_revpar"})
    merged = mkt.merge(prop, on=["year", "month"], how="inner")
    merged["gap"] = merged["property_revpar"] - merged["market_revpar"]
    merged["gap_pct"] = (merged["gap"] / merged["market_revpar"] * 100).round(1)
    return merged.sort_values(["year", "month"]).reset_index(drop=True)


def occupancy_gap(
    airdna: AirDNAData,
    property_occ_by_month: pd.DataFrame,
    years: list[int],
) -> pd.DataFrame:
    """
    Compare property occupancy % to market occupancy % month-by-month.

    property_occ_by_month must have columns: year, month, occupancy_pct.

    Returns DataFrame with columns:
        year, month, market_occ, property_occ, gap_ppt
    """
    if airdna.occupancy is None:
        return pd.DataFrame()

    mkt = _filter_years(airdna.occupancy, years).rename(columns={"occupancy_pct": "market_occ"})
    prop = _filter_years(property_occ_by_month, years).rename(columns={"occupancy_pct": "property_occ"})
    merged = mkt.merge(prop, on=["year", "month"], how="inner")
    merged["gap_ppt"] = (merged["property_occ"] - merged["market_occ"]).round(2)
    return merged.sort_values(["year", "month"]).reset_index(drop=True)


def bedroom_revenue_comparison(
    airdna: AirDNAData,
    property_br_revenue: dict[str, float],
    years: list[int],
) -> pd.DataFrame:
    """
    Compare property average monthly revenue per bedroom type to market averages.

    property_br_revenue: {"1BR": avg_monthly_revenue, "2BR": ..., ...}
    Computes market averages over the filtered years and returns a comparison table.

    Returns DataFrame with columns:
        bedroom, market_avg_monthly, property_avg_monthly, gap, gap_pct
    """
    if airdna.revenue_by_bedroom is None:
        return pd.DataFrame()

    br_df = _filter_years(airdna.revenue_by_bedroom, years)
    br_cols = [c for c in ["1BR", "2BR", "3BR", "4BR", "5BR", "6BR"] if c in br_df.columns]
    mkt_avgs = br_df[br_cols].mean()

    rows = []
    for br, mkt_val in mkt_avgs.items():
        prop_val = property_br_revenue.get(br)
        if prop_val is None or math.isnan(mkt_val):
            continue
        gap = prop_val - mkt_val
        gap_pct = (gap / mkt_val * 100) if mkt_val > 0 else float("nan")
        rows.append({
            "bedroom": br,
            "market_avg_monthly": round(mkt_val, 0),
            "property_avg_monthly": round(prop_val, 0),
            "gap": round(gap, 0),
            "gap_pct": round(gap_pct, 1),
        })
    return pd.DataFrame(rows)


def percentile_positioning(
    airdna: AirDNAData,
    property_avg_monthly_revenue: float,
    years: list[int],
) -> dict:
    """
    Estimate where the property's average monthly revenue sits in the
    market distribution (25/50/75/90 percentiles).

    Returns a dict with keys:
        property_avg, p25, p50, p75, p90, estimated_band
    """
    if airdna.revenue_by_percentile is None:
        return {}

    pct_df = _filter_years(airdna.revenue_by_percentile, years)
    avgs = {col: pct_df[col].mean() for col in ["25%", "50%", "75%", "90%"] if col in pct_df.columns}

    p25 = avgs.get("25%", float("nan"))
    p50 = avgs.get("50%", float("nan"))
    p75 = avgs.get("75%", float("nan"))
    p90 = avgs.get("90%", float("nan"))
    v = property_avg_monthly_revenue

    if v < p25:
        band = f"below 25th percentile"
    elif v < p50:
        band = f"25th–50th percentile"
    elif v < p75:
        band = f"50th–75th percentile"
    elif v < p90:
        band = f"75th–90th percentile"
    else:
        band = f"above 90th percentile"

    return {
        "property_avg": round(v, 0),
        "p25": round(p25, 0),
        "p50": round(p50, 0),
        "p75": round(p75, 0),
        "p90": round(p90, 0),
        "estimated_band": band,
    }


def peak_days(
    airdna: AirDNAData,
    top_n: int = 20,
    year_filter: Optional[list[int]] = None,
) -> pd.DataFrame:
    """
    Return the top-N highest market RevPAR days (from revpar_peak_days).

    Useful for identifying which specific dates to target with event pricing.

    Returns DataFrame with columns: Date, year, month, RevPAR.
    """
    if airdna.peak_days is None:
        return pd.DataFrame()

    df = airdna.peak_days.copy()
    if year_filter:
        df = df[df["year"].isin(year_filter)]
    return df.nlargest(top_n, "RevPAR").reset_index(drop=True)


def market_seasonality_index(
    airdna: AirDNAData,
    years: list[int],
) -> pd.DataFrame:
    """
    Compute a market seasonality index from RevPAR: each month's value
    as a % of the annual average, to identify relative high/low seasons.

    Returns DataFrame: month, market_revpar_avg, seasonality_index
    """
    if airdna.revpar is None:
        return pd.DataFrame()

    df = _filter_years(airdna.revpar, years)
    monthly_avg = df.groupby("month")["revpar"].mean().reset_index()
    annual_avg = monthly_avg["revpar"].mean()
    monthly_avg["seasonality_index"] = (monthly_avg["revpar"] / annual_avg * 100).round(1)
    monthly_avg = monthly_avg.rename(columns={"revpar": "market_revpar_avg"})
    monthly_avg["market_revpar_avg"] = monthly_avg["market_revpar_avg"].round(0)
    return monthly_avg.sort_values("month").reset_index(drop=True)
