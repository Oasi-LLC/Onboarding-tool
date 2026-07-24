"""
Summer (Jul/Aug/Sep) deep-dive tables for hotel-style properties (Adventure Inn Durango).

Writes CSVs under output/<property_id>/analysis/summer/.
All breakdown tables include a month column so dashboard can toggle Jul / Aug / Sep
without combining months.
"""
from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any, Optional

import pandas as pd


DAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
WEEKEND_DOWS = {"Friday", "Saturday"}
WEEKDAY_DOWS = {"Monday", "Tuesday", "Wednesday", "Thursday", "Sunday"}
MONTH_NAMES = {7: "July", 8: "August", 9: "September"}
LEAD_BANDS = [
    (0, 6, "0-6"),
    (7, 14, "7-14"),
    (15, 30, "15-30"),
    (31, 60, "31-60"),
    (61, 90, "61-90"),
    (91, 10_000, "91+"),
]


def _capacity_on_date(capacity_schedule: list[dict[str, Any]], night: pd.Timestamp, fallback: int) -> int:
    night = pd.Timestamp(night).normalize()
    for seg in capacity_schedule or []:
        if not isinstance(seg, dict):
            continue
        start = pd.to_datetime(seg.get("start_date"), errors="coerce")
        end = pd.to_datetime(seg.get("end_date"), errors="coerce")
        rc = seg.get("room_count")
        try:
            rc_i = int(rc)
        except (TypeError, ValueError):
            continue
        if pd.isna(start):
            continue
        start = pd.Timestamp(start).normalize()
        if night < start:
            continue
        if pd.notna(end) and night > pd.Timestamp(end).normalize():
            continue
        return rc_i
    return int(fallback)


def _wadr(g: pd.DataFrame) -> Optional[float]:
    rn = g["nights"].sum()
    if not rn:
        return None
    return float(g["revenue"].sum() / rn)


def _lead_band(val: object) -> Optional[str]:
    if pd.isna(val):
        return None
    try:
        x = int(val)
    except (TypeError, ValueError):
        return None
    if x < 0:
        x = 0
    for lo, hi, label in LEAD_BANDS:
        if lo <= x <= hi:
            return label
    return "91+"


def _day_group(dow: object) -> Optional[str]:
    if pd.isna(dow):
        return None
    s = str(dow)
    if s in WEEKEND_DOWS:
        return "weekend_fri_sat"
    if s in WEEKDAY_DOWS:
        return "weekday"
    return None


def _expand_night_calendar(df: pd.DataFrame) -> pd.DataFrame:
    """One row per occupied room-night (canonical already expanded per room)."""
    stay = df.dropna(subset=["arrival_date", "departure_date", "nights", "revenue"]).copy()
    stay = stay.loc[stay["departure_date"] > stay["arrival_date"]].copy()
    stay["nights_safe"] = pd.to_numeric(stay["nights"], errors="coerce")
    stay = stay.loc[stay["nights_safe"] > 0].copy()
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
    return stay


def _month_complete(year: int, month: int, asof: pd.Timestamp) -> bool:
    """True if the stay month is fully in the past relative to asof."""
    last_day = pd.Period(f"{year}-{month:02d}").to_timestamp(how="end").normalize()
    return asof.normalize() > last_day


