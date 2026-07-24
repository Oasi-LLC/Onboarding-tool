"""
Onboarding analysis: build summary tables from canonical data per docs/07-analysis-tables-and-formulas.md.
Uses two full calendar years (current_year - 2, current_year - 1). Assignment by arrival date only.
"""
from __future__ import annotations

import re
from datetime import datetime
from typing import Any, Optional, Union

import pandas as pd

from src.parser import reservation_status_is_excluded

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


def _per_listing_physical_units(
    unit_id: object,
    listing_unit_counts: dict[str, int],
    listing_capacity_fallback: Optional[dict[str, str]] = None,
) -> int:
    """
    Physical unit count for per-listing occupancy / RevPAR denominators.
    When unit_count is 0 (overlay SKU sharing another listing's inventory), use the
    fallback base listing's unit_count if configured.
    """
    key = str(unit_id)
    counts = listing_unit_counts or {}
    if key in counts:
        raw = int(counts[key])
    else:
        raw = 1
    if raw > 0:
        return raw
    base = (listing_capacity_fallback or {}).get(key)
    if base:
        bs = str(base)
        if bs in counts:
            v = int(counts[bs])
            if v > 0:
                return v
    return 1


def _property_pool_row_units(unit_id: object, listing_unit_counts: dict[str, int]) -> float:
    """
    Units counted toward property-level available room-nights for this SKU.
    Returns 0 for overlay listings (unit_count 0) so they are not double-counted.
    """
    key = str(unit_id)
    counts = listing_unit_counts or {}
    if key in counts:
        v = float(int(counts[key]))
        return v if v > 0 else 0.0
    return 1.0


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


_ANALYSIS_END_DATA_MAX_ALIASES: frozenset[str] = frozenset(
    {"data_max", "canonical_max", "from_data", "max_arrival"}
)


def resolve_analysis_window(
    analysis_window: Optional[dict[str, Any]] = None,
    *,
    canonical_max_arrival: Optional[pd.Timestamp] = None,
) -> tuple[pd.Timestamp, pd.Timestamp]:
    """
    Resolve analysis window bounds.
    Default: two full prior calendar years.
    Override: config ``{start_date, end_date}`` in YYYY-MM-DD (inclusive).

    If ``end_date`` is one of ``data_max``, ``canonical_max``, ``from_data``, or ``max_arrival``
    (case-insensitive), the window end is the latest ``arrival_date`` in the canonical file
    (pass ``canonical_max_arrival`` from the loaded dataframe). If that value is missing,
    falls back to the default calendar end.
    """
    y1, y2 = get_analysis_years()
    default_start = pd.Timestamp(year=y1, month=1, day=1)
    default_end = pd.Timestamp(year=y2, month=12, day=31)
    cfg = analysis_window or {}
    end_raw = cfg.get("end_date")
    use_data_max = isinstance(end_raw, str) and end_raw.strip().lower() in _ANALYSIS_END_DATA_MAX_ALIASES

    if use_data_max:
        start = pd.to_datetime(cfg.get("start_date"), errors="coerce")
        if pd.isna(start):
            start = default_start
        else:
            start = pd.Timestamp(start).normalize()
        if canonical_max_arrival is not None:
            end = pd.Timestamp(canonical_max_arrival).normalize()
            if pd.isna(end):
                end = default_end
        else:
            end = default_end
        if end < start:
            end = start
        return start, end

    start = pd.to_datetime(cfg.get("start_date"), errors="coerce")
    end = pd.to_datetime(cfg.get("end_date"), errors="coerce")
    if pd.isna(start) or pd.isna(end):
        return default_start, default_end
    start = pd.Timestamp(start).normalize()
    end = pd.Timestamp(end).normalize()
    if end < start:
        return default_start, default_end
    return start, end


def _resolve_yoy_periods(
    analysis_window: Optional[dict[str, Any]] = None,
    *,
    canonical_max_arrival: Optional[pd.Timestamp] = None,
) -> tuple[pd.Timestamp, pd.Timestamp, pd.Timestamp, pd.Timestamp]:
    """
    Build YoY comparison windows aligned to analysis end-date.
    Example: end_date=2026-03-31 -> compare 2025-01-01..2025-03-31 vs 2026-01-01..2026-03-31.
    Falls back safely for leap-year edge cases.

    Optional analysis_window["yoy_compare_end_date"] (YYYY-MM-DD): cap the compare-year
    slice so YoY uses the same calendar span in each year (e.g. Jan–Apr 2025 vs Jan–Apr 2026)
    while the main analysis window can still include more months via end_date.
    """
    cfg = analysis_window or {}
    _, window_end = resolve_analysis_window(
        analysis_window, canonical_max_arrival=canonical_max_arrival
    )
    yoy_cap = pd.to_datetime(cfg.get("yoy_compare_end_date"), errors="coerce")
    end_date = pd.Timestamp(window_end).normalize()
    if not pd.isna(yoy_cap):
        end_date = min(end_date, pd.Timestamp(yoy_cap).normalize())
    compare_year = int(end_date.year)
    base_year = compare_year - 1
    compare_start = pd.Timestamp(year=compare_year, month=1, day=1)
    compare_end = end_date
    try:
        base_end = pd.Timestamp(year=base_year, month=end_date.month, day=end_date.day)
    except ValueError:
        # e.g., Feb 29 alignment to non-leap year
        base_end = pd.Timestamp(year=base_year, month=end_date.month, day=1) + pd.offsets.MonthEnd(0)
    base_start = pd.Timestamp(year=base_year, month=1, day=1)
    return base_start, base_end.normalize(), compare_start, compare_end.normalize()


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
    if "channel" in out.columns:
        # Keep this exclusion in analysis prep so legacy canonical files are safe.
        ch_norm = out["channel"].astype(str).str.strip().str.lower()
        out = out.loc[ch_norm != "sales group booking"].copy()
    # Defense in depth: drop cancel/pending rows if a status column exists on canonical (ingestion
    # should already exclude them; this keeps 2025+ and future runs aligned if schema evolves).
    for _status_col in ("reservation_status", "status", "Status"):
        if _status_col in out.columns:
            out = out.loc[~out[_status_col].map(reservation_status_is_excluded)].copy()
            break
    out["arrival_year_month"] = out["arrival_date"].dt.strftime("%Y-%m")
    out["arrival_day_of_week"] = out["arrival_date"].dt.day_name()
    out["month_of_year"] = out["arrival_date"].dt.month
    return out


def filter_canonical_to_period(
    df: pd.DataFrame,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
) -> pd.DataFrame:
    """
    Keep only rows where arrival_date is in [Jan 1, year_1] through [Dec 31, year_2].
    Stays spanning year boundaries: include only if arrival is in window; assign whole stay to arrival month.
    """
    mask = (df["arrival_date"] >= start_date) & (df["arrival_date"] <= end_date)
    return df.loc[mask].copy()


def _active_days_in_window(
    unit_id: object,
    window_start: pd.Timestamp,
    window_end: pd.Timestamp,
    listing_start_dates: Optional[dict[str, str]] = None,
) -> int:
    """
    Active days for a listing inside [window_start, window_end], inclusive.
    If listing_start_dates is missing for unit_id, assume active from window_start.
    """
    listing_start_dates = listing_start_dates or {}
    raw_start = listing_start_dates.get(str(unit_id))
    start_dt = pd.to_datetime(raw_start, errors="coerce") if raw_start else pd.NaT
    effective_start = window_start if pd.isna(start_dt) else max(window_start, pd.Timestamp(start_dt).normalize())
    if effective_start > window_end:
        return 0
    return int((window_end - effective_start).days + 1)


