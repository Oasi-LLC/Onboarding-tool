from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd


def _coerce_provisional_bool(series: pd.Series) -> pd.Series:
    """CSV-safe: booleans or string 'True'/'false' from read_csv."""
    out = []
    for v in series.tolist():
        if isinstance(v, bool):
            out.append(v)
        elif pd.isna(v):
            out.append(False)
        else:
            out.append(str(v).strip().lower() in ("true", "1", "t", "yes"))
    return pd.Series(out, index=series.index, dtype=bool)


def _month_performance_score_for_pricing(monthly_combined: pd.DataFrame) -> pd.Series:
    """
    Raw performance_score_1_10 for month_factor / ordering, except provisional months -> 5
    (neutral: no uplift or discount vs the 1–10 scale midpoint).
    """
    df = monthly_combined
    base = pd.to_numeric(df["performance_score_1_10"], errors="coerce").fillna(5.0)
    if "performance_rank_provisional" in df.columns:
        prov = _coerce_provisional_bool(df["performance_rank_provisional"])
    else:
        prov = pd.Series(False, index=df.index)
    return base.where(~prov, 5.0)


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


def _score_to_month_factor(score: float) -> float:
    return 0.75 + 0.05 * float(score)


def listing_factors_from_price_hierarchy(raw: Any) -> Optional[Dict[str, float]]:
    """
    Parse ``pricing.listing_price_hierarchy`` from property YAML into ``unit_id -> listing_factor``.

    Each tier's ``order`` (1 = weakest … N = strongest) is mapped with the same curve as months:
    ``_score_to_month_factor(order)`` with ``order`` clamped to ``[1, 10]``.
    """
    if not raw or not isinstance(raw, dict):
        return None
    tiers = raw.get("tiers")
    if not tiers or not isinstance(tiers, list):
        return None
    out: Dict[str, float] = {}
    for t in tiers:
        if not isinstance(t, dict):
            continue
        try:
            order = float(t.get("order"))
        except (TypeError, ValueError):
            continue
        score = min(10.0, max(1.0, order))
        fac = _score_to_month_factor(score)
        for u in t.get("unit_ids") or []:
            su = str(u).strip()
            if su:
                out[su] = fac
    return out or None


def apply_within_tier_median_rates(
    wide: pd.DataFrame,
    listing_price_hierarchy: Any,
) -> pd.DataFrame:
    """
    For each tier in ``listing_price_hierarchy.tiers``, set every member
    ``unit_id``'s rate (per ``month_index`` × weekday column) to the **median**
    of that tier's member rates in the same cell. Single-listing tiers keep
    their value; ``unit_id`` rows not listed in any tier are unchanged.
    """
    if wide.empty or not listing_price_hierarchy or not isinstance(listing_price_hierarchy, dict):
        return wide
    tiers = listing_price_hierarchy.get("tiers")
    if not tiers or not isinstance(tiers, list):
        return wide

    tier_unit_lists: List[List[str]] = []
    for t in tiers:
        if not isinstance(t, dict):
            continue
        uids = [str(u).strip() for u in (t.get("unit_ids") or []) if str(u).strip()]
        if uids:
            tier_unit_lists.append(uids)

    if not tier_unit_lists:
        return wide

    day_cols = [
        c
        for c in (
            "Monday",
            "Tuesday",
            "Wednesday",
            "Thursday",
            "Friday",
            "Saturday",
            "Sunday",
        )
        if c in wide.columns
    ]
    if not day_cols or "month_index" not in wide.columns or "unit_id" not in wide.columns:
        return wide

    out = wide.copy()
    out["unit_id"] = out["unit_id"].astype(str)
    matrix_units = set(out["unit_id"].unique())

    for tier_uids in tier_unit_lists:
        present = [u for u in tier_uids if u in matrix_units]
        if not present:
            continue
        for m in sorted(out["month_index"].unique().tolist()):
            mask = (out["month_index"] == m) & (out["unit_id"].isin(present))
            if not mask.any():
                continue
            for d in day_cols:
                vals = pd.to_numeric(out.loc[mask, d], errors="coerce")
                med = vals.median()
                if pd.isna(med):
                    continue
                out.loc[mask, d] = round(float(med))

    return out


def coerce_month_score_by_index_config(raw: Any) -> Optional[Dict[int, float]]:
    """
    Parse pricing.month_score_by_index from YAML into {1..12 -> score}.
    Returns None if empty / invalid.
    """
    if not raw or not isinstance(raw, dict):
        return None
    out: Dict[int, float] = {}
    for k, v in raw.items():
        try:
            ki = int(k)
            if 1 <= ki <= 12:
                out[ki] = float(v)
        except (TypeError, ValueError):
            continue
    return out or None


