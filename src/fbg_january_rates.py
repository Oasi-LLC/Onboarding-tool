"""
Build FBG January pricing from stay-night ADR with optional manager anchors.

Manager inputs (January only):
  - haus: Mon–Wed = 209, Fri–Sat = 359
  - elite: Fri–Sat = 729

Thu/Sun and all other groups are derived from January stay-night medians
(listing → tier → portfolio shrinkage). Listing rows are then median-combined
within each pricing group.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from src.analysis import DAY_ORDER
from src.pricing_matrix import _apply_weekday_hierarchy
from src.pricing_verify import DOW_COLS, expand_reservations_to_stay_nights

MTW = ["Monday", "Tuesday", "Wednesday"]
WEEKEND_TOP = ["Friday", "Saturday"]

# Manager-fixed cells for January (group_id → day → rate).
MANAGER_JANUARY_ANCHORS: Dict[str, Dict[str, int]] = {
    "haus": {
        "Monday": 209,
        "Tuesday": 209,
        "Wednesday": 209,
        "Friday": 359,
        "Saturday": 359,
    },
    "elite": {
        "Friday": 729,
        "Saturday": 729,
    },
}

MIN_LISTING_JAN_NIGHTS = 8
MIN_TIER_JAN_NIGHTS = 15

# Data-informed January grid with DOW + listing tier hierarchy pre-resolved.
# Bands: Mon=Tue=Wed < Thu=Sun < Fri=Sat; haus < king_room < … < elite < buyout.
RECOMMENDED_JANUARY_BASE_BY_GROUP: Dict[str, Dict[str, int]] = {
    "haus": {
        "Monday": 209,
        "Tuesday": 209,
        "Wednesday": 209,
        "Thursday": 235,
        "Sunday": 235,
        "Friday": 359,
        "Saturday": 359,
    },
    "king_room": {
        "Monday": 229,
        "Tuesday": 229,
        "Wednesday": 229,
        "Thursday": 275,
        "Sunday": 275,
        "Friday": 379,
        "Saturday": 379,
    },
    "entry": {
        "Monday": 275,
        "Tuesday": 275,
        "Wednesday": 275,
        "Thursday": 310,
        "Sunday": 310,
        "Friday": 409,
        "Saturday": 409,
    },
    "soft": {
        "Monday": 290,
        "Tuesday": 290,
        "Wednesday": 290,
        "Thursday": 340,
        "Sunday": 340,
        "Friday": 430,
        "Saturday": 430,
    },
    "core": {
        "Monday": 329,
        "Tuesday": 329,
        "Wednesday": 329,
        "Thursday": 395,
        "Sunday": 395,
        "Friday": 499,
        "Saturday": 499,
    },
    "signature": {
        "Monday": 351,
        "Tuesday": 351,
        "Wednesday": 351,
        "Thursday": 439,
        "Sunday": 439,
        "Friday": 561,
        "Saturday": 561,
    },
    "hard": {
        "Monday": 399,
        "Tuesday": 399,
        "Wednesday": 399,
        "Thursday": 481,
        "Sunday": 481,
        "Friday": 599,
        "Saturday": 599,
    },
    "elite": {
        "Monday": 440,
        "Tuesday": 440,
        "Wednesday": 440,
        "Thursday": 529,
        "Sunday": 529,
        "Friday": 729,
        "Saturday": 729,
    },
    "buyout": {
        "Monday": 2731,
        "Tuesday": 2731,
        "Wednesday": 2731,
        "Thursday": 3172,
        "Sunday": 3172,
        "Friday": 3909,
        "Saturday": 3909,
    },
}


def _tier_maps(listing_price_hierarchy: dict[str, Any]) -> Tuple[Dict[str, str], Dict[str, List[str]]]:
    """unit_id → group_id, group_id → unit_ids."""
    unit_to_group: Dict[str, str] = {}
    group_units: Dict[str, List[str]] = {}
    for tier in listing_price_hierarchy.get("tiers") or []:
        if not isinstance(tier, dict):
            continue
        gid = str(tier.get("id", ""))
        uids = [str(u) for u in (tier.get("unit_ids") or []) if str(u).strip()]
        if not gid or not uids:
            continue
        group_units[gid] = uids
        for u in uids:
            unit_to_group[u] = gid
    return unit_to_group, group_units


def _median_dow(
    jan_stay: pd.DataFrame,
    *,
    unit_id: Optional[str] = None,
    unit_ids: Optional[List[str]] = None,
) -> Dict[str, float]:
    sub = jan_stay
    if unit_id:
        sub = sub.loc[sub["unit_id"] == unit_id]
    elif unit_ids:
        sub = sub.loc[sub["unit_id"].isin(unit_ids)]
    if sub.empty:
        return {}
    med = sub.groupby("day_of_week")["nightly_adr"].median()
    return {str(k): float(v) for k, v in med.items() if pd.notna(v)}


def _mtw_base(medians: Dict[str, float]) -> Optional[float]:
    vals = [medians[d] for d in MTW if d in medians]
    if not vals:
        return None
    return float(np.median(vals))


def _dow_ratios(medians: Dict[str, float]) -> Dict[str, float]:
    base = _mtw_base(medians)
    if not base or base <= 0:
        if medians:
            base = float(np.median(list(medians.values())))
    if not base or base <= 0:
        return {}
    return {d: medians[d] / base for d in DOW_COLS if d in medians}


def _pick_ratio_source(
    jan_stay: pd.DataFrame,
    unit_id: str,
    group_id: str,
    group_units: Dict[str, List[str]],
) -> Dict[str, float]:
    """Listing → tier → portfolio median DOW ratios for January."""
    listing_med = _median_dow(jan_stay, unit_id=unit_id)
    n_listing = len(jan_stay.loc[jan_stay["unit_id"] == unit_id])
    if n_listing >= MIN_LISTING_JAN_NIGHTS:
        ratios = _dow_ratios(listing_med)
        if ratios:
            return ratios

    tier_med = _median_dow(jan_stay, unit_ids=group_units.get(group_id, []))
    n_tier = len(jan_stay.loc[jan_stay["unit_id"].isin(group_units.get(group_id, []))])
    if n_tier >= MIN_TIER_JAN_NIGHTS:
        ratios = _dow_ratios(tier_med)
        if ratios:
            return ratios

    port_med = _median_dow(jan_stay)
    return _dow_ratios(port_med)


def _round_rate(x: float) -> int:
    return int(round(x))


def _build_haus_january(ratios: Dict[str, float], dow_hierarchy: List[List[str]]) -> Dict[str, int]:
    fixed = MANAGER_JANUARY_ANCHORS["haus"]
    base = float(fixed["Monday"])
    top = float(fixed["Friday"])
    thu_r = ratios.get("Thursday", 1.12)
    sun_r = ratios.get("Sunday", 1.15)
    thu = _round_rate(base * thu_r)
    sun = _round_rate(base * sun_r)
    rates = {**fixed, "Thursday": thu, "Sunday": sun}
    # Clamp derived days into ladder band before hierarchy pass.
    rates["Thursday"] = max(_round_rate(base) + 1, min(rates["Thursday"], _round_rate(top) - 2))
    rates["Sunday"] = max(rates["Thursday"] + 1, min(rates["Sunday"], _round_rate(top) - 1))
    adjusted = _apply_weekday_hierarchy({d: float(rates[d]) for d in DOW_COLS}, dow_hierarchy)
    # Restore manager-fixed cells.
    for d, v in fixed.items():
        adjusted[d] = float(v)
    return {d: _round_rate(adjusted[d]) for d in DOW_COLS}


def _build_elite_january(ratios: Dict[str, float], dow_hierarchy: List[List[str]]) -> Dict[str, int]:
    fixed = MANAGER_JANUARY_ANCHORS["elite"]
    fri_sat = float(fixed["Friday"])
    fri_r = ratios.get("Friday", 1.55)
    if fri_r <= 0:
        fri_r = 1.55
    base = fri_sat / fri_r
    rates: Dict[str, float] = {}
    for d in MTW:
        rates[d] = base
    rates["Thursday"] = base * ratios.get("Thursday", 1.08)
    rates["Sunday"] = base * ratios.get("Sunday", 1.18)
    rates["Friday"] = fri_sat
    rates["Saturday"] = fri_sat
    adjusted = _apply_weekday_hierarchy(rates, dow_hierarchy)
    for d in WEEKEND_TOP:
        adjusted[d] = fri_sat
    return {d: _round_rate(adjusted[d]) for d in DOW_COLS}


def _resolve_listing_base(
    listing_med: Dict[str, float],
    tier_med: Dict[str, float],
    port_med: Dict[str, float],
) -> Optional[float]:
    """Weekday anchor: listing M/T/W → tier M/T/W → listing all-DOW median → tier → portfolio."""
    for med in (listing_med, tier_med, port_med):
        base = _mtw_base(med)
        if base and base > 0:
            return base
    for med in (listing_med, tier_med, port_med):
        if med:
            return float(np.median(list(med.values())))
    return None


def _build_from_history(
    ratios: Dict[str, float],
    base: float,
    dow_hierarchy: List[List[str]],
) -> Dict[str, int]:
    rates = {d: base * ratios.get(d, 1.0) for d in DOW_COLS}
    adjusted = _apply_weekday_hierarchy(rates, dow_hierarchy)
    return {d: _round_rate(adjusted[d]) for d in DOW_COLS}


def audit_listing_dow_hierarchy(
    listing_df: pd.DataFrame,
    dow_hierarchy: List[List[str]],
) -> pd.DataFrame:
    """One row per listing: pass/fail vs configured DOW ladder."""
    tier_rank: Dict[str, int] = {}
    for i, tier in enumerate(dow_hierarchy):
        for d in tier:
            tier_rank[str(d)] = i

    rows = []
    for r in listing_df.itertuples(index=False):
        prices = {d: float(getattr(r, d)) for d in DOW_COLS if hasattr(r, d)}
        ordered = sorted(set(tier_rank.values()))
        prev_max = -np.inf
        violations: List[str] = []
        for tr in ordered:
            days = [d for d, t in tier_rank.items() if t == tr and d in prices]
            if not days:
                continue
            tier_min = min(prices[d] for d in days)
            tier_max = max(prices[d] for d in days)
            if tier_min < prev_max - 0.01:
                violations.append(f"tier {tr} min {tier_min} < prior max {prev_max}")
            within = max(prices[d] for d in days) - min(prices[d] for d in days)
            if within > 0.01:
                violations.append(f"tier {tr} not flat ({days})")
            prev_max = max(prev_max, tier_max)
        rows.append(
            {
                "unit_id": r.unit_id,
                "group_id": getattr(r, "group_id", None),
                "hierarchy_ok": len(violations) == 0,
                "violations": "; ".join(violations) if violations else "",
            }
        )
    return pd.DataFrame(rows)


def build_january_listing_rates(
    canonical: pd.DataFrame,
    *,
    pricing_cfg: dict[str, Any],
    analysis_window: Optional[dict[str, Any]] = None,
    use_manager_anchors: bool = False,
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Returns (listing_df, diagnostics_df).
    listing_df columns: group_id, unit_id, month_index, Monday..Sunday, source
    """
    stay = expand_reservations_to_stay_nights(canonical)
    if analysis_window:
        if analysis_window.get("start_date"):
            stay = stay.loc[stay["stay_date"] >= pd.Timestamp(analysis_window["start_date"])]
        if analysis_window.get("end_date"):
            stay = stay.loc[stay["stay_date"] <= pd.Timestamp(analysis_window["end_date"])]
    jan = stay.loc[stay["month_index"] == 1].copy()

    listing_h = pricing_cfg.get("listing_price_hierarchy") or {}
    dow_h = pricing_cfg.get("dow_hierarchy") or []
    unit_to_group, group_units = _tier_maps(listing_h)

    rows: List[dict[str, Any]] = []
    diag: List[dict[str, Any]] = []

    tier_order = {
        str(t.get("id")): int(t.get("order", 99))
        for t in (listing_h.get("tiers") or [])
        if isinstance(t, dict) and t.get("id")
    }
    for gid in sorted(group_units.keys(), key=lambda g: tier_order.get(g, 99)):
        for uid in group_units[gid]:
            ratios = _pick_ratio_source(jan, uid, gid, group_units)
            n_listing = int((jan["unit_id"] == uid).sum())
            ratio_src = "listing"
            if n_listing < MIN_LISTING_JAN_NIGHTS:
                n_tier = int(jan["unit_id"].isin(group_units.get(gid, [])).sum())
                ratio_src = "tier" if n_tier >= MIN_TIER_JAN_NIGHTS else "portfolio"

            listing_med = _median_dow(jan, unit_id=uid)
            tier_med = _median_dow(jan, unit_ids=group_units.get(gid, []))
            port_med = _median_dow(jan)

            base = _resolve_listing_base(listing_med, tier_med, port_med)
            if not base or base <= 0:
                base = 250.0

            if use_manager_anchors and gid == "haus":
                rates = _build_haus_january(ratios, dow_h)
                source = "manager_haus+history"
            elif use_manager_anchors and gid == "elite":
                rates = _build_elite_january(ratios, dow_h)
                source = "manager_elite+history"
            else:
                rates = _build_from_history(ratios, base, dow_h)
                source = f"history_{ratio_src}"

            row = {"group_id": gid, "unit_id": uid, "month_index": 1, "source": source}
            row.update(rates)
            rows.append(row)
            med = _median_dow(jan, unit_id=uid)
            diag.append(
                {
                    "group_id": gid,
                    "unit_id": uid,
                    "jan_stay_nights": n_listing,
                    "ratio_source": ratio_src,
                    "hist_mtw_median": _mtw_base(med),
                    **{f"hist_{d[:3].lower()}": med.get(d) for d in DOW_COLS},
                    **{f"rate_{d[:3].lower()}": rates[d] for d in DOW_COLS},
                }
            )

    listing_df = pd.DataFrame(rows)
    cols = ["group_id", "unit_id", "month_index", "source"] + DOW_COLS
    listing_df = listing_df[[c for c in cols if c in listing_df.columns]]
    return listing_df, pd.DataFrame(diag)