def _property_available_room_nights(
    room_count: Optional[int],
    window_start: pd.Timestamp,
    window_end: pd.Timestamp,
    listing_start_dates: Optional[dict[str, str]] = None,
    listing_unit_counts: Optional[dict[str, int]] = None,
    capacity_schedule: Optional[list[dict[str, Any]]] = None,
) -> Optional[float]:
    """
    Property availability denominator in room-nights.
    Prefer capacity_schedule when present; else listing-level rollout; else room_count*days.
    """
    window_start = pd.Timestamp(window_start).normalize()
    window_end = pd.Timestamp(window_end).normalize()
    if window_end < window_start:
        return 0.0

    schedule = capacity_schedule or []
    if schedule:
        total = 0.0
        for seg in schedule:
            if not isinstance(seg, dict):
                continue
            seg_start = pd.to_datetime(seg.get("start_date"), errors="coerce")
            seg_end = pd.to_datetime(seg.get("end_date"), errors="coerce")
            rc = seg.get("room_count")
            try:
                rc_i = int(rc)
            except (TypeError, ValueError):
                continue
            if pd.isna(seg_start):
                continue
            seg_start = pd.Timestamp(seg_start).normalize()
            if pd.isna(seg_end):
                seg_end = window_end
            else:
                seg_end = pd.Timestamp(seg_end).normalize()
            lo = max(window_start, seg_start)
            hi = min(window_end, seg_end)
            if hi < lo:
                continue
            total += rc_i * int((hi - lo).days + 1)
        return total

    listing_start_dates = listing_start_dates or {}
    listing_unit_counts = listing_unit_counts or {}
    window_days = int((window_end - window_start).days + 1)
    if listing_start_dates:
        total = 0.0
        for unit_id in listing_start_dates.keys():
            units = _property_pool_row_units(unit_id, listing_unit_counts)
            if units <= 0:
                continue
            total += units * _active_days_in_window(
                unit_id=unit_id,
                window_start=window_start,
                window_end=window_end,
                listing_start_dates=listing_start_dates,
            )
        return total
    if room_count and room_count > 0:
        return float(room_count * window_days)
    return None


def build_overall_summary(
    df: pd.DataFrame,
    room_count: Optional[int],
    analysis_window: Optional[dict[str, Any]] = None,
    listing_unit_counts: Optional[dict[str, int]] = None,
    listing_start_dates: Optional[dict[str, str]] = None,
    listing_capacity_fallback: Optional[dict[str, str]] = None,
    capacity_schedule: Optional[list[dict[str, Any]]] = None,
    *,
    canonical_max_arrival: Optional[pd.Timestamp] = None,
) -> pd.DataFrame:
    """
    Table 1: One row per listing (unit_id) + one row for PROPERTY.
    Metrics for combined analysis window; YoY compares year_1 vs year_2 within that window.

    analysis_window: if provided, availability denominator uses the actual window length
        instead of the default two-full-calendar-year assumption (730 days).
    listing_unit_counts: {unit_id: physical_unit_count} — for properties where a single
        unit_id represents multiple physical units (e.g. wmb listing types).  When absent
        for a given listing, 1 physical unit is assumed.
    """
    base_start, base_end, compare_start, compare_end = _resolve_yoy_periods(
        analysis_window, canonical_max_arrival=canonical_max_arrival
    )
    df1 = df.loc[(df["arrival_date"] >= base_start) & (df["arrival_date"] <= base_end)]
    df2 = df.loc[(df["arrival_date"] >= compare_start) & (df["arrival_date"] <= compare_end)]

    # Resolve the actual analysis window so availability denominators are correct.
    start_date, end_date = resolve_analysis_window(
        analysis_window, canonical_max_arrival=canonical_max_arrival
    )

    listing_unit_counts = listing_unit_counts or {}
    listing_start_dates = listing_start_dates or {}
    listing_capacity_fallback = listing_capacity_fallback or {}

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
        adr_yoy = (adr2 - adr1) / adr1 * 100 if adr1 is not None and adr1 != 0 and adr2 is not None else None

        physical_units = _per_listing_physical_units(
            unit_id, listing_unit_counts, listing_capacity_fallback=listing_capacity_fallback
        )
        active_days = _active_days_in_window(
            unit_id=unit_id,
            window_start=start_date,
            window_end=end_date,
            listing_start_dates=listing_start_dates,
        )
        avail = physical_units * active_days
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

    # Property total row — denominator is room_count * window_days.
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
    adr_yoy = (adr2 - adr1) / adr1 * 100 if adr1 is not None and adr1 != 0 and adr2 is not None else None
    avail = _property_available_room_nights(
        room_count=room_count,
        window_start=start_date,
        window_end=end_date,
        listing_start_dates=listing_start_dates,
        listing_unit_counts=listing_unit_counts,
        capacity_schedule=capacity_schedule,
    )
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


def build_period_summary(
    df: pd.DataFrame,
    room_count: Optional[int],
    period_start: pd.Timestamp,
    period_end: pd.Timestamp,
    listing_unit_counts: Optional[dict[str, int]] = None,
    listing_start_dates: Optional[dict[str, str]] = None,
    period_label: str = "",
    listing_capacity_fallback: Optional[dict[str, str]] = None,
    capacity_schedule: Optional[list[dict[str, Any]]] = None,
) -> pd.DataFrame:
    """
    Summary table for a fixed period (e.g., 2026 Q1), one row per listing + PROPERTY.
    """
    listing_unit_counts = listing_unit_counts or {}
    listing_start_dates = listing_start_dates or {}
    listing_capacity_fallback = listing_capacity_fallback or {}
    sub = df.loc[(df["arrival_date"] >= period_start) & (df["arrival_date"] <= period_end)].copy()
    rows: list[dict[str, Any]] = []
    for unit_id, grp in sub.groupby("unit_id", sort=False):
        rev = grp["revenue"].sum()
        rn = grp["nights"].sum()
        bookings = len(grp)
        adr = (rev / rn) if rn and rn > 0 else None
        physical_units = _per_listing_physical_units(
            unit_id, listing_unit_counts, listing_capacity_fallback=listing_capacity_fallback
        )
        active_days = _active_days_in_window(
            unit_id=unit_id,
            window_start=period_start,
            window_end=period_end,
            listing_start_dates=listing_start_dates,
        )
        avail = physical_units * active_days
        occ = (rn / avail * 100) if avail else None
        revpar = (rev / avail) if avail else None
        rows.append({
            "period_label": period_label,
            "period_start": period_start.strftime("%Y-%m-%d"),
            "period_end": period_end.strftime("%Y-%m-%d"),
            "unit_id": unit_id,
            "revenue": round(rev, 2),
            "room_nights": int(rn),
            "bookings": bookings,
            "adr": round(adr, 2) if adr is not None else None,
            "occupancy_pct": round(occ, 2) if occ is not None else None,
            "revpar": round(revpar, 2) if revpar is not None else None,
        })
    rev = sub["revenue"].sum()
    rn = sub["nights"].sum()
    bookings = len(sub)
    adr = (rev / rn) if rn and rn > 0 else None
    avail = _property_available_room_nights(
        room_count=room_count,
        window_start=period_start,
        window_end=period_end,
        listing_start_dates=listing_start_dates,
        listing_unit_counts=listing_unit_counts,
        capacity_schedule=capacity_schedule,
    )
    occ = (rn / avail * 100) if avail and avail > 0 else None
    revpar = (rev / avail) if avail and avail > 0 else None
    rows.append({
        "period_label": period_label,
        "period_start": period_start.strftime("%Y-%m-%d"),
        "period_end": period_end.strftime("%Y-%m-%d"),
        "unit_id": "PROPERTY",
        "revenue": round(rev, 2),
        "room_nights": int(rn),
        "bookings": bookings,
        "adr": round(adr, 2) if adr is not None else None,
        "occupancy_pct": round(occ, 2) if occ is not None else None,
        "revpar": round(revpar, 2) if revpar is not None else None,
    })
    rows.sort(key=lambda r: _unit_id_numeric_sort_key(r["unit_id"]))
    return pd.DataFrame(rows)


def build_monthly_performance(
    df: pd.DataFrame,
    room_count: Optional[int],
    listing_start_dates: Optional[dict[str, str]] = None,
    listing_unit_counts: Optional[dict[str, int]] = None,
    capacity_schedule: Optional[list[dict[str, Any]]] = None,
) -> pd.DataFrame:
    """Table 2: One row per month (24 months). Property-level only."""
    months = df.groupby("arrival_year_month", sort=True).agg(
        revenue=("revenue", "sum"),
        room_nights=("nights", "sum"),
        bookings=("unit_id", "count"),
    ).reset_index()
    months["adr"] = (months["revenue"] / months["room_nights"]).round(2)
    months.loc[months["room_nights"] == 0, "adr"] = None

    # Occupancy and RevPAR use active-in-month inventory when rollout dates are available.
    listing_start_dates = listing_start_dates or {}
    listing_unit_counts = listing_unit_counts or {}
    month_avail = []
    for ym in months["arrival_year_month"].tolist():
        y, m = int(ym[:4]), int(ym[5:7])
        m_start = pd.Timestamp(year=y, month=m, day=1)
        m_end = m_start + pd.offsets.MonthEnd(0)
        avail = _property_available_room_nights(
            room_count=room_count,
            window_start=m_start,
            window_end=m_end,
            listing_start_dates=listing_start_dates,
            listing_unit_counts=listing_unit_counts,
            capacity_schedule=capacity_schedule,
        )
        month_avail.append(avail)
    months["available_room_nights"] = month_avail
    if "available_room_nights" in months.columns:
        months["occupancy_pct"] = (months["room_nights"] / months["available_room_nights"] * 100).round(2)
        months["revpar"] = (months["revenue"] / months["available_room_nights"]).round(2)
    else:
        months["occupancy_pct"] = None
        months["revpar"] = None

    # Score is only in monthly_performance_combined (12 calendar months), not in this 24-row table
    months = months.rename(columns={"arrival_year_month": "year_month"})
    cols = [
        "year_month",
        "revenue",
        "room_nights",
        "adr",
        "bookings",
        "occupancy_pct",
        "revpar",
    ]
    return months[[c for c in cols if c in months.columns]]


