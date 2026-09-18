"""
Build FBG 12-month group pricing matrix from the January anchor grid.

Each DOW band scales vs January using historical band ratios (not spread-% reapply):
  MTW_m  = jan_mtw  × min(hist_mtw_m  / hist_mtw_jan,  cap_mtw[month_score])
  Thu/Sun_m = jan_mid × min(hist_thu_sun_m / hist_thu_sun_jan, cap_mid[month_score])
  Fri/Sat_m = jan_top × min(hist_fs_m / hist_fs_jan, cap_top[month_score])

Caps increase with month_score so weak months (Jun score 2) stay near January.
Months sharing a score (Feb/Oct, Jul/Aug) use the same portfolio band ratio for that score.

Then enforce DOW hierarchy and listing tier hierarchy per month.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.fbg_january_rates import (
    RECOMMENDED_JANUARY_BASE_BY_GROUP,
    _tier_maps,
    apply_january_hierarchies,
    build_recommended_january_base_group_df,
)
from src.fbg_locked_month_rates import LOCKED_MONTH_INDICES, LOCKED_MONTH_RATES
from src.pricing_verify import DOW_COLS

MTW = ["Monday", "Tuesday", "Wednesday"]
MID = ["Thursday", "Sunday"]
TOP = ["Friday", "Saturday"]

# Month strength order (weak → strong) for uplift assessment.
MONTH_SCORE_BY_INDEX: Dict[int, int] = {
    1: 1,
    6: 2,
    9: 3,
    7: 4,
    8: 4,
    5: 5,
    11: 6,
    12: 7,
    2: 8,
    10: 8,
    4: 9,
    3: 10,
}

MONTH_HIERARCHY_ORDER: List[Tuple[int, str, int]] = [
    (1, "January", 1),
    (6, "June", 2),
    (9, "September", 3),
    (7, "July", 4),
    (8, "August", 4),
    (5, "May", 5),
    (11, "November", 6),
    (12, "December", 7),
    (2, "February", 8),
    (10, "October", 8),
    (4, "April", 9),
    (3, "March", 10),
]

MIN_HIST_NIGHTS = 12

# Max band ratio vs January anchor, by month_score (weak → strong).
# Tuned so score-2 June stays ~2–6% above Jan weekends, not +40%.
SCORE_MAX_BAND_RATIO: Dict[int, Dict[str, float]] = {
    1: {"mtw": 1.00, "mid": 1.00, "top": 1.00},
    2: {"mtw": 1.02, "mid": 1.05, "top": 1.06},   # June
    3: {"mtw": 1.03, "mid": 1.06, "top": 1.08},   # September
    4: {"mtw": 1.06, "mid": 1.10, "top": 1.12},   # July / August
    5: {"mtw": 1.12, "mid": 1.15, "top": 1.22},   # May
    6: {"mtw": 1.15, "mid": 1.18, "top": 1.28},   # November
    7: {"mtw": 1.18, "mid": 1.20, "top": 1.32},   # December
    8: {"mtw": 1.22, "mid": 1.25, "top": 1.40},   # February / October
    9: {"mtw": 1.25, "mid": 1.28, "top": 1.50},   # April
    10: {"mtw": 1.30, "mid": 1.32, "top": 1.60},  # March (peak)
}

# Minimum uplift vs max(January, prior score-tier month) on every DOW band.
MIN_BAND_UPLIFT_PCT_BY_SCORE: Dict[int, float] = {
    2: 0.02,
    3: 0.03,
    4: 0.04,
    5: 0.06,
    6: 0.08,
    7: 0.09,
    8: 0.10,
    9: 0.12,
    10: 0.14,
}

# Manual per-group month overrides (applied before DOW/tier hierarchy for that month).
SEASONAL_RATE_OVERRIDES: Dict[Tuple[str, int], Dict[str, int]] = {}

# Months priced from Jan × capped historical band ratios only (no prior-score % chain).
# Peak / shoulder months: score tier sets caps vs Jan; need not exceed prior locked months.
HIST_ANCHORED_FROM_JAN_MONTH_INDICES: frozenset[int] = frozenset({2, 3, 4, 10, 11, 12})

# Hist-anchor from Jan, then raise only where a prior locked month would be exceeded.
# Value = calendar month_index of the prior month to stay above (e.g. Sep bumps above Jun).
HYBRID_HIST_ANCHORED_PRIOR_MONTH: Dict[int, int] = {9: 6}


def _round_rate(x: float) -> int:
    if x is None or (isinstance(x, float) and np.isnan(x)):
        return 0
    return int(round(x))


def _safe_spread(value: Any, fallback: float) -> float:
    if value is None or (isinstance(value, float) and np.isnan(value)):
        return fallback
    return float(value)


def _bands_from_row(row: Dict[str, int]) -> Dict[str, float]:
    mtw = float(np.mean([row[d] for d in MTW]))
    mid = float(np.mean([row[d] for d in MID]))
    top = float(np.mean([row[d] for d in TOP]))
    return {"mtw": mtw, "mid": mid, "top": top}


def _row_from_bands(mtw: float, mid: float, top: float) -> Dict[str, int]:
    m, mi, t = _round_rate(mtw), _round_rate(mid), _round_rate(top)
    return {
        "Monday": m,
        "Tuesday": m,
        "Wednesday": m,
        "Thursday": mi,
        "Sunday": mi,
        "Friday": t,
        "Saturday": t,
    }


def load_historical_dow_spreads(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df["month_index"] = df["month_index"].astype(int)
    for col in ("mtw_median", "thu_sun_median", "fri_sat_median", "thu_sun_uplift_pct", "fri_sat_uplift_pct", "n_nights"):
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def _portfolio_score_tier_stats(
    hist: pd.DataFrame,
    month_index: int,
    month_score: int,
    groups: List[str],
) -> Dict[str, float]:
    """Night-weighted portfolio ratios vs January for a month_score tier (Jul+Aug, Feb+Oct)."""
    score_months = [m for m, _, s in MONTH_HIERARCHY_ORDER if s == month_score and m != 1]
    if month_index not in score_months:
        score_months = [month_index]

    mtw_r, fs_r, sp_ts, sp_fs, w = 0.0, 0.0, 0.0, 0.0, 0.0
    for g in groups:
        jan = hist.loc[(hist["group_id"] == g) & (hist["month_index"] == 1)]
        if jan.empty:
            continue
        j = jan.iloc[0]
        for m in score_months:
            row = hist.loc[(hist["group_id"] == g) & (hist["month_index"] == m)]
            if row.empty:
                continue
            h = row.iloc[0]
            n = float(min(j.get("n_nights") or 0, h.get("n_nights") or 0))
            if n < 1 or not j.get("mtw_median") or not h.get("mtw_median"):
                continue
            mtw_r += n * float(h["mtw_median"]) / float(j["mtw_median"])
            if j.get("fri_sat_median") and h.get("fri_sat_median"):
                fs_r += n * float(h["fri_sat_median"]) / float(j["fri_sat_median"])
            sp_ts += n * float(h.get("thu_sun_uplift_pct") or 0)
            sp_fs += n * float(h.get("fri_sat_uplift_pct") or 0)
            w += n
    if w <= 0:
        return {"mtw_ratio": 1.0, "fri_sat_ratio": 1.0, "spread_ts": 20.0, "spread_fs": 50.0}
    return {
        "mtw_ratio": mtw_r / w,
        "fri_sat_ratio": fs_r / w if fs_r else mtw_r / w,
        "spread_ts": sp_ts / w,
        "spread_fs": sp_fs / w,
    }


def _hist_row(hist: pd.DataFrame, group_id: str, month_index: int) -> Optional[pd.Series]:
    sub = hist.loc[(hist["group_id"] == group_id) & (hist["month_index"] == month_index)]
    if sub.empty:
        return None
    return sub.iloc[0]


def _portfolio_jan_bands(jan_bands: Dict[str, Dict[str, float]]) -> Dict[str, float]:
    vals = list(jan_bands.values())
    return {
        "mtw": float(np.mean([v["mtw"] for v in vals])),
        "mid": float(np.mean([v["mid"] for v in vals])),
        "top": float(np.mean([v["top"] for v in vals])),
    }


def _uplift_summary_from_matrix(
    matrix_df: pd.DataFrame,
    jan_bands: Dict[str, Dict[str, float]],
    groups: List[str],
) -> pd.DataFrame:
    """Portfolio-average uplift vs January from a group × month matrix (post score-tier pass)."""
    jb_ref = _portfolio_jan_bands({g: jan_bands[g] for g in groups if g in jan_bands})
    jan_ts = (jb_ref["mid"] / jb_ref["mtw"] - 1) * 100 if jb_ref["mtw"] else 0
    jan_fs = (jb_ref["top"] / jb_ref["mtw"] - 1) * 100 if jb_ref["mtw"] else 0

    rows: List[dict[str, Any]] = []
    seen_scores: set[int] = set()
    for month_index, month_name, month_score in MONTH_HIERARCHY_ORDER:
        if month_score in seen_scores and month_index != 1:
            continue
        seen_scores.add(month_score)
        sub = matrix_df.loc[
            (matrix_df["month_index"] == month_index) & (matrix_df["group_id"].isin(groups))
        ]
        if month_index == 1:
            sub = matrix_df.loc[matrix_df["month_index"] == 1]
        mtw_vals, mid_vals, top_vals = [], [], []
        for g in groups:
            gs = sub.loc[sub["group_id"] == g]
            if gs.empty:
                continue
            r = gs.iloc[0]
            mtw_vals.append(float(np.mean([r[d] for d in MTW])))
            mid_vals.append(float(np.mean([r[d] for d in MID])))
            top_vals.append(float(np.mean([r[d] for d in TOP])))
        row_mtw = float(np.mean(mtw_vals)) if mtw_vals else jb_ref["mtw"]
        row_mid = float(np.mean(mid_vals)) if mid_vals else jb_ref["mid"]
        row_top = float(np.mean(top_vals)) if top_vals else jb_ref["top"]
        ts_spread = (row_mid / row_mtw - 1) * 100 if row_mtw else 0
        fs_spread = (row_top / row_mtw - 1) * 100 if row_mtw else 0
        rows.append(
            {
                "month_index": month_index,
                "month_name": month_name,
                "month_score": month_score,
                "also_applies_to": _months_with_same_score(month_score, month_index),
                "mtw_uplift_ratio": round(row_mtw / jb_ref["mtw"], 4) if jb_ref["mtw"] else 1.0,
                "thu_sun_uplift_ratio": round(row_mid / jb_ref["mid"], 4) if jb_ref["mid"] else 1.0,
                "fri_sat_uplift_ratio": round(row_top / jb_ref["top"], 4) if jb_ref["top"] else 1.0,
                "thu_sun_spread_pct": round(ts_spread, 1),
                "fri_sat_spread_pct": round(fs_spread, 1),
                "spread_ts_delta_vs_jan_pp": round(ts_spread - jan_ts, 1),
                "spread_fs_delta_vs_jan_pp": round(fs_spread - jan_fs, 1),
                "portfolio_mtw": _round_rate(row_mtw),
                "portfolio_thu_sun": _round_rate(row_mid),
                "portfolio_fri_sat": _round_rate(row_top),
            }
        )
    return pd.DataFrame(rows)


def _months_with_same_score(score: int, representative_month: int) -> str:
    peers = [name for m, name, s in MONTH_HIERARCHY_ORDER if s == score and m != representative_month]
    return ", ".join(peers) if peers else ""


def compute_month_uplift_table(
    hist: pd.DataFrame,
    jan_bands: Dict[str, Dict[str, float]],
    groups: List[str],
) -> pd.DataFrame:
    """One row per month in hierarchy order: level + spread uplift vs January."""
    jb_ref = _portfolio_jan_bands(jan_bands)
    rows: List[dict[str, Any]] = []
    for month_index, month_name, month_score in MONTH_HIERARCHY_ORDER:
        w = 0.0
        mtw_u, mid_u, top_u = 0.0, 0.0, 0.0
        for g in groups:
            jb = jan_bands[g]
            if month_index == 1:
                mtw_u += jb["mtw"]
                mid_u += jb["mid"]
                top_u += jb["top"]
                w += 1.0
                continue
            h = _hist_row(hist, g, month_index)
            hj = _hist_row(hist, g, 1)
            tier = _portfolio_score_tier_stats(hist, month_index, month_score, groups)
            if (
                h is None
                or hj is None
                or float(hj.get("n_nights") or 0) < MIN_HIST_NIGHTS
                or float(h.get("n_nights") or 0) < MIN_HIST_NIGHTS
                or not hj.get("mtw_median")
                or not h.get("mtw_median")
            ):
                mtw_m = jb["mtw"] * tier["mtw_ratio"]
                spread_ts = tier["spread_ts"]
                spread_fs = tier["spread_fs"]
                n = 1.0
            else:
                n = float(min(hj.get("n_nights") or 0, h.get("n_nights") or 0))
                mtw_m = jb["mtw"] * float(h["mtw_median"]) / float(hj["mtw_median"])
                spread_ts = _safe_spread(h.get("thu_sun_uplift_pct"), tier["spread_ts"])
                spread_fs = _safe_spread(h.get("fri_sat_uplift_pct"), tier["spread_fs"])
            mid_m = mtw_m * (1 + spread_ts / 100)
            top_m = mtw_m * (1 + spread_fs / 100)
            if not np.isfinite(mid_m):
                mid_m = mtw_m * 1.15
            if not np.isfinite(top_m):
                top_m = mtw_m * 1.5
            mtw_u += n * mtw_m
            mid_u += n * mid_m
            top_u += n * top_m
            w += n

        if month_index == 1:
            row_mtw = mtw_u / w if w else jb_ref["mtw"]
            row_mid = mid_u / w if w else jb_ref["mid"]
            row_top = top_u / w if w else jb_ref["top"]
            mtw_ratio = mid_ratio = top_ratio = 1.0
            d_ts = d_fs = 0.0
            ts_spread = (row_mid / row_mtw - 1) * 100 if row_mtw else 0
            fs_spread = (row_top / row_mtw - 1) * 100 if row_mtw else 0
        else:
            row_mtw = mtw_u / w if w else jb_ref["mtw"]
            row_mid = mid_u / w if w else jb_ref["mid"]
            row_top = top_u / w if w else jb_ref["top"]
            mtw_ratio = row_mtw / jb_ref["mtw"] if jb_ref["mtw"] else 1.0
            mid_ratio = row_mid / jb_ref["mid"] if jb_ref["mid"] and row_mid else 1.0
            top_ratio = row_top / jb_ref["top"] if jb_ref["top"] and row_top else 1.0
            ts_spread = (row_mid / row_mtw - 1) * 100 if row_mtw else 0
            fs_spread = (row_top / row_mtw - 1) * 100 if row_mtw else 0
            jan_ts = (jb_ref["mid"] / jb_ref["mtw"] - 1) * 100 if jb_ref["mtw"] else 0
            jan_fs = (jb_ref["top"] / jb_ref["mtw"] - 1) * 100 if jb_ref["mtw"] else 0
            d_ts = ts_spread - jan_ts
            d_fs = fs_spread - jan_fs

        rows.append(
            {
                "month_index": month_index,
                "month_name": month_name,
                "month_score": month_score,
                "mtw_uplift_ratio": round(mtw_ratio, 4),
                "thu_sun_uplift_ratio": round(mid_ratio, 4),
                "fri_sat_uplift_ratio": round(top_ratio, 4),
                "thu_sun_spread_pct": round(ts_spread, 1),
                "fri_sat_spread_pct": round(fs_spread, 1),
                "spread_ts_delta_vs_jan_pp": round(d_ts, 1),
                "spread_fs_delta_vs_jan_pp": round(d_fs, 1),
                "example_mtw": _round_rate(row_mtw),
                "example_thu_sun": _round_rate(row_mid),
                "example_fri_sat": _round_rate(row_top),
            }
        )
    return pd.DataFrame(rows)


def _capped_ratio(hist_num: float, hist_den: float, cap: float, *, floor: Optional[float] = None) -> float:
    if not hist_den or not hist_num or not np.isfinite(hist_num) or not np.isfinite(hist_den):
        return 1.0
    r = float(hist_num) / float(hist_den)
    if floor is not None:
        r = max(r, floor)
    return min(r, cap)


def _group_band_ratios_for_score(
    hist: pd.DataFrame,
    group_id: str,
    month_score: int,
    groups: List[str],
) -> Dict[str, float]:
    """
    Night-weighted historical band ratio vs January for this group, averaged across
    calendar months that share month_score, then capped.
    """
    caps = SCORE_MAX_BAND_RATIO.get(month_score, SCORE_MAX_BAND_RATIO[10])
    months = [m for m, s in MONTH_SCORE_BY_INDEX.items() if s == month_score and m != 1]
    if not months:
        return {"mtw": 1.0, "mid": 1.0, "top": 1.0}

    hj = _hist_row(hist, group_id, 1)
    if hj is None:
        tier = _portfolio_score_tier_stats(hist, months[0], month_score, groups)
        return {
            "mtw": min(tier["mtw_ratio"], caps["mtw"]),
            "mid": min(tier["mtw_ratio"], caps["mid"]),
            "top": min(tier["fri_sat_ratio"], caps["top"]),
        }

    w = 0.0
    mtw_r = mid_r = top_r = 0.0
    for m in months:
        h = _hist_row(hist, group_id, m)
        if h is None:
            continue
        n = float(min(hj.get("n_nights") or 0, h.get("n_nights") or 0))
        if n < 1:
            continue
        mtw_r += n * _capped_ratio(h.get("mtw_median"), hj.get("mtw_median"), caps["mtw"])
        mid_r += n * _capped_ratio(
            h.get("thu_sun_median"), hj.get("thu_sun_median"), caps["mid"], floor=1.0
        )
        top_r += n * _capped_ratio(
            h.get("fri_sat_median"), hj.get("fri_sat_median"), caps["top"], floor=1.0
        )
        w += n

    if w <= 0:
        tier = _portfolio_score_tier_stats(hist, months[0], month_score, groups)
        return {
            "mtw": min(tier["mtw_ratio"], caps["mtw"]),
            "mid": min(max(tier["mtw_ratio"], 1.0), caps["mid"]),
            "top": min(max(tier["fri_sat_ratio"], 1.0), caps["top"]),
        }
    return {"mtw": mtw_r / w, "mid": mid_r / w, "top": top_r / w}


def _project_group_month(
    group_id: str,
    month_index: int,
    month_score: int,
    jan_row: Dict[str, int],
    hist: pd.DataFrame,
    groups: List[str],
) -> Dict[str, int]:
    jb = _bands_from_row(jan_row)
    if month_index == 1:
        return dict(jan_row)

    ratios = _group_band_ratios_for_score(hist, group_id, month_score, groups)
    mtw_m = jb["mtw"] * ratios["mtw"]
    mid_m = jb["mid"] * ratios["mid"]
    top_m = jb["top"] * ratios["top"]
    mid_m = max(mid_m, mtw_m + 1)
    top_m = max(top_m, mid_m + 1)
    return _row_from_bands(mtw_m, mid_m, top_m)


def enforce_month_score_monotonicity(
    raw_df: pd.DataFrame,
    month_scores: Dict[int, int],
    *,
    anchor_month: int = 1,
) -> pd.DataFrame:
    """
    Per group × DOW: rates non-decrease as month_score increases (1→10).
    Only raises weaker months; never lowers a month below its capped projection
    except when a lower score month was already raised by a prior score step.
    """
    out = raw_df.copy()
    scores_sorted = sorted({s for m, s in month_scores.items() if m != anchor_month})

    for gid in out["group_id"].unique():
        gmask = out["group_id"] == gid
        for dow in DOW_COLS:
            anchor_mask = gmask & (out["month_index"] == anchor_month)
            prev = float(out.loc[anchor_mask, dow].iloc[0]) if anchor_mask.any() else 0.0
            for score in scores_sorted:
                months = [m for m, s in month_scores.items() if s == score and m != anchor_month]
                if not months:
                    continue
                vals = [float(out.loc[gmask & (out["month_index"] == m), dow].iloc[0]) for m in months]
                val = max(max(vals), prev)
                val = _round_rate(val)
                for m in months:
                    out.loc[gmask & (out["month_index"] == m), dow] = val
                prev = float(val)
    return out


def _reference_month_indices(month_score: int, month_scores: Dict[int, int]) -> List[int]:
    """January plus calendar months at the highest month_score strictly below month_score."""
    refs = [1]
    if month_score <= 1:
        return refs
    for s in range(month_score - 1, 0, -1):
        months = [m for m, sc in month_scores.items() if sc == s]
        if months:
            refs.extend(months)
            break
    return refs


def _min_rate_above_reference(reference: float, min_pct: float) -> int:
    """Strictly above reference by at least min_pct (or $1 if pct rounds flat)."""
    if reference <= 0:
        return _round_rate(reference + 1)
    bumped = _round_rate(reference * (1.0 + min_pct))
    return bumped if bumped > reference else _round_rate(reference) + 1


def enforce_bands_above_jan_and_prior_score(
    month_df: pd.DataFrame,
    reference_df: pd.DataFrame,
    month_score: int,
    month_scores: Dict[int, int],
) -> pd.DataFrame:
    """
    Every DOW cell must exceed max(January, prior score-tier month) for that group.
    """
    if month_score <= 1:
        return month_df.copy()

    min_pct = MIN_BAND_UPLIFT_PCT_BY_SCORE.get(month_score, 0.03)
    ref_months = _reference_month_indices(month_score, month_scores)
    out = month_df.copy()

    for gid in out["group_id"].unique():
        gmask = out["group_id"] == gid
        for dow in DOW_COLS:
            ref_vals = []
            for rm in ref_months:
                sub = reference_df.loc[
                    (reference_df["group_id"] == gid) & (reference_df["month_index"] == rm)
                ]
                if not sub.empty:
                    ref_vals.append(float(sub.iloc[0][dow]))
            if not ref_vals:
                continue
            floor = _min_rate_above_reference(max(ref_vals), min_pct)
            idx = out.index[gmask]
            current = float(out.loc[idx[0], dow])
            if current < floor:
                out.loc[idx[0], dow] = floor
    return out


def enforce_bands_above_january_only(
    month_df: pd.DataFrame,
    reference_df: pd.DataFrame,
    month_score: int,
) -> pd.DataFrame:
    """Floor each cell at January + score-tier min uplift (no prior-score month chain)."""
    if month_score <= 1:
        return month_df.copy()

    min_pct = MIN_BAND_UPLIFT_PCT_BY_SCORE.get(month_score, 0.03)
    out = month_df.copy()

    for gid in out["group_id"].unique():
        gmask = out["group_id"] == gid
        jan = reference_df.loc[
            (reference_df["group_id"] == gid) & (reference_df["month_index"] == 1)
        ]
        if jan.empty:
            continue
        for dow in DOW_COLS:
            floor = _min_rate_above_reference(float(jan.iloc[0][dow]), min_pct)
            idx = out.index[gmask]
            current = float(out.loc[idx[0], dow])
            if current < floor:
                out.loc[idx[0], dow] = floor
    return out


def enforce_bands_above_prior_month(
    month_df: pd.DataFrame,
    reference_df: pd.DataFrame,
    prior_month_index: int,
) -> pd.DataFrame:
    """Raise cells to strictly above a prior finalized month when projection is at or below it."""
    out = month_df.copy()
    for gid in out["group_id"].unique():
        gmask = out["group_id"] == gid
        prior = reference_df.loc[
            (reference_df["group_id"] == gid)
            & (reference_df["month_index"] == prior_month_index)
        ]
        if prior.empty:
            continue
        idx = out.index[gmask][0]
        for dow in DOW_COLS:
            prior_val = float(prior.iloc[0][dow])
            current = float(out.loc[idx, dow])
            if current <= prior_val:
                out.loc[idx, dow] = _round_rate(prior_val) + 1
    return out


def build_hist_anchored_month_slice(
    month_index: int,
    month_name: str,
    month_score: int,
    january_base: Dict[str, Dict[str, int]],
    hist: pd.DataFrame,
    pricing_groups: List[str],
    listing_price_hierarchy: dict[str, Any],
) -> pd.DataFrame:
    """Project one month from Jan anchor × capped historical band ratios for that month."""
    _, group_units = _tier_maps(listing_price_hierarchy)
    rows: List[dict[str, Any]] = []
    for gid in pricing_groups:
        base = january_base.get(gid)
        if not base:
            continue
        projected = _project_group_month(
            gid, month_index, month_score, base, hist, pricing_groups
        )
        rows.append(
            {
                "group_id": gid,
                "month_index": month_index,
                "month_name": month_name,
                "month_score": month_score,
                "n_listings": len(group_units.get(gid, [])),
                **projected,
            }
        )
    return pd.DataFrame(rows)


def build_locked_month_slice(
    month_index: int,
    month_name: str,
    month_score: int,
    listing_price_hierarchy: dict[str, Any],
) -> pd.DataFrame:
    """Return finalized rates for a walkthrough-approved month (no recalculation)."""
    _, group_units = _tier_maps(listing_price_hierarchy)
    tier_order = {
        str(t.get("id")): int(t.get("order", 99))
        for t in (listing_price_hierarchy.get("tiers") or [])
        if isinstance(t, dict) and t.get("id")
    }
    groups = sorted(group_units.keys(), key=lambda g: tier_order.get(g, 99))
    rows: List[dict[str, Any]] = []
    for gid in groups:
        key = (gid, month_index)
        cells = LOCKED_MONTH_RATES.get(key)
        if not cells:
            continue
        rows.append(
            {
                "group_id": gid,
                "month_index": month_index,
                "month_name": month_name,
                "month_score": month_score,
                "n_listings": len(group_units.get(gid, [])),
                **{d: int(cells[d]) for d in DOW_COLS if d in cells},
            }
        )
    return pd.DataFrame(rows)


def apply_seasonal_rate_overrides(month_df: pd.DataFrame, month_index: int) -> pd.DataFrame:
    out = month_df.copy()
    for (gid, midx), cells in SEASONAL_RATE_OVERRIDES.items():
        if midx != month_index:
            continue
        mask = out["group_id"].astype(str) == gid
        if not mask.any():
            continue
        for dow, rate in cells.items():
            if dow in out.columns:
                out.loc[mask, dow] = int(rate)
    return out


def sync_months_by_score_tier(raw_df: pd.DataFrame, month_scores: Dict[int, int]) -> pd.DataFrame:
    """Feb=Oct and Jul=Aug share identical rates per group (score-tier sync)."""
    out = raw_df.copy()
    for score in sorted(set(month_scores.values())):
        if score == 1:
            continue
        months = [m for m, s in month_scores.items() if s == score]
        if len(months) < 2:
            continue
        for gid in out["group_id"].unique():
            gmask = out["group_id"] == gid
            for d in DOW_COLS:
                vals = [float(out.loc[gmask & (out["month_index"] == m), d].iloc[0]) for m in months]
                synced = _round_rate(float(np.mean(vals)))
                for m in months:
                    out.loc[gmask & (out["month_index"] == m), d] = synced
    return out


def build_seasonal_matrix_from_january_anchor(
    listing_price_hierarchy: dict[str, Any],
    dow_hierarchy: List[List[str]],
    hist: pd.DataFrame,
    january_base: Optional[Dict[str, Dict[str, int]]] = None,
) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """
    Returns (raw_by_month, final_by_month, uplift_summary).
    Rows are group_id × month_index; January matches the anchor exactly before enforcement.
    """
    base_src = january_base or RECOMMENDED_JANUARY_BASE_BY_GROUP
    _, group_units = _tier_maps(listing_price_hierarchy)
    tier_order = {
        str(t.get("id")): int(t.get("order", 99))
        for t in (listing_price_hierarchy.get("tiers") or [])
        if isinstance(t, dict) and t.get("id")
    }
    groups = sorted(group_units.keys(), key=lambda g: tier_order.get(g, 99))
    pricing_groups = [g for g in groups if g in base_src]
    uplift_groups = [g for g in pricing_groups if g != "buyout"]

    jan_bands = {g: _bands_from_row(base_src[g]) for g in pricing_groups}

    month_meta = {m: (name, score) for m, name, score in MONTH_HIERARCHY_ORDER}
    raw_rows: List[dict[str, Any]] = []
    for month_index in range(1, 13):
        month_name, month_score = month_meta.get(month_index, ("", 0))
        for gid in pricing_groups:
            row = _project_group_month(
                gid, month_index, month_score, base_src[gid], hist, pricing_groups
            )
            raw_rows.append(
                {
                    "group_id": gid,
                    "month_index": month_index,
                    "month_name": month_name,
                    "month_score": month_score,
                    "n_listings": len(group_units.get(gid, [])),
                    **row,
                }
            )

    raw_all = pd.DataFrame(raw_rows)
    # Lock January to manual anchor before score-tier pass
    jan_enforced = build_recommended_january_base_group_df(listing_price_hierarchy)
    jan_enforced = apply_january_hierarchies(jan_enforced, listing_price_hierarchy, dow_hierarchy)
    for _, r in jan_enforced.iterrows():
        mask = (raw_all["group_id"] == r["group_id"]) & (raw_all["month_index"] == 1)
        for d in DOW_COLS:
            raw_all.loc[mask, d] = int(r[d])

    raw_all = sync_months_by_score_tier(raw_all, MONTH_SCORE_BY_INDEX)
    raw_all = enforce_month_score_monotonicity(raw_all, MONTH_SCORE_BY_INDEX)

    # Build final matrix in month-strength order so overrides and band floors
    # on earlier tiers (e.g. September) flow into later months (July/August).
    reference_df = jan_enforced.copy()
    final_by_month: Dict[int, pd.DataFrame] = {}
    score_primary_month: Dict[int, int] = {}

    for month_index, month_name, month_score in MONTH_HIERARCHY_ORDER:
        if month_score in score_primary_month:
            primary_idx = score_primary_month[month_score]
            month_slice = final_by_month[primary_idx].copy()
            month_slice["month_index"] = month_index
            if "month_name" in month_slice.columns:
                month_slice["month_name"] = month_name
        elif month_index in LOCKED_MONTH_INDICES:
            month_slice = build_locked_month_slice(
                month_index, month_name, month_score, listing_price_hierarchy
            )
        elif month_index in HYBRID_HIST_ANCHORED_PRIOR_MONTH:
            prior_month = HYBRID_HIST_ANCHORED_PRIOR_MONTH[month_index]
            month_slice = build_hist_anchored_month_slice(
                month_index,
                month_name,
                month_score,
                base_src,
                hist,
                pricing_groups,
                listing_price_hierarchy,
            )
            month_slice = enforce_bands_above_prior_month(
                month_slice, reference_df, prior_month
            )
            month_slice = apply_seasonal_rate_overrides(month_slice, month_index)
            month_slice = apply_january_hierarchies(
                month_slice, listing_price_hierarchy, dow_hierarchy
            )
        elif month_index in HIST_ANCHORED_FROM_JAN_MONTH_INDICES:
            month_slice = build_hist_anchored_month_slice(
                month_index,
                month_name,
                month_score,
                base_src,
                hist,
                pricing_groups,
                listing_price_hierarchy,
            )
            month_slice = enforce_bands_above_january_only(
                month_slice, reference_df, month_score
            )
            month_slice = apply_seasonal_rate_overrides(month_slice, month_index)
            month_slice = apply_january_hierarchies(
                month_slice, listing_price_hierarchy, dow_hierarchy
            )
        else:
            month_slice = raw_all.loc[raw_all["month_index"] == month_index].copy()
            month_slice = enforce_bands_above_jan_and_prior_score(
                month_slice, reference_df, month_score, MONTH_SCORE_BY_INDEX
            )
            month_slice = apply_seasonal_rate_overrides(month_slice, month_index)
            month_slice = apply_january_hierarchies(
                month_slice, listing_price_hierarchy, dow_hierarchy
            )

        score_primary_month.setdefault(month_score, month_index)
        final_by_month[month_index] = month_slice
        if month_index != 1:
            reference_df = pd.concat([reference_df, month_slice], ignore_index=True)

    final_rows: List[dict[str, Any]] = []
    for month_index in range(1, 13):
        for _, r in final_by_month[month_index].iterrows():
            final_rows.append(r.to_dict())

    raw_out = raw_all.copy()
    uplift_df = _uplift_summary_from_matrix(raw_out, jan_bands, uplift_groups)
    final_out = pd.DataFrame(final_rows)
    cols = ["group_id", "month_index", "n_listings"] + DOW_COLS
    if "month_name" in raw_out.columns:
        pass
    raw_out = raw_out[
        [c for c in ["group_id", "month_index", "month_name", "month_score", "n_listings"] + DOW_COLS if c in raw_out.columns]
    ]
    final_out = final_out[[c for c in cols if c in final_out.columns]]
    return raw_out, final_out, uplift_df
