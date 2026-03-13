"""
PMS-agnostic parser: loads and maps raw export data to canonical schema.
Dispatches by pms_id (from property config); each PMS has its own loader and mapper.
"""
from __future__ import annotations

import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable, Optional

import pandas as pd

# ---------------------------------------------------------------------------
# Canonical schema (same for all PMSs)
# ---------------------------------------------------------------------------

def canonical_columns() -> list[str]:
    """Column order for canonical output (same for all properties/PMS)."""
    return [
        "reservation_id",
        "arrival_date",
        "departure_date",
        "nights",
        "unit_id",
        "guest_name",
        "revenue",
        "amount_paid",
        "booking_date",
        "lead_time_days",
        "adr",
        "channel",
        "arrival_day_of_week",
        "arrival_year_month",
    ]


# ---------------------------------------------------------------------------
# ResNexus implementation (one PMS type)
# ---------------------------------------------------------------------------

_RESNEXUS_REQUIRED = [
    "Res#", "Date", "Guest", "Unit", "Amount", "Reserved On",
]
_RESNEXUS_CHANNEL = [
    "Booked Online", "GDS", "Trip Connect", "MyAllocator", "Direct Connect",
]
_DATE_FORMAT = "%m/%d/%Y"


def _parse_date_resnexus(s: str) -> Optional[datetime]:
    """Parse M/D/YYYY or M/D/YY to datetime."""
    if pd.isna(s) or not str(s).strip():
        return None
    s = str(s).strip()
    try:
        return datetime.strptime(s, _DATE_FORMAT)
    except ValueError:
        pass
    try:
        return datetime.strptime(s, "%m/%d/%y")
    except ValueError:
        return None


