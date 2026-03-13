"""
Onboarding analysis: build summary tables from canonical data per docs/07-analysis-tables-and-formulas.md.
Uses two full calendar years (current_year - 2, current_year - 1). Assignment by arrival date only.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Optional, Union

import pandas as pd

# Default booking window bands (lead_time_days): (min_inclusive, max_inclusive, label)
# Finer granularity from 31+ days so large windows are not over-aggregated.
DEFAULT_BOOKING_WINDOW_BANDS = [
    (0, 7, "0-7 days"),
    (8, 14, "8-14 days"),
    (15, 30, "15-30 days"),
    (31, 45, "31-45 days"),
    (46, 60, "46-60 days"),
    (61, 75, "61-75 days"),
    (76, 90, "76-90 days"),
    (91, 120, "91-120 days"),
    (121, 180, "121-180 days"),
    (181, 270, "181-270 days"),
    (271, 365, "271-365 days"),
    (366, 99999, "365+ days"),
]

DAY_ORDER = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


def _unit_id_numeric_sort_key(unit_id: Union[str, object]) -> tuple[int, str]:
    """
    Return (numeric_key, unit_id) so listings sort 100, 101, 102, ... 200, 201, ...; PROPERTY last.
    Unit IDs like "100 LaFave South: Temple of Sinawava" -> (100, ...).
    """
    if unit_id == "PROPERTY" or (isinstance(unit_id, str) and str(unit_id).strip().upper() == "PROPERTY"):
        return (999999, str(unit_id))
    s = str(unit_id).strip()
    match = re.match(r"^(\d+)", s)
    if match:
        return (int(match.group(1)), s)
    return (0, s)


def get_analysis_years() -> tuple[int, int]:
    """Return (year_1, year_2) = (current_year - 2, current_year - 1) for the two full calendar years."""
    now = datetime.now()
    y = now.year
    return (y - 2, y - 1)


def _ensure_datetime(ser: pd.Series) -> pd.Series:
    """Parse to datetime if not already."""
    if pd.api.types.is_datetime64_any_dtype(ser):
        return ser
    return pd.to_datetime(ser, errors="coerce")


def prepare_canonical_for_analysis(df: pd.DataFrame) -> pd.DataFrame:
    """
    Ensure canonical df has datetime arrival_date and derived columns for analysis.
    Adds or overwrites: arrival_year_month, arrival_day_of_week, month_of_year.
    """
    out = df.copy()
    if "arrival_date" not in out.columns:
        raise ValueError("Canonical data must have 'arrival_date'")
    out["arrival_date"] = _ensure_datetime(out["arrival_date"])
    out = out.loc[out["arrival_date"].notna()].copy()
    out["arrival_year_month"] = out["arrival_date"].dt.strftime("%Y-%m")
    out["arrival_day_of_week"] = out["arrival_date"].dt.day_name()
    out["month_of_year"] = out["arrival_date"].dt.month
    return out


def filter_canonical_to_period(
    df: pd.DataFrame,
    year_1: int,
    year_2: int,
) -> pd.DataFrame:
    """
    Keep only rows where arrival_date is in [Jan 1, year_1] through [Dec 31, year_2].
    Stays spanning year boundaries: include only if arrival is in window; assign whole stay to arrival month.
    """
    start = pd.Timestamp(year=year_1, month=1, day=1)
    end = pd.Timestamp(year=year_2, month=12, day=31, hour=23, minute=59, second=59)
    mask = (df["arrival_date"] >= start) & (df["arrival_date"] <= end)
    return df.loc[mask].copy()


def build_overall_summary(
    df: pd.DataFrame,
    room_count: Optional[int],
) -> pd.DataFrame:
    """
    Table 1: One row per listing (unit_id) + one row for PROPERTY.
    Metrics for combined two years; YoY compares year_1 vs year_2.
    """
    year_1, year_2 = get_analysis_years()
    df1 = df.loc[df["arrival_date"].dt.year == year_1]
    df2 = df.loc[df["arrival_date"].dt.year == year_2]

    rows = []
    for unit_id, grp in df.groupby("unit_id", sort=False):
        rev = grp["revenue"].sum()
        rn = grp["nights"].sum()
        bookings = len(grp)
        rev1 = df1.loc[df1["unit_id"] == unit_id, "revenue"].sum()
        rev2 = df2.loc[df2["unit_id"] == unit_id, "revenue"].sum()
        rn1 = df1.loc[df1["unit_id"] == unit_id, "nights"].sum()
        rn2 = df2.loc[df2["unit_id"] == unit_id, "nights"].sum()
        b1 = len(df1.loc[df1["unit_id"] == unit_id])
        b2 = len(df2.loc[df2["unit_id"] == unit_id])
        adr1 = rev1 / rn1 if rn1 and rn1 > 0 else None
        adr2 = rev2 / rn2 if rn2 and rn2 > 0 else None
        adr = rev / rn if rn and rn > 0 else None

        revenue_yoy = (rev2 - rev1) / rev1 * 100 if rev1 and rev1 != 0 else None
        bookings_yoy = (b2 - b1) / b1 * 100 if b1 and b1 != 0 else None
        adr_yoy = (adr2 - adr1) / adr1 * 100 if adr1 and adr1 != 0 else None

        avail = 730  # 365 * 2 for one unit
        occ = (rn / avail * 100) if avail else None
        revpar = rev / avail if avail else None

        rows.append({
            "unit_id": unit_id,
            "revenue": round(rev, 2),
            "revenue_yoy_pct": round(revenue_yoy, 2) if revenue_yoy is not None else None,
            "room_nights": int(rn),
            "bookings": bookings,
            "bookings_yoy_pct": round(bookings_yoy, 2) if bookings_yoy is not None else None,
            "adr": round(adr, 2) if adr is not None else None,
            "adr_yoy_pct": round(adr_yoy, 2) if adr_yoy is not None else None,
            "occupancy_pct": round(occ, 2) if occ is not None else None,
            "revpar": round(revpar, 2) if revpar is not None else None,
        })

    # Property total row
    rev = df["revenue"].sum()
    rn = df["nights"].sum()
    bookings = len(df)
    rev1 = df1["revenue"].sum()
    rev2 = df2["revenue"].sum()
    b1, b2 = len(df1), len(df2)
    rn1, rn2 = df1["nights"].sum(), df2["nights"].sum()
    adr1 = rev1 / rn1 if rn1 and rn1 > 0 else None
    adr2 = rev2 / rn2 if rn2 and rn2 > 0 else None
    adr = rev / rn if rn and rn > 0 else None
    revenue_yoy = (rev2 - rev1) / rev1 * 100 if rev1 and rev1 != 0 else None
    bookings_yoy = (b2 - b1) / b1 * 100 if b1 and b1 != 0 else None
    adr_yoy = (adr2 - adr1) / adr1 * 100 if adr1 and adr1 != 0 else None
    avail = (room_count * 730) if room_count else None
    occ = (rn / avail * 100) if avail and avail > 0 else None
    revpar = (rev / avail) if avail and avail > 0 else None

    rows.append({
        "unit_id": "PROPERTY",
        "revenue": round(rev, 2),
        "revenue_yoy_pct": round(revenue_yoy, 2) if revenue_yoy is not None else None,
        "room_nights": int(rn),
        "bookings": bookings,
        "bookings_yoy_pct": round(bookings_yoy, 2) if bookings_yoy is not None else None,
        "adr": round(adr, 2) if adr is not None else None,
        "adr_yoy_pct": round(adr_yoy, 2) if adr_yoy is not None else None,
        "occupancy_pct": round(occ, 2) if occ is not None else None,
        "revpar": round(revpar, 2) if revpar is not None else None,
    })

    # Sort by unit_id numerically (100, 101, ..., 511), PROPERTY last
    rows.sort(key=lambda r: _unit_id_numeric_sort_key(r["unit_id"]))
    return pd.DataFrame(rows)


def build_monthly_performance(
    df: pd.DataFrame,
    room_count: Optional[int],
) -> pd.DataFrame:
    """Table 2: One row per month (24 months). Property-level only."""
    months = df.groupby("arrival_year_month", sort=True).agg(
        revenue=("revenue", "sum"),
        room_nights=("nights", "sum"),
        bookings=("unit_id", "count"),
    ).reset_index()
    months["adr"] = (months["revenue"] / months["room_nights"]).round(2)
    months.loc[months["room_nights"] == 0, "adr"] = None

    # Occupancy and RevPAR: available = room_count * days in month
    def days_in_month(ym: str) -> int:
        y, m = int(ym[:4]), int(ym[5:7])
        return (pd.Timestamp(year=y, month=m, day=1) + pd.offsets.MonthEnd(0)).day

    months["days_in_month"] = months["arrival_year_month"].map(days_in_month)
    avail = room_count * months["days_in_month"] if room_count else None
    if avail is not None:
        months["occupancy_pct"] = (months["room_nights"] / avail * 100).round(2)
        months["revpar"] = (months["revenue"] / avail).round(2)
    else:
        months["occupancy_pct"] = None
        months["revpar"] = None

    # Performance score 1–10 combining revenue and RevPAR, but by month-of-year (Jan, Feb, ... across both years)
    # 1) Derive month index (1–12) from year_month
    months["month_index"] = months["arrival_year_month"].str[5:7].astype(int)
    # 2) Aggregate by month_index across all years
    month_groups = months.groupby("month_index").agg(
        total_revenue=("revenue", "sum"),
        avg_revpar=("revpar", "mean"),
    )
    n_months = len(month_groups)
    if n_months > 1:
        rev_rank = month_groups["total_revenue"].rank(method="min", ascending=True)
        revpar_rank = month_groups["avg_revpar"].rank(method="min", ascending=True)
        s_rev = (rev_rank - 1) / (n_months - 1)
        s_revpar = (revpar_rank - 1) / (n_months - 1)
        s_combined = 0.5 * s_rev + 0.5 * s_revpar
        scores = (1 + 9 * s_combined).round().astype(int)
        score_map = scores.to_dict()
        months["performance_score_1_10"] = months["month_index"].map(score_map).astype(int)
    else:
        months["performance_score_1_10"] = 5

    months = months.rename(columns={"arrival_year_month": "year_month"})
    cols = [
        "year_month",
        "revenue",
        "room_nights",
        "adr",
        "bookings",
        "occupancy_pct",
        "revpar",
        "performance_score_1_10",
    ]
    return months[[c for c in cols if c in months.columns]]


def build_monthly_performance_combined(
    df: pd.DataFrame,
    room_count: Optional[int],
) -> pd.DataFrame:
    """
    Combined monthly performance across both years (one row per calendar month).
    Aggregates revenue, room_nights, bookings, occupancy, RevPAR, and re-uses the
    same 1–10 performance score by month-of-year.
    """
    months = df.groupby("arrival_year_month", sort=True).agg(
        revenue=("revenue", "sum"),
        room_nights=("nights", "sum"),
        bookings=("unit_id", "count"),
    ).reset_index()

    # Days per year-month and month index (1–12)
    def days_in_month(ym: str) -> int:
        y, m = int(ym[:4]), int(ym[5:7])
        return (pd.Timestamp(year=y, month=m, day=1) + pd.offsets.MonthEnd(0)).day

    months["days_in_month"] = months["arrival_year_month"].map(days_in_month)
    months["month_index"] = months["arrival_year_month"].str[5:7].astype(int)
    months["year"] = months["arrival_year_month"].str[:4].astype(int)

    # Aggregate across years by calendar month, then compute averages per year
    grouped = months.groupby("month_index").agg(
        revenue_sum=("revenue", "sum"),
        room_nights_sum=("room_nights", "sum"),
        bookings_sum=("bookings", "sum"),
        total_days_sum=("days_in_month", "sum"),
        year_count=("year", "nunique"),
    ).reset_index()

    # Avoid division by zero if somehow year_count is 0
    grouped["year_count"] = grouped["year_count"].replace(0, 1)

    grouped["revenue"] = (grouped["revenue_sum"] / grouped["year_count"]).round(2)
    grouped["room_nights"] = (grouped["room_nights_sum"] / grouped["year_count"]).round(2)
    grouped["bookings"] = (grouped["bookings_sum"] / grouped["year_count"]).round(2)
    grouped["days_in_month_avg"] = grouped["total_days_sum"] / grouped["year_count"]

    # ADR and occupancy / RevPAR based on averaged values
    grouped["adr"] = (grouped["revenue"] / grouped["room_nights"]).round(2)
    grouped.loc[grouped["room_nights"] == 0, "adr"] = None
    if room_count:
        avail = room_count * grouped["days_in_month_avg"]
        grouped["occupancy_pct"] = (grouped["room_nights"] / avail * 100).round(2)
        grouped["revpar"] = (grouped["revenue"] / avail).round(2)
    else:
        grouped["occupancy_pct"] = None
        grouped["revpar"] = None

    # Performance score 1–10 by calendar month (same logic as Table 2)
    n_months = len(grouped)
    if n_months > 1:
        rev_rank = grouped["revenue"].rank(method="min", ascending=True)
        revpar_rank = grouped["revpar"].rank(method="min", ascending=True)
        s_rev = (rev_rank - 1) / (n_months - 1)
        s_revpar = (revpar_rank - 1) / (n_months - 1)
        s_combined = 0.5 * s_rev + 0.5 * s_revpar
        grouped["performance_score_1_10"] = (1 + 9 * s_combined).round().astype(int)
    else:
        grouped["performance_score_1_10"] = 5

    # Month labels
    month_names = {
        1: "January", 2: "February", 3: "March", 4: "April", 5: "May", 6: "June",
        7: "July", 8: "August", 9: "September", 10: "October", 11: "November", 12: "December",
    }
    grouped["month_name"] = grouped["month_index"].map(month_names)

    cols = [
        "month_index",
        "month_name",
        "revenue",
        "room_nights",
        "adr",
        "bookings",
        "occupancy_pct",
        "revpar",
        "performance_score_1_10",
    ]
    return grouped[[c for c in cols if c in grouped.columns]]


def build_channel_by_year(df: pd.DataFrame) -> pd.DataFrame:
    """Table 3a: One row per channel per year."""
    df = df.copy()
    df["year"] = df["arrival_date"].dt.year
    grp = df.groupby(["channel", "year"], sort=False).agg(
        bookings=("unit_id", "count"),
        revenue=("revenue", "sum"),
        room_nights=("nights", "sum"),
    ).reset_index()
    tot_by_year = grp.groupby("year").agg(
        total_revenue=("revenue", "sum"),
        total_bookings=("bookings", "sum"),
    )
    grp["share_of_revenue_pct"] = grp.apply(
        lambda r: round((r["revenue"] / tot_by_year.loc[r["year"], "total_revenue"] * 100), 2)
        if tot_by_year.loc[r["year"], "total_revenue"] else None,
        axis=1,
    )
    grp["share_of_bookings_pct"] = grp.apply(
        lambda r: round((r["bookings"] / tot_by_year.loc[r["year"], "total_bookings"] * 100), 2)
        if tot_by_year.loc[r["year"], "total_bookings"] else None,
        axis=1,
    )
    grp["adr"] = (grp["revenue"] / grp["room_nights"]).round(2)
    grp.loc[grp["room_nights"] == 0, "adr"] = None
    return grp


def build_channel_summary(df: pd.DataFrame) -> pd.DataFrame:
    """Table 3b: One row per channel (combined two years)."""
    grp = df.groupby("channel", sort=False).agg(
        bookings=("unit_id", "count"),
        revenue=("revenue", "sum"),
        room_nights=("nights", "sum"),
    ).reset_index()
    tot_rev = grp["revenue"].sum()
    tot_bk = grp["bookings"].sum()
    grp["share_of_revenue_pct"] = (grp["revenue"] / tot_rev * 100).round(2) if tot_rev else None
    grp["share_of_bookings_pct"] = (grp["bookings"] / tot_bk * 100).round(2) if tot_bk else None
    grp["adr"] = (grp["revenue"] / grp["room_nights"]).round(2)
    grp.loc[grp["room_nights"] == 0, "adr"] = None
    return grp


def build_channel_by_listing(df: pd.DataFrame) -> pd.DataFrame:
    """Table 3c: One row per channel x unit_id (combined two years). Share within listing."""
    grp = df.groupby(["unit_id", "channel"], sort=False).agg(
        bookings=("unit_id", "count"),
        revenue=("revenue", "sum"),
        room_nights=("nights", "sum"),
    ).reset_index()
    tot_by_unit = grp.groupby("unit_id").agg(
        total_revenue=("revenue", "sum"),
        total_bookings=("bookings", "sum"),
    )
    grp["share_of_revenue_pct"] = grp.apply(
        lambda r: round((r["revenue"] / tot_by_unit.loc[r["unit_id"], "total_revenue"] * 100), 2)
        if tot_by_unit.loc[r["unit_id"], "total_revenue"] else None,
        axis=1,
    )
    grp["share_of_bookings_pct"] = grp.apply(
        lambda r: round((r["bookings"] / tot_by_unit.loc[r["unit_id"], "total_bookings"] * 100), 2)
        if tot_by_unit.loc[r["unit_id"], "total_bookings"] else None,
        axis=1,
    )
    grp["adr"] = (grp["revenue"] / grp["room_nights"]).round(2)
    grp.loc[grp["room_nights"] == 0, "adr"] = None
    # Sort by unit_id numerically (100, 101, ...), then channel
    grp["_unit_sort"] = grp["unit_id"].map(lambda u: _unit_id_numeric_sort_key(u)[0])
    grp = grp.sort_values(["_unit_sort", "channel"]).drop(columns=["_unit_sort"])
    return grp


def build_by_day_of_week(df: pd.DataFrame) -> pd.DataFrame:
    """Table 4: One row per day of week (arrival-based check-ins). Combined two years."""
    grp = df.groupby("arrival_day_of_week", sort=False).agg(
        check_ins=("unit_id", "count"),
        revenue=("revenue", "sum"),
        room_nights=("nights", "sum"),
    ).reset_index()
    tot_ci = grp["check_ins"].sum()
    tot_rev = grp["revenue"].sum()
    grp["share_of_check_ins_pct"] = (grp["check_ins"] / tot_ci * 100).round(2) if tot_ci else None
    grp["share_of_revenue_pct"] = (grp["revenue"] / tot_rev * 100).round(2) if tot_rev else None
    grp["adr"] = (grp["revenue"] / grp["room_nights"]).round(2)
    grp.loc[grp["room_nights"] == 0, "adr"] = None

    # Day-of-week performance score 1–10 combining ADR, share_of_revenue_pct and share_of_check_ins_pct (relative across the 7 days)
    n = len(grp)
    if n > 1:
        adr_rank = grp["adr"].rank(method="min", ascending=True)
        revshare_rank = grp["share_of_revenue_pct"].rank(method="min", ascending=True)
        checkins_rank = grp["share_of_check_ins_pct"].rank(method="min", ascending=True)
        s_adr = (adr_rank - 1) / (n - 1)
        s_revshare = (revshare_rank - 1) / (n - 1)
        s_check = (checkins_rank - 1) / (n - 1)
        # Equal weights for rate (ADR), revenue share, and check-in share
        s_combined = (s_adr + s_revshare + s_check) / 3.0
        grp["dow_score_1_10"] = (1 + 9 * s_combined).round().astype(int)
    else:
        grp["dow_score_1_10"] = 5

    # Order Mon..Sun
    grp["arrival_day_of_week"] = pd.Categorical(grp["arrival_day_of_week"], categories=DAY_ORDER, ordered=True)
    grp = grp.sort_values("arrival_day_of_week").dropna(subset=["arrival_day_of_week"])
    grp["arrival_day_of_week"] = grp["arrival_day_of_week"].astype(str)
    grp = grp.rename(columns={"arrival_day_of_week": "day_of_week"})
    return grp


def _assign_booking_window(lead_time_days: pd.Series, bands: list[tuple[int, int, str]]) -> pd.Series:
    """Assign each value to a band; returns series of labels."""
    def one(val):
        if pd.isna(val):
            return None
        try:
            v = int(val)
            for lo, hi, label in bands:
                if lo <= v <= hi:
                    return label
        except (ValueError, TypeError):
            pass
        return None

    return lead_time_days.map(one)


def build_booking_window(
    df: pd.DataFrame,
    bands: Optional[list[tuple[int, int, str]]] = None,
) -> pd.DataFrame:
    """Table 5: One row per booking window band. Bands are (min, max, label)."""
    bands = bands or DEFAULT_BOOKING_WINDOW_BANDS
    if "lead_time_days" not in df.columns:
        return pd.DataFrame(columns=["booking_window", "lead_time_min", "lead_time_max", "bookings", "revenue", "share_of_revenue_pct", "share_of_bookings_pct", "room_nights"])
    df = df.copy()
    df["_bw"] = _assign_booking_window(df["lead_time_days"], bands)
    df = df.loc[df["_bw"].notna()]
    tot_rev = df["revenue"].sum()
    tot_bk = len(df)
    rows = []
    for lo, hi, label in bands:
        sub = df.loc[df["_bw"] == label]
        bookings = len(sub)
        revenue = sub["revenue"].sum()
        room_nights = sub["nights"].sum()
        share_rev = (revenue / tot_rev * 100) if tot_rev else None
        share_bk = (bookings / tot_bk * 100) if tot_bk else None
        rows.append({
            "booking_window": label,
            "lead_time_min": lo,
            "lead_time_max": hi if hi < 99999 else None,
            "bookings": bookings,
            "revenue": round(revenue, 2),
            "share_of_revenue_pct": round(share_rev, 2) if share_rev is not None else None,
            "share_of_bookings_pct": round(share_bk, 2) if share_bk is not None else None,
            "room_nights": int(room_nights),
        })
    return pd.DataFrame(rows)


def build_adr_by_listing_by_month(df: pd.DataFrame) -> pd.DataFrame:
    """Table 6: One row per unit_id x year_month (e.g. 2024-01, 2025-06).
    Includes volume, revenue, mean ADR, and min/max row-level ADR per month."""
    grp = df.groupby(["unit_id", "arrival_year_month"], sort=False).agg(
        bookings=("unit_id", "count"),
        room_nights=("nights", "sum"),
        revenue=("revenue", "sum"),
        min_adr=("adr", "min"),
        max_adr=("adr", "max"),
    ).reset_index()
    grp["adr"] = (grp["revenue"] / grp["room_nights"]).round(2)
    grp.loc[grp["room_nights"] == 0, "adr"] = None
    grp = grp.rename(columns={"arrival_year_month": "year_month"})
    # Sort by unit_id numerically (100, 101, ...), then year_month
    grp["_unit_sort"] = grp["unit_id"].map(lambda u: _unit_id_numeric_sort_key(u)[0])
    grp = grp.sort_values(["_unit_sort", "year_month"]).drop(columns=["_unit_sort"])
    cols = ["unit_id", "year_month", "bookings", "room_nights", "revenue", "adr", "min_adr", "max_adr"]
    return grp[[c for c in cols if c in grp.columns]]


def build_listing_season_performance(df: pd.DataFrame) -> pd.DataFrame:
    """
    Listing performance by season (High / Shoulder / Low).
    Seasons are defined by calendar month:
      - High: April, May, June, September, October
      - Low: January, February, December
      - Shoulder: March, July, August, November
    For each unit_id x season we compute revenue, room_nights, bookings, ADR,
    and a 1–10 score combining revenue and ADR (relative within that season).
    """
    if "arrival_date" not in df.columns or "unit_id" not in df.columns:
        return pd.DataFrame(
            columns=["unit_id", "season", "season_revenue", "season_room_nights", "season_bookings", "season_adr", "season_score_1_10"]
        )

    tmp = df.copy()
    tmp["month_index"] = tmp["arrival_date"].dt.month
    # Map months to seasons
    def _season_for_month(m: int) -> Optional[str]:
        if m in (4, 5, 6, 9, 10):
            return "High"
        if m in (1, 2, 12):
            return "Low"
        if m in (3, 7, 8, 11):
            return "Shoulder"
        return None

    tmp["season"] = tmp["month_index"].map(_season_for_month)
    tmp = tmp.loc[tmp["season"].notna()].copy()
    if tmp.empty:
        return pd.DataFrame(
            columns=["unit_id", "season", "season_revenue", "season_room_nights", "season_bookings", "season_adr", "season_score_1_10"]
        )

    grouped = tmp.groupby(["unit_id", "season"], sort=False).agg(
        season_revenue=("revenue", "sum"),
        season_room_nights=("nights", "sum"),
        season_bookings=("unit_id", "count"),
    ).reset_index()

    grouped["season_adr"] = (grouped["season_revenue"] / grouped["season_room_nights"]).round(2)
    grouped.loc[grouped["season_room_nights"] == 0, "season_adr"] = None

    # Score 1–10 within each season using revenue and ADR (50/50)
    grouped["season_score_1_10"] = 5
    for seas in ["High", "Shoulder", "Low"]:
        mask = grouped["season"] == seas
        n = int(mask.sum())
        if n <= 1:
            continue
        rev = grouped.loc[mask, "season_revenue"]
        adr = grouped.loc[mask, "season_adr"]
        rev_rank = rev.rank(method="min", ascending=True)
        adr_rank = adr.rank(method="min", ascending=True)
        s_rev = (rev_rank - 1) / (n - 1)
        s_adr = (adr_rank - 1) / (n - 1)
        s_combined = 0.5 * s_rev + 0.5 * s_adr
        scores = (1 + 9 * s_combined).round().astype(int)
        grouped.loc[mask, "season_score_1_10"] = scores

    # Sort listings numerically within each season for readability
    grouped["_unit_sort"] = grouped["unit_id"].map(lambda u: _unit_id_numeric_sort_key(u)[0])
    grouped = grouped.sort_values(["season", "_unit_sort"]).drop(columns=["_unit_sort"])

    cols = [
        "unit_id",
        "season",
        "season_revenue",
        "season_room_nights",
        "season_bookings",
        "season_adr",
        "season_score_1_10",
    ]
    return grouped[[c for c in cols if c in grouped.columns]]


def run_analysis(
    df: pd.DataFrame,
    room_count: Optional[int] = None,
    booking_window_bands: Optional[list[tuple[int, int, str]]] = None,
) -> dict[str, pd.DataFrame]:
    """
    Run all analysis tables on canonical df (already filtered to analysis period).
    Returns dict of table_name -> DataFrame.
    """
    year_1, year_2 = get_analysis_years()
    df = prepare_canonical_for_analysis(df)
    df = filter_canonical_to_period(df, year_1, year_2)

    return {
        "overall_summary": build_overall_summary(df, room_count),
        "monthly_performance": build_monthly_performance(df, room_count),
        "monthly_performance_combined": build_monthly_performance_combined(df, room_count),
        "listing_season_performance": build_listing_season_performance(df),
        "channel_by_year": build_channel_by_year(df),
        "channel_summary": build_channel_summary(df),
        "channel_by_listing": build_channel_by_listing(df),
        "by_day_of_week": build_by_day_of_week(df),
        "booking_window": build_booking_window(df, bands=booking_window_bands),
        "adr_by_listing_by_month": build_adr_by_listing_by_month(df),
    }