def _month_factors_and_scores_merged(
    monthly_combined: pd.DataFrame,
    overrides: Dict[int, float],
) -> tuple[Dict[int, float], Dict[int, float]]:
    """
    For each calendar month 1-12, use override score if present, else analysis CSV score
    (same provisional handling as _month_performance_score_for_pricing).
    """
    csv_scores: Dict[int, float] = {}
    df = monthly_combined.copy()
    if not df.empty and "month_index" in df.columns and "performance_score_1_10" in df.columns:
        tmp = df.assign(_s=_month_performance_score_for_pricing(df))
        csv_scores = {int(k): float(v) for k, v in tmp.groupby("month_index")["_s"].first().items()}

    factor: Dict[int, float] = {}
    scores: Dict[int, float] = {}
    for m in range(1, 13):
        sc = float(overrides[m]) if m in overrides else float(csv_scores.get(m, 5.0))
        scores[m] = sc
        factor[m] = _score_to_month_factor(sc)
    return factor, scores


def _compute_month_factor(monthly_combined: pd.DataFrame) -> Dict[int, float]:
    """Map month_index -> month_factor using performance_score_1_10 (provisional months -> score 5)."""
    df = monthly_combined.copy()
    if "month_index" not in df.columns:
        raise ValueError("monthly_performance_combined must have 'month_index'")
    if "performance_score_1_10" not in df.columns:
        raise ValueError("monthly_performance_combined must have 'performance_score_1_10'")

    df["_score_for_pricing"] = _month_performance_score_for_pricing(df)
    df["month_factor"] = df["_score_for_pricing"].apply(_score_to_month_factor)
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