def _parse_date_range_resnexus(date_str: str) -> tuple[Optional[datetime], Optional[datetime], int]:
    """Parse Date: M/D/YYYY, M/D-M/D/YYYY, or M/D/YYYY-M/D/YYYY. Returns (arrival, departure, nights)."""
    if pd.isna(date_str) or not str(date_str).strip():
        return None, None, 0
    s = str(date_str).strip()
    range_two_years = re.match(r"(\d{1,2})/(\d{1,2})/(\d{4})-(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if range_two_years:
        m1, d1, y1, m2, d2, y2 = map(int, range_two_years.groups())
        arrival = datetime(y1, m1, d1)
        last_night = datetime(y2, m2, d2)
        if last_night < arrival:
            return None, None, 0
        nights = (last_night - arrival).days + 1
        return arrival, last_night + timedelta(days=1), nights
    range_match = re.match(r"(\d{1,2})/(\d{1,2})-(\d{1,2})/(\d{1,2})/(\d{4})$", s)
    if range_match:
        m1, d1, m2, d2, y = map(int, range_match.groups())
        arrival = datetime(y, m1, d1)
        last_night = datetime(y, m2, d2)
        if last_night < arrival:
            return None, None, 0
        nights = (last_night - arrival).days + 1
        return arrival, last_night + timedelta(days=1), nights
    arrival = _parse_date_resnexus(s)
    if arrival is None:
        return None, None, 0
    return arrival, arrival + timedelta(days=1), 1


def _parse_currency(val) -> Optional[float]:
    if pd.isna(val):
        return None
    s = str(val).strip()
    if s in ("--", ""):
        return None
    s = s.replace("$", "").replace(",", "").strip()
    try:
        return float(s)
    except ValueError:
        return None


def _derive_channel_resnexus(row: pd.Series) -> str:
    for col in _RESNEXUS_CHANNEL:
        if col in row.index and str(row.get(col, "")).strip().upper() == "X":
            return col
    return "Other"


def _resnexus_load_csv(path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    return pd.read_csv(path, encoding="utf-8")


def _resnexus_map_to_canonical(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.copy()
    if "Unit" in df.columns:
        df["unit_id"] = df["Unit"].astype(str).str.strip()
    else:
        df["unit_id"] = ""
    df["reservation_id"] = df["Res#"].astype(str).str.strip()
    df["guest_name"] = df["Guest"].astype(str).replace("nan", "")
    dates = df["Date"].apply(_parse_date_range_resnexus)
    df["arrival_date"] = [d[0] for d in dates]
    df["departure_date"] = [d[1] for d in dates]
    df["nights"] = [d[2] for d in dates]
    df["revenue"] = df["Amount"].apply(_parse_currency)
    df["amount_paid"] = df["Paid"].apply(_parse_currency)
    df["booking_date"] = df["Reserved On"].apply(_parse_date_resnexus)
    df["channel"] = df.apply(_derive_channel_resnexus, axis=1)
    df["lead_time_days"] = df.apply(
        lambda r: (r["arrival_date"] - r["booking_date"]).days
        if pd.notna(r["arrival_date"]) and pd.notna(r["booking_date"]) else None,
        axis=1,
    )
    df["adr"] = df.apply(
        lambda r: (r["revenue"] / r["nights"]) if (r["nights"] and r["nights"] > 0 and r["revenue"] is not None) else None,
        axis=1,
    )
    if pd.api.types.is_datetime64_any_dtype(df["arrival_date"]):
        df["arrival_day_of_week"] = df["arrival_date"].dt.day_name()
        df["arrival_year_month"] = df["arrival_date"].dt.strftime("%Y-%m")
    else:
        df["arrival_day_of_week"] = ""
        df["arrival_year_month"] = ""
    return df


# ---------------------------------------------------------------------------
# PMS registry and public API (property-agnostic: driven by pms_id)
# ---------------------------------------------------------------------------

_PMS_PARSERS: dict[str, dict[str, Any]] = {
    "resnexus": {
        "required_columns": _RESNEXUS_REQUIRED,
        "channel_columns": _RESNEXUS_CHANNEL,
        "load_csv": _resnexus_load_csv,
        "map_to_canonical": _resnexus_map_to_canonical,
    },
}


def list_pms_ids() -> list[str]:
    """Return registered pms_id values (e.g. ['resnexus'])."""
    return list(_PMS_PARSERS.keys())


def get_required_columns(pms_id: str) -> list[str]:
    """Required columns in raw data for this PMS. Used by validation."""
    if pms_id not in _PMS_PARSERS:
        raise ValueError(f"Unknown pms_id: {pms_id}. Available: {list_pms_ids()}")
    return _PMS_PARSERS[pms_id]["required_columns"]


def get_channel_columns(pms_id: str) -> list[str]:
    """Channel columns in raw data for this PMS. Used by validation."""
    if pms_id not in _PMS_PARSERS:
        raise ValueError(f"Unknown pms_id: {pms_id}. Available: {list_pms_ids()}")
    return _PMS_PARSERS[pms_id]["channel_columns"]


def load_csv(path, pms_id: str) -> pd.DataFrame:
    """Load raw CSV for the given PMS format (from property's pms_id)."""
    if pms_id not in _PMS_PARSERS:
        raise ValueError(f"Unknown pms_id: {pms_id}. Available: {list_pms_ids()}")
    return _PMS_PARSERS[pms_id]["load_csv"](path)


def map_to_canonical(raw: pd.DataFrame, pms_id: str) -> pd.DataFrame:
    """Map raw dataframe to canonical schema using the parser for this PMS."""
    if pms_id not in _PMS_PARSERS:
        raise ValueError(f"Unknown pms_id: {pms_id}. Available: {list_pms_ids()}")
    return _PMS_PARSERS[pms_id]["map_to_canonical"](raw)


def normalize(
    raw: pd.DataFrame,
    pms_id: str,
    exclude_unpaid_below_amount: bool = True,
    exclude_invalid_revenue: bool = True,
) -> pd.DataFrame:
    """Map to canonical and exclude invalid/unpaid rows. Uses parser for this PMS."""
    df = map_to_canonical(raw, pms_id)
    if exclude_unpaid_below_amount:
        mask = df["amount_paid"].notna() & (df["amount_paid"] >= df["revenue"].fillna(0))
        df = df.loc[mask].copy()
    if exclude_invalid_revenue:
        df = df.loc[df["revenue"].notna() & (df["revenue"] >= 0)].copy()
    df = df.loc[df["nights"].notna() & (df["nights"] >= 1)].copy()
    cols = [c for c in canonical_columns() if c in df.columns]
    return df[cols]


# Backward compatibility: ResNexus-specific names (delegate to pms_id)
def load_resnexus_csv(path) -> pd.DataFrame:
    """Deprecated: use load_csv(path, 'resnexus') or run with --property and property's pms_id."""
    return load_csv(path, "resnexus")
