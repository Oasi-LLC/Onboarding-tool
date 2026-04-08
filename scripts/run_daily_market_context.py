#!/usr/bin/env python3
"""
Build listing-day AirDNA market context (extension layer).

Output:
  output/<property_id>/benchmark/daily_market_context.csv

This does NOT change tiering logic. It enriches listing-day rows with:
- market_revpar_monthly
- rpi
- market_condition (submarket terciles)
- market_reliability / benchmark_warning
- optional percentile/bedroom benchmark context
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.analysis import get_analysis_years
from src.property_config import load_property_inventory


def _build_listing_day_stay_night(
    canonical: pd.DataFrame,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
) -> pd.DataFrame:
    """
    Build listing-day rows on stay-night grain from canonical reservation rows.
    Revenue is distributed per occupied night; bookings remain arrival-day counts.
    """
    can = canonical.copy()
    can["arrival_date"] = pd.to_datetime(can.get("arrival_date"), errors="coerce")
    can["departure_date"] = pd.to_datetime(can.get("departure_date"), errors="coerce")
    can["revenue"] = pd.to_numeric(can.get("revenue"), errors="coerce")
    can["nights"] = pd.to_numeric(can.get("nights"), errors="coerce")
    can = can.dropna(subset=["unit_id", "arrival_date", "departure_date", "revenue", "nights"])
    can = can[(can["departure_date"] > can["arrival_date"]) & (can["nights"] > 0)].copy()
    can["date"] = can.apply(
        lambda r: pd.date_range(
            start=pd.Timestamp(r["arrival_date"]).normalize(),
            end=(pd.Timestamp(r["departure_date"]).normalize() - pd.Timedelta(days=1)),
            freq="D",
        ),
        axis=1,
    )
    can["rev_per_night"] = can["revenue"] / can["nights"]
    stay = can.explode("date")
    stay = stay[(stay["date"] >= start_date) & (stay["date"] <= end_date)].copy()
    stay_daily = stay.groupby(["unit_id", "date"], as_index=False).agg(
        property_revenue=("rev_per_night", "sum"),
        property_room_nights=("date", "count"),
    )
    arrivals = canonical.copy()
    arrivals["arrival_date"] = pd.to_datetime(arrivals.get("arrival_date"), errors="coerce")
    arrivals = arrivals.dropna(subset=["unit_id", "arrival_date"])
    arrivals["date"] = arrivals["arrival_date"].dt.normalize()
    arrivals = arrivals[(arrivals["date"] >= start_date) & (arrivals["date"] <= end_date)].copy()
    arr_daily = arrivals.groupby(["unit_id", "date"], as_index=False).agg(
        bookings=("reservation_id", "count"),
    )
    out = stay_daily.merge(arr_daily, on=["unit_id", "date"], how="left")
    out["bookings"] = out["bookings"].fillna(0.0)
    out["year"] = out["date"].dt.year
    out["month"] = out["date"].dt.month
    out["property_revpar"] = out["property_revenue"] / out["property_room_nights"]
    out.loc[out["property_room_nights"] <= 0, "property_revpar"] = pd.NA
    return out


def _norm_name(s: str) -> str:
    return "".join(ch if ch.isalnum() or ch.isspace() else " " for ch in str(s).lower()).strip()


def _tokens(s: str) -> set[str]:
    return {t for t in _norm_name(s).split() if t}


def _resolve_submarket_dir(base: Path, submarket_name: str) -> Optional[Path]:
    dirs = [p for p in base.iterdir() if p.is_dir()] if base.exists() else []
    if not dirs:
        return None
    want_norm = _norm_name(submarket_name)
    want_tokens = _tokens(submarket_name)
    exact = [d for d in dirs if _norm_name(d.name) == want_norm]
    if len(exact) == 1:
        return exact[0]
    if len(exact) > 1:
        raise ValueError(
            f"Ambiguous submarket directory for '{submarket_name}': "
            + ", ".join(str(d.name) for d in exact)
        )
    token_eq = [d for d in dirs if _tokens(d.name) == want_tokens]
    if len(token_eq) == 1:
        return token_eq[0]
    if len(token_eq) > 1:
        raise ValueError(
            f"Ambiguous token-equivalent submarket directory for '{submarket_name}': "
            + ", ".join(str(d.name) for d in token_eq)
        )
    return None


def _resolve_metric_file(
    submarket_dir: Path,
    prefix: str,
    configured_filename: Optional[str] = None,
) -> Optional[Path]:
    if configured_filename:
        p = submarket_dir / configured_filename
        if not p.exists():
            raise FileNotFoundError(
                f"Configured AirDNA file not found: {p}"
            )
        return p
    files = sorted(submarket_dir.glob(f"{prefix}*.csv"))
    if len(files) == 1:
        return files[0]
    if len(files) > 1:
        raise ValueError(
            f"Ambiguous AirDNA files for prefix '{prefix}' in '{submarket_dir}': "
            + ", ".join(f.name for f in files)
            + ". Set explicit filenames in airdna.submarket_pulls."
        )
    return None


def _load_monthly_metric(
    submarket_dir: Path,
    prefix: str,
    value_col: str,
    out_col: str,
    configured_filename: Optional[str] = None,
) -> pd.DataFrame:
    fp = _resolve_metric_file(submarket_dir, prefix, configured_filename=configured_filename)
    if fp is None:
        return pd.DataFrame(columns=["year", "month", out_col])
    df = pd.read_csv(fp, encoding="utf-8-sig")
    if "Date" not in df.columns or value_col not in df.columns:
        return pd.DataFrame(columns=["year", "month", out_col])
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"])
    df[out_col] = pd.to_numeric(df[value_col], errors="coerce")
    df["year"] = df["Date"].dt.year
    df["month"] = df["Date"].dt.month
    return df[["year", "month", out_col]].dropna(subset=[out_col])


def _load_percentiles(submarket_dir: Path, configured_filename: Optional[str] = None) -> pd.DataFrame:
    fp = _resolve_metric_file(
        submarket_dir,
        "revenueByPercentile_last_3_years",
        configured_filename=configured_filename,
    )
    if fp is None:
        return pd.DataFrame(columns=["year", "month", "p25", "p50", "p75", "p90"])
    df = pd.read_csv(fp, encoding="utf-8-sig")
    if "Date" not in df.columns:
        return pd.DataFrame(columns=["year", "month", "p25", "p50", "p75", "p90"])
    df["Date"] = pd.to_datetime(df["Date"], errors="coerce")
    df = df.dropna(subset=["Date"])
    col_map = {"25%": "p25", "50%": "p50", "75%": "p75", "90%": "p90"}
    for src, dst in col_map.items():
        df[dst] = pd.to_numeric(df[src], errors="coerce") if src in df.columns else pd.NA
    df["year"] = df["Date"].dt.year
    df["month"] = df["Date"].dt.month
    return df[["year", "month", "p25", "p50", "p75", "p90"]]


def _has_bedroom_coverage_shift(submarket_dir: Path, configured_filename: Optional[str] = None) -> bool:
    fp = _resolve_metric_file(
        submarket_dir,
        "revenueByBedroom_last_3_years",
        configured_filename=configured_filename,
    )
    if fp is None:
        return False
    df = pd.read_csv(fp, encoding="utf-8-sig")
    br_cols = [c for c in ["1 bedroom", "2 bedroom", "3 bedroom", "4 bedroom", "5 bedroom", "6+ bedroom"] if c in df.columns]
    if not br_cols:
        return False
    non_null_count = df[br_cols].notna().sum(axis=1)
    return int(non_null_count.min()) != int(non_null_count.max())


def _condition_from_terciles(market_revpar: float, q1: float, q2: float) -> str:
    if pd.isna(market_revpar):
        return ""
    if market_revpar <= q1:
        return "weak"
    if market_revpar <= q2:
        return "neutral"
    return "strong"


def _warning_from_reliability(reliability: str, note: str) -> Optional[str]:
    rel = str(reliability or "").strip().lower()
    n = str(note or "").lower()
    if rel == "pending":
        return "inactive_listing"
    if "thin" in n:
        return "thin_market"
    if rel == "unreliable":
        return "circular_market"
    return None


def main() -> None:
    ap = argparse.ArgumentParser(description="Build listing-day AirDNA market context CSV.")
    ap.add_argument("--property", required=True, dest="property_id", help="Property id, e.g. flohom")
    args = ap.parse_args()

    prop_id = args.property_id
    inv = load_property_inventory(prop_id)
    airdna_cfg = inv.get("airdna") or {}
    pulls = airdna_cfg.get("submarket_pulls") or []
    if not pulls:
        raise SystemExit(f"No airdna.submarket_pulls configured for property '{prop_id}'.")

    canonical_path = PROJECT_ROOT / "output" / prop_id / "ingestion" / "canonical.csv"
    tier_path = PROJECT_ROOT / "output" / prop_id / "analysis" / "daily_tier_calendar.csv"
    if not canonical_path.exists():
        raise SystemExit(f"Canonical file not found: {canonical_path}")
    if not tier_path.exists():
        raise SystemExit(f"Tier calendar not found: {tier_path}. Run analysis first.")

    y1, y2 = get_analysis_years()

    can = pd.read_csv(canonical_path)
    start_date = pd.Timestamp(year=y1, month=1, day=1)
    end_date = pd.Timestamp(year=y2, month=12, day=31)
    # listing-day grain from canonical (stay-night based; aligned with tier pipeline)
    listing_day = _build_listing_day_stay_night(can, start_date=start_date, end_date=end_date)

    # property-day tiers to enrich listing-day rows
    tiers = pd.read_csv(tier_path)
    tiers["date"] = pd.to_datetime(tiers["date"], errors="coerce")
    tier_cols = ["date", "tier_id", "tier_label", "base_tier_id", "base_tier_label", "tier_method"]
    tiers = tiers[[c for c in tier_cols if c in tiers.columns]].copy()
    out = listing_day.merge(tiers, on="date", how="left")

    # listing -> submarket metadata map
    rows = []
    for pull in pulls:
        for uid in pull.get("listings") or []:
            rows.append(
                {
                    "unit_id": str(uid),
                    "submarket": str(pull.get("submarket", "")).strip(),
                    "reliability": str(pull.get("reliability", "")).strip().lower(),
                    "note": str(pull.get("note", "")).strip(),
                }
            )
    map_df = pd.DataFrame(rows).drop_duplicates(subset=["unit_id"])
    out = out.merge(map_df, on="unit_id", how="left")

    # enrich each submarket
    airdna_root = PROJECT_ROOT / "data" / "airdna" / prop_id
    market_frames = []
    pct_frames = []
    submarket_thin_coverage: dict[str, bool] = {}
    for pull in pulls:
        subm = str(pull.get("submarket", "")).strip()
        rel = str(pull.get("reliability", "")).strip().lower()
        d = _resolve_submarket_dir(airdna_root, subm)
        if d is None:
            if rel == "pending":
                continue
            raise SystemExit(
                f"AirDNA submarket directory not found for '{subm}' in {airdna_root}. "
                "Provide matching folder name or mark pull as reliability: pending."
            )
        revpar_file = pull.get("revpar_file")
        occupancy_file = pull.get("occupancy_file")
        percentile_file = pull.get("revenue_percentile_file")
        bedroom_file = pull.get("revenue_bedroom_file")
        try:
            submarket_thin_coverage[subm] = _has_bedroom_coverage_shift(d, configured_filename=bedroom_file)
            rev = _load_monthly_metric(
                d, "revpar_last_3_years", "Average RevPAR", "market_revpar_monthly",
                configured_filename=revpar_file,
            )
            occ = _load_monthly_metric(
                d, "occupancy_last_3_years", "Occupancy", "market_occupancy_monthly",
                configured_filename=occupancy_file,
            )
            pct = _load_percentiles(d, configured_filename=percentile_file)
        except (ValueError, FileNotFoundError) as e:
            raise SystemExit(f"AirDNA file resolution error for submarket '{subm}': {e}") from e
        if not rev.empty:
            merged = rev.merge(occ, on=["year", "month"], how="left") if not occ.empty else rev
            merged["submarket"] = subm
            q1 = merged["market_revpar_monthly"].quantile(1.0 / 3.0)
            q2 = merged["market_revpar_monthly"].quantile(2.0 / 3.0)
            merged["market_condition"] = merged["market_revpar_monthly"].map(lambda v: _condition_from_terciles(v, q1, q2))
            market_frames.append(merged[["submarket", "year", "month", "market_revpar_monthly", "market_occupancy_monthly", "market_condition"]])
        if not pct.empty:
            pct["submarket"] = subm
            pct_frames.append(pct[["submarket", "year", "month", "p25", "p50", "p75", "p90"]])

    market_df = pd.concat(market_frames, ignore_index=True) if market_frames else pd.DataFrame(
        columns=["submarket", "year", "month", "market_revpar_monthly", "market_occupancy_monthly", "market_condition"]
    )
    pct_df = pd.concat(pct_frames, ignore_index=True) if pct_frames else pd.DataFrame(
        columns=["submarket", "year", "month", "p25", "p50", "p75", "p90"]
    )
    out = out.merge(market_df, on=["submarket", "year", "month"], how="left")
    out = out.merge(pct_df, on=["submarket", "year", "month"], how="left")

    out["rpi"] = out["property_revpar"] / out["market_revpar_monthly"]
    out["market_reliability"] = out["reliability"].map(
        {"reliable": "reliable", "unreliable": "low", "pending": "pending"}
    )
    out["benchmark_warning"] = out.apply(
        lambda r: _warning_from_reliability(r.get("reliability", ""), r.get("note", "")), axis=1
    )
    # If bedroom benchmark coverage shifts over time, mark as thin_market where no higher-priority warning exists.
    thin_mask = out["submarket"].map(lambda s: bool(submarket_thin_coverage.get(s, False)))
    out.loc[thin_mask & out["benchmark_warning"].isna(), "benchmark_warning"] = "thin_market"
    # Optional enrichments (stable schema)
    out["bedroom_revenue_benchmark"] = pd.NA
    out["percentile_band"] = pd.NA
    has_pct = out["property_revenue"].notna() & out["p25"].notna() & out["p75"].notna()
    out.loc[has_pct & (out["property_revenue"] < out["p25"]), "percentile_band"] = "bottom25"
    out.loc[has_pct & (out["property_revenue"] >= out["p75"]), "percentile_band"] = "top25"
    out.loc[
        has_pct & (out["property_revenue"] >= out["p25"]) & (out["property_revenue"] < out["p75"]),
        "percentile_band",
    ] = "mid50"

    final_cols = [
        "date",
        "unit_id",
        "tier_id",
        "tier_label",
        "base_tier_id",
        "base_tier_label",
        "tier_method",
        "submarket",
        "market_reliability",
        "benchmark_warning",
        "property_revpar",
        "market_revpar_monthly",
        "rpi",
        "market_condition",
        "bedroom_revenue_benchmark",
        "percentile_band",
        "property_revenue",
        "property_room_nights",
        "bookings",
        "market_occupancy_monthly",
    ]
    out = out[[c for c in final_cols if c in out.columns]].sort_values(["date", "unit_id"]).reset_index(drop=True)

    out_dir = PROJECT_ROOT / "output" / prop_id / "benchmark"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "daily_market_context.csv"
    out.to_csv(out_path, index=False)
    print(f"Wrote {out_path} ({len(out)} rows)")


if __name__ == "__main__":
    main()