def _build_sos_reservation_grounded_matrix(
    analysis_dir: Path,
    dow_hierarchy: List[List[str]] | None = None,
) -> pd.DataFrame:
    """
    Build SOS pricing from reservation-grounded 11BR month×DOW ADR medians,
    then derive 12BR/23BR via fixed multipliers.
    """
    canonical_path = analysis_dir.parent / "ingestion" / "canonical.csv"
    if not canonical_path.exists():
        raise ValueError(f"Canonical file not found for SOS pricing: {canonical_path}")

    df = pd.read_csv(canonical_path)
    if df.empty:
        return pd.DataFrame(columns=["unit_id", "month_index", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])

    df["arrival_date"] = pd.to_datetime(df.get("arrival_date"), errors="coerce")
    df["nights"] = pd.to_numeric(df.get("nights"), errors="coerce")
    df["revenue"] = pd.to_numeric(df.get("revenue"), errors="coerce")
    df = df.loc[
        df["arrival_date"].notna()
        & df["nights"].notna()
        & df["revenue"].notna()
        & (df["nights"] > 0)
        & (df["revenue"] > 0)
    ].copy()
    if df.empty:
        return pd.DataFrame(columns=["unit_id", "month_index", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"])

    df["month_index"] = df["arrival_date"].dt.month
    df["day_of_week"] = df["arrival_date"].dt.day_name()
    df["adr"] = df["revenue"] / df["nights"]

    days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
    months = list(range(1, 13))
    min_samples = 2

    d11 = df.loc[df["unit_id"] == "11BR"].copy()
    if d11.empty:
        raise ValueError("No 11BR reservation rows found for SOS in canonical data.")

    # Core and fallback stats (median ADR + sample size).
    cell_stats = (
        d11.groupby(["month_index", "day_of_week"], as_index=False)["adr"]
        .agg(cell_median="median", cell_n="count")
    )
    dow_stats = (
        d11.groupby("day_of_week", as_index=False)["adr"]
        .agg(dow_median="median", dow_n="count")
    )
    month_stats = (
        d11.groupby("month_index", as_index=False)["adr"]
        .agg(month_median="median", month_n="count")
    )
    listing_overall = float(d11["adr"].median())

    property_cell = (
        df.groupby(["month_index", "day_of_week"], as_index=False)["adr"]
        .agg(prop_cell_median="median", prop_cell_n="count")
    )
    property_dow = (
        df.groupby("day_of_week", as_index=False)["adr"]
        .agg(prop_dow_median="median", prop_dow_n="count")
    )
    property_month = (
        df.groupby("month_index", as_index=False)["adr"]
        .agg(prop_month_median="median", prop_month_n="count")
    )
    property_overall = float(df["adr"].median())

    # Convert to fast lookup dicts.
    cell_map = {(int(r.month_index), str(r.day_of_week)): (float(r.cell_median), int(r.cell_n)) for r in cell_stats.itertuples(index=False)}
    dow_map = {str(r.day_of_week): (float(r.dow_median), int(r.dow_n)) for r in dow_stats.itertuples(index=False)}
    month_map = {int(r.month_index): (float(r.month_median), int(r.month_n)) for r in month_stats.itertuples(index=False)}
    prop_cell_map = {(int(r.month_index), str(r.day_of_week)): (float(r.prop_cell_median), int(r.prop_cell_n)) for r in property_cell.itertuples(index=False)}
    prop_dow_map = {str(r.day_of_week): (float(r.prop_dow_median), int(r.prop_dow_n)) for r in property_dow.itertuples(index=False)}
    prop_month_map = {int(r.month_index): (float(r.prop_month_median), int(r.prop_month_n)) for r in property_month.itertuples(index=False)}

    def _fallback_value(month_idx: int, day_name: str) -> float:
        val, n = dow_map.get(day_name, (None, 0))
        if val is not None and n > 0:
            return float(val)
        val, n = month_map.get(month_idx, (None, 0))
        if val is not None and n > 0:
            return float(val)
        if pd.notna(listing_overall):
            return float(listing_overall)
        val, n = prop_cell_map.get((month_idx, day_name), (None, 0))
        if val is not None and n > 0:
            return float(val)
        val, n = prop_dow_map.get(day_name, (None, 0))
        if val is not None and n > 0:
            return float(val)
        val, n = prop_month_map.get(month_idx, (None, 0))
        if val is not None and n > 0:
            return float(val)
        return float(property_overall)

    # Build pre-hierarchy 11BR matrix with sparse-cell smoothing.
    rows: List[Dict[str, float]] = []
    for m in months:
        row: Dict[str, float] = {"unit_id": "11BR", "month_index": m}
        for d in days:
            direct_val, direct_n = cell_map.get((m, d), (None, 0))
            fallback = _fallback_value(m, d)
            if direct_val is not None and direct_n >= min_samples:
                value = float(direct_val)
            elif direct_val is not None and direct_n > 0:
                w = float(direct_n) / float(min_samples)
                value = float(direct_val) * w + float(fallback) * (1.0 - w)
            else:
                value = float(fallback)
            row[d] = value
        rows.append(row)
    base = pd.DataFrame(rows)

    # Cap extreme month-to-month movement (soft continuity) by day.
    max_step_ratio = 0.35
    for d in days:
        for m in range(2, 13):
            prev = float(base.loc[base["month_index"] == m - 1, d].iloc[0])
            cur = float(base.loc[base["month_index"] == m, d].iloc[0])
            upper = prev * (1.0 + max_step_ratio)
            lower = prev * (1.0 - max_step_ratio)
            if cur > upper:
                base.loc[base["month_index"] == m, d] = upper
            elif cur < lower:
                base.loc[base["month_index"] == m, d] = lower

    # Month hierarchy: strict_score with minimal uplift only (provisional -> neutral 5).
    monthly = pd.read_csv(analysis_dir / "monthly_performance_combined.csv")
    monthly["_score_for_pricing"] = _month_performance_score_for_pricing(monthly)
    score_map = monthly.set_index("month_index")["_score_for_pricing"].to_dict()
    for d in days:
        ordered_months = sorted(months, key=lambda x: (float(score_map.get(x, 0.0)), x))
        running = None
        for m in ordered_months:
            cur = float(base.loc[base["month_index"] == m, d].iloc[0])
            if running is None:
                running = cur
            elif cur < running:
                base.loc[base["month_index"] == m, d] = running
            else:
                running = cur

    # DOW hierarchy per month with minimal uplift, no broad flattening.
    eps = 1.0
    for m in months:
        row_idx = base["month_index"] == m
        mon = float(base.loc[row_idx, "Monday"].iloc[0])
        tue = float(base.loc[row_idx, "Tuesday"].iloc[0])
        wed = float(base.loc[row_idx, "Wednesday"].iloc[0])
        tri = max(mon, tue, wed)
        sun = float(base.loc[row_idx, "Sunday"].iloc[0])
        thu = float(base.loc[row_idx, "Thursday"].iloc[0])
        fri = float(base.loc[row_idx, "Friday"].iloc[0])
        sat = float(base.loc[row_idx, "Saturday"].iloc[0])

        sun = max(sun, tri + eps)
        thu = max(thu, sun + eps)
        weekend = max(fri, sat, thu + eps)

        base.loc[row_idx, "Monday"] = tri
        base.loc[row_idx, "Tuesday"] = tri
        base.loc[row_idx, "Wednesday"] = tri
        base.loc[row_idx, "Sunday"] = sun
        base.loc[row_idx, "Thursday"] = thu
        base.loc[row_idx, "Friday"] = weekend
        base.loc[row_idx, "Saturday"] = weekend

    # Final round for 11BR and derive other required listings.
    for d in days:
        base[d] = base[d].round(0)

    b11 = base.copy()
    b12 = b11.copy()
    b23 = b11.copy()
    b12["unit_id"] = "12BR"
    b23["unit_id"] = "23BR"
    for d in days:
        b12[d] = (b11[d].astype(float) * 1.2).round(0)
        b23[d] = (b12[d].astype(float) * 2.0).round(0)

    out = (
        pd.concat([b11, b12, b23], ignore_index=True)
        [["unit_id", "month_index"] + days]
        .sort_values(["unit_id", "month_index"])
        .reset_index(drop=True)
    )

    # Ensure requested DOW hierarchy and listing hierarchy are satisfied.
    for _, r in out.iterrows():
        mon, tue, wed = float(r["Monday"]), float(r["Tuesday"]), float(r["Wednesday"])
        sun, thu = float(r["Sunday"]), float(r["Thursday"])
        fri, sat = float(r["Friday"]), float(r["Saturday"])
        if not (mon == tue == wed and wed < sun < thu < fri and fri == sat):
            raise ValueError(f"SOS DOW hierarchy violation for {r['unit_id']} month {int(r['month_index'])}")

    for m in months:
        r11 = out[(out["unit_id"] == "11BR") & (out["month_index"] == m)].iloc[0]
        r12 = out[(out["unit_id"] == "12BR") & (out["month_index"] == m)].iloc[0]
        r23 = out[(out["unit_id"] == "23BR") & (out["month_index"] == m)].iloc[0]
        for d in days:
            if not (float(r11[d]) < float(r12[d]) < float(r23[d])):
                raise ValueError(f"SOS listing hierarchy violation month {m} day {d}")

    return out


def build_pricing_matrix(
    analysis_dir: Path,
    dow_hierarchy: List[List[str]] | None = None,
    month_score_by_index: Optional[Dict[int, float]] = None,
    listing_factor_by_unit: Optional[Dict[str, float]] = None,
) -> pd.DataFrame:
    """
    Build the draft pricing matrix from analysis CSVs in analysis_dir.

    When ``month_score_by_index`` is set (from property YAML ``pricing.month_score_by_index``),
    those values are used as the 1-10 style month strength for each calendar month; any month
    not listed falls back to ``monthly_performance_combined`` (with provisional neutralization).

    When ``listing_factor_by_unit`` is set (from ``pricing.listing_price_hierarchy``), those
    factors override RevPAR-percentile listing factors for each matching ``unit_id``; any unit
    not listed keeps the RevPAR-derived factor.

    Returns a DataFrame with columns:
      unit_id, month_index, day_of_week,
      base_adr_anchor, listing_score, month_score, dow_score, draft_adr
    """
    if analysis_dir.parent.name.lower() == "sos":
        return _build_sos_reservation_grounded_matrix(analysis_dir, dow_hierarchy=dow_hierarchy)

    inputs = _load_pricing_inputs(analysis_dir)

    base_adr = _compute_base_adr_anchor(inputs.overall_summary)
    listing_factor = _compute_listing_factor(inputs.overall_summary)
    if listing_factor_by_unit:
        merged = dict(listing_factor)
        merged.update(listing_factor_by_unit)
        listing_factor = merged
    if month_score_by_index:
        month_factor, month_score_map_dict = _month_factors_and_scores_merged(
            inputs.monthly_combined, month_score_by_index
        )
    else:
        month_factor = _compute_month_factor(inputs.monthly_combined)
        month_score_map_dict = (
            inputs.monthly_combined.assign(
                _score_for_pricing=_month_performance_score_for_pricing(inputs.monthly_combined)
            )
            .set_index("month_index")["_score_for_pricing"]
            .to_dict()
        )
        month_score_map_dict = {int(k): float(v) for k, v in month_score_map_dict.items()}
    dow_factor = _compute_dow_factor(inputs.by_day_of_week)

    # Helpful lookups for scores / strength metrics
    listing_revpar = (
        inputs.overall_summary
        .loc[inputs.overall_summary["unit_id"] != "PROPERTY"]
        .set_index("unit_id")["revpar"]
        .to_dict()
    )
    month_score_map = month_score_map_dict
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


# ══════════════════════════════════════════════════════════════════════════════
# TIERED PRICING MATRIX
# Anchor × (season_index / low_index) × (dow_index / d1_index) × premium
# Config lives in pricing: section of the property YAML.
# ══════════════════════════════════════════════════════════════════════════════

_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]

_DAY_ORDER = [
    "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday",
]


def build_tiered_pricing_matrix(pricing_cfg: Dict[str, Any]) -> pd.DataFrame:
    """
    Build a pricing matrix using explicit anchor × seasonal × DOW tier multipliers.

    Formula per cell:
        rate = anchor × (season_index / low_index) × (dow_index / d1_index) × group_premium

    Parameters
    ----------
    pricing_cfg : dict
        The ``pricing:`` block from the property YAML.  Expected keys:
        - anchor_rate (float)
        - product_groups (list of {name, unit_ids, premium})
        - seasonal_tiers (list of {name, months, index})
        - dow_tiers (list of {name, days, index})

    Returns
    -------
    pd.DataFrame
        Columns: listing_group, month_index, month_name, season,
                 Monday … Sunday  (one column per day, rounded to nearest dollar)
    """
    anchor = float(pricing_cfg.get("anchor_rate", 300))
    product_groups = pricing_cfg.get("product_groups") or []
    seasonal_tiers = pricing_cfg.get("seasonal_tiers") or []
    dow_tiers = pricing_cfg.get("dow_tiers") or []

    # ── Build month → (season_name, season_index) lookup ──────────────────────
    month_to_season: Dict[int, tuple] = {}
    low_index: Optional[float] = None
    for tier in seasonal_tiers:
        idx = float(tier.get("index", 1.0))
        for m in tier.get("months") or []:
            month_to_season[int(m)] = (str(tier.get("name", "")), idx)
        if str(tier.get("name", "")).lower() == "low":
            low_index = idx

    if low_index is None:
        low_index = min((float(t.get("index", 1.0)) for t in seasonal_tiers), default=1.0)

    # ── Build day → (tier_name, dow_index) lookup ──────────────────────────────
    day_to_dow: Dict[str, tuple] = {}
    d1_index: Optional[float] = None
    for tier in dow_tiers:
        idx = float(tier.get("index", 1.0))
        for d in tier.get("days") or []:
            day_to_dow[str(d)] = (str(tier.get("name", "")), idx)
        if str(tier.get("name", "")).upper() == "D1":
            d1_index = idx

    if d1_index is None:
        d1_index = min((float(t.get("index", 1.0)) for t in dow_tiers), default=1.0)

    # ── Build matrix rows ──────────────────────────────────────────────────────
    rows: List[Dict[str, Any]] = []
    for group in product_groups:
        group_name = str(group.get("name", "Unknown"))
        premium = float(group.get("premium", 1.0))

        for m in range(1, 13):
            season_name, season_idx = month_to_season.get(m, ("Unknown", 1.0))
            season_mult = season_idx / low_index

            row: Dict[str, Any] = {
                "listing_group": group_name,
                "month_index": m,
                "month_name": _MONTH_NAMES[m - 1],
                "season": season_name,
            }
            for d in _DAY_ORDER:
                _, dow_idx = day_to_dow.get(d, ("D1", d1_index))
                dow_mult = dow_idx / d1_index
                row[d] = int(round(anchor * season_mult * dow_mult * premium))

            rows.append(row)

    cols = ["listing_group", "month_index", "month_name", "season"] + _DAY_ORDER
    return pd.DataFrame(rows)[cols]


# ── Colour palette (openpyxl) ─────────────────────────────────────────────────
_C = {
    "navy":    "1F3864",
    "blue":    "2E75B6",
    "peak":    "C00000",
    "high":    "FF0000",
    "shldr":   "FF9900",
    "low":     "FFD966",
    "sat":     "7030A0",
    "fri":     "375623",
    "sun":     "833C00",
    "thu":     "595959",
    "input":   "EBF3FB",
    "grey":    "F2F2F2",
    "white":   "FFFFFF",
    "blk":     "000000",
    "blue_tx": "0000FF",
}

_SEASON_FILLS = {"Peak": _C["peak"], "High": _C["high"], "Shoulder": _C["shldr"], "Low": _C["low"]}
_SEASON_FONT_WHITE = {"Peak", "High"}
_DOW_FILLS = {
    "Saturday": _C["sat"], "Friday": _C["fri"], "Sunday": _C["sun"],
    "Thursday": _C["thu"],
    "Monday": _C["grey"], "Tuesday": _C["grey"], "Wednesday": _C["grey"],
}
_DOW_TIER_LABEL = {
    "Monday": "D1", "Tuesday": "D1", "Wednesday": "D1",
    "Thursday": "D2", "Sunday": "D3", "Friday": "D4", "Saturday": "D5",
}
_DOW_FONT_WHITE = {"Saturday", "Friday", "Sunday", "Thursday"}

_ROW_CELL_BG = {
    "Peak": "FFF2F2", "High": "FFF9F2", "Shoulder": "FFFDF2", "Low": "FEFEF5",
}


def write_tiered_pricing_xlsx(
    matrix_df: pd.DataFrame,
    pricing_cfg: Dict[str, Any],
    output_path: Path,
) -> None:
    """
    Write the tiered pricing matrix to a formatted Excel workbook.

    Sheets produced
    ---------------
    Assumptions
        Anchor rate (editable), Spyglass premium (editable), seasonal index
        table, DOW index table, month→season lookup.  All formulas reference
        this sheet so changing the anchor auto-updates both matrices.
    <group_name>  (one sheet per product_group, e.g. Greenhouse / Spyglass)
        12-row × 7-column rate matrix, colour-coded by season and DOW tier.

    Parameters
    ----------
    matrix_df : pd.DataFrame
        Output of :func:`build_tiered_pricing_matrix`.
    pricing_cfg : dict
        The ``pricing:`` block from the property YAML.
    output_path : Path
        Destination .xlsx file path.
    """
    try:
        from openpyxl import Workbook
        from openpyxl.styles import Font, PatternFill, Alignment, Border, Side
        from openpyxl.utils import get_column_letter
    except ImportError:
        raise ImportError("openpyxl is required for XLSX output: pip install openpyxl")

    anchor = float(pricing_cfg.get("anchor_rate", 300))
    product_groups = pricing_cfg.get("product_groups") or []
    seasonal_tiers = pricing_cfg.get("seasonal_tiers") or []
    dow_tiers = pricing_cfg.get("dow_tiers") or []

    # ── openpyxl helpers ──────────────────────────────────────────────────────
    def _fill(hex_color: str) -> PatternFill:
        return PatternFill("solid", start_color=hex_color)

    def _font(color: str = _C["blk"], bold: bool = False, size: int = 11,
              italic: bool = False) -> Font:
        return Font(name="Arial", color=color, bold=bold, size=size, italic=italic)

    _thin = Side(style="thin", color="BFBFBF")
    _med  = Side(style="medium", color="1F3864")
    _border = Border(left=_thin, right=_thin, top=_thin, bottom=_thin)
    _center = Alignment(horizontal="center", vertical="center", wrap_text=True)
    _left   = Alignment(horizontal="left",   vertical="center")

    def _hdr(cell, text, bg=_C["navy"], fg=_C["white"], bold=True, size=11, align=_center):
        cell.value = text
        cell.font = _font(fg, bold=bold, size=size)
        cell.fill = _fill(bg)
        cell.alignment = align
        cell.border = _border

    def _inp(cell, val, fmt="$#,##0"):
        cell.value = val
        cell.font = _font(_C["blue_tx"], bold=True)
        cell.fill = _fill(_C["input"])
        cell.alignment = _center
        cell.number_format = fmt
        cell.border = _border

    def _calc(cell, formula, fmt="0.00x"):
        cell.value = formula
        cell.font = _font()
        cell.fill = _fill(_C["white"])
        cell.alignment = _center
        cell.number_format = fmt
        cell.border = _border

    def _label(cell, text, bold=False, bg=None, align=_center):
        cell.value = text
        cell.font = _font(bold=bold)
        if bg:
            cell.fill = _fill(bg)
        cell.alignment = align
        cell.border = _border

    wb = Workbook()

    # ══════════════════════════════════════════════════════════════════════════
    # ASSUMPTIONS SHEET
    # ══════════════════════════════════════════════════════════════════════════
    ws_a = wb.active
    ws_a.title = "Assumptions"
    ws_a.sheet_view.showGridLines = False
    for col, width in zip("ABCDEFGHIJ", [28, 16, 18, 24, 16, 4, 24, 14, 14, 2]):
        ws_a.column_dimensions[col].width = width

    # Title
    ws_a.merge_cells("A1:F1")
    _hdr(ws_a["A1"], "WMB PRICING MATRIX — ASSUMPTIONS", size=13)
    ws_a.row_dimensions[1].height = 30

    # ── Anchor & Premium ──────────────────────────────────────────────────────
    ws_a.merge_cells("A3:F3")
    _hdr(ws_a["A3"], "ANCHOR RATE & PRODUCT PREMIUMS", bg=_C["blue"])
    for col, txt in zip("ABCD", ["Parameter", "Value", "Units", "Notes"]):
        _hdr(ws_a[f"{col}4"], txt, bg=_C["grey"], fg=_C["blk"])

    _label(ws_a["A5"], "Anchor Rate  (GH · Low · Mon–Wed)", bold=True, align=_left)
    _inp(ws_a["B5"], anchor, "$#,##0")
    _label(ws_a["C5"], "USD / night")
    _label(ws_a["D5"], "Change this cell to shift all rates", align=_left)
    ws_a["D5"].font = _font(_C["blk"], italic=True, size=10)

    # One row per product group premium
    for i, grp in enumerate(product_groups):
        r = str(6 + i)
        _label(ws_a[f"A{r}"], f"{grp.get('name', '')} Premium", bold=True, align=_left)
        _inp(ws_a[f"B{r}"], float(grp.get("premium", 1.0)), "0.00x")
        _label(ws_a[f"C{r}"], "× Greenhouse rate")
        _label(ws_a[f"D{r}"], "Applied to all Greenhouse cells", align=_left)
        ws_a[f"D{r}"].font = _font(_C["blk"], italic=True, size=10)

    prem_last_row = 5 + len(product_groups)  # last premium row (1-indexed)

    # ── Seasonal Tiers ────────────────────────────────────────────────────────
    s_start = prem_last_row + 2
    ws_a.merge_cells(f"A{s_start}:F{s_start}")
    _hdr(ws_a[f"A{s_start}"], "SEASONAL TIER INDICES", bg=_C["blue"])
    for col, txt in zip("ABCDE", ["Season", "Months", "Occ% (2025)", "Index", "Eff. × Anchor"]):
        _hdr(ws_a[f"{col}{s_start+1}"], txt, bg=_C["grey"], fg=_C["blk"])

    # Row number of the Low tier (needed for VLOOKUP divisor formula)
    low_row_excel = None
    s_data_start = s_start + 2
    for i, tier in enumerate(seasonal_tiers):
        r = s_data_start + i
        name = str(tier.get("name", ""))
        bg = _SEASON_FILLS.get(name, _C["grey"])
        fg = _C["white"] if name in _SEASON_FONT_WHITE else _C["blk"]
        ws_a[f"A{r}"].value = name
        ws_a[f"A{r}"].font = _font(fg, bold=True)
        ws_a[f"A{r}"].fill = _fill(bg)
        ws_a[f"A{r}"].alignment = _center
        ws_a[f"A{r}"].border = _border
        months_str = ", ".join(_MONTH_NAMES[m - 1][:3] for m in (tier.get("months") or []))
        _label(ws_a[f"B{r}"], months_str, align=_left)
        _label(ws_a[f"C{r}"], tier.get("occ_note", ""))   # optional
        _inp(ws_a[f"D{r}"], float(tier.get("index", 1.0)), "0.00x")
        # Eff. multiplier = index / Low_index — Low_index resolved after loop
        if name.lower() == "low":
            low_row_excel = r

    # Now fill eff. multiplier column (needs low_row_excel)
    if low_row_excel:
        for i in range(len(seasonal_tiers)):
            r = s_data_start + i
            _calc(ws_a[f"E{r}"], f"=D{r}/D{low_row_excel}", "0.00x")

    s_data_end = s_data_start + len(seasonal_tiers) - 1

    # ── DOW Tiers ─────────────────────────────────────────────────────────────
    d_start = s_data_end + 2
    ws_a.merge_cells(f"A{d_start}:F{d_start}")
    _hdr(ws_a[f"A{d_start}"], "DAY-OF-WEEK TIER INDICES", bg=_C["blue"])
    for col, txt in zip("ABCDE", ["Tier", "Days", "Occ%", "Index", "Eff. × Anchor DOW"]):
        _hdr(ws_a[f"{col}{d_start+1}"], txt, bg=_C["grey"], fg=_C["blk"])

    d1_row_excel = None
    d_data_start = d_start + 2
    for i, tier in enumerate(dow_tiers):
        r = d_data_start + i
        name = str(tier.get("name", ""))
        days_str = ", ".join(tier.get("days") or [])
        # pick fill from first day
        first_day = (tier.get("days") or ["Monday"])[0]
        bg = _DOW_FILLS.get(first_day, _C["grey"])
        fg = _C["white"] if first_day in _DOW_FONT_WHITE else _C["blk"]
        ws_a[f"A{r}"].value = name
        ws_a[f"A{r}"].font = _font(fg, bold=True)
        ws_a[f"A{r}"].fill = _fill(bg)
        ws_a[f"A{r}"].alignment = _center
        ws_a[f"A{r}"].border = _border
        _label(ws_a[f"B{r}"], days_str, align=_left)
        _label(ws_a[f"C{r}"], tier.get("occ_note", ""))
        _inp(ws_a[f"D{r}"], float(tier.get("index", 1.0)), "0.00x")
        if name.upper() == "D1":
            d1_row_excel = r

    if d1_row_excel:
        for i in range(len(dow_tiers)):
            r = d_data_start + i
            _calc(ws_a[f"E{r}"], f"=D{r}/D{d1_row_excel}", "0.00x")

    d_data_end = d_data_start + len(dow_tiers) - 1

    # ── Month → Season lookup (right-hand side) ────────────────────────────────
    ws_a.merge_cells("H3:J3")
    _hdr(ws_a["H3"], "MONTH → SEASON", bg=_C["blue"])
    for col, txt in zip("HIJ", ["Month", "Season", "Season Index"]):
        _hdr(ws_a[f"{col}4"], txt, bg=_C["grey"], fg=_C["blk"])

    month_season_map = {}
    for tier in seasonal_tiers:
        for m in tier.get("months") or []:
            month_season_map[int(m)] = str(tier.get("name", ""))

    for i, m in enumerate(range(1, 13)):
        r = 5 + i
        season = month_season_map.get(m, "")
        bg = _SEASON_FILLS.get(season, _C["grey"])
        fg = _C["white"] if season in _SEASON_FONT_WHITE else _C["blk"]
        _label(ws_a[f"H{r}"], _MONTH_NAMES[m - 1], bold=True, align=_left)
        ws_a[f"I{r}"].value = season
        ws_a[f"I{r}"].font = _font(fg, bold=True)
        ws_a[f"I{r}"].fill = _fill(bg)
        ws_a[f"I{r}"].alignment = _center
        ws_a[f"I{r}"].border = _border

    ws_a.sheet_properties.tabColor = _C["blue"]

    # ══════════════════════════════════════════════════════════════════════════
    # MATRIX SHEETS  — one per product group
    # The cell formula references the Assumptions sheet so changing the anchor
    # or any index auto-recalculates.  Row/column offsets match the layout above.
    # ══════════════════════════════════════════════════════════════════════════

    # Precompute Assumptions cell addresses we'll embed into formulas
    anchor_ref = "Assumptions!$B$5"

    # season_name → index cell address on Assumptions sheet
    season_index_cell: Dict[str, str] = {}
    for i in range(len(seasonal_tiers)):
        r = s_data_start + i
        name = str(seasonal_tiers[i].get("name", ""))
        season_index_cell[name] = f"Assumptions!$D${r}"
    low_ref = f"Assumptions!$D${low_row_excel}" if low_row_excel else None

    # day_name → index cell address on Assumptions sheet
    day_index_cell: Dict[str, str] = {}
    for i, tier in enumerate(dow_tiers):
        r = d_data_start + i
        for d in tier.get("days") or []:
            day_index_cell[d] = f"Assumptions!$D${r}"
    d1_ref = f"Assumptions!$D${d1_row_excel}" if d1_row_excel else None

    # Premium row per group name on Assumptions sheet
    group_prem_cell: Dict[str, str] = {}
    for i, grp in enumerate(product_groups):
        r = 6 + i
        group_prem_cell[str(grp.get("name", ""))] = f"Assumptions!$B${r}"

    tab_colours = [_C["fri"], _C["sat"], _C["sun"], _C["thu"], _C["blue"]]

    for gi, grp in enumerate(product_groups):
        gname = str(grp.get("name", f"Group{gi+1}"))
        prem_ref = group_prem_cell.get(gname, "1")
        ws = wb.create_sheet(gname)
        ws.sheet_view.showGridLines = False
        ws.column_dimensions["A"].width = 14
        for c in range(2, 10):
            ws.column_dimensions[get_column_letter(c)].width = 13

        # Title
        ws.merge_cells("A1:H1")
        _hdr(ws["A1"], f"{gname.upper()} — NIGHTLY RATE MATRIX (USD)", size=13)
        ws.row_dimensions[1].height = 28

        # Subtitle
        ws.merge_cells("A2:H2")
        ws["A2"].value = "All rates are formulas — change Anchor on Assumptions tab to shift everything"
        ws["A2"].font = _font(_C["blk"], italic=True, size=10)
        ws["A2"].alignment = _left
        ws.row_dimensions[2].height = 15

        # DOW tier label row (row 3) + DOW header row (row 4)
        ws.row_dimensions[3].height = 14
        ws.row_dimensions[4].height = 22
        ws.merge_cells("A3:A4")
        _hdr(ws["A3"], "Month \\ DOW")

        for j, day in enumerate(_DAY_ORDER):
            bg  = _DOW_FILLS.get(day, _C["grey"])
            fg  = _C["white"] if day in _DOW_FONT_WHITE else _C["blk"]
            tier_lbl = _DOW_TIER_LABEL.get(day, "")
            # tier row
            c3 = ws.cell(row=3, column=j + 2)
            c3.value = tier_lbl
            c3.font = _font(fg, size=9)
            c3.fill = _fill(bg)
            c3.alignment = _center
            c3.border = _border
            # header row
            c4 = ws.cell(row=4, column=j + 2)
            _hdr(c4, day[:3], bg=bg, fg=fg)

        # Data rows
        for i, m in enumerate(range(1, 13)):
            r = i + 5
            ws.row_dimensions[r].height = 20
            season = month_season_map.get(m, "")
            s_bg = _SEASON_FILLS.get(season, _C["grey"])
            s_fg = _C["white"] if season in _SEASON_FONT_WHITE else _C["blk"]
            cell_bg = _ROW_CELL_BG.get(season, _C["white"])

            # Month label
            mc = ws.cell(row=r, column=1)
            mc.value = _MONTH_NAMES[m - 1]
            mc.font = _font(s_fg, bold=True)
            mc.fill = _fill(s_bg)
            mc.alignment = _center
            mc.border = _border

            for j, day in enumerate(_DAY_ORDER):
                s_idx_ref = season_index_cell.get(season, "1")
                d_idx_ref = day_index_cell.get(day, "1")
                # rate = anchor × (season_idx/low_idx) × (dow_idx/d1_idx) × premium
                formula = (
                    f"=ROUND({anchor_ref}"
                    f"*({s_idx_ref}/{low_ref})"
                    f"*({d_idx_ref}/{d1_ref})"
                    f"*{prem_ref},0)"
                )
                rc = ws.cell(row=r, column=j + 2)
                rc.value = formula
                rc.font = _font()
                rc.number_format = "$#,##0"
                rc.alignment = _center
                rc.border = _border
                rc.fill = _fill(cell_bg)

        # Season legend
        leg = 18
        ws.merge_cells(f"A{leg}:H{leg}")
        _hdr(ws[f"A{leg}"], "SEASON LEGEND", bg=_C["blue"])
        legend_items = [
            ("Peak – May, Jun",         _C["peak"],  _C["white"]),
            ("High – Mar, Apr, Oct, Dec", _C["high"], _C["white"]),
            ("Shoulder – Feb, Aug, Sep, Nov", _C["shldr"], _C["blk"]),
            ("Low – Jan, Jul",          _C["low"],   _C["blk"]),
        ]
        col_pairs = [("A", "B"), ("C", "D"), ("E", "F"), ("G", "H")]
        for (c1, c2), (txt, bg, fg) in zip(col_pairs, legend_items):
            ws.merge_cells(f"{c1}{leg+1}:{c2}{leg+1}")
            cell = ws[f"{c1}{leg+1}"]
            cell.value = txt
            cell.font = _font(fg, bold=True)
            cell.fill = _fill(bg)
            cell.alignment = _center
            cell.border = _border
        ws.row_dimensions[leg].height = 16
        ws.row_dimensions[leg + 1].height = 20

        ws.freeze_panes = "B5"
        ws.sheet_properties.tabColor = tab_colours[gi % len(tab_colours)]

    output_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(str(output_path))

