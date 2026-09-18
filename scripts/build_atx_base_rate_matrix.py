#!/usr/bin/env python3
"""
Build ATX base rate matrix from canonical reservations.

Both listings share the same month-strength curve (pricing.month_score_by_index).
Rates are grounded in observed month×DOW medians where sample size allows.

Usage (from project root):
  python scripts/build_atx_base_rate_matrix.py
  python scripts/build_atx_base_rate_matrix.py --property atx
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from scripts.run_pricing_matrix import write_pricing_outputs
from src.pricing_matrix import _apply_weekday_hierarchy, _score_to_month_factor
from src.property_config import load_property_inventory

_DAY_COLS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
_MONTH_NAMES = [
    "January", "February", "March", "April", "May", "June",
    "July", "August", "September", "October", "November", "December",
]
_LISTINGS = ("Malvern", "Sunstrip")


def _normalize_factor_map(factors: dict[int, float]) -> dict[int, float]:
    vals = [float(factors[m]) for m in range(1, 13)]
    mean_f = sum(vals) / len(vals) if vals else 1.0
    if mean_f <= 0:
        return {m: 1.0 for m in range(1, 13)}
    return {m: float(factors[m]) / mean_f for m in range(1, 13)}


def _month_factors_from_config(month_score_by_index: dict) -> dict[int, float]:
    raw = {m: _score_to_month_factor(float(month_score_by_index.get(m, 5))) for m in range(1, 13)}
    return _normalize_factor_map(raw)


def _dow_factors_from_analysis(analysis_dir: Path) -> dict[str, float]:
    path = analysis_dir / "by_day_of_week.csv"
    df = pd.read_csv(path)
    raw: dict[str, float] = {}
    for _, row in df.iterrows():
        d = str(row["day_of_week"]).strip()
        sc = float(row.get("dow_score_1_10", 5) or 5)
        raw[d] = 0.80 + 0.04 * sc
    out = {d: raw.get(d, 1.0) for d in _DAY_COLS}
    mean_f = sum(out.values()) / len(_DAY_COLS)
    return {d: (out[d] / mean_f if mean_f > 0 else 1.0) for d in _DAY_COLS}


def _load_canonical_stays(property_id: str) -> pd.DataFrame:
    path = PROJECT_ROOT / "output" / property_id / "ingestion" / "canonical.csv"
    df = pd.read_csv(path, parse_dates=["arrival_date"])
    df = df.loc[df["unit_id"].isin(_LISTINGS)].copy()
    df["adr"] = pd.to_numeric(df["revenue"], errors="coerce") / pd.to_numeric(df["nights"], errors="coerce")
    df = df.loc[df["adr"].notna() & (df["adr"] > 0)].copy()
    df["month_index"] = df["arrival_date"].dt.month
    df["day_of_week"] = df["arrival_date"].dt.day_name()
    return df


def _cell_median_table(stays: pd.DataFrame, unit_id: str) -> pd.DataFrame:
    """Median ADR by month_index × day_of_week with fallbacks."""
    sub = stays.loc[stays["unit_id"] == unit_id]
    overall = float(sub["adr"].median()) if not sub.empty else 300.0
    cell = (
        sub.groupby(["month_index", "day_of_week"], as_index=False)
        .agg(cell_median=("adr", "median"), n=("adr", "count"))
    )
    dow_only = sub.groupby("day_of_week", as_index=False).agg(dow_median=("adr", "median"))
    month_only = sub.groupby("month_index", as_index=False).agg(month_median=("adr", "median"))

    rows: list[dict] = []
    for m in range(1, 13):
        for d in _DAY_COLS:
            med = None
            n = 0
            hit = cell[(cell["month_index"] == m) & (cell["day_of_week"] == d)]
            if not hit.empty and int(hit.iloc[0]["n"]) >= 2:
                med = float(hit.iloc[0]["cell_median"])
                n = int(hit.iloc[0]["n"])
            if med is None:
                dm = dow_only.loc[dow_only["day_of_week"] == d, "dow_median"]
                mm = month_only.loc[month_only["month_index"] == m, "month_median"]
                if not dm.empty and not mm.empty:
                    med = float(dm.iloc[0]) * (float(mm.iloc[0]) / overall)
                elif not dm.empty:
                    med = float(dm.iloc[0])
                elif not mm.empty:
                    med = float(mm.iloc[0])
                else:
                    med = overall
            rows.append({"month_index": m, "day_of_week": d, "observed_median": med, "n": n})
    return pd.DataFrame(rows)


def _build_listing_matrix(
    unit_id: str,
    label: str,
    stays: pd.DataFrame,
    anchor_adr: float,
    month_fac: dict[int, float],
    dow_fac: dict[str, float],
    dow_hierarchy: list[list[str]] | None,
    month_score: dict[int, float],
    blend_observed: float = 0.55,
) -> pd.DataFrame:
    """
    Shared month curve × per-listing anchor × DOW; blend toward observed cell medians.
    """
    cells = _cell_median_table(stays, unit_id)
    rows: list[dict] = []
    for m in range(1, 13):
        day_prices: dict[str, float] = {}
        for d in _DAY_COLS:
            obs_row = cells[(cells["month_index"] == m) & (cells["day_of_week"] == d)]
            obs = float(obs_row.iloc[0]["observed_median"]) if not obs_row.empty else anchor_adr
            formula = anchor_adr * month_fac[m] * dow_fac[d]
            blended = (1.0 - blend_observed) * formula + blend_observed * obs
            day_prices[d] = blended
        day_prices = _apply_weekday_hierarchy(day_prices, dow_hierarchy)
        row = {"unit_id": label, "month_index": m}
        for d in _DAY_COLS:
            row[d] = day_prices[d]
        rows.append(row)

    wide = pd.DataFrame(rows)

    # Lock month shape: same month_factor curve for every listing (Fri as reference DOW).
    fri_dow = dow_fac.get("Friday", 1.0)
    for m in range(1, 13):
        target_fri = anchor_adr * month_fac[m] * fri_dow
        cur_fri = float(wide.loc[wide["month_index"] == m, "Friday"].iloc[0])
        scale = (target_fri / cur_fri) if cur_fri > 0 else 1.0
        mask = wide["month_index"] == m
        for d in _DAY_COLS:
            wide.loc[mask, d] = (pd.to_numeric(wide.loc[mask, d], errors="coerce") * scale).round(0)

    wide[_DAY_COLS] = wide[_DAY_COLS].round(0)
    return wide


def _derive_malvern_5br(
    matrix: pd.DataFrame,
    malvern_cfg: dict,
) -> pd.DataFrame:
    fb = malvern_cfg.get("five_br_from_4br") or {}
    mult = float(fb.get("multiplier", 1.45))
    add = float(fb.get("additive_usd", 225))
    sun_prem = float(fb.get("sunstrip_floor_premium_usd", 25))
    floor = float(malvern_cfg.get("five_br_min_rate_usd", 600))

    m4 = matrix.loc[matrix["unit_id"] == "Malvern (4BR)"].copy()
    sun = matrix.loc[matrix["unit_id"] == "Sunstrip"].copy()
    sun_by_month = sun.set_index("month_index")
    m5_rows: list[dict] = []
    for _, r in m4.iterrows():
        m = int(r["month_index"])
        sr = sun_by_month.loc[m]
        out: dict = {"unit_id": "Malvern (5BR)", "month_index": m}
        for d in _DAY_COLS:
            v4 = float(r[d])
            vs = float(sr[d])
            out[d] = int(round(max(floor, min(v4 * mult, v4 + add), vs + sun_prem)))
        m5_rows.append(out)
    return pd.concat([matrix, pd.DataFrame(m5_rows)], ignore_index=True)


def _write_stacked(matrix: pd.DataFrame, path: Path) -> None:
    lines: list[str] = []
    for uid in ["Malvern (4BR)", "Sunstrip", "Malvern (5BR)"]:
        if uid not in matrix["unit_id"].values:
            continue
        sub = matrix.loc[matrix["unit_id"] == uid].sort_values("month_index")
        lines.append(f"LISTING: {uid}")
        lines.append("month," + ",".join(_DAY_COLS))
        for _, r in sub.iterrows():
            mi = int(r["month_index"])
            lines.append(f"{_MONTH_NAMES[mi - 1]}," + ",".join(str(int(r[d])) for d in _DAY_COLS))
        lines.append("")
    path.write_text("\n".join(lines), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser(description="Build ATX base rate matrix (shared month curve).")
    ap.add_argument("--property", default="atx")
    args = ap.parse_args()
    prop_id = args.property

    inv = load_property_inventory(prop_id)
    pricing_cfg = inv.get("pricing") or {}
    analysis_dir = PROJECT_ROOT / "output" / prop_id / "analysis"
    pricing_dir = PROJECT_ROOT / "output" / prop_id / "pricing"
    pricing_dir.mkdir(parents=True, exist_ok=True)

    overall = pd.read_csv(analysis_dir / "overall_summary.csv")
    anchors = {
        "Malvern": float(overall.loc[overall["unit_id"] == "Malvern", "adr"].iloc[0]),
        "Sunstrip": float(overall.loc[overall["unit_id"] == "Sunstrip", "adr"].iloc[0]),
    }

    month_score = {int(k): float(v) for k, v in (pricing_cfg.get("month_score_by_index") or {}).items()}
    month_fac = _month_factors_from_config(month_score)
    dow_fac = _dow_factors_from_analysis(analysis_dir)
    dow_hierarchy = pricing_cfg.get("dow_hierarchy")
    stays = _load_canonical_stays(prop_id)

    m4 = _build_listing_matrix(
        "Malvern", "Malvern (4BR)", stays, anchors["Malvern"], month_fac, dow_fac, dow_hierarchy, month_score
    )
    sun = _build_listing_matrix(
        "Sunstrip", "Sunstrip", stays, anchors["Sunstrip"], month_fac, dow_fac, dow_hierarchy, month_score
    )
    matrix = pd.concat([m4, sun], ignore_index=True)
    matrix = _derive_malvern_5br(matrix, pricing_cfg.get("malvern") or {})

    write_pricing_outputs(pricing_dir, matrix, pd.DataFrame())
    stacked_path = pricing_dir / "atx_base_rate_matrix_stacked.csv"
    _write_stacked(matrix, stacked_path)
    print(f"Wrote stacked view: {stacked_path}")

    # Sanity: Friday month ranks must match month_score order for both SKUs
    for uid in ["Malvern (4BR)", "Sunstrip"]:
        sub = matrix.loc[matrix["unit_id"] == uid].sort_values("month_index")
        fris = {int(r["month_index"]): float(r["Friday"]) for _, r in sub.iterrows()}
        order = sorted(fris.keys(), key=lambda m: (month_score.get(m, 5), m))
        prev = -1.0
        ok = True
        for m in order:
            if fris[m] < prev - 0.01:
                ok = False
            prev = fris[m]
        print(f"Month hierarchy OK for {uid}: {ok}")


if __name__ == "__main__":
    main()