def _monthly_combined_provisional_flags(
    months_detail: pd.DataFrame,
    ratio: float,
) -> dict[int, bool]:
    """
    True when the latest calendar year in months_detail has fewer bookings for that
    month_index than ratio * mean(bookings) in strictly prior years (same month_index).
    """
    if months_detail.empty or "year" not in months_detail.columns or "month_index" not in months_detail.columns:
        return {}
    ly = int(months_detail["year"].max())
    prev = months_detail.loc[months_detail["year"] < ly]
    if prev.empty:
        return {int(m): False for m in months_detail["month_index"].dropna().unique().astype(int)}
    hist_mean = prev.groupby("month_index")["bookings"].mean()
    latest = months_detail.loc[months_detail["year"] == ly].groupby("month_index")["bookings"].sum()
    idx = sorted(
        set(hist_mean.index.astype(int).tolist()) | set(latest.index.astype(int).tolist())
    )
    out: dict[int, bool] = {}
    for m in idx:
        h = hist_mean.get(m, float("nan"))
        cur = latest.get(m, float("nan"))
        if pd.isna(h) or float(h) <= 0 or pd.isna(cur):
            out[int(m)] = False
        else:
            out[int(m)] = float(cur) < float(ratio) * float(h)
    return out


def build_monthly_performance_combined(
    df: pd.DataFrame,
    room_count: Optional[int],
    listing_start_dates: Optional[dict[str, str]] = None,
    listing_unit_counts: Optional[dict[str, int]] = None,
    combined_min_arrival_date: Optional[pd.Timestamp] = None,
    combined_max_arrival_date: Optional[pd.Timestamp] = None,
    provisional_bookings_ratio: Optional[float] = None,
    capacity_schedule: Optional[list[dict[str, Any]]] = None,
) -> pd.DataFrame:
    """
    Combined monthly performance across years (one row per calendar month observed).

    Optional filters (pricing-rank basis only; other analysis tables use full ``df``):
    ``combined_min_arrival_date`` / ``combined_max_arrival_date`` restrict which reservation
    rows contribute (e.g. FBG after Basse 2 go-live, or cap future pickup months at today).

    When ``provisional_bookings_ratio`` is set (e.g. 0.6), ``performance_rank_provisional`` is
    True if the latest year's booking count for that month_index is below the ratio times
    the mean booking count in prior years for the same month_index.
    """
    sub = df.copy()
    if combined_min_arrival_date is not None:
        sub = sub.loc[sub["arrival_date"] >= combined_min_arrival_date].copy()
    if combined_max_arrival_date is not None:
        sub = sub.loc[sub["arrival_date"] <= combined_max_arrival_date].copy()

    meta_min = (
        pd.Timestamp(combined_min_arrival_date).strftime("%Y-%m-%d")
        if combined_min_arrival_date is not None
        else ""
    )
    meta_max = (
        pd.Timestamp(combined_max_arrival_date).strftime("%Y-%m-%d")
        if combined_max_arrival_date is not None
        else ""
    )

    prov_ratio: Optional[float] = None
    if provisional_bookings_ratio is not None:
        try:
            pr = float(provisional_bookings_ratio)
            if 0 < pr <= 1:
                prov_ratio = pr
        except (TypeError, ValueError):
            prov_ratio = None

    if sub.empty:
        cols = [
            "month_index",
            "month_name",
            "avg_basis_year_count",
            "revenue",
            "room_nights",
            "adr",
            "bookings",
            "occupancy_pct",
            "revpar",
            "performance_score_1_10",
            "performance_rank_provisional",
            "rank_filter_min_arrival",
            "rank_filter_max_arrival",
        ]
        return pd.DataFrame(columns=cols)

    months = sub.groupby("arrival_year_month", sort=True).agg(
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
        avg_basis_year_count=("year", "nunique"),
    ).reset_index()

    # Distinct calendar years contributing to this month_index (before averaging).
    # Shown on output for sanity checks (e.g. one year only when today-cap drops the compare year).
    div = grouped["avg_basis_year_count"].replace(0, 1)

    grouped["revenue"] = (grouped["revenue_sum"] / div).round(2)
    grouped["room_nights"] = (grouped["room_nights_sum"] / div).round(2)
    grouped["bookings"] = (grouped["bookings_sum"] / div).round(2)
    # Active inventory availability averaged by month index across years.
    listing_start_dates = listing_start_dates or {}
    listing_unit_counts = listing_unit_counts or {}
    availability_rows = []
    for y in sorted(months["year"].dropna().astype(int).unique().tolist()):
        for m in sorted(months["month_index"].dropna().astype(int).unique().tolist()):
            m_start = pd.Timestamp(year=int(y), month=int(m), day=1)
            m_end = m_start + pd.offsets.MonthEnd(0)
            avail = _property_available_room_nights(
                room_count=room_count,
                window_start=m_start,
                window_end=m_end,
                listing_start_dates=listing_start_dates,
                listing_unit_counts=listing_unit_counts,
                capacity_schedule=capacity_schedule,
            )
            availability_rows.append({"year": int(y), "month_index": int(m), "available_room_nights": avail})
    avail_df = pd.DataFrame(availability_rows)
    if not avail_df.empty:
        avail_month = avail_df.groupby("month_index", as_index=False).agg(
            available_room_nights_avg=("available_room_nights", "mean")
        )
        grouped = grouped.merge(avail_month, on="month_index", how="left")

    # ADR and occupancy / RevPAR based on averaged values
    grouped["adr"] = (grouped["revenue"] / grouped["room_nights"]).round(2)
    grouped.loc[grouped["room_nights"] == 0, "adr"] = None
    if "available_room_nights_avg" in grouped.columns:
        grouped["occupancy_pct"] = (grouped["room_nights"] / grouped["available_room_nights_avg"] * 100).round(2)
        grouped["revpar"] = (grouped["revenue"] / grouped["available_room_nights_avg"]).round(2)
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

    if prov_ratio is not None:
        prov_flags = _monthly_combined_provisional_flags(months, prov_ratio)
        grouped["performance_rank_provisional"] = grouped["month_index"].astype(int).map(
            lambda m: bool(prov_flags.get(int(m), False))
        )
    else:
        grouped["performance_rank_provisional"] = False

    grouped["rank_filter_min_arrival"] = meta_min
    grouped["rank_filter_max_arrival"] = meta_max

    cols = [
        "month_index",
        "month_name",
        "avg_basis_year_count",
        "revenue",
        "room_nights",
        "adr",
        "bookings",
        "occupancy_pct",
        "revpar",
        "performance_score_1_10",
        "performance_rank_provisional",
        "rank_filter_min_arrival",
        "rank_filter_max_arrival",
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
    """
    Table 4: One row per day of week using stay-date (night-of-week) attribution.

    Revenue is allocated to occupied nights via revenue / nights and then aggregated by
    stay_date weekday. Arrival-day check-ins are still included as a contextual column.
    """
    df = df.copy()

    # Keep arrival-day check-ins as context for booking pattern interpretation.
    arrivals = (
        df.groupby("arrival_day_of_week", sort=False)
        .agg(check_ins=("unit_id", "count"))
        .reset_index()
        .rename(columns={"arrival_day_of_week": "day_of_week"})
    )

    stay = df[["arrival_date", "departure_date", "revenue", "nights"]].dropna(
        subset=["arrival_date", "departure_date", "revenue", "nights"]
    ).copy()
    stay = stay.loc[stay["departure_date"] > stay["arrival_date"]].copy()
    stay["nights_safe"] = pd.to_numeric(stay["nights"], errors="coerce")
    stay = stay.loc[stay["nights_safe"] > 0].copy()
    stay["rev_per_night"] = stay["revenue"] / stay["nights_safe"]

    # Expand each reservation into occupied nights [arrival_date, departure_date).
    stay["stay_date"] = stay.apply(
        lambda r: pd.date_range(
            start=pd.Timestamp(r["arrival_date"]).normalize(),
            end=(pd.Timestamp(r["departure_date"]).normalize() - pd.Timedelta(days=1)),
            freq="D",
        ),
        axis=1,
    )
    stay = stay.explode("stay_date")
    stay["day_of_week"] = pd.to_datetime(stay["stay_date"], errors="coerce").dt.day_name()

    grp = stay.groupby("day_of_week", sort=False).agg(
        revenue=("rev_per_night", "sum"),
        room_nights=("stay_date", "count"),
    ).reset_index()
    grp = grp.merge(arrivals, on="day_of_week", how="left")
    grp["check_ins"] = grp["check_ins"].fillna(0)

    tot_ci = grp["check_ins"].sum()
    tot_rev = grp["revenue"].sum()
    tot_rn = grp["room_nights"].sum()
    grp["share_of_check_ins_pct"] = (grp["check_ins"] / tot_ci * 100).round(2) if tot_ci else None
    grp["share_of_revenue_pct"] = (grp["revenue"] / tot_rev * 100).round(2) if tot_rev else None
    grp["share_of_room_nights_pct"] = (grp["room_nights"] / tot_rn * 100).round(2) if tot_rn else None
    grp["adr"] = (grp["revenue"] / grp["room_nights"]).round(2)
    grp.loc[grp["room_nights"] == 0, "adr"] = None

    # Day-of-week performance score 1-10: ADR + revenue share + room-night share.
    n = len(grp)
    if n > 1:
        adr_rank = grp["adr"].rank(method="min", ascending=True)
        revshare_rank = grp["share_of_revenue_pct"].rank(method="min", ascending=True)
        room_nights_rank = grp["share_of_room_nights_pct"].rank(method="min", ascending=True)
        s_adr = (adr_rank - 1) / (n - 1)
        s_revshare = (revshare_rank - 1) / (n - 1)
        s_room_nights = (room_nights_rank - 1) / (n - 1)
        s_combined = 0.4 * s_adr + 0.4 * s_revshare + 0.2 * s_room_nights
        grp["dow_score_1_10"] = 1 + 9 * s_combined
    else:
        grp["dow_score_1_10"] = 5.0

    grp["day_of_week"] = pd.Categorical(grp["day_of_week"], categories=DAY_ORDER, ordered=True)
    grp = grp.sort_values("day_of_week").dropna(subset=["day_of_week"])
    grp["day_of_week"] = grp["day_of_week"].astype(str)
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

    # ADR at season level (for reference only)
    grouped["season_adr"] = (grouped["season_revenue"] / grouped["season_room_nights"]).round(2)
    grouped.loc[grouped["season_room_nights"] == 0, "season_adr"] = None

    # Approximate seasonal RevPAR: revenue / days_in_season (one unit per listing)
    def _days_in_season(seas: str) -> int:
        # Months per season (two analysis years)
        if seas == "High":
            months_in = (4, 5, 6, 9, 10)
        elif seas == "Low":
            months_in = (1, 2, 12)
        else:  # Shoulder
            months_in = (3, 7, 8, 11)
        days = 0
        y1, y2 = get_analysis_years()
        for y in (y1, y2):
            for m in months_in:
                days += (pd.Timestamp(year=y, month=m, day=1) + pd.offsets.MonthEnd(0)).day
        return days

    grouped["season_revpar"] = None
    for seas in ["High", "Shoulder", "Low"]:
        mask = grouped["season"] == seas
        if not mask.any():
            continue
        days_in_season = _days_in_season(seas)
        if days_in_season > 0:
            grouped.loc[mask, "season_revpar"] = (
                grouped.loc[mask, "season_revenue"] / float(days_in_season)
            ).round(2)

    # Score within each season using revenue (70%) and RevPAR (30%), keep continuous score
    grouped["season_score_1_10"] = 5.0
    grouped["season_percentile"] = None
    for seas in ["High", "Shoulder", "Low"]:
        mask = grouped["season"] == seas
        n = int(mask.sum())
        if n <= 1:
            continue
        rev = grouped.loc[mask, "season_revenue"]
        revpar = grouped.loc[mask, "season_revpar"]
        rev_rank = rev.rank(method="min", ascending=True)
        revpar_rank = revpar.rank(method="min", ascending=True)
        s_rev = (rev_rank - 1) / (n - 1)
        s_revpar = (revpar_rank - 1) / (n - 1)
        # Revenue-heavy weighting: 70% revenue, 30% RevPAR
        s_combined = 0.7 * s_rev + 0.3 * s_revpar
        scores = 1 + 9 * s_combined  # float in [1, 10]
        grouped.loc[mask, "season_score_1_10"] = scores
        grouped.loc[mask, "season_percentile"] = (s_combined * 100).round(1)

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
        "season_percentile",
    ]
    return grouped[[c for c in cols if c in grouped.columns]]