def _tier_order_by_group_id(listing_price_hierarchy: dict[str, Any]) -> Dict[str, int]:
    return {
        str(t.get("id")): int(t.get("order", 99))
        for t in (listing_price_hierarchy.get("tiers") or [])
        if isinstance(t, dict) and t.get("id")
    }


def apply_dow_hierarchy_to_group_df(
    group_df: pd.DataFrame,
    dow_hierarchy: List[List[str]],
) -> pd.DataFrame:
    """Enforce within-group DOW ladder on each group row."""
    out = group_df.copy()
    for idx in out.index:
        rates = {d: float(out.at[idx, d]) for d in DOW_COLS}
        adjusted = _apply_weekday_hierarchy(rates, dow_hierarchy)
        for d in DOW_COLS:
            out.at[idx, d] = _round_rate(adjusted[d])
    return out


def enforce_listing_tier_hierarchy_on_groups(
    group_df: pd.DataFrame,
    listing_price_hierarchy: dict[str, Any],
) -> pd.DataFrame:
    """
    Per DOW column, cascade-raise group rates so tier order 1..N is non-decreasing
    (haus < king_room < … < buyout). Never lowers rates.
    """
    tier_order = _tier_order_by_group_id(listing_price_hierarchy)
    out = group_df.copy()
    ordered_gids = sorted(
        [str(g) for g in out["group_id"].unique() if str(g) in tier_order],
        key=lambda g: tier_order[g],
    )
    for d in DOW_COLS:
        prev_max = -np.inf
        for gid in ordered_gids:
            mask = out["group_id"].astype(str) == gid
            if not mask.any():
                continue
            idx = out.index[mask][0]
            val = float(out.at[idx, d])
            if val < prev_max:
                val = prev_max
            out.at[idx, d] = _round_rate(val)
            prev_max = float(out.at[idx, d])
    return out


