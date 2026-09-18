"""
Verify a proposed pricing matrix against stay-night historical ADR benchmarks.

Reservations are exploded to occupied nights [arrival_date, departure_date) with
revenue allocated evenly across nights (revenue / nights).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Optional

import numpy as np
import pandas as pd

from src.analysis import DAY_ORDER

DOW_COLS = list(DAY_ORDER)
MONTH_NAMES = [
    "January",
    "February",
    "March",
    "April",
    "May",
    "June",
    "July",
    "August",
    "September",
    "October",
    "November",
    "December",
]


def expand_reservations_to_stay_nights(df: pd.DataFrame) -> pd.DataFrame:
    """One row per occupied night with unit_id, stay_date, nightly_adr, month_index, day_of_week."""
    req = {"arrival_date", "departure_date", "revenue", "nights", "unit_id"}
    missing = req - set(df.columns)
    if missing:
        raise ValueError(f"canonical missing columns for stay expansion: {sorted(missing)}")

    stay = df[list(req)].copy()
    stay["arrival_date"] = pd.to_datetime(stay["arrival_date"], errors="coerce")
    stay["departure_date"] = pd.to_datetime(stay["departure_date"], errors="coerce")
    stay["nights_safe"] = pd.to_numeric(stay["nights"], errors="coerce")
    stay["revenue"] = pd.to_numeric(stay["revenue"], errors="coerce")
    stay = stay.dropna(subset=["arrival_date", "departure_date", "unit_id", "revenue", "nights_safe"])
    stay = stay.loc[
        (stay["departure_date"] > stay["arrival_date"]) & (stay["nights_safe"] > 0) & (stay["revenue"] > 0)
    ].copy()
    stay["rev_per_night"] = stay["revenue"] / stay["nights_safe"]
    stay["stay_date"] = stay.apply(
        lambda r: pd.date_range(
            start=pd.Timestamp(r["arrival_date"]).normalize(),
            end=(pd.Timestamp(r["departure_date"]).normalize() - pd.Timedelta(days=1)),
            freq="D",
        ),
        axis=1,
    )
    stay = stay.explode("stay_date")
    stay["stay_date"] = pd.to_datetime(stay["stay_date"], errors="coerce")
    stay = stay.dropna(subset=["stay_date"])
    stay["month_index"] = stay["stay_date"].dt.month.astype(int)
    stay["month_name"] = stay["month_index"].map(lambda m: MONTH_NAMES[m - 1])
    stay["day_of_week"] = stay["stay_date"].dt.day_name()
    stay["nightly_adr"] = stay["rev_per_night"].round(2)
    return stay[
        ["unit_id", "stay_date", "month_index", "month_name", "day_of_week", "nightly_adr", "revenue", "nights_safe"]
    ].reset_index(drop=True)


def build_historical_adr_benchmarks(stay_nights: pd.DataFrame) -> dict[str, pd.DataFrame]:
    """Min / max / median ADR at listing × month × DOW and rollups."""
    if stay_nights.empty:
        empty_cols = [
            "unit_id",
            "month_index",
            "day_of_week",
            "n_nights",
            "adr_min",
            "adr_median",
            "adr_max",
            "adr_p25",
            "adr_p75",
        ]
        return {
            "by_listing_month_dow": pd.DataFrame(columns=empty_cols),
            "by_listing_month": pd.DataFrame(),
            "by_listing_dow": pd.DataFrame(),
            "by_listing": pd.DataFrame(),
        }

    def _agg(g: pd.DataFrame) -> pd.Series:
        s = g["nightly_adr"]
        return pd.Series(
            {
                "n_nights": int(len(s)),
                "adr_min": round(float(s.min()), 2),
                "adr_median": round(float(s.median()), 2),
                "adr_max": round(float(s.max()), 2),
                "adr_p25": round(float(s.quantile(0.25)), 2),
                "adr_p75": round(float(s.quantile(0.75)), 2),
            }
        )

    by_lmd = (
        stay_nights.groupby(["unit_id", "month_index", "day_of_week"], as_index=False)
        .apply(_agg, include_groups=False)
        .reset_index(drop=True)
    )
    by_lm = (
        stay_nights.groupby(["unit_id", "month_index"], as_index=False)
        .apply(_agg, include_groups=False)
        .reset_index(drop=True)
    )
    by_lm["month_name"] = by_lm["month_index"].map(lambda m: MONTH_NAMES[int(m) - 1])
    by_ld = (
        stay_nights.groupby(["unit_id", "day_of_week"], as_index=False)
        .apply(_agg, include_groups=False)
        .reset_index(drop=True)
    )
    by_l = stay_nights.groupby("unit_id", as_index=False).apply(_agg, include_groups=False).reset_index(drop=True)
    return {
        "by_listing_month_dow": by_lmd,
        "by_listing_month": by_lm,
        "by_listing_dow": by_ld,
        "by_listing": by_l,
    }


def _long_matrix(matrix_wide: pd.DataFrame) -> pd.DataFrame:
    """Wide matrix (unit_id × month × DOW columns) → long rows."""
    id_cols = [c for c in ("group_id", "unit_id", "month_index") if c in matrix_wide.columns]
    if "unit_id" not in id_cols or "month_index" not in id_cols:
        raise ValueError("matrix must include unit_id and month_index")
    day_cols = [c for c in DOW_COLS if c in matrix_wide.columns]
    if not day_cols:
        raise ValueError(f"matrix missing day columns; expected one of {DOW_COLS}")
    long = matrix_wide.melt(
        id_vars=id_cols,
        value_vars=day_cols,
        var_name="day_of_week",
        value_name="proposed_rate",
    )
    long["month_index"] = pd.to_numeric(long["month_index"], errors="coerce").astype("Int64")
    long["proposed_rate"] = pd.to_numeric(long["proposed_rate"], errors="coerce")
    long["month_name"] = long["month_index"].map(lambda m: MONTH_NAMES[int(m) - 1] if pd.notna(m) else None)
    return long.dropna(subset=["unit_id", "month_index", "day_of_week", "proposed_rate"])


def compare_rates_to_history(
    proposed_long: pd.DataFrame,
    hist_by_lmd: pd.DataFrame,
    hist_by_lm: pd.DataFrame,
) -> pd.DataFrame:
    """Cell-level proposed rate vs historical min/median/max."""
    cmp_df = proposed_long.merge(
        hist_by_lmd,
        on=["unit_id", "month_index", "day_of_week"],
        how="left",
    )
    lm = hist_by_lm.rename(
        columns={
            "adr_min": "month_adr_min",
            "adr_median": "month_adr_median",
            "adr_max": "month_adr_max",
            "n_nights": "month_n_nights",
        }
    )
    cmp_df = cmp_df.merge(
        lm[["unit_id", "month_index", "month_adr_min", "month_adr_median", "month_adr_max", "month_n_nights"]],
        on=["unit_id", "month_index"],
        how="left",
    )

    def _position(row: pd.Series) -> Optional[float]:
        lo, hi = row.get("adr_min"), row.get("adr_max")
        rate = row.get("proposed_rate")
        if pd.isna(lo) or pd.isna(hi) or pd.isna(rate) or hi <= lo:
            return None
        return round((float(rate) - float(lo)) / (float(hi) - float(lo)) * 100, 1)

    cmp_df["hist_position_pct"] = cmp_df.apply(_position, axis=1)

    def _flag(row: pd.Series) -> str:
        rate = row.get("proposed_rate")
        if pd.isna(rate):
            return "missing_rate"
        n = row.get("n_nights")
        if pd.isna(n) or int(n) < 3:
            return "thin_history"
        lo, med, hi = row.get("adr_min"), row.get("adr_median"), row.get("adr_max")
        if pd.isna(lo) or pd.isna(hi):
            return "no_history"
        r = float(rate)
        if r < float(lo) * 0.85:
            return "below_hist_min"
        if r > float(hi) * 1.25:
            return "above_hist_max"
        if pd.notna(med) and abs(r - float(med)) / float(med) > 0.45:
            return "far_from_median"
        return "ok"

    cmp_df["cell_flag"] = cmp_df.apply(_flag, axis=1)
    cmp_df["vs_median_pct"] = np.where(
        cmp_df["adr_median"].notna() & (cmp_df["adr_median"] > 0),
        ((cmp_df["proposed_rate"] - cmp_df["adr_median"]) / cmp_df["adr_median"] * 100).round(1),
        np.nan,
    )
    return cmp_df.sort_values(["unit_id", "month_index", "day_of_week"]).reset_index(drop=True)


def _tier_lookup(listing_price_hierarchy: dict[str, Any]) -> dict[str, tuple[int, str]]:
    out: dict[str, tuple[int, str]] = {}
    for tier in listing_price_hierarchy.get("tiers") or []:
        if not isinstance(tier, dict):
            continue
        order = int(tier.get("order", 999))
        tid = str(tier.get("id", order))
        for uid in tier.get("unit_ids") or []:
            out[str(uid)] = (order, tid)
    return out


def check_dow_hierarchy(
    proposed_long: pd.DataFrame,
    dow_hierarchy: list[list[str]],
) -> pd.DataFrame:
    """Within each unit × month, verify configured DOW ladder (non-decreasing)."""
    tier_rank: dict[str, int] = {}
    for i, tier in enumerate(dow_hierarchy):
        for d in tier:
            tier_rank[str(d)] = i

    rows = []
    for (unit_id, month_index), grp in proposed_long.groupby(["unit_id", "month_index"]):
        prices = {str(r.day_of_week): float(r.proposed_rate) for r in grp.itertuples()}
        ordered_tiers = sorted(set(tier_rank.values()))
        prev_max = -np.inf
        for tr in ordered_tiers:
            days = [d for d, t in tier_rank.items() if t == tr and d in prices]
            if not days:
                continue
            tier_min = min(prices[d] for d in days)
            tier_max = max(prices[d] for d in days)
            if tier_min < prev_max - 0.01:
                rows.append(
                    {
                        "check": "dow_hierarchy",
                        "unit_id": unit_id,
                        "month_index": int(month_index),
                        "violation": f"tier {tr} min {tier_min} < prior tier max {prev_max}",
                        "status": "fail",
                    }
                )
            prev_max = max(prev_max, tier_max)
    return pd.DataFrame(rows)


def check_listing_hierarchy(
    proposed_long: pd.DataFrame,
    listing_price_hierarchy: dict[str, Any],
    anchor_dow: str = "Friday",
) -> pd.DataFrame:
    """For each month, higher listing tiers should not price below lower tiers (anchor DOW)."""
    tier_map = _tier_lookup(listing_price_hierarchy)
    rows = []
    pivot = proposed_long.pivot_table(
        index=["unit_id", "month_index"],
        columns="day_of_week",
        values="proposed_rate",
        aggfunc="first",
    )
    for month_index in sorted(proposed_long["month_index"].unique()):
        month_units = proposed_long.loc[proposed_long["month_index"] == month_index, "unit_id"].unique()
        by_tier: dict[int, list[tuple[str, float]]] = {}
        for uid in month_units:
            if uid not in pivot.index.get_level_values(0):
                continue
            try:
                rate = float(pivot.loc[(uid, month_index), anchor_dow])
            except (KeyError, TypeError):
                continue
            if uid not in tier_map:
                continue
            order, tid = tier_map[uid]
            by_tier.setdefault(order, []).append((uid, rate))
        orders = sorted(by_tier)
        for i in range(1, len(orders)):
            lo, hi = orders[i - 1], orders[i]
            lo_max = max(r for _, r in by_tier[lo])
            hi_min = min(r for _, r in by_tier[hi])
            if hi_min < lo_max - 0.01:
                rows.append(
                    {
                        "check": "listing_hierarchy",
                        "month_index": int(month_index),
                        "anchor_dow": anchor_dow,
                        "lower_tier_order": lo,
                        "higher_tier_order": hi,
                        "lower_tier_max_rate": lo_max,
                        "higher_tier_min_rate": hi_min,
                        "status": "fail",
                    }
                )
    return pd.DataFrame(rows)


def check_month_hierarchy(
    proposed_long: pd.DataFrame,
    month_score_by_index: dict[int, float],
    anchor_dow: str = "Friday",
) -> pd.DataFrame:
    """Higher-scored months should not price below lower-scored months (per unit, anchor DOW)."""
    rows = []
    scores = {int(k): float(v) for k, v in month_score_by_index.items()}
    for unit_id, grp in proposed_long.groupby("unit_id"):
        month_rates = (
            grp.loc[grp["day_of_week"] == anchor_dow]
            .set_index("month_index")["proposed_rate"]
            .to_dict()
        )
        months = [int(m) for m in month_rates]
        for i, m1 in enumerate(months):
            for m2 in months[i + 1 :]:
                s1, s2 = scores.get(m1, 5), scores.get(m2, 5)
                r1, r2 = float(month_rates[m1]), float(month_rates[m2])
                if s2 > s1 and r2 < r1 - 0.01:
                    rows.append(
                        {
                            "check": "month_hierarchy",
                            "unit_id": unit_id,
                            "anchor_dow": anchor_dow,
                            "weaker_month": m1,
                            "weaker_score": s1,
                            "weaker_rate": r1,
                            "stronger_month": m2,
                            "stronger_score": s2,
                            "stronger_rate": r2,
                            "status": "fail",
                        }
                    )
                if s1 > s2 and r1 < r2 - 0.01:
                    rows.append(
                        {
                            "check": "month_hierarchy",
                            "unit_id": unit_id,
                            "anchor_dow": anchor_dow,
                            "weaker_month": m2,
                            "weaker_score": s2,
                            "weaker_rate": r2,
                            "stronger_month": m1,
                            "stronger_score": s1,
                            "stronger_rate": r1,
                            "status": "fail",
                        }
                    )
    return pd.DataFrame(rows)


def build_spread_analysis(
    proposed_long: pd.DataFrame,
    hist_by_lmd: pd.DataFrame,
) -> pd.DataFrame:
    """
    Weekend / weekday spreads per unit × month for proposed vs historical median.
    Ratios use Monday as the base (Mon/Tue/Wed should be equal in proposed matrix).
    """
    rows = []
    for (unit_id, month_index), grp in proposed_long.groupby(["unit_id", "month_index"]):
        p = {str(r.day_of_week): float(r.proposed_rate) for r in grp.itertuples()}
        base = p.get("Monday")
        if not base or base <= 0:
            continue
        hist = hist_by_lmd.loc[
            (hist_by_lmd["unit_id"] == unit_id) & (hist_by_lmd["month_index"] == month_index)
        ]
        h = {str(r.day_of_week): float(r.adr_median) for r in hist.itertuples() if pd.notna(r.adr_median)}
        h_base = h.get("Monday") or (sum(h.get(d, 0) for d in ("Monday", "Tuesday", "Wednesday")) / 3 if h else None)
        row: dict[str, Any] = {
            "unit_id": unit_id,
            "month_index": int(month_index),
            "month_name": MONTH_NAMES[int(month_index) - 1],
            "proposed_mon": base,
        }
        for dow in DOW_COLS:
            if dow in p:
                row[f"proposed_{dow[:3].lower()}_ratio"] = round(p[dow] / base, 4)
            if h_base and h_base > 0 and dow in h:
                row[f"hist_{dow[:3].lower()}_ratio"] = round(h[dow] / h_base, 4)
        if "Saturday" in p and "Monday" in p:
            row["proposed_sat_mon_spread_pct"] = round((p["Saturday"] / base - 1) * 100, 1)
        if h_base and "Saturday" in h:
            row["hist_sat_mon_spread_pct"] = round((h["Saturday"] / h_base - 1) * 100, 1)
        rows.append(row)
    return pd.DataFrame(rows)


def audit_spread_vs_history(spread_df: pd.DataFrame) -> pd.DataFrame:
    """Flag months where proposed weekend premium diverges from historical stay-night median."""
    if spread_df.empty:
        return pd.DataFrame()
    rows = []
    for r in spread_df.itertuples(index=False):
        p = getattr(r, "proposed_sat_mon_spread_pct", None)
        h = getattr(r, "hist_sat_mon_spread_pct", None)
        if pd.isna(p) or pd.isna(h):
            continue
        delta = float(p) - float(h)
        status = "ok"
        if abs(delta) > 40:
            status = "warn"
        if abs(delta) > 70:
            status = "fail"
        rows.append(
            {
                "unit_id": r.unit_id,
                "month_index": int(r.month_index),
                "month_name": r.month_name,
                "proposed_sat_mon_spread_pct": round(float(p), 1),
                "hist_sat_mon_spread_pct": round(float(h), 1),
                "spread_delta_pct": round(delta, 1),
                "status": status,
            }
        )
    return pd.DataFrame(rows)


def audit_spread_uniformity(spread_df: pd.DataFrame, ratio_col: str = "proposed_sat_mon_ratio") -> pd.DataFrame:
    """
    Flag listings whose proposed DOW spread is nearly identical across all months
    (low coefficient of variation) — often a sign the matrix copied one season's shape everywhere.
    """
    if spread_df.empty or ratio_col not in spread_df.columns:
        return pd.DataFrame(columns=["unit_id", "ratio_col", "cv_pct", "status", "note"])
    rows = []
    for unit_id, grp in spread_df.groupby("unit_id"):
        vals = grp[ratio_col].dropna()
        if len(vals) < 2:
            continue
        mean = float(vals.mean())
        if mean <= 0:
            continue
        cv = float(vals.std(ddof=0) / mean * 100)
        status = "warn" if cv < 3.0 else "ok"
        rows.append(
            {
                "unit_id": unit_id,
                "ratio_col": ratio_col,
                "cv_pct": round(cv, 2),
                "min_ratio": round(float(vals.min()), 4),
                "max_ratio": round(float(vals.max()), 4),
                "status": status,
                "note": "spread nearly flat across months" if status == "warn" else "",
            }
        )
    return pd.DataFrame(rows)


def summarize_verification(
    comparison: pd.DataFrame,
    dow_violations: pd.DataFrame,
    listing_violations: pd.DataFrame,
    month_violations: pd.DataFrame,
    spread_uniformity: pd.DataFrame,
) -> pd.DataFrame:
    flag_counts = comparison["cell_flag"].value_counts().to_dict() if not comparison.empty else {}
    rows = [
        {"metric": "cells_total", "value": len(comparison)},
        {"metric": "cells_ok", "value": flag_counts.get("ok", 0)},
        {"metric": "cells_below_hist_min", "value": flag_counts.get("below_hist_min", 0)},
        {"metric": "cells_above_hist_max", "value": flag_counts.get("above_hist_max", 0)},
        {"metric": "cells_far_from_median", "value": flag_counts.get("far_from_median", 0)},
        {"metric": "cells_thin_history", "value": flag_counts.get("thin_history", 0)},
        {"metric": "cells_no_history", "value": flag_counts.get("no_history", 0)},
        {"metric": "dow_hierarchy_violations", "value": len(dow_violations)},
        {"metric": "listing_hierarchy_violations", "value": len(listing_violations)},
        {"metric": "month_hierarchy_violations", "value": len(month_violations)},
        {"metric": "listings_flat_spread_warn", "value": int((spread_uniformity["status"] == "warn").sum()) if not spread_uniformity.empty else 0},
    ]
    return pd.DataFrame(rows)


def run_pricing_verification(
    canonical: pd.DataFrame,
    matrix_wide: pd.DataFrame,
    *,
    pricing_cfg: dict[str, Any],
    analysis_window: Optional[dict[str, Any]] = None,
) -> dict[str, pd.DataFrame]:
    """Full pipeline: expand stays → benchmarks → compare → hierarchy & spread audits."""
    stay = expand_reservations_to_stay_nights(canonical)
    if analysis_window:
        start = analysis_window.get("start_date")
        end = analysis_window.get("end_date")
        if start:
            stay = stay.loc[stay["stay_date"] >= pd.Timestamp(start)]
        if end:
            stay = stay.loc[stay["stay_date"] <= pd.Timestamp(end)]

    benchmarks = build_historical_adr_benchmarks(stay)
    proposed_long = _long_matrix(matrix_wide)
    comparison = compare_rates_to_history(
        proposed_long,
        benchmarks["by_listing_month_dow"],
        benchmarks["by_listing_month"],
    )
    spread = build_spread_analysis(proposed_long, benchmarks["by_listing_month_dow"])
    dow_h = pricing_cfg.get("dow_hierarchy") or []
    month_scores = pricing_cfg.get("month_score_by_index") or {}
    listing_h = pricing_cfg.get("listing_price_hierarchy") or {}
    dow_v = check_dow_hierarchy(proposed_long, dow_h) if dow_h else pd.DataFrame()
    listing_v = check_listing_hierarchy(proposed_long, listing_h) if listing_h else pd.DataFrame()
    month_v = check_month_hierarchy(proposed_long, month_scores) if month_scores else pd.DataFrame()
    spread_u = audit_spread_uniformity(spread)
    spread_hist = audit_spread_vs_history(spread)

    return {
        "stay_nights": stay,
        **benchmarks,
        "proposed_long": proposed_long,
        "rate_comparison": comparison,
        "spread_by_month": spread,
        "spread_uniformity": spread_u,
        "spread_vs_history": spread_hist,
        "dow_hierarchy_violations": dow_v,
        "listing_hierarchy_violations": listing_v,
        "month_hierarchy_violations": month_v,
        "summary": summarize_verification(
            comparison,
            dow_v,
            listing_v,
            month_v,
            spread_u,
        ),
    }