def build_listing_season_performance_from_tiers(listing_daily: pd.DataFrame) -> pd.DataFrame:
    """
    Build listing performance by tier bucket (using tier labels as 'season').
    Output schema intentionally matches listing_season_performance for dashboard reuse.
    """
    if listing_daily.empty:
        return pd.DataFrame(
            columns=[
                "unit_id", "season", "season_order", "season_revenue", "season_room_nights",
                "season_bookings", "season_adr", "season_score_1_10", "season_percentile",
            ]
        )
    req = {"unit_id", "active", "tier_label", "tier_id", "property_revenue", "property_room_nights", "bookings"}
    if not req.issubset(set(listing_daily.columns)):
        return pd.DataFrame(
            columns=[
                "unit_id", "season", "season_order", "season_revenue", "season_room_nights",
                "season_bookings", "season_adr", "season_score_1_10", "season_percentile",
            ]
        )
    tmp = listing_daily.copy()
    tmp = tmp[tmp["active"] == True].copy()
    tmp["season"] = tmp["tier_label"].astype(str)
    tmp["season_order"] = pd.to_numeric(tmp["tier_id"], errors="coerce").fillna(999).astype(int)
    grouped = tmp.groupby(["unit_id", "season", "season_order"], as_index=False).agg(
        season_revenue=("property_revenue", "sum"),
        season_room_nights=("property_room_nights", "sum"),
        season_bookings=("bookings", "sum"),
        season_days=("date", "count"),
    )
    grouped["season_adr"] = (grouped["season_revenue"] / grouped["season_room_nights"]).round(2)
    grouped.loc[grouped["season_room_nights"] == 0, "season_adr"] = None
    grouped["season_revpar"] = (grouped["season_revenue"] / grouped["season_days"]).round(2)

    grouped["season_score_1_10"] = 5.0
    grouped["season_percentile"] = None
    for seas in sorted(grouped["season"].dropna().unique().tolist(), key=lambda s: grouped.loc[grouped["season"] == s, "season_order"].min()):
        mask = grouped["season"] == seas
        n = int(mask.sum())
        if n <= 1:
            continue
        rev = grouped.loc[mask, "season_revenue"]
        revpar = grouped.loc[mask, "season_revpar"]
        rev_rank = rev.rank(method="min", ascending=True)
        revpar_rank = revpar.rank(method="min", ascending=True)
        s_rev = (rev_rank - 1) / (n - 1)
        s_revpar = (revpar_rank - 1) / (n - 1)
        s_combined = 0.7 * s_rev + 0.3 * s_revpar
        grouped.loc[mask, "season_score_1_10"] = 1 + 9 * s_combined
        grouped.loc[mask, "season_percentile"] = (s_combined * 100).round(1)

    grouped["_unit_sort"] = grouped["unit_id"].map(lambda u: _unit_id_numeric_sort_key(u)[0])
    grouped = grouped.sort_values(["season_order", "_unit_sort"]).drop(columns=["_unit_sort"])
    cols = [
        "unit_id",
        "season",
        "season_order",
        "season_revenue",
        "season_room_nights",
        "season_bookings",
        "season_adr",
        "season_score_1_10",
        "season_percentile",
    ]
    return grouped[[c for c in cols if c in grouped.columns]]


def _build_tier_edges_from_gap_detection(
    percentiles: pd.Series,
    gap_threshold_pct: float,
    min_tiers: int,
    max_tiers: int,
) -> Optional[list[tuple[float, float]]]:
    """Return percentile bucket edges [(low, high), ...] if valid; else None."""
    uniq = sorted(set(float(x) for x in percentiles.dropna().tolist()))
    if len(uniq) < 2:
        return None
    boundaries = []
    for a, b in zip(uniq[:-1], uniq[1:]):
        if (b - a) >= gap_threshold_pct:
            boundaries.append((a + b) / 2.0)
    cuts = [0.0] + boundaries + [100.0]
    if any(cuts[i] >= cuts[i + 1] for i in range(len(cuts) - 1)):
        return None
    n_tiers = len(cuts) - 1
    if n_tiers < min_tiers or n_tiers > max_tiers:
        return None
    return [(cuts[i], cuts[i + 1]) for i in range(n_tiers)]