def finalize_group_january_rates(
    group_df: pd.DataFrame,
    listing_price_hierarchy: dict[str, Any],
    dow_hierarchy: List[List[str]],
) -> pd.DataFrame:
    """DOW ladder → listing-tier ladder → DOW ladder again (tier step can widen weekend spread)."""
    g = apply_dow_hierarchy_to_group_df(group_df, dow_hierarchy)
    g = enforce_listing_tier_hierarchy_on_groups(g, listing_price_hierarchy)
    g = apply_dow_hierarchy_to_group_df(g, dow_hierarchy)
    return g


def build_recommended_january_base_group_df(
    listing_price_hierarchy: dict[str, Any],
) -> pd.DataFrame:
    """Recommended data-informed January rates before hierarchy enforcement."""
    _, group_units = _tier_maps(listing_price_hierarchy)
    tier_order = _tier_order_by_group_id(listing_price_hierarchy)
    rows: List[dict[str, Any]] = []
    for gid in sorted(group_units.keys(), key=lambda g: tier_order.get(g, 99)):
        base = RECOMMENDED_JANUARY_BASE_BY_GROUP.get(gid)
        if not base:
            continue
        row: dict[str, Any] = {
            "group_id": gid,
            "month_index": 1,
            "n_listings": len(group_units[gid]),
        }
        for d in DOW_COLS:
            row[d] = int(base[d])
        rows.append(row)
    return pd.DataFrame(rows)[["group_id", "month_index", "n_listings"] + DOW_COLS]


