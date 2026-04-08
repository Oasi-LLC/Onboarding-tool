from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, List

import pandas as pd

@dataclass
class PricingInputs:
    overall_summary: pd.DataFrame
    monthly_combined: pd.DataFrame
    by_day_of_week: pd.DataFrame
    adr_by_listing_by_month: pd.DataFrame


def _load_pricing_inputs(analysis_dir: Path) -> PricingInputs:
    """Load all analysis tables needed for pricing from a given directory."""
    overall = pd.read_csv(analysis_dir / "overall_summary.csv")
    monthly_combined = pd.read_csv(analysis_dir / "monthly_performance_combined.csv")
    dow = pd.read_csv(analysis_dir / "by_day_of_week.csv")
    adr_listing_month = pd.read_csv(analysis_dir / "adr_by_listing_by_month.csv")
    return PricingInputs(
        overall_summary=overall,
        monthly_combined=monthly_combined,
        by_day_of_week=dow,
        adr_by_listing_by_month=adr_listing_month,
    )


def _compute_base_adr_anchor(overall_summary: pd.DataFrame) -> Dict[str, float]:
    """Return mapping unit_id -> base_adr_anchor (two-year ADR)."""
    df = overall_summary.copy()
    # Exclude property total row if present
    df = df.loc[df["unit_id"] != "PROPERTY"]
    return df.set_index("unit_id")["adr"].to_dict()


def _compute_listing_factor(overall_summary: pd.DataFrame) -> Dict[str, float]:
    """Map each listing to a strength multiplier based on RevPAR percentile."""
    df = overall_summary.copy()
    df = df.loc[df["unit_id"] != "PROPERTY"].copy()
    if df.empty or "revpar" not in df.columns:
        return {}

    # Percentile in [0, 1]
    df["revpar_percentile"] = df["revpar"].rank(method="min", pct=True)

    def to_factor(p: float) -> float:
        if p >= 0.80:
            return 1.10
        if p >= 0.60:
            return 1.05
        if p >= 0.40:
            return 1.00
        if p >= 0.20:
            return 0.95
        return 0.90

    df["listing_factor"] = df["revpar_percentile"].apply(to_factor)
    return df.set_index("unit_id")["listing_factor"].to_dict()


def _compute_month_factor(monthly_combined: pd.DataFrame) -> Dict[int, float]:
    """Map month_index -> month_factor using performance_score_1_10."""
    df = monthly_combined.copy()
    if "month_index" not in df.columns:
        raise ValueError("monthly_performance_combined must have 'month_index'")
    if "performance_score_1_10" not in df.columns:
        raise ValueError("monthly_performance_combined must have 'performance_score_1_10'")

    def to_factor(score: float) -> float:
        return 0.75 + 0.05 * float(score)

    df["month_factor"] = df["performance_score_1_10"].apply(to_factor)
    return df.set_index("month_index")["month_factor"].to_dict()


def _compute_dow_factor(by_day_of_week: pd.DataFrame) -> Dict[str, float]:
    """Map day_of_week -> dow_factor using dow_score_1_10."""
    df = by_day_of_week.copy()
    if "day_of_week" not in df.columns:
        raise ValueError("by_day_of_week must have 'day_of_week'")
    if "dow_score_1_10" not in df.columns:
        raise ValueError("by_day_of_week must have 'dow_score_1_10'")

    def to_factor(score: float) -> float:
        # Weekday spread tuned for STR / destination markets
        # Score 1  -> ~0.84, score 5 -> ~1.00, score 10 -> 1.20
        return 0.80 + 0.04 * float(score)

    df["dow_factor"] = df["dow_score_1_10"].apply(to_factor)
    return df.set_index("day_of_week")["dow_factor"].to_dict()


def _compute_adr_bounds(adr_by_listing_by_month: pd.DataFrame) -> pd.DataFrame:
    """
    Return DataFrame with columns:
      unit_id, month_index, floor, ceiling
    based on min_adr / max_adr across both years for that listing × calendar month.
    """
    df = adr_by_listing_by_month.copy()
    if "unit_id" not in df.columns or "year_month" not in df.columns:
        raise ValueError("adr_by_listing_by_month must have 'unit_id' and 'year_month'")
    if "min_adr" not in df.columns or "max_adr" not in df.columns:
        raise ValueError("adr_by_listing_by_month must have 'min_adr' and 'max_adr'")

    df["month_index"] = df["year_month"].astype(str).str[5:7].astype(int)
    grp = df.groupby(["unit_id", "month_index"], as_index=False).agg(
        min_adr=("min_adr", "min"),
        max_adr=("max_adr", "max"),
    )

    # If min/max are missing, fall back to adr
    if "adr" in df.columns:
        fallback = df.groupby(["unit_id", "month_index"], as_index=False)["adr"].agg(
            adr_min="min",
            adr_max="max",
        )
        grp = grp.merge(fallback, on=["unit_id", "month_index"], how="left")
        grp["min_adr"] = grp["min_adr"].fillna(grp["adr_min"])
        grp["max_adr"] = grp["max_adr"].fillna(grp["adr_max"])
        grp = grp.drop(columns=["adr_min", "adr_max"])

    # Store raw min/max; adaptive floor/ceiling are derived later using month strength
    grp["floor"] = grp["min_adr"]
    grp["ceiling"] = grp["max_adr"]
    return grp[["unit_id", "month_index", "floor", "ceiling"]]