def _assign_tier_from_edges(
    percentiles: pd.Series,
    edges: list[tuple[float, float]],
) -> pd.Series:
    """Assign 1..N tier id from percentile edges."""
    out = pd.Series(index=percentiles.index, dtype="Int64")
    for i, (lo, hi) in enumerate(edges, start=1):
        if i < len(edges):
            mask = (percentiles >= lo) & (percentiles < hi)
        else:
            mask = (percentiles >= lo) & (percentiles <= hi)
        out.loc[mask] = i
    return out


def _assign_tier_quantile(
    percentiles: pd.Series,
    requested_tiers: int,
) -> Optional[pd.Series]:
    """Assign quantile tiers 1..N (N may be less than requested when duplicates collapse)."""
    try:
        labels = list(range(1, max(2, int(requested_tiers)) + 1))
        q = pd.qcut(percentiles, q=len(labels), labels=labels, duplicates="drop")
        if q is None:
            return None
        return q.astype("Int64")
    except ValueError:
        return None


def _default_tier_labels_for_count(n: int) -> dict[int, str]:
    """
    Deterministic default labels for 2..15 tiers.
    Keeps lowest=Soft and highest=Peak across properties.
    """
    n = max(2, min(15, int(n)))
    fixed = {
        2: ["Soft", "Peak"],
        3: ["Soft", "Shoulder", "Peak"],
        4: ["Soft", "Low", "High", "Peak"],
        5: ["Soft", "Low", "Shoulder", "High", "Peak"],
        6: ["Soft", "Low", "Shoulder Low", "Shoulder High", "High", "Peak"],
    }
    if n in fixed:
        labels = fixed[n]
    else:
        # For 7..15, insert deterministic intermediate bands between Low and High.
        middle_count = n - 4  # Soft, Low, [middle...], High, Peak
        mids = [f"Mid {i}" for i in range(1, middle_count + 1)]
        labels = ["Soft", "Low"] + mids + ["High", "Peak"]
    return {i + 1: labels[i] for i in range(len(labels))}