def apply_january_hierarchies(
    group_df: pd.DataFrame,
    listing_price_hierarchy: dict[str, Any],
    dow_hierarchy: List[List[str]],
) -> pd.DataFrame:
    """
    Enforce January pricing rules in order:
      1) DOW: Mon=Tue=Wed < Thu=Sun < Fri=Sat
      2) Listing tier: haus < king_room < … < elite (per day, cascade-raise only)
    """
    g = apply_dow_hierarchy_to_group_df(group_df, dow_hierarchy)
    g = enforce_listing_tier_hierarchy_on_groups(g, listing_price_hierarchy)
    return g


def build_recommended_january_rates(
    listing_price_hierarchy: dict[str, Any],
    dow_hierarchy: List[List[str]],
) -> Tuple[pd.DataFrame, pd.DataFrame]:
    """
    Returns (base_group_df, final_group_df) from RECOMMENDED_JANUARY_BASE_BY_GROUP.
    """
    base = build_recommended_january_base_group_df(listing_price_hierarchy)
    final = apply_january_hierarchies(base, listing_price_hierarchy, dow_hierarchy)
    return base, final


def audit_listing_tier_hierarchy(
    listing_df: pd.DataFrame,
    listing_price_hierarchy: dict[str, Any],
) -> pd.DataFrame:
    """Run listing-tier check for every DOW; empty result means all pass."""
    from src.pricing_verify import check_listing_hierarchy

    tier_order = _tier_order_by_group_id(listing_price_hierarchy)
    tier_names = {
        int(t.get("order")): str(t.get("id"))
        for t in (listing_price_hierarchy.get("tiers") or [])
        if isinstance(t, dict) and t.get("id") is not None
    }
    rows: List[dict[str, Any]] = []
    for d in DOW_COLS:
        long = listing_df.melt(
            id_vars=["unit_id", "month_index"],
            value_vars=DOW_COLS,
            var_name="day_of_week",
            value_name="proposed_rate",
        )
        long = long.loc[long["day_of_week"] == d]
        v = check_listing_hierarchy(long, listing_price_hierarchy, anchor_dow=d)
        if v.empty:
            continue
        for _, r in v.iterrows():
            rows.append(
                {
                    "day_of_week": d,
                    "lower_tier": tier_names.get(int(r["lower_tier_order"]), "?"),
                    "higher_tier": tier_names.get(int(r["higher_tier_order"]), "?"),
                    "lower_tier_max_rate": r["lower_tier_max_rate"],
                    "higher_tier_min_rate": r["higher_tier_min_rate"],
                    "status": "fail",
                }
            )
    return pd.DataFrame(rows)