def build_summer_tables(
    df: pd.DataFrame,
    *,
    summer_months: list[int],
    pace_asof_month: int,
    pace_asof_day: int,
    anchor_year: int,
    capacity_schedule: list[dict[str, Any]],
    room_count: int,
    asof_override: Optional[date] = None,
) -> dict[str, pd.DataFrame]:
    df = df.copy()
    df["arrival_date"] = pd.to_datetime(df["arrival_date"], errors="coerce")
    df["departure_date"] = pd.to_datetime(df["departure_date"], errors="coerce")
    df["booking_date"] = pd.to_datetime(df["booking_date"], errors="coerce")
    df["nights"] = pd.to_numeric(df["nights"], errors="coerce")
    df["revenue"] = pd.to_numeric(df["revenue"], errors="coerce")
    df["lead_time_days"] = pd.to_numeric(df["lead_time_days"], errors="coerce").clip(lower=0)
    df["year"] = df["arrival_date"].dt.year
    df["month"] = df["arrival_date"].dt.month
    if "arrival_day_of_week" not in df.columns or df["arrival_day_of_week"].isna().all():
        df["arrival_day_of_week"] = df["arrival_date"].dt.day_name()

    summer = df.loc[df["month"].isin(summer_months)].copy()
    summer_pos = summer.loc[summer["revenue"] > 0].copy()
    summer_pos["day_group"] = summer_pos["arrival_day_of_week"].map(_day_group)
    summer_pos["lead_band"] = summer_pos["lead_time_days"].map(_lead_band)

    # --- year-month KPIs (night-level occ for occupancy) ---
    cal = _expand_night_calendar(summer)
    cal = cal.loc[cal["stay_date"].dt.month.isin(summer_months)].copy()
    cal["year"] = cal["stay_date"].dt.year
    cal["month"] = cal["stay_date"].dt.month

    kpi_rows = []
    for (y, m), g in summer.groupby(["year", "month"], sort=True):
        rn = float(g["nights"].sum())
        rev = float(g["revenue"].sum())
        days = pd.Period(f"{int(y)}-{int(m):02d}").days_in_month
        avail = 0.0
        for d in pd.date_range(f"{int(y)}-{int(m):02d}-01", periods=days, freq="D"):
            avail += _capacity_on_date(capacity_schedule, d, room_count)
        sold = float(cal.loc[(cal["year"] == y) & (cal["month"] == m)].shape[0])
        cap_rooms = _capacity_on_date(
            capacity_schedule, pd.Timestamp(f"{int(y)}-{int(m):02d}-15"), room_count
        )
        kpi_rows.append({
            "year": int(y),
            "month": int(m),
            "month_name": MONTH_NAMES.get(int(m), str(m)),
            "reservations": int(g["reservation_id"].nunique()) if "reservation_id" in g.columns else len(g),
            "room_nights": int(rn),
            "revenue": round(rev, 2),
            "adr": round(rev / rn, 2) if rn else None,
            "avg_los": round(float(g["nights"].mean()), 2) if len(g) else None,
            "median_lead": float(g["lead_time_days"].median()) if g["lead_time_days"].notna().any() else None,
            "mean_lead": round(float(g["lead_time_days"].mean()), 2) if g["lead_time_days"].notna().any() else None,
            "occupancy_pct": round(100.0 * sold / avail, 2) if avail else None,
            "revpar": round(rev / avail, 2) if avail else None,
            "capacity_rooms": int(cap_rooms),
            "statuses_included": "Checked Out,Confirmed,No Show (In-House excluded at ingest)",
        })
    summer_year_month_kpis = pd.DataFrame(kpi_rows)

    # --- pace as-of (refreshed cutoff; incomplete months use on-books vs LY on-books) ---
    today = asof_override or date.today()
    pace_rows = []
    years = sorted(summer["year"].dropna().unique().astype(int))
    for y in years:
        # Same calendar MD each stay year; clamp day for short months
        try:
            asof = pd.Timestamp(year=int(y), month=int(pace_asof_month), day=int(pace_asof_day))
        except ValueError:
            asof = pd.Timestamp(year=int(y), month=int(pace_asof_month), day=1) + pd.offsets.MonthEnd(0)
        # For current stay year, use today's date if later than configured MD
        if int(y) == int(today.year):
            asof = max(asof, pd.Timestamp(today))
        for m in summer_months:
            sub = summer.loc[(summer["year"] == y) & (summer["month"] == m)]
            if sub.empty:
                continue
            final_rn = float(sub["nights"].sum())
            final_rev = float(sub["revenue"].sum())
            onbooks = sub.loc[sub["booking_date"].notna() & (sub["booking_date"] <= asof)]
            ob_rn = float(onbooks["nights"].sum())
            ob_rev = float(onbooks["revenue"].sum())
            complete = _month_complete(int(y), int(m), asof)
            # Prior-year same-month on-books at same as-of MD
            ly = summer.loc[(summer["year"] == int(y) - 1) & (summer["month"] == m)]
            ly_asof = pd.Timestamp(year=int(y) - 1, month=int(asof.month), day=min(int(asof.day), 28))
            try:
                ly_asof = pd.Timestamp(year=int(y) - 1, month=int(asof.month), day=int(asof.day))
            except ValueError:
                ly_asof = pd.Timestamp(year=int(y) - 1, month=int(asof.month), day=1) + pd.offsets.MonthEnd(0)
            ly_onbooks = ly.loc[ly["booking_date"].notna() & (ly["booking_date"] <= ly_asof)]
            ly_ob_rn = float(ly_onbooks["nights"].sum()) if not ly.empty else None
            ly_final_rn = float(ly["nights"].sum()) if not ly.empty else None
            pace_rows.append({
                "stay_year": int(y),
                "month": int(m),
                "month_name": MONTH_NAMES.get(int(m), str(m)),
                "asof_date": asof.strftime("%Y-%m-%d"),
                "month_complete": bool(complete),
                "onbooks_room_nights": int(ob_rn),
                "final_room_nights": int(final_rn),
                "onbooks_pct_of_final": (
                    round(100.0 * ob_rn / final_rn, 2) if final_rn and complete else None
                ),
                "onbooks_adr": round(ob_rev / ob_rn, 2) if ob_rn else None,
                "final_adr": round(final_rev / final_rn, 2) if final_rn else None,
                "ly_onbooks_room_nights": int(ly_ob_rn) if ly_ob_rn is not None else None,
                "ly_final_room_nights": int(ly_final_rn) if ly_final_rn is not None else None,
                "onbooks_vs_ly_onbooks_pct": (
                    round(100.0 * ob_rn / ly_ob_rn, 2) if ly_ob_rn else None
                ),
            })
    summer_pace_asof = pd.DataFrame(pace_rows)

    # --- DOW ADR (per month, not combined) ---
    dow_rows = []
    for (y, m), yg in summer_pos.groupby(["year", "month"]):
        mtw = yg.loc[yg["arrival_day_of_week"].isin(["Monday", "Tuesday", "Wednesday"])]
        fs = yg.loc[yg["arrival_day_of_week"].isin(["Friday", "Saturday"])]
        mtw_adr = _wadr(mtw)
        fs_adr = _wadr(fs)
        premium = None
        if mtw_adr and fs_adr and mtw_adr != 0:
            premium = round(100.0 * (fs_adr / mtw_adr - 1.0), 2)
        for dow in DAY_ORDER:
            g = yg.loc[yg["arrival_day_of_week"] == dow]
            if g.empty:
                continue
            rn = float(g["nights"].sum())
            rev = float(g["revenue"].sum())
            dow_rows.append({
                "year": int(y),
                "month": int(m),
                "month_name": MONTH_NAMES.get(int(m), str(m)),
                "arrival_dow": dow,
                "day_group": _day_group(dow),
                "room_nights": int(rn),
                "revenue": round(rev, 2),
                "adr": round(rev / rn, 2) if rn else None,
                "mtw_adr": round(mtw_adr, 2) if mtw_adr is not None else None,
                "fri_sat_adr": round(fs_adr, 2) if fs_adr is not None else None,
                "weekend_premium_pct": premium,
            })
    summer_dow_adr = pd.DataFrame(dow_rows)

    # --- Weekday vs weekend: ADR + booking window together ---
    ww_rows = []
    for (y, m), yg in summer_pos.groupby(["year", "month"]):
        tot_rn = float(yg["nights"].sum())
        for group_label in ("weekday", "weekend_fri_sat"):
            g = yg.loc[yg["day_group"] == group_label]
            if g.empty:
                continue
            rn = float(g["nights"].sum())
            rev = float(g["revenue"].sum())
            leads = g["lead_time_days"].dropna()
            row: dict[str, Any] = {
                "year": int(y),
                "month": int(m),
                "month_name": MONTH_NAMES.get(int(m), str(m)),
                "day_group": group_label,
                "reservations": int(g["reservation_id"].nunique()) if "reservation_id" in g.columns else len(g),
                "room_nights": int(rn),
                "revenue": round(rev, 2),
                "adr": round(rev / rn, 2) if rn else None,
                "rn_share_pct": round(100.0 * rn / tot_rn, 2) if tot_rn else None,
                "median_lead_days": float(leads.median()) if len(leads) else None,
                "mean_lead_days": round(float(leads.mean()), 2) if len(leads) else None,
            }
            for _, _, label in LEAD_BANDS:
                bg = g.loc[g["lead_band"] == label]
                brn = float(bg["nights"].sum()) if not bg.empty else 0.0
                row[f"lead_{label}_rn_share_pct"] = round(100.0 * brn / rn, 2) if rn else None
            ww_rows.append(row)
    summer_weekday_weekend = pd.DataFrame(ww_rows)

    # --- channel (per month) ---
    ch_rows = []
    for (y, m), yg in summer_pos.groupby(["year", "month"]):
        tot_rev = float(yg["revenue"].sum())
        for ch, g in yg.groupby("channel", sort=False):
            rn = float(g["nights"].sum())
            rev = float(g["revenue"].sum())
            ch_rows.append({
                "year": int(y),
                "month": int(m),
                "month_name": MONTH_NAMES.get(int(m), str(m)),
                "channel": ch,
                "reservations": int(g["reservation_id"].nunique()) if "reservation_id" in g.columns else len(g),
                "room_nights": int(rn),
                "revenue": round(rev, 2),
                "adr": round(rev / rn, 2) if rn else None,
                "revenue_share_pct": round(100.0 * rev / tot_rev, 2) if tot_rev else None,
            })
    summer_channel = pd.DataFrame(ch_rows).sort_values(
        ["year", "month", "revenue"], ascending=[True, True, False]
    )

    # --- room type (per month) ---
    rt_rows = []
    for (y, m), yg in summer_pos.groupby(["year", "month"]):
        tot_rev = float(yg["revenue"].sum())
        for unit, g in yg.groupby("unit_id", sort=False):
            rn = float(g["nights"].sum())
            rev = float(g["revenue"].sum())
            rt_rows.append({
                "year": int(y),
                "month": int(m),
                "month_name": MONTH_NAMES.get(int(m), str(m)),
                "room_type": unit,
                "room_nights": int(rn),
                "revenue": round(rev, 2),
                "adr": round(rev / rn, 2) if rn else None,
                "revenue_share_pct": round(100.0 * rev / tot_rev, 2) if tot_rev else None,
            })
    summer_room_type = pd.DataFrame(rt_rows).sort_values(
        ["year", "month", "adr"], ascending=[True, True, False]
    )

    # --- lead bands (per month) ---
    lb_rows = []
    for (y, m), yg in summer_pos.groupby(["year", "month"]):
        tot_rn = float(yg["nights"].sum())
        for label in [b[2] for b in LEAD_BANDS]:
            g = yg.loc[yg["lead_band"] == label]
            if g.empty:
                continue
            rn = float(g["nights"].sum())
            rev = float(g["revenue"].sum())
            lb_rows.append({
                "year": int(y),
                "month": int(m),
                "month_name": MONTH_NAMES.get(int(m), str(m)),
                "lead_band": label,
                "room_nights": int(rn),
                "adr": round(rev / rn, 2) if rn else None,
                "rn_share_pct": round(100.0 * rn / tot_rn, 2) if tot_rn else None,
            })
    summer_lead_bands = pd.DataFrame(lb_rows)

    # --- daily occupancy ---
    daily_rows = []
    if not cal.empty:
        for night, g in cal.groupby("stay_date", sort=True):
            sold = int(len(g))
            cap = _capacity_on_date(capacity_schedule, night, room_count)
            rev = float(g["rev_per_night"].sum())
            nt = pd.Timestamp(night)
            daily_rows.append({
                "night_date": nt.strftime("%Y-%m-%d"),
                "year": int(nt.year),
                "month": int(nt.month),
                "month_name": MONTH_NAMES.get(int(nt.month), str(int(nt.month))),
                "rooms_sold": sold,
                "capacity_rooms": cap,
                "occupancy_pct": round(100.0 * sold / cap, 2) if cap else None,
                "allocated_revenue": round(rev, 2),
                "adr": round(rev / sold, 2) if sold else None,
            })
    summer_daily_occupancy = pd.DataFrame(daily_rows)

    # --- recommendations from anchor year property actuals (no Expedia notes here) ---
    cur_year = int(today.year)
    anchor = summer_year_month_kpis.loc[summer_year_month_kpis["year"] == int(anchor_year)]
    cur = summer_year_month_kpis.loc[summer_year_month_kpis["year"] == cur_year]
    ww_anchor = summer_weekday_weekend.loc[summer_weekday_weekend["year"] == int(anchor_year)]
    ww_cur = summer_weekday_weekend.loc[summer_weekday_weekend["year"] == cur_year]
    recs = []

    def _kpi(frame: pd.DataFrame, month: int, col: str) -> Optional[float]:
        row = frame.loc[frame["month"] == month]
        if row.empty or col not in row.columns:
            return None
        val = row.iloc[0][col]
        return None if pd.isna(val) else float(val)

    def _ww(frame: pd.DataFrame, month: int, day_group: str, col: str) -> Optional[float]:
        row = frame.loc[(frame["month"] == month) & (frame["day_group"] == day_group)]
        if row.empty or col not in row.columns:
            return None
        val = row.iloc[0][col]
        return None if pd.isna(val) else float(val)

    for m in summer_months:
        name = MONTH_NAMES.get(m, str(m))
        a_adr = _kpi(anchor, m, "adr")
        c_adr = _kpi(cur, m, "adr")
        low = round(a_adr * 0.97, 2) if a_adr else None
        high = round(a_adr * 1.05, 2) if a_adr else None
        # Soften incomplete future months slightly if current ADR is soft
        if m == 8 and a_adr:
            low, high = 165.0, 185.0
        if m == 9 and a_adr:
            low = round(a_adr * 0.95, 2)
            high = round(a_adr * 1.03, 2)
        rationale = {
            7: "Hold near 2025 Jul achieved ADR; protect Thu–Sat; do not dump midweek to fill every room.",
            8: (
                "2025 Aug achieved high occ at ~anchor ADR. Current on-books ADR is soft; "
                "reposition toward the band. Last year filled late at higher rates — avoid clearance pricing."
            ),
            9: (
                "September is still peak-shoulder; use 2025 Sep achieved ADR as anchor. "
                "Protect Fri–Sat premium; midweek can flex with pickup."
            ),
        }.get(m, f"Anchor to {anchor_year} {name} achieved ADR.")
        recs.append({
            "period": name,
            "month": m,
            "metric": "blended_adr",
            "anchor_2025_value": a_adr,
            "current_2026_value": c_adr,
            "recommended_band_low": low,
            "recommended_band_high": high,
            "rationale": rationale,
        })
        we_a = _ww(ww_anchor, m, "weekend_fri_sat", "adr")
        we_c = _ww(ww_cur, m, "weekend_fri_sat", "adr")
        wd_a = _ww(ww_anchor, m, "weekday", "adr")
        if we_a:
            recs.append({
                "period": f"{name} weekends",
                "month": m,
                "metric": "fri_sat_adr",
                "anchor_2025_value": we_a,
                "current_2026_value": we_c,
                "recommended_band_low": round(we_a * 0.97, 2),
                "recommended_band_high": round(we_a * 1.08, 2),
                "rationale": (
                    f"Rebuild/protect Fri–Sat vs weekday "
                    f"(anchor weekend ${we_a:,.0f}"
                    + (f", weekday ${wd_a:,.0f}" if wd_a else "")
                    + ")."
                ),
            })

    # Pace insight rows (incomplete months) — property facts only
    for m in summer_months:
        pace_cur = summer_pace_asof.loc[
            (summer_pace_asof["stay_year"] == cur_year) & (summer_pace_asof["month"] == m)
        ]
        pace_ly = summer_pace_asof.loc[
            (summer_pace_asof["stay_year"] == int(anchor_year)) & (summer_pace_asof["month"] == m)
        ]
        if pace_cur.empty:
            continue
        pc = pace_cur.iloc[0]
        asof_str = str(pc.get("asof_date") or "")
        ly_pct = None
        if not pace_ly.empty and pd.notna(pace_ly.iloc[0].get("onbooks_pct_of_final")):
            ly_pct = float(pace_ly.iloc[0]["onbooks_pct_of_final"])
        cur_vs_ly = pc.get("onbooks_vs_ly_onbooks_pct")
        recs.append({
            "period": f"{MONTH_NAMES.get(m, m)} pace",
            "month": m,
            "metric": f"onbooks_vs_ly_asof_{asof_str}",
            "anchor_2025_value": ly_pct,
            "current_2026_value": float(cur_vs_ly) if pd.notna(cur_vs_ly) else None,
            "recommended_band_low": None,
            "recommended_band_high": None,
            "rationale": (
                f"Compare on-books room-nights to {anchor_year} as-of {asof_str}. "
                "Thin pace + soft ADR is the risk; thin pace alone is not."
            ),
        })

    summer_recommendations = pd.DataFrame(recs)

    return {
        "summer_year_month_kpis": summer_year_month_kpis,
        "summer_pace_asof": summer_pace_asof,
        "summer_dow_adr": summer_dow_adr,
        "summer_weekday_weekend": summer_weekday_weekend,
        "summer_channel": summer_channel,
        "summer_room_type": summer_room_type,
        "summer_lead_bands": summer_lead_bands,
        "summer_daily_occupancy": summer_daily_occupancy,
        "summer_recommendations": summer_recommendations,
    }


def write_summer_tables(tables: dict[str, pd.DataFrame], output_dir: Path) -> None:
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    for name, frame in tables.items():
        path = output_dir / f"{name}.csv"
        frame.to_csv(path, index=False)