def build_daily_tier_outputs(
    df: pd.DataFrame,
    room_count: Optional[int],
    tiering_cfg: Optional[dict[str, Any]] = None,
    listing_start_dates: Optional[dict[str, str]] = None,
    analysis_window: Optional[dict[str, Any]] = None,
    *,
    canonical_max_arrival: Optional[pd.Timestamp] = None,
) -> dict[str, pd.DataFrame]:
    """
    Build tier outputs from smoothed daily RevPAR percentiles.
    Returns:
      - daily_tier_calendar
      - tier_summary
      - tier_blocks
    """
    cfg = tiering_cfg or {}
    window_days = int(cfg.get("smoothing_window_days", 14))
    gap_threshold_pct = float(cfg.get("gap_threshold_pct", 6))
    gap_threshold_z = float(cfg.get("gap_threshold_zscore", 0.6))
    gap_score_basis = str(cfg.get("gap_score_basis", "percentile_int")).strip().lower()
    min_tiers = int(cfg.get("min_tiers", 2))
    max_tiers = int(cfg.get("max_tiers", 15))
    fallback_tiers = int(cfg.get("quantile_fallback_tiers", 5))
    min_days_per_tier = int(cfg.get("min_days_per_tier", 14))
    min_active_months_for_portfolio = int(cfg.get("min_active_months_for_portfolio", 6))

    start_date, end_date = resolve_analysis_window(
        analysis_window, canonical_max_arrival=canonical_max_arrival
    )
    daily = pd.DataFrame({"date": pd.date_range(start_date, end_date, freq="D")})
    if df.empty:
        daily["tier_id"] = 1
        daily["tier_label"] = "Tier 1"
        return {
            "daily_tier_calendar": daily,
            "tier_summary": pd.DataFrame(columns=["tier_id", "tier_label", "days", "avg_revpar", "avg_adr", "avg_occupancy_pct"]),
            "tier_blocks": pd.DataFrame(columns=["tier_id", "tier_label", "start_date", "end_date", "length_days"]),
        }

    listing_start_dates = listing_start_dates or {}
    require_listing_start_dates = bool(cfg.get("require_listing_start_dates", False))
    units = sorted(df["unit_id"].dropna().astype(str).unique().tolist())
    if require_listing_start_dates:
        missing_units: list[str] = []
        invalid_units: list[str] = []
        for unit in units:
            raw = listing_start_dates.get(unit)
            if raw is None or str(raw).strip() == "":
                missing_units.append(unit)
                continue
            parsed = pd.to_datetime(raw, errors="coerce")
            if parsed is None or pd.isna(parsed):
                invalid_units.append(unit)
        if missing_units or invalid_units:
            parts = []
            if missing_units:
                parts.append(f"missing start dates for units: {', '.join(missing_units)}")
            if invalid_units:
                parts.append(f"invalid start dates for units: {', '.join(invalid_units)}")
            raise ValueError(
                "tiering.require_listing_start_dates is true; "
                + "; ".join(parts)
                + ". Add valid listing_start_date values in property config."
            )
    unit_rows = []
    for unit in units:
        unit_df = df[df["unit_id"] == unit].copy()
        if unit_df.empty:
            continue
        start_raw = listing_start_dates.get(unit)
        start_dt = pd.to_datetime(start_raw, errors="coerce") if start_raw else None
        start_date_source = "config"
        if start_dt is None or pd.isna(start_dt):
            start_date_source = "inferred_first_arrival"
            start_dt = unit_df["arrival_date"].min()
        start_dt = pd.Timestamp(start_dt).normalize()

        base = pd.DataFrame({"date": daily["date"]})
        base["unit_id"] = unit
        base["active"] = base["date"] >= start_dt
        base["listing_start_date"] = start_dt
        base["start_date_source"] = start_date_source
        base["out_of_window"] = bool(start_dt > end_date)
        # Listing-day context should be stay-night based (not arrival-only).
        # Expand each reservation to occupied nights: [arrival_date, departure_date).
        stay = unit_df[
            ["arrival_date", "departure_date", "revenue", "nights"]
        ].dropna(subset=["arrival_date", "departure_date"]).copy()
        stay = stay[stay["departure_date"] > stay["arrival_date"]].copy()
        stay["nights_safe"] = pd.to_numeric(stay["nights"], errors="coerce").fillna(0)
        stay = stay[stay["nights_safe"] > 0].copy()
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
        grp_u = (
            stay.groupby("stay_date", as_index=False).agg(
                property_revenue=("rev_per_night", "sum"),
                property_room_nights=("stay_date", "count"),
            )
            .rename(columns={"stay_date": "date"})
        )
        # Keep check-in count separately on arrival date.
        arr = unit_df.groupby("arrival_date", as_index=False).agg(bookings=("unit_id", "count")).rename(
            columns={"arrival_date": "date"}
        )
        grp_u = grp_u.merge(arr, on="date", how="left")
        grp_u["bookings"] = grp_u["bookings"].fillna(0.0)
        base = base.merge(grp_u, on="date", how="left")
        for c in ("property_revenue", "property_room_nights", "bookings"):
            base[c] = base[c].fillna(0.0)
            base.loc[~base["active"], c] = pd.NA
        base["property_revpar"] = base["property_revenue"]  # one unit per listing-day
        base["month_day"] = base["date"].dt.strftime("%m-%d")
        md = (
            base.loc[base["active"]]
            .groupby("month_day", as_index=False)
            .agg(
                listing_revpar_doy_avg=("property_revpar", "mean"),
                contributing_year_count=("date", lambda s: s.dt.year.nunique()),
                data_years_contributing=("date", lambda s: ",".join(str(int(y)) for y in sorted(s.dt.year.unique()))),
            )
        )
        base = base.merge(md, on="month_day", how="left")
        months_active = ((end_date - start_dt).days + 1) / 30.44
        base["insufficient_data"] = bool(months_active < min_active_months_for_portfolio)
        unit_rows.append(base)

    listing_daily = pd.concat(unit_rows, ignore_index=True) if unit_rows else pd.DataFrame()
    if listing_daily.empty:
        daily["tier_id"] = 1
        daily["tier_label"] = "Tier 1"
        return {
            "daily_tier_calendar": daily,
            "listing_daily_tier_calendar": pd.DataFrame(),
            "tier_summary": pd.DataFrame(columns=["tier_id", "tier_label", "days", "avg_revpar", "avg_adr", "avg_occupancy_pct"]),
            "tier_blocks": pd.DataFrame(columns=["tier_id", "tier_label", "start_date", "end_date", "length_days"]),
            "tier_diagnostics": pd.DataFrame([{"selected_method": "none", "fallback_used": True, "fallback_reason": "no_listing_daily_data"}]),
        }

    # Listing-first portfolio rollup: each day uses only eligible active listings.
    elig = listing_daily[(listing_daily["active"] == True) & (listing_daily["insufficient_data"] == False)].copy()
    active_counts = (
        listing_daily[listing_daily["active"] == True]
        .groupby("date", as_index=False)
        .agg(active_units_count=("unit_id", "nunique"))
    )
    elig["w"] = pd.to_numeric(elig["contributing_year_count"], errors="coerce").fillna(1.0)
    elig["wr"] = pd.to_numeric(elig["listing_revpar_doy_avg"], errors="coerce").fillna(0.0) * elig["w"]
    roll = (
        elig.groupby("date", as_index=False).agg(
            revenue=("property_revenue", "sum"),
            room_nights=("property_room_nights", "sum"),
            bookings=("bookings", "sum"),
            contributing_units_count=("unit_id", "nunique"),
            weight_sum=("w", "sum"),
            weighted_revpar_sum=("wr", "sum"),
        )
    )
    roll["revpar"] = roll["weighted_revpar_sum"] / roll["weight_sum"]
    roll = roll.drop(columns=["weight_sum", "weighted_revpar_sum"])
    daily = daily.merge(roll, on="date", how="left").merge(active_counts, on="date", how="left")
    daily["active_units_count"] = daily["active_units_count"].fillna(0.0)
    daily["excluded_units_count"] = (daily["active_units_count"] - daily["contributing_units_count"].fillna(0.0)).clip(lower=0)
    for c in ("revenue", "room_nights", "bookings", "contributing_units_count", "excluded_units_count", "active_units_count"):
        daily[c] = daily[c].fillna(0.0)
    daily["revpar"] = pd.to_numeric(daily["revpar"], errors="coerce").fillna(0.0)
    daily["occupancy_pct"] = (daily["room_nights"] / float(room_count) * 100).round(2) if room_count and room_count > 0 else None
    daily["adr"] = (daily["revenue"] / daily["room_nights"]).replace([float("inf"), -float("inf")], pd.NA)
    daily.loc[daily["room_nights"] == 0, "adr"] = pd.NA
    daily["adr"] = daily["adr"].round(2)

    daily["revpar_smoothed"] = (
        daily["revpar"]
        .rolling(window=window_days, min_periods=max(3, window_days // 2), center=True)
        .mean()
        .fillna(daily["revpar"])
    )
    # Composition-shift transparency: smoothing can cross denominator changes.
    daily["contributing_units_min_14d"] = (
        daily["contributing_units_count"]
        .rolling(window=window_days, min_periods=max(3, window_days // 2), center=True)
        .min()
        .fillna(daily["contributing_units_count"])
    )
    daily["contributing_units_max_14d"] = (
        daily["contributing_units_count"]
        .rolling(window=window_days, min_periods=max(3, window_days // 2), center=True)
        .max()
        .fillna(daily["contributing_units_count"])
    )
    daily["composition_shift_flag"] = daily["contributing_units_min_14d"] != daily["contributing_units_max_14d"]
    daily["revpar_percentile"] = daily["revpar_smoothed"].rank(method="average", pct=True) * 100.0
    # Gap detection uses integer percentile buckets (0-100) to avoid ultra-fine adjacent-score spacing.
    daily["revpar_percentile_int"] = daily["revpar_percentile"].round().clip(0, 100)
    revpar_std = float(daily["revpar_smoothed"].std(ddof=0) or 0.0)
    if revpar_std > 0:
        daily["revpar_zscore"] = (daily["revpar_smoothed"] - float(daily["revpar_smoothed"].mean())) / revpar_std
    else:
        daily["revpar_zscore"] = 0.0

    method = "gap_detection"
    diagnostics: dict[str, Any] = {
        "selected_method": None,
        "fallback_used": False,
        "fallback_reason": "",
        "gap_score_basis": gap_score_basis,
        "gap_threshold_pct": gap_threshold_pct,
        "gap_threshold_zscore": gap_threshold_z,
        "min_tiers": min_tiers,
        "max_tiers": max_tiers,
        "quantile_fallback_tiers": fallback_tiers,
        "min_days_per_tier": min_days_per_tier,
        "smoothing_window_days": window_days,
        "n_days": int(len(daily)),
    }
    diagnostics["n_unique_percentiles_raw"] = int(daily["revpar_percentile"].nunique())
    diagnostics["n_unique_percentiles_int"] = int(daily["revpar_percentile_int"].nunique())
    diagnostics["n_unique_zscore"] = int(daily["revpar_zscore"].nunique())
    diagnostics["require_listing_start_dates"] = require_listing_start_dates
    diagnostics["units_total"] = int(listing_daily["unit_id"].nunique())
    diagnostics["units_out_of_window"] = int(
        listing_daily.loc[listing_daily["out_of_window"] == True, "unit_id"].nunique()
    )
    diagnostics["units_inferred_start_date"] = int(
        listing_daily.loc[listing_daily["start_date_source"] == "inferred_first_arrival", "unit_id"].nunique()
    )
    diagnostics["avg_excluded_units_per_day"] = round(float(daily["excluded_units_count"].mean()), 2)
    diagnostics["composition_shift_days"] = int(daily["composition_shift_flag"].sum())

    # Compare gap distributions for both bases, regardless of selected basis.
    uniq_pct = pd.Series(sorted(set(float(x) for x in daily["revpar_percentile_int"].dropna().tolist())))
    pct_gaps = (uniq_pct.shift(-1) - uniq_pct).dropna() if len(uniq_pct) > 1 else pd.Series(dtype=float)
    diagnostics["max_gap_percentile_int"] = round(float(pct_gaps.max()), 4) if len(pct_gaps) else 0.0
    diagnostics["qualifying_gaps_percentile_int"] = int((pct_gaps >= gap_threshold_pct).sum()) if len(pct_gaps) else 0
    diagnostics["candidate_tiers_percentile_int"] = int(diagnostics["qualifying_gaps_percentile_int"] + 1)
    uniq_z = pd.Series(sorted(set(float(x) for x in daily["revpar_zscore"].dropna().tolist())))
    z_gaps = (uniq_z.shift(-1) - uniq_z).dropna() if len(uniq_z) > 1 else pd.Series(dtype=float)
    diagnostics["max_gap_zscore"] = round(float(z_gaps.max()), 4) if len(z_gaps) else 0.0
    diagnostics["qualifying_gaps_zscore"] = int((z_gaps >= gap_threshold_z).sum()) if len(z_gaps) else 0
    diagnostics["candidate_tiers_zscore"] = int(diagnostics["qualifying_gaps_zscore"] + 1)

    if gap_score_basis == "revpar_zscore":
        gap_score_ser = daily["revpar_zscore"]
        gap_threshold_use = gap_threshold_z
    else:
        gap_score_ser = daily["revpar_percentile_int"]
        gap_threshold_use = gap_threshold_pct

    edges = _build_tier_edges_from_gap_detection(
        gap_score_ser, gap_threshold_pct=gap_threshold_use, min_tiers=min_tiers, max_tiers=max_tiers
    )
    if edges is not None:
        diagnostics["gap_candidate_tiers"] = int(len(edges))
        tier_id = _assign_tier_from_edges(gap_score_ser, edges)
    else:
        method = "quantile_fallback"
        diagnostics["fallback_used"] = True
        diagnostics["fallback_reason"] = "gap_detection_invalid_or_out_of_bounds"
        tier_id = _assign_tier_quantile(daily["revpar_percentile"], fallback_tiers)

    # Enforce minimum days per tier; fallback if invalid.
    valid = tier_id is not None and tier_id.notna().all()
    if valid:
        counts = tier_id.value_counts(dropna=True)
        diagnostics["tier_count_before_min_days_check"] = int(len(counts))
        diagnostics["min_days_observed_before_check"] = int(counts.min()) if len(counts) else 0
        if (counts < min_days_per_tier).any():
            valid = False
    if not valid:
        method = "quantile_fallback"
        diagnostics["fallback_used"] = True
        if not diagnostics["fallback_reason"]:
            diagnostics["fallback_reason"] = "min_days_per_tier_failed_or_invalid_assignment"
        tier_id = _assign_tier_quantile(daily["revpar_percentile"], fallback_tiers)

    if tier_id is None or tier_id.isna().any():
        # Final defensive fallback: median split
        method = "median_fallback"
        diagnostics["fallback_used"] = True
        diagnostics["fallback_reason"] = "quantile_fallback_failed"
        med = float(daily["revpar_percentile"].median())
        tier_id = pd.Series(1, index=daily.index, dtype="Int64")
        tier_id.loc[daily["revpar_percentile"] > med] = 2

    daily["base_tier_id"] = tier_id.astype(int)
    daily["base_tier_label"] = daily["base_tier_id"].map(lambda t: f"Tier {int(t)}")
    # Optional property-level overrides:
    # - tier_label_map: {1: "Soft", 2: "Low", ...}
    # - tier_merge_map: {1: 1, 2: 1, 3: 2, ...}  (source_tier -> target_tier)
    label_map_cfg = cfg.get("tier_label_map") or {}
    merge_map_cfg = cfg.get("tier_merge_map") or {}
    merge_map: dict[int, int] = {}
    for k, v in merge_map_cfg.items():
        try:
            merge_map[int(k)] = int(v)
        except (TypeError, ValueError):
            continue
    daily["tier_id"] = daily["base_tier_id"].map(lambda x: merge_map.get(int(x), int(x))).astype(int)
    label_map: dict[int, str] = {}
    for k, v in label_map_cfg.items():
        try:
            label_map[int(k)] = str(v).strip()
        except (TypeError, ValueError):
            continue
    if not label_map:
        label_map = _default_tier_labels_for_count(int(daily["tier_id"].nunique()))
    daily["tier_label"] = daily["tier_id"].map(lambda t: label_map.get(int(t), f"Tier {int(t)}"))
    daily["tier_method"] = method

    summary = daily.groupby(["tier_id", "tier_label"], as_index=False).agg(
        days=("date", "count"),
        avg_revpar=("revpar", "mean"),
        min_revpar=("revpar", "min"),
        max_revpar=("revpar", "max"),
        avg_adr=("adr", "mean"),
        avg_occupancy_pct=("occupancy_pct", "mean"),
        min_percentile=("revpar_percentile", "min"),
        max_percentile=("revpar_percentile", "max"),
    )
    summary["share_of_days_pct"] = (summary["days"] / float(len(daily)) * 100.0).round(2)
    for c in ("avg_revpar", "min_revpar", "max_revpar", "avg_adr", "avg_occupancy_pct", "min_percentile", "max_percentile"):
        if c in summary.columns:
            summary[c] = summary[c].round(2)
    summary = summary.sort_values("tier_id")

    # contiguous blocks by tier
    tmp = daily[["date", "tier_id", "tier_label"]].copy()
    tmp["block_id"] = (tmp["tier_id"] != tmp["tier_id"].shift(1)).cumsum()
    blocks = tmp.groupby(["block_id", "tier_id", "tier_label"], as_index=False).agg(
        start_date=("date", "min"),
        end_date=("date", "max"),
        length_days=("date", "count"),
    )
    blocks = blocks.drop(columns=["block_id"]).sort_values(["start_date", "tier_id"])
    # propagate final tier labels to listing-day rows for tier-season downstream analysis
    if not listing_daily.empty and "date" in listing_daily.columns:
        listing_daily = listing_daily.merge(
            daily[["date", "tier_id", "tier_label"]],
            on="date",
            how="left",
            suffixes=("", "_portfolio"),
        )

    diagnostics["selected_method"] = method
    diagnostics["overrides_applied"] = bool(label_map or merge_map)
    diagnostics["merge_rules_count"] = int(len(merge_map))
    diagnostics["label_rules_count"] = int(len(label_map))
    diagnostics["base_tier_count"] = int(daily["base_tier_id"].nunique())
    diagnostics["final_tier_count"] = int(daily["tier_id"].nunique())
    diagnostics["final_min_days_per_tier"] = int(daily["tier_id"].value_counts().min())
    diagnostics_df = pd.DataFrame([diagnostics])

    daily_out_cols = [
        "date",
        "revenue",
        "room_nights",
        "bookings",
        "active_units_count",
        "contributing_units_count",
        "excluded_units_count",
        "adr",
        "occupancy_pct",
        "revpar",
        "revpar_smoothed",
        "contributing_units_min_14d",
        "contributing_units_max_14d",
        "composition_shift_flag",
        "revpar_percentile",
        "base_tier_id",
        "base_tier_label",
        "tier_id",
        "tier_label",
        "tier_method",
    ]
    return {
        "daily_tier_calendar": daily[daily_out_cols].copy(),
        "listing_daily_tier_calendar": listing_daily.copy(),
        "tier_summary": summary.copy(),
        "tier_blocks": blocks.copy(),
        "tier_diagnostics": diagnostics_df.copy(),
    }


def build_tier_leadtime_pricing_integrity(
    df: pd.DataFrame,
    daily_tier_calendar: pd.DataFrame,
    room_count: Optional[int] = None,
    bands: Optional[list[tuple[int, int, str]]] = None,
) -> pd.DataFrame:
    """
    Operational pricing integrity table:
    tier_label x lead_band with ADR, revenue, share, and booking count.
    Join is arrival-date based (booking behavior relative to arrival day tier).
    """
    if df.empty or daily_tier_calendar.empty:
        return pd.DataFrame(
            columns=[
                "tier_id",
                "tier_label",
                "lead_band",
                "adr",
                "revenue",
                "revenue_share_of_tier",
                "booking_count",
                "room_nights",
                "room_night_share_of_tier",
                "occupancy_pct_of_tier_capacity",
                "tier_occupancy_pct",
            ]
        )
    bands = bands or DEFAULT_BOOKING_WINDOW_BANDS
    req = {"arrival_date", "lead_time_days", "revenue", "nights"}
    if not req.issubset(set(df.columns)):
        return pd.DataFrame(
            columns=[
                "tier_id",
                "tier_label",
                "lead_band",
                "adr",
                "revenue",
                "revenue_share_of_tier",
                "booking_count",
                "room_nights",
                "room_night_share_of_tier",
                "occupancy_pct_of_tier_capacity",
                "tier_occupancy_pct",
            ]
        )
    x = df.copy()
    x["arrival_date"] = pd.to_datetime(x["arrival_date"], errors="coerce")
    x = x.dropna(subset=["arrival_date"]).copy()
    x["lead_band"] = _assign_booking_window(pd.to_numeric(x["lead_time_days"], errors="coerce"), bands)
    x["revenue"] = pd.to_numeric(x["revenue"], errors="coerce")
    x["nights"] = pd.to_numeric(x["nights"], errors="coerce")
    x = x.dropna(subset=["lead_band", "revenue", "nights"]).copy()
    x = x[x["nights"] > 0].copy()

    d = daily_tier_calendar.copy()
    d["date"] = pd.to_datetime(d["date"], errors="coerce")
    d = d.dropna(subset=["date"]).copy()
    join_cols = [c for c in ["date", "tier_id", "tier_label"] if c in d.columns]
    d = d[join_cols].rename(columns={"date": "arrival_date"})

    m = x.merge(d, on="arrival_date", how="left")
    m = m.dropna(subset=["tier_id", "tier_label"]).copy()

    grp = (
        m.groupby(["tier_id", "tier_label", "lead_band"], as_index=False)
        .agg(
            revenue=("revenue", "sum"),
            room_nights=("nights", "sum"),
            booking_count=("arrival_date", "count"),
        )
    )
    grp["adr"] = grp["revenue"] / grp["room_nights"]
    grp.loc[grp["room_nights"] <= 0, "adr"] = pd.NA
    tier_totals = grp.groupby(["tier_id", "tier_label"], as_index=False).agg(
        tier_revenue=("revenue", "sum"),
        tier_room_nights=("room_nights", "sum"),
    )
    grp = grp.merge(tier_totals, on=["tier_id", "tier_label"], how="left")
    grp["revenue_share_of_tier"] = grp["revenue"] / grp["tier_revenue"]
    grp["room_night_share_of_tier"] = grp["room_nights"] / grp["tier_room_nights"]

    # Occupancy context by tier capacity:
    # denominator = days in tier * room_count (if room_count is available).
    if room_count and room_count > 0 and {"tier_id", "date"}.issubset(set(daily_tier_calendar.columns)):
        tier_days = (
            daily_tier_calendar.groupby("tier_id", as_index=False)
            .agg(tier_days=("date", "count"))
        )
        grp = grp.merge(tier_days, on="tier_id", how="left")
        grp["tier_capacity_room_nights"] = pd.to_numeric(grp["tier_days"], errors="coerce") * float(room_count)
        grp["occupancy_pct_of_tier_capacity"] = (grp["room_nights"] / grp["tier_capacity_room_nights"]) * 100.0
        grp["tier_occupancy_pct"] = (grp["tier_room_nights"] / grp["tier_capacity_room_nights"]) * 100.0
    else:
        grp["occupancy_pct_of_tier_capacity"] = pd.NA
        grp["tier_occupancy_pct"] = pd.NA

    band_order = [label for _, _, label in bands]
    grp["lead_band"] = pd.Categorical(grp["lead_band"], categories=band_order, ordered=True)
    grp = grp.sort_values(["tier_id", "lead_band"]).reset_index(drop=True)
    grp["revenue"] = grp["revenue"].round(2)
    grp["adr"] = pd.to_numeric(grp["adr"], errors="coerce").round(2)
    grp["revenue_share_of_tier"] = (pd.to_numeric(grp["revenue_share_of_tier"], errors="coerce") * 100.0).round(2)
    grp["room_nights"] = pd.to_numeric(grp["room_nights"], errors="coerce").round(2)
    grp["room_night_share_of_tier"] = (pd.to_numeric(grp["room_night_share_of_tier"], errors="coerce") * 100.0).round(2)
    grp["occupancy_pct_of_tier_capacity"] = pd.to_numeric(grp["occupancy_pct_of_tier_capacity"], errors="coerce").round(2)
    grp["tier_occupancy_pct"] = pd.to_numeric(grp["tier_occupancy_pct"], errors="coerce").round(2)
    return grp[
        [
            "tier_id",
            "tier_label",
            "lead_band",
            "adr",
            "revenue",
            "revenue_share_of_tier",
            "booking_count",
            "room_nights",
            "room_night_share_of_tier",
            "occupancy_pct_of_tier_capacity",
            "tier_occupancy_pct",
        ]
    ]


def _resolve_monthly_combined_rank_filters(
    analysis_window: Optional[dict[str, Any]],
    monthly_performance_combined_cfg: Optional[dict[str, Any]],
    *,
    canonical_max_arrival: Optional[pd.Timestamp] = None,
) -> tuple[Optional[pd.Timestamp], Optional[pd.Timestamp], Optional[float]]:
    """
    Returns (min_arrival, max_arrival, provisional_ratio) for combined month ranking only.
    max_arrival is None when no tighter bound than analysis end_date is requested.
    """
    mc = monthly_performance_combined_cfg or {}
    _, end_date = resolve_analysis_window(
        analysis_window or {}, canonical_max_arrival=canonical_max_arrival
    )
    end_norm = pd.Timestamp(end_date).normalize()

    min_raw = pd.to_datetime(mc.get("min_arrival_date"), errors="coerce")
    min_ad = None if pd.isna(min_raw) else pd.Timestamp(min_raw).normalize()

    combined_max: Optional[pd.Timestamp] = None
    tighten = False
    u = end_norm
    if bool(mc.get("cap_max_arrival_at_today")):
        u = min(u, pd.Timestamp.today().normalize())
        tighten = True
    max_cfg_raw = pd.to_datetime(mc.get("max_arrival_date"), errors="coerce")
    if not pd.isna(max_cfg_raw):
        u = min(u, pd.Timestamp(max_cfg_raw).normalize())
        tighten = True
    if tighten:
        combined_max = u

    prov_raw = mc.get("provisional_bookings_vs_hist_ratio")
    prov_ratio: Optional[float] = None
    if prov_raw is not None:
        try:
            pr = float(prov_raw)
            if 0 < pr <= 1:
                prov_ratio = pr
        except (TypeError, ValueError):
            prov_ratio = None

    return min_ad, combined_max, prov_ratio


def run_analysis(
    df: pd.DataFrame,
    room_count: Optional[int] = None,
    booking_window_bands: Optional[list[tuple[int, int, str]]] = None,
    tiering_cfg: Optional[dict[str, Any]] = None,
    listing_start_dates: Optional[dict[str, str]] = None,
    analysis_window: Optional[dict[str, Any]] = None,
    listing_unit_counts: Optional[dict[str, int]] = None,
    listing_capacity_fallback: Optional[dict[str, str]] = None,
    monthly_performance_combined_cfg: Optional[dict[str, Any]] = None,
    capacity_schedule: Optional[list[dict[str, Any]]] = None,
) -> dict[str, pd.DataFrame]:
    """
    Run all analysis tables on canonical df (full export is fine; rows are clipped to the
    resolved analysis window inside this function).

    Returns dict of table_name -> DataFrame.

    listing_unit_counts: {unit_id: physical_unit_count} — forwarded to build_overall_summary
        so per-listing occupancy and RevPAR use the correct availability denominator for
        properties where one unit_id represents multiple physical units (e.g. wmb).

    monthly_performance_combined_cfg: optional dict (from property yaml
        ``monthly_performance_combined``) with min_arrival_date, cap_max_arrival_at_today,
        max_arrival_date, provisional_bookings_vs_hist_ratio — affects only
        ``monthly_performance_combined`` output.
    """
    df = prepare_canonical_for_analysis(df)
    max_arr: Optional[pd.Timestamp] = None
    if not df.empty and "arrival_date" in df.columns and df["arrival_date"].notna().any():
        max_arr = pd.Timestamp(df["arrival_date"].max()).normalize()
    start_date, end_date = resolve_analysis_window(
        analysis_window, canonical_max_arrival=max_arr
    )
    df = filter_canonical_to_period(df, start_date, end_date)
    mc_min, mc_max, mc_prov = _resolve_monthly_combined_rank_filters(
        analysis_window,
        monthly_performance_combined_cfg,
        canonical_max_arrival=max_arr,
    )
    tier_tables = build_daily_tier_outputs(
        df,
        room_count=room_count,
        tiering_cfg=tiering_cfg,
        listing_start_dates=listing_start_dates,
        analysis_window=analysis_window,
        canonical_max_arrival=max_arr,
    )

    # Tool-standard behavior: listing seasonality always follows the discovered
    # tier calendar for consistency across properties.
    listing_season_df = build_listing_season_performance_from_tiers(
        tier_tables["listing_daily_tier_calendar"]
    )

    out = {
        "overall_summary": build_overall_summary(
            df,
            room_count,
            analysis_window=analysis_window,
            listing_unit_counts=listing_unit_counts,
            listing_start_dates=listing_start_dates,
            listing_capacity_fallback=listing_capacity_fallback,
            capacity_schedule=capacity_schedule,
            canonical_max_arrival=max_arr,
        ),
        "monthly_performance": build_monthly_performance(
            df,
            room_count,
            listing_start_dates=listing_start_dates,
            listing_unit_counts=listing_unit_counts,
            capacity_schedule=capacity_schedule,
        ),
        "monthly_performance_combined": build_monthly_performance_combined(
            df,
            room_count,
            listing_start_dates=listing_start_dates,
            listing_unit_counts=listing_unit_counts,
            combined_min_arrival_date=mc_min,
            combined_max_arrival_date=mc_max,
            provisional_bookings_ratio=mc_prov,
            capacity_schedule=capacity_schedule,
        ),
        "listing_season_performance": listing_season_df,
        "channel_by_year": build_channel_by_year(df),
        "channel_summary": build_channel_summary(df),
        "channel_by_listing": build_channel_by_listing(df),
        "by_day_of_week": build_by_day_of_week(df),
        "booking_window": build_booking_window(df, bands=booking_window_bands),
        "adr_by_listing_by_month": build_adr_by_listing_by_month(df),
        "daily_tier_calendar": tier_tables["daily_tier_calendar"],
        "listing_daily_tier_calendar": tier_tables["listing_daily_tier_calendar"],
        "tier_summary": tier_tables["tier_summary"],
        "tier_blocks": tier_tables["tier_blocks"],
        "tier_diagnostics": tier_tables["tier_diagnostics"],
        "tier_leadtime_pricing_integrity": build_tier_leadtime_pricing_integrity(
            df,
            tier_tables["daily_tier_calendar"],
            room_count=room_count,
            bands=booking_window_bands,
        ),
    }
    # Optional helper table for the requested 2026 Q1 period if present in window.
    q1_start = pd.Timestamp(year=2026, month=1, day=1)
    q1_end = pd.Timestamp(year=2026, month=3, day=31)
    if start_date <= q1_end and end_date >= q1_start:
        out["period_summary_2026_q1"] = build_period_summary(
            df,
            room_count=room_count,
            period_start=max(start_date, q1_start),
            period_end=min(end_date, q1_end),
            listing_unit_counts=listing_unit_counts,
            listing_start_dates=listing_start_dates,
            period_label="2026_Q1",
            listing_capacity_fallback=listing_capacity_fallback,
            capacity_schedule=capacity_schedule,
        )
    return out