def _apply_weekday_hierarchy(prices: Dict[str, float], hierarchy: List[List[str]] | None) -> Dict[str, float]:
    """
    Enforce a configurable weekday price ladder defined as tiers from lowest to highest.
    Each tier is a list of day names; lower tiers must not price above higher tiers.
    Implementation raises lower days up to match the required minimums.
    """
    if not hierarchy:
        return prices

    p = dict(prices)

    # First, within each tier ensure all days are at least the max within that tier.
    for tier in hierarchy:
        tier_values = [p.get(d) for d in tier if d in p]
        if not tier_values or any(v is None for v in tier_values):
            continue
        tier_max = max(tier_values)
        for d in tier:
            if d in p and p[d] is not None and p[d] < tier_max:
                p[d] = tier_max

    # Then ensure non-decreasing across tiers
    prev_max = None
    for tier in hierarchy:
        tier_values = [p.get(d) for d in tier if d in p]
        if not tier_values or any(v is None for v in tier_values):
            continue
        tier_max = max(tier_values)
        if prev_max is not None and tier_max < prev_max:
            tier_max = prev_max
            for d in tier:
                if d in p and p[d] is not None and p[d] < tier_max:
                    p[d] = tier_max
        prev_max = tier_max

    return p


def build_pricing_matrix(analysis_dir: Path, dow_hierarchy: List[List[str]] | None = None) -> pd.DataFrame:
    """
    Build the draft pricing matrix from analysis CSVs in analysis_dir.

    Returns a DataFrame with columns:
      unit_id, month_index, day_of_week,
      base_adr_anchor, listing_score, month_score, dow_score, draft_adr
    """
    inputs = _load_pricing_inputs(analysis_dir)

    base_adr = _compute_base_adr_anchor(inputs.overall_summary)
    listing_factor = _compute_listing_factor(inputs.overall_summary)
    month_factor = _compute_month_factor(inputs.monthly_combined)
    dow_factor = _compute_dow_factor(inputs.by_day_of_week)

    # Helpful lookups for scores / strength metrics
    listing_revpar = (
        inputs.overall_summary
        .loc[inputs.overall_summary["unit_id"] != "PROPERTY"]
        .set_index("unit_id")["revpar"]
        .to_dict()
    )
    month_score_map = (
        inputs.monthly_combined.set_index("month_index")["performance_score_1_10"].to_dict()
    )
    dow_score_map = (
        inputs.by_day_of_week.set_index("day_of_week")["dow_score_1_10"].to_dict()
    )

    adr_listing_month = inputs.adr_by_listing_by_month.copy()
    adr_listing_month["month_index"] = adr_listing_month["year_month"].astype(str).str[5:7].astype(int)
    bounds = _compute_adr_bounds(adr_listing_month)

    # Pre-compute fallback ranges for missing listing-month combos
    # 1) listing-wide ADR range
    listing_adr_range = (
        adr_listing_month.groupby("unit_id")["adr"].agg(listing_min="min", listing_max="max").reset_index()
    )
    listing_adr_range = listing_adr_range.set_index("unit_id")
    # 2) portfolio-wide ADR range per calendar month
    month_adr_range = (
        adr_listing_month.groupby("month_index")["adr"].agg(month_min="min", month_max="max").reset_index()
    )
    month_adr_range = month_adr_range.set_index("month_index")

    # 3) same-season fallback: map month -> season and reuse listing-wide ADR in that season
    def _season_for_month(m: int) -> str | None:
        if m in (4, 5, 6, 9, 10):
            return "High"
        if m in (1, 2, 12):
            return "Low"
        if m in (3, 7, 8, 11):
            return "Shoulder"
        return None

    adr_listing_month["season"] = adr_listing_month["month_index"].map(_season_for_month)
    listing_season_range = (
        adr_listing_month.groupby(["unit_id", "season"])["adr"]
        .agg(season_min="min", season_max="max")
        .reset_index()
        .set_index(["unit_id", "season"])
    )

    # Base grid
    unit_ids = sorted(
        [u for u in base_adr.keys()],
        key=lambda s: int(str(s).split()[0]) if str(s).split()[0].isdigit() else 0,
    )
    months = list(range(1, 13))
    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

    rows = []
    bounds_idx = bounds.set_index(["unit_id", "month_index"])

    for unit in unit_ids:
        b_anchor = float(base_adr.get(unit, 0.0))
        l_factor = float(listing_factor.get(unit, 1.0))
        l_adj = l_factor - 1.0
        l_metric = float(listing_revpar.get(unit, 0.0)) if unit in listing_revpar else None

        for m in months:
            m_factor = float(month_factor.get(m, 1.0))
            m_adj = m_factor - 1.0
            m_score = float(month_score_map.get(m, 0.0)) if m in month_score_map else None

            # Adaptive historical bounds based on month strength
            min_adr = None
            max_adr = None
            if (unit, m) in bounds_idx.index:
                rec = bounds_idx.loc[(unit, m)]
                min_adr = float(rec["floor"]) if pd.notna(rec["floor"]) else None
                max_adr = float(rec["ceiling"]) if pd.notna(rec["ceiling"]) else None

            # Fallback chain if specific listing × month has no ADR history
            if min_adr is None or max_adr is None:
                # same season for this listing
                season = _season_for_month(m)
                if season and (unit, season) in listing_season_range.index:
                    r = listing_season_range.loc[(unit, season)]
                    min_adr = float(r["season_min"])
                    max_adr = float(r["season_max"])
            if (min_adr is None or max_adr is None) and unit in listing_adr_range.index:
                r = listing_adr_range.loc[unit]
                min_adr = float(r["listing_min"])
                max_adr = float(r["listing_max"])
            if (min_adr is None or max_adr is None) and m in month_adr_range.index:
                r = month_adr_range.loc[m]
                min_adr = float(r["month_min"])
                max_adr = float(r["month_max"])

            if m_score is None:
                floor_mult, ceil_mult = 0.90, 1.15
            elif m_score <= 3:
                floor_mult, ceil_mult = 0.85, 1.10
            elif m_score <= 7:
                floor_mult, ceil_mult = 0.90, 1.15
            else:
                floor_mult, ceil_mult = 0.95, 1.25

            floor = min_adr * floor_mult if min_adr is not None else None
            ceiling = max_adr * ceil_mult if max_adr is not None else None

            # First compute raw prices for all days in this listing × month (additive adjustments)
            day_prices: Dict[str, float] = {}
            day_scores: Dict[str, float] = {}
            for d in days:
                d_factor = float(dow_factor.get(d, 1.0))
                d_adj = d_factor - 1.0
                d_score = float(dow_score_map.get(d, 0.0)) if d in dow_score_map else None
                raw = b_anchor * (1.0 + l_adj + m_adj + d_adj)

                day_prices[d] = raw
                day_scores[d] = d_score

            # Enforce weekday hierarchy (if provided via config)
            day_prices = _apply_weekday_hierarchy(day_prices, dow_hierarchy)

            # Apply adaptive historical bounds at the week level: scale proportionally if needed
            if floor is not None or ceiling is not None:
                vals = [v for v in day_prices.values() if v is not None]
                if vals:
                    week_min = min(vals)
                    week_max = max(vals)

                    # Scale up if everything is below floor
                    if floor is not None and week_min < floor and week_min > 0:
                        scale_up = floor / week_min
                        day_prices = {d: v * scale_up for d, v in day_prices.items()}
                        vals = [v for v in day_prices.values() if v is not None]
                        week_max = max(vals)

                    # Scale down if anything is above ceiling
                    if ceiling is not None and week_max > ceiling and week_max > 0:
                        scale_down = ceiling / week_max
                        day_prices = {d: v * scale_down for d, v in day_prices.items()}

            for d in days:
                rows.append(
                    {
                        "unit_id": unit,
                        "month_index": m,
                        "day_of_week": d,
                        "base_adr_anchor": round(b_anchor, 2),
                        "listing_revpar": l_metric,
                        "listing_strength_factor": round(l_factor, 3),
                        "month_score": m_score,
                        "dow_score": day_scores.get(d),
                        "draft_adr": day_prices[d],
                    }
                )

    df = pd.DataFrame(rows)

    if df.empty:
        return df

    # 1) Enforce month hierarchy: for each unit × day, prices must be non-decreasing with month_score
    def _enforce_month_hierarchy(group: pd.DataFrame) -> pd.DataFrame:
        grp = group.copy()
        grp = grp.sort_values(["month_score", "month_index"])
        grp["draft_adr"] = grp["draft_adr"].cummax()
        # restore original ordering by month_index
        return grp.sort_values("month_index")

    df = (
        df.groupby(["unit_id", "day_of_week"], group_keys=False)
        .apply(_enforce_month_hierarchy)
        .reset_index(drop=True)
    )

    # 2) Enforce weekday hierarchy (pre-bounds) per unit × month
    def _enforce_dow(group: pd.DataFrame) -> pd.DataFrame:
        if not dow_hierarchy:
            return group
        prices = {row["day_of_week"]: row["draft_adr"] for _, row in group.iterrows()}
        adjusted = _apply_weekday_hierarchy(prices, dow_hierarchy)
        grp = group.copy()
        grp["draft_adr"] = grp["day_of_week"].map(adjusted)
        return grp

    df = (
        df.groupby(["unit_id", "month_index"], group_keys=False)
        .apply(_enforce_dow)
        .reset_index(drop=True)
    )

    # 3) Apply adaptive historical bounds with week-level scaling
    def _apply_bounds(group: pd.DataFrame) -> pd.DataFrame:
        unit = group["unit_id"].iloc[0]
        m = int(group["month_index"].iloc[0])
        # Rebuild min_adr / max_adr and multipliers as above
        m_score = group["month_score"].iloc[0]
        if m_score is None or pd.isna(m_score):
            floor_mult, ceil_mult = 0.90, 1.15
        elif m_score <= 3:
            floor_mult, ceil_mult = 0.85, 1.10
        elif m_score <= 7:
            floor_mult, ceil_mult = 0.90, 1.15
        else:
            floor_mult, ceil_mult = 0.95, 1.25

        # Find min/max ADR history for this unit × month using same fallback as above
        min_adr = max_adr = None
        if (unit, m) in bounds_idx.index:
            rec = bounds_idx.loc[(unit, m)]
            min_adr = float(rec["floor"]) if pd.notna(rec["floor"]) else None
            max_adr = float(rec["ceiling"]) if pd.notna(rec["ceiling"]) else None
        if min_adr is None or max_adr is None:
            season = _season_for_month(m)
            if season and (unit, season) in listing_season_range.index:
                r = listing_season_range.loc[(unit, season)]
                min_adr = float(r["season_min"])
                max_adr = float(r["season_max"])
        if (min_adr is None or max_adr is None) and unit in listing_adr_range.index:
            r = listing_adr_range.loc[unit]
            min_adr = float(r["listing_min"])
            max_adr = float(r["listing_max"])
        if (min_adr is None or max_adr is None) and m in month_adr_range.index:
            r = month_adr_range.loc[m]
            min_adr = float(r["month_min"])
            max_adr = float(r["month_max"])

        if min_adr is None or max_adr is None:
            return group

        floor = min_adr * floor_mult
        ceiling = max_adr * ceil_mult

        vals = group["draft_adr"].tolist()
        if not vals:
            return group
        week_min = min(vals)
        week_max = max(vals)

        new_vals = vals
        # Scale up if everything is below floor
        if floor is not None and week_min < floor and week_min > 0:
            scale_up = floor / week_min
            new_vals = [v * scale_up for v in new_vals]
            week_max = max(new_vals)
        # Scale down if anything above ceiling
        if ceiling is not None and week_max > ceiling and week_max > 0:
            scale_down = ceiling / week_max
            new_vals = [v * scale_down for v in new_vals]

        grp = group.copy()
        grp["draft_adr"] = new_vals
        return grp

    df = (
        df.groupby(["unit_id", "month_index"], group_keys=False)
        .apply(_apply_bounds)
        .reset_index(drop=True)
    )

    # 4) Re-apply month and weekday hierarchies after bounds
    df = (
        df.groupby(["unit_id", "day_of_week"], group_keys=False)
        .apply(_enforce_month_hierarchy)
        .reset_index(drop=True)
    )
    df = (
        df.groupby(["unit_id", "month_index"], group_keys=False)
        .apply(_enforce_dow)
        .reset_index(drop=True)
    )

    # Final rounding to nearest whole dollar
    df["draft_adr"] = df["draft_adr"].round(0)

    # Return matrix in wide format: one row per unit_id × month_index,
    # with one column per day_of_week containing the rate.
    wide = (
        df.pivot(index=["unit_id", "month_index"], columns="day_of_week", values="draft_adr")
        .reset_index()
    )

    # Ensure a consistent weekday column order if present
    day_cols = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    cols = ["unit_id", "month_index"] + [c for c in day_cols if c in wide.columns]
    wide = wide[cols]

    return wide