def combine_listings_to_groups(
    listing_df: pd.DataFrame,
    dow_hierarchy: Optional[List[List[str]]] = None,
) -> pd.DataFrame:
    """Median rate per group_id × DOW across member listings; optional DOW ladder on group row."""
    rows = []
    for gid, grp in listing_df.groupby("group_id", sort=False):
        row: dict[str, Any] = {"group_id": gid, "month_index": 1}
        for d in DOW_COLS:
            row[d] = int(grp[d].median())
        if dow_hierarchy:
            adjusted = _apply_weekday_hierarchy({d: float(row[d]) for d in DOW_COLS}, dow_hierarchy)
            for d in DOW_COLS:
                row[d] = _round_rate(adjusted[d])
        row["n_listings"] = len(grp)
        rows.append(row)
    out = pd.DataFrame(rows)
    return out[["group_id", "month_index", "n_listings"] + DOW_COLS]


def apply_group_rates_to_listings(
    listing_df: pd.DataFrame,
    group_df: pd.DataFrame,
) -> pd.DataFrame:
    """Broadcast group-level DOW rates to every listing in the group."""
    skeleton = listing_df[["group_id", "unit_id", "month_index"]].drop_duplicates()
    rate_cols = ["group_id", "month_index"] + DOW_COLS
    merged = skeleton.merge(group_df[rate_cols], on=["group_id", "month_index"], how="left")
    merged["source"] = "group_uniform"
    cols = ["group_id", "unit_id", "month_index", "source"] + DOW_COLS
    return merged[cols]
