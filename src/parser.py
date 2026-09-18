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
        "listing_group",
        "arrival_day_of_week",
        "arrival_year_month",
    ]


# Exact + prefix rule: US/UK spellings, pending states, and any status starting with "cancel".
_EXCLUDED_CANONICAL_RESERVATION_STATUSES_EXACT: frozenset[str] = frozenset(
    {
        "cancelled",
        "canceled",
        "cancellation pending",
        "confirmation pending",
        "void",
        "declined",
        # Guesty-style non-stay rows (also excluded when STATUS maps here)
        "expired",
        "inquiry",
        "inquirynotpossible",
        "ownerstay",
        "owner stay",
    }
)


def reservation_status_is_excluded(status_val: object) -> bool:
    """
    True if this reservation status must not appear in canonical or analysis.

    Used for Cloudbeds, Track, and any canonical rows that carry an optional status column.
    """
    s = str(status_val or "").strip().lower()
    if not s or s == "nan":
        return False
    if s in _EXCLUDED_CANONICAL_RESERVATION_STATUSES_EXACT:
        return True
    if s.startswith("cancel"):
        return True
    return False


# ---------------------------------------------------------------------------
# ResNexus implementation (one PMS type)
# ---------------------------------------------------------------------------

_RESNEXUS_REQUIRED = [
    "Res#", "Date", "Guest", "Unit", "Amount", "Reserved On",
]
_RESNEXUS_CHANNEL = [
    "Booked Online", "GDS", "Trip Connect", "MyAllocator", "Direct Connect",
]
# Wider ResNexus / LaFave dashboard export: separate stay dates + explicit channel.
_RESNEXUS_SHEET_REQUIRED = [
    "Res#",
    "Guest",
    "Arrival",
    "Departure",
    "Amount",
    "Reservation Date",
    "# Nights",
    "Listing Name",
]
_RESNEXUS_SHEET_CHANNEL = [
    "Channel",
    "By Phone",
    "Booked Online",
    "3rd Party",
]
_DATE_FORMAT = "%m/%d/%Y"
_HOSTAWAY_LISTING_COLUMNS = ("Listing Name", "Listing.1", "Listing")
_HOSTAWAY_REQUIRED = [
    "Guest",
    "Payment status",
    "Check-in date",
    "Check-out date",
    "Reservation date",
    "rentalRevenue",
]
_HOSTAWAY_CHANNEL = [
    "Channel.1",  # normalized channel values after pandas de-duplicates headers
    "Channel",
]
_CLOUDBEDS_REQUIRED = [
    "Reservation Number",
    "Check in Date",
    "Check out Date",
    "Nights",
    "Room Type",
    "Revenue",
    "Status",
]
_CLOUDBEDS_CHANNEL = [
    "Channel Group",
    "Channel",
    "Source",
    "Y",
]
_TRACK_UNIT_COLUMNS = ("Listing Name", "Unit Name")
_TRACK_REQUIRED = [
    "Res. #",
    "Check-In",
    "Checkout",
    "Nights",
    "Rev",
    "Channel",
]
_TRACK_CHANNEL = [
    "Channel",
]
_GUESTY_REQUIRED = [
    "CREATION DATE",
    "CHECK-IN DATE",
    "CHECK-OUT DATE",
    "NUMBER OF NIGHTS",
    "LISTING'S NICKNAME",
    "Revenue",
    "SOURCE",
    "STATUS",
]
_GUESTY_CHANNEL = [
    "Channel",
    "SOURCE",
]
# OwnerRez / Oasi dashboard export (e.g. Spoon Mountain).
_OWNERREZ_REQUIRED = [
    "Booking #",
    "Property",
    "Guest",
    "Booked",
    "Arrival",
    "Departure",
    "N",
    "Rent",
    "Revenue",
    "Booking Window",
]
_OWNERREZ_CHANNEL = [
    "Channel",
    "Listing Site",
]
# Unknown PMS — "Reservations with Financials" style export (e.g. Adventure Inn Durango).
# Not Cloudbeds: dedicated loader/mapper; do not reuse cloudbeds column expectations.
_UNKNOWN_PMS_REQUIRED = [
    "Reservation Number",
    "Check-In Date",
    "Check-Out Date",
    "Room Revenue Total",
    "Reservation Status",
]
_UNKNOWN_PMS_CHANNEL = [
    "Reservation Source",
    "Reservation Source Category",
]
_UNKNOWN_PMS_ROOM_CODE_TO_TYPE = {
    "STQ": "Standard Queen",
    "STK": "Standard King",
    "DQ": "Double Queen",
    "SMQ": "Small Queen",
    "STQK": "Standard Queen with Kitchen",
}
_PROPERTIES_DIR = Path(__file__).resolve().parent.parent / "config" / "properties"
_PROPERTY_LISTING_MAP_CACHE: dict[str, dict[str, str]] = {}


def _normalize_channel_label(raw: object) -> str:
    s = str(raw or "").strip()
    if not s:
        return "Unknown"
    key = s.lower()
    # Canonical OTA / direct channel grouping.
    if "airbnb" in key:
        return "Airbnb"
    if "vrbo" in key:
        return "VRBO"
    if "booking.com" in key:
        return "Booking.com"
    if "expedia" in key:
        return "Expedia"
    if "website" in key or key in {"manual", "direct", "pms"}:
        return "Direct"
    if "owner guest" in key:
        return "Owner Guest"
    if "influencer" in key or "giveaway" in key:
        return "Influencers"
    if key == "phone":
        return "Phone"
    if key == "email":
        return "Email"
    if key == "guest":
        return "Guest"
    return s


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


def resnexus_raw_format(raw: pd.DataFrame) -> str:
    """
    Detect which ResNexus CSV layout is in use.
    Returns 'legacy' (Date + Unit + Reserved On), 'sheet' (Arrival/Departure + Listing Name), or 'unknown'.
    """
    cols = set(raw.columns)
    if all(c in cols for c in _RESNEXUS_REQUIRED):
        return "legacy"
    if all(c in cols for c in _RESNEXUS_SHEET_REQUIRED):
        return "sheet"
    return "unknown"


def _derive_channel_resnexus_sheet(row: pd.Series) -> str:
    """Channel for sheet export: use Channel when specific; else X-markers on phone/OTA columns."""
    raw_ch = ""
    if "Channel" in row.index:
        raw_ch = str(row.get("Channel", "")).strip()
    if raw_ch and raw_ch.lower() not in {"", "nan", "other"}:
        return _normalize_channel_label(raw_ch)
    for col in ("Booked Online", "By Phone", "3rd Party"):
        if col in row.index and str(row.get(col, "")).strip().upper() == "X":
            return col
    if raw_ch and raw_ch.lower() not in {"", "nan"}:
        return _normalize_channel_label(raw_ch)
    return "Other"


def _resnexus_load_csv(path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    try:
        return pd.read_csv(path, encoding="utf-8")
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="latin1")


def _hostaway_load_csv(path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    try:
        return pd.read_csv(path, encoding="utf-8")
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="latin1")


def _cloudbeds_load_csv(path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    try:
        df = pd.read_csv(path, encoding="utf-8")
    except UnicodeDecodeError:
        df = pd.read_csv(path, encoding="latin1")
    # Some Cloudbeds dashboard exports use uppercase REVENUE; parser expects Revenue.
    if "REVENUE" in df.columns and "Revenue" not in df.columns:
        df = df.rename(columns={"REVENUE": "Revenue"})
    return df


def _track_load_csv(path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    try:
        return pd.read_csv(path, encoding="utf-8")
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="latin1")


def _guesty_load_csv(path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    try:
        return pd.read_csv(path, encoding="utf-8")
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="latin1")


def _ownerrez_load_csv(path) -> pd.DataFrame:
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    try:
        return pd.read_csv(path, encoding="utf-8")
    except UnicodeDecodeError:
        return pd.read_csv(path, encoding="latin1")


def _unknown_pms_strip_junk_rows(df: pd.DataFrame) -> pd.DataFrame:
    """Drop totals row, blank spacer, and embedded report-filter string rows."""
    if df.empty:
        return df
    out = df.copy()
    if "Reservation Number" in out.columns:
        res = out["Reservation Number"].astype(str).str.strip()
        out = out.loc[res.notna() & (res != "") & (res.str.lower() != "nan")].copy()
    if "Property Name" in out.columns:
        prop = out["Property Name"].astype(str).str.strip()
        out = out.loc[
            prop.notna()
            & (prop != "")
            & (prop.str.lower() != "nan")
            & (~prop.str.contains("Check-In Date", case=False, na=False))
            & (~prop.str.contains("Booking Date Time", case=False, na=False))
        ].copy()
    return out.reset_index(drop=True)


def _unknown_pms_load_csv(path) -> pd.DataFrame:
    """
    Load Reservations-with-Financials style export (xlsx or csv).
    Excel: metadata rows 0-4, header on row 5; strip trailing junk rows.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"File not found: {path}")
    suffix = path.suffix.lower()
    if suffix in {".xlsx", ".xls"}:
        df = pd.read_excel(path, sheet_name=0, header=5)
    else:
        try:
            df = pd.read_csv(path, encoding="utf-8")
        except UnicodeDecodeError:
            df = pd.read_csv(path, encoding="latin1")
    # Drop unnamed index columns from Excel exports
    drop_cols = [c for c in df.columns if str(c).startswith("Unnamed")]
    if drop_cols:
        df = df.drop(columns=drop_cols)
    return _unknown_pms_strip_junk_rows(df)


def _unknown_pms_parse_rooms(room_numbers: object, room_types: object) -> list[str]:
    """
    Return one room-type label per physical room on the reservation.
    Prefer codes inside Room Numbers, e.g. '104 (STK), 103 (STK)'.
    """
    rn = str(room_numbers or "").strip()
    rt = str(room_types or "").strip()
    if rn and rn.lower() not in {"nan", "-", ""}:
        parts = [p.strip() for p in rn.split(",") if p.strip()]
        types: list[str] = []
        for p in parts:
            m = re.search(r"\(([^)]+)\)\s*$", p)
            if m:
                code = m.group(1).strip().upper()
                types.append(_UNKNOWN_PMS_ROOM_CODE_TO_TYPE.get(code, code))
            else:
                types.append(p)
        if types:
            return types
    if rt and rt.lower() not in {"nan", "-", ""}:
        parts = [p.strip() for p in rt.split(",") if p.strip()]
        if parts:
            return parts
    return ["Unknown"]


def _derive_unknown_pms_channel(row: pd.Series) -> str:
    raw = str(row.get("Reservation Source", "") or "").strip()
    if not raw or raw.lower() in {"nan", "-"}:
        raw = str(row.get("Reservation Source Category", "") or "").strip()
    return _normalize_channel_label(raw)


def _unknown_pms_map_to_canonical(raw: pd.DataFrame, property_id: Optional[str] = None) -> pd.DataFrame:
    """
    Map unknown-PMS financials export to canonical.
    Multi-room reservations are expanded to one row per room; revenue split evenly.
    Excludes In-House. Lead time clipped at 0 (UTC booking vs local check-in).
    """
    df = _unknown_pms_strip_junk_rows(raw)
    if df.empty:
        return pd.DataFrame(columns=canonical_columns())

    rows: list[dict[str, Any]] = []
    for idx, r in df.iterrows():
        status = str(r.get("Reservation Status", "") or "").strip()
        if status.lower() == "in-house" or reservation_status_is_excluded(status):
            continue

        arrival = pd.to_datetime(r.get("Check-In Date"), errors="coerce")
        departure = pd.to_datetime(r.get("Check-Out Date"), errors="coerce")
        booked = pd.to_datetime(r.get("Booking Date Time - UTC"), errors="coerce")
        if pd.isna(arrival) or pd.isna(departure):
            continue
        los = int((departure - arrival).days)
        if los < 1:
            continue

        revenue_total = _parse_currency(r.get("Room Revenue Total"))
        if revenue_total is None:
            continue
        paid_total = _parse_currency(r.get("Reservation Paid Amount"))

        room_types_list = _unknown_pms_parse_rooms(r.get("Room Numbers"), r.get("Room Types"))
        n_rooms = max(len(room_types_list), 1)
        # Prefer explicit Room Count when present and consistent
        rc_raw = pd.to_numeric(r.get("Room Count"), errors="coerce")
        if pd.notna(rc_raw) and int(rc_raw) > n_rooms:
            # Pad with primary type if Room Count > parsed rooms
            primary = room_types_list[0]
            room_types_list = room_types_list + [primary] * (int(rc_raw) - n_rooms)
            n_rooms = len(room_types_list)

        rev_each = float(revenue_total) / n_rooms
        paid_each = (float(paid_total) / n_rooms) if paid_total is not None else None
        res_id = str(r.get("Reservation Number", "") or "").strip()
        if not res_id or res_id.lower() == "nan":
            res_id = f"unknown-pms-{idx}"

        lead = None
        if pd.notna(booked):
            lead = int((arrival.normalize() - booked.normalize()).days)
            if lead < 0:
                lead = 0

        channel = _derive_unknown_pms_channel(r)
        book_date = booked.normalize() if pd.notna(booked) else pd.NaT

        for room_i, unit_id in enumerate(room_types_list):
            unit_id = str(unit_id).strip() or "Unknown"
            if property_id:
                mapped = _apply_property_listing_name_map(pd.Series([unit_id]), property_id)
                unit_id = str(mapped.iloc[0])
            rows.append({
                "reservation_id": res_id if n_rooms == 1 else f"{res_id}-r{room_i + 1}",
                "arrival_date": arrival.normalize(),
                "departure_date": departure.normalize(),
                "nights": los,
                "unit_id": unit_id,
                "guest_name": "",
                "revenue": rev_each,
                "amount_paid": paid_each,
                "booking_date": book_date,
                "lead_time_days": lead,
                "adr": (rev_each / los) if los > 0 else None,
                "channel": channel,
                "arrival_day_of_week": arrival.day_name(),
                "arrival_year_month": arrival.strftime("%Y-%m"),
            })

    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=canonical_columns())
    # Keep non-negative revenue (zero-revenue No Shows allowed through to normalize)
    out = out.loc[out["revenue"].notna() & (out["revenue"] >= 0)].copy()
    return out


def _hostaway_listing_series(raw: pd.DataFrame) -> pd.Series:
    """Oasi/Hostaway dashboard exports: Listing Name, Listing, or pandas-deduped Listing.1."""
    for col in _HOSTAWAY_LISTING_COLUMNS:
        if col in raw.columns:
            ser = raw[col].astype(str).str.strip()
            if ser.replace({"": None, "nan": None}).notna().any():
                return ser
    return pd.Series([""] * len(raw), index=raw.index)


def _track_unit_series(raw: pd.DataFrame) -> pd.Series:
    for col in _TRACK_UNIT_COLUMNS:
        if col in raw.columns:
            return raw[col].astype(str).str.strip()
    return pd.Series([""] * len(raw), index=raw.index)


def _apply_property_listing_name_map(series: pd.Series, property_id: Optional[str]) -> pd.Series:
    mapping = _get_property_listing_name_map(property_id)
    if not mapping:
        return series
    return series.replace(mapping)


def _dedupe_hostaway_stay_rows(df: pd.DataFrame) -> pd.DataFrame:
    """
    Some Hostaway exports emit two paid rows per stay (gross rent vs accommodation-only).
    Keep the row with the highest revenue per guest + stay dates + unit.
    """
    if df.empty or "revenue" not in df.columns:
        return df
    stay_key = (
        df["guest_name"].astype(str)
        + "|"
        + df["arrival_date"].astype(str)
        + "|"
        + df["departure_date"].astype(str)
        + "|"
        + df["unit_id"].astype(str)
    )
    dup_mask = stay_key.duplicated(keep=False)
    if not dup_mask.any():
        return df
    tagged = df.assign(_stay_key=stay_key)
    keep_idx = tagged.groupby("_stay_key", sort=False)["revenue"].idxmax()
    return tagged.loc[keep_idx].drop(columns=["_stay_key"])


def _normalize_flohom_listing_name(val: object) -> str:
    s = str(val).strip()
    # Normalize zero-padded ids (e.g. "FLOHOM 01" -> "FLOHOM 1")
    m = re.match(r"^(FLOHOM)\s+0*(\d+)$", s, flags=re.IGNORECASE)
    if m:
        return f"FLOHOM {int(m.group(2))}"
    return s


def _derive_hostaway_channel(row: pd.Series) -> str:
    raw = None
    if "Channel.1" in row.index and pd.notna(row.get("Channel.1")):
        raw = str(row.get("Channel.1")).strip()
    elif "Channel" in row.index and pd.notna(row.get("Channel")):
        raw = str(row.get("Channel")).strip()
    if not raw:
        return "Other"

    key = raw.lower()
    mapping = {
        "direct": "Direct",
        "airbnb": "Airbnb",
        "airbnbofficial": "Airbnb",
        "vrbo": "VRBO",
    }
    return mapping.get(key, raw if raw in {"Direct", "Airbnb", "VRBO"} else "Other")


# Pet-friendly Cypress Lodge room types → same SKU as non–pet-friendly sleeps count.
_FBG_CYPRESS_PET_TO_MAIN = {
    "Cypress Lodge | Pet-Friendly • Sleeps 2": "Cypress Lodge | Sleeps 2",
    "Cypress Lodge | Pet-Friendly • Sleeps 4": "Cypress Lodge | Sleeps 4",
    "Cypress Lodge | Pet-Friendly • Sleeps 6": "Cypress Lodge | Sleeps 6",
}


def _normalize_fbg_listing_label(val: object) -> str:
    """
    Onera Fredericksburg (FBG): Cloudbeds exports use Listing Name for clean labels
    but Room Type carries rate-plan suffixes; some rows use multi-listing labels
    joined with commas (first segment only). Pet-friendly / Accessible suffixes on
    the listing name are merged into the base listing for a fixed pricing SKU set.
    Pet-friendly Cypress Lodge | … rows map to Cypress Lodge | Sleeps N.
    """
    s = str(val or "").strip()
    if not s or s.lower() == "nan":
        return ""
    if "," in s:
        s = s.split(",")[0].strip()
    # Strip trailing parenthetical rate-plan / promo noise; keep (Pet-friendly), (Accessible).
    discard_inside = re.compile(
        r"OFF|%|promo|birthday|refundable|furnished|igfam|gregory|winter|weekday|weekend|"
        r"onera|rob10|fbg20|fall20|offdec|gindele|february|thank you|flexible|december|"
        r"standard rate|weekend stay|weekday stay|furnished finder",
        re.I,
    )
    paren_tail = re.compile(r" \(([^)]*)\)$")
    changed = True
    while changed:
        changed = False
        m = paren_tail.search(s)
        if not m:
            break
        inner = m.group(1)
        if discard_inside.search(inner):
            s = s[: m.start()].strip()
            changed = True
    # FBG: price on base listing only — pet-friendly / accessible are the same SKU as the main name.
    sku_merge_tail = re.compile(r" \((Pet-friendly|Pet friendly|Accessible)\)$", re.I)
    while True:
        m = sku_merge_tail.search(s)
        if not m:
            break
        s = s[: m.start()].strip()
    s = _FBG_CYPRESS_PET_TO_MAIN.get(s, s)
    return s.strip()


def _normalize_wmb_listing_type(val: object) -> str:
    """
    WMB canonical unit_id is listing type (not physical room).
    Handles occasional comma-delimited multi-type labels by picking first recognized token.
    """
    raw = str(val or "").strip()
    if not raw:
        return ""
    parts = [p.strip() for p in raw.split(",") if p.strip()]
    candidates = parts if parts else [raw]
    known = {
        "Greenhouse",
        "Greenhouse (Pet Friendly)",
        "Greenhouse (Accessible)",
        "Spyglass",
        "Spyglass (Pet Friendly)",
        "Spyglass (Accessible)",
    }
    for c in candidates:
        if c in known:
            return c
    # fallback: first token-like segment as listing type surrogate
    return candidates[0]


def _derive_cloudbeds_channel(row: pd.Series) -> str:
    # User rule: primary from Channel Group; if blank, use column Y.
    raw = str(row.get("Channel Group", "") or "").strip()
    if not raw:
        raw = str(row.get("Y", "") or "").strip()
    if not raw:
        raw = str(row.get("Source", "") or "").strip()
    return _normalize_channel_label(raw)


def _derive_track_channel(row: pd.Series) -> str:
    raw = str(row.get("Channel", "") or "").strip()
    return _normalize_channel_label(raw)


def _guest_name_is_canceled(guest_val: object) -> bool:
    """OwnerRez exports mark voided stays in Guest, e.g. 'Smith, Jane [CANCELED]'."""
    s = str(guest_val or "").strip()
    if not s or s.lower() == "nan":
        return False
    return "[canceled]" in s.lower() or "[cancelled]" in s.lower()


def _derive_ownerrez_channel(row: pd.Series) -> str:
    for col in _OWNERREZ_CHANNEL:
        if col not in row.index:
            continue
        raw = str(row.get(col, "")).strip()
        if raw and raw.lower() not in {"nan", ""}:
            return _normalize_channel_label(raw)
    return "Unknown"


def _derive_guesty_channel(row: pd.Series) -> str:
    raw = str(row.get("Channel", "") or "").strip()
    if raw:
        return _normalize_channel_label(raw)
    src = str(row.get("SOURCE", "") or "").strip().lower()
    return _normalize_channel_label(src)


def _get_property_listing_name_map(property_id: Optional[str]) -> dict[str, str]:
    pid = str(property_id or "").strip().lower()
    if not pid:
        return {}
    if pid in _PROPERTY_LISTING_MAP_CACHE:
        return _PROPERTY_LISTING_MAP_CACHE[pid]
    path = _PROPERTIES_DIR / f"{pid}.yaml"
    if not path.exists():
        _PROPERTY_LISTING_MAP_CACHE[pid] = {}
        return {}
    try:
        import yaml
        with open(path, encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        mapping = cfg.get("listing_name_map") or {}
        out = {str(k).strip(): str(v).strip() for k, v in mapping.items() if str(k).strip() and str(v).strip()}
        _PROPERTY_LISTING_MAP_CACHE[pid] = out
        return out
    except Exception:
        _PROPERTY_LISTING_MAP_CACHE[pid] = {}
        return {}


def _normalize_sos_listing_name(val: object, property_id: Optional[str] = None) -> str:
    s = str(val or "").strip()
    if not s:
        return ""
    mapping = _get_property_listing_name_map(property_id)
    if s in mapping:
        return mapping[s]
    # Backward-compatible defaults if config mapping is missing.
    if s == "2":
        return ""
    fallback = {
        "SOS 23BR": "23BR",
        "SOS 12BR Frontside": "12BR",
        "SOS 11BR Backside": "11BR",
    }
    for k, v in fallback.items():
        if s == k:
            return v
    if s.startswith("SOS 11BR"):
        return "11BR"
    if s.startswith("SOS 12BR"):
        return "12BR"
    if s.startswith("SOS 23BR"):
        return "23BR"
    return s


def _cloudbeds_map_to_canonical(raw: pd.DataFrame, property_id: Optional[str] = None) -> pd.DataFrame:
    df = raw.copy()
    def _col(name: str) -> pd.Series:
        return df[name] if name in df.columns else pd.Series([None] * len(df), index=df.index)
    def _pick_cloudbeds_property_listing_col() -> pd.Series:
        """
        Cloudbeds exports can contain duplicate "Property" headers.
        Pandas de-duplicates as Property, Property.1, Property.2, ...
        Choose the Property* column with the richest set of non-empty values
        (the listing-type column), rather than the constant property name column.
        """
        prop_like = [c for c in df.columns if c == "Property" or c.startswith("Property.")]
        if not prop_like:
            return pd.Series([None] * len(df), index=df.index)
        best_col = prop_like[0]
        best_score = -1
        for c in prop_like:
            ser = df[c].astype(str).str.strip()
            uniq = {v for v in ser.unique() if v and v.lower() != "nan"}
            score = len(uniq)
            if score > best_score:
                best_col = c
                best_score = score
        return df[best_col].astype(str).str.strip()

    df["arrival_date"] = pd.to_datetime(df.get("Check in Date"), errors="coerce")
    df["departure_date"] = pd.to_datetime(df.get("Check out Date"), errors="coerce")
    booking_primary = pd.to_datetime(_col("booking date"), errors="coerce")
    booking_fallback = pd.to_datetime(_col("Reservation Date"), errors="coerce")
    df["booking_date"] = booking_primary.where(booking_primary.notna(), booking_fallback)
    df["booking_date"] = pd.to_datetime(df["booking_date"], errors="coerce")
    listing_name = _col("Listing Name").astype(str).str.strip()
    room_type = _col("Room Type").astype(str).str.strip()
    prop_series = _pick_cloudbeds_property_listing_col()
    prop_str = prop_series.astype(str).str.strip()
    prop_uniq = {v for v in prop_str.unique() if v and v.lower() != "nan"}
    # Only treat Property* as the listing discriminator when it varies per row (e.g. SOS
    # duplicate "Property" columns). A single constant site name must not override Listing Name.
    property_type = prop_series if len(prop_uniq) > 1 else pd.Series([""] * len(df), index=df.index)
    unit_source = listing_name.where(listing_name != "", room_type)
    unit_source = property_type.where(property_type.astype(str).str.strip() != "", unit_source)
    pid = str(property_id or "").strip().lower()
    if pid in {"wmb", "onera_wimberley"}:
        df["unit_id"] = unit_source.apply(_normalize_wmb_listing_type)
    elif pid == "sos":
        df["unit_id"] = unit_source.apply(lambda v: _normalize_sos_listing_name(v, property_id=property_id))
    elif pid == "fbg":
        mapped = unit_source.apply(_normalize_fbg_listing_label)
        name_map = _get_property_listing_name_map(property_id)
        if name_map:
            mapped = mapped.replace(name_map)
        df["unit_id"] = mapped
    else:
        df["unit_id"] = unit_source
    df["guest_name"] = df.get("Name", "").astype(str).replace("nan", "").str.strip()
    df["revenue"] = df.get("Revenue").apply(_parse_currency)
    df["amount_paid"] = df.get("Amount Paid").apply(_parse_currency) if "Amount Paid" in df.columns else None
    df["channel"] = df.apply(_derive_cloudbeds_channel, axis=1)
    df["nights"] = pd.to_numeric(df.get("Nights"), errors="coerce")
    inferred_nights = (df["departure_date"] - df["arrival_date"]).dt.days
    df["nights"] = df["nights"].where(df["nights"].notna() & (df["nights"] > 0), inferred_nights)
    res_num = df.get("Reservation Number", "").astype(str).str.strip()
    df["reservation_id"] = res_num.where(res_num != "", other=None)
    missing_res = df["reservation_id"].isna() | (df["reservation_id"].astype(str).str.strip() == "")
    df.loc[missing_res, "reservation_id"] = [
        f"cloudbeds-{idx + 1}-{u}-{a.date() if pd.notna(a) else 'na'}"
        for idx, (u, a) in enumerate(zip(df.loc[missing_res, "unit_id"], df.loc[missing_res, "arrival_date"]))
    ]
    df["lead_time_days"] = (df["arrival_date"] - df["booking_date"]).dt.days
    df["adr"] = df["revenue"] / df["nights"]
    df.loc[df["nights"].isna() | (df["nights"] <= 0), "adr"] = None
    if pd.api.types.is_datetime64_any_dtype(df["arrival_date"]):
        df["arrival_day_of_week"] = df["arrival_date"].dt.day_name()
        df["arrival_year_month"] = df["arrival_date"].dt.strftime("%Y-%m")
    else:
        df["arrival_day_of_week"] = ""
        df["arrival_year_month"] = ""
    # User rule: exclude all cancel / pending-nonstay reservation statuses (unified with Track).
    if "Status" in df.columns:
        st = df["Status"].astype(str)
        df = df.loc[~st.map(reservation_status_is_excluded)].copy()
    # User rule: exclude Sales Group Booking rows from analysis universe.
    channel_norm = df["channel"].astype(str).str.strip().str.lower()
    df = df.loc[channel_norm != "sales group booking"].copy()
    # User rule: exclude non-positive revenue rows.
    df = df.loc[df["revenue"].notna() & (df["revenue"] > 0)].copy()
    return df


def _track_map_to_canonical(raw: pd.DataFrame, property_id: Optional[str] = None) -> pd.DataFrame:
    df = raw.copy()
    def _col(name: str) -> pd.Series:
        return df[name] if name in df.columns else pd.Series([None] * len(df), index=df.index)

    df["arrival_date"] = pd.to_datetime(df.get("Check-In"), errors="coerce")
    df["departure_date"] = pd.to_datetime(df.get("Checkout"), errors="coerce")
    booking_primary = pd.to_datetime(_col("booking date"), errors="coerce")
    booking_fallback = pd.to_datetime(_col("Booking Date"), errors="coerce")
    df["booking_date"] = booking_primary.where(booking_primary.notna(), booking_fallback)
    unit_ser = _track_unit_series(df)
    if str(property_id or "").strip().lower() == "sos":
        df["unit_id"] = unit_ser.apply(lambda v: _normalize_sos_listing_name(v, property_id=property_id))
    else:
        df["unit_id"] = _apply_property_listing_name_map(unit_ser, property_id)
    full_name = (
        df.get("First Name", "").astype(str).replace("nan", "").str.strip()
        + " "
        + df.get("Last Name", "").astype(str).replace("nan", "").str.strip()
    ).str.strip()
    df["guest_name"] = full_name
    df["revenue"] = df.get("Rev").apply(_parse_currency)
    df["amount_paid"] = df.get("Payments").apply(_parse_currency) if "Payments" in df.columns else None
    df["channel"] = df.apply(_derive_track_channel, axis=1)
    df["nights"] = pd.to_numeric(df.get("Nights"), errors="coerce")
    inferred_nights = (df["departure_date"] - df["arrival_date"]).dt.days
    df["nights"] = df["nights"].where(df["nights"].notna() & (df["nights"] > 0), inferred_nights)
    res_num = df.get("Res. #", "").astype(str).str.strip()
    df["reservation_id"] = res_num.where(res_num != "", other=None)
    missing_res = df["reservation_id"].isna() | (df["reservation_id"].astype(str).str.strip() == "")
    df.loc[missing_res, "reservation_id"] = [
        f"track-{idx + 1}-{u}-{a.date() if pd.notna(a) else 'na'}"
        for idx, (u, a) in enumerate(zip(df.loc[missing_res, "unit_id"], df.loc[missing_res, "arrival_date"]))
    ]
    df["booking_date"] = pd.to_datetime(df["booking_date"], errors="coerce")
    df["lead_time_days"] = (df["arrival_date"] - df["booking_date"]).dt.days
    df["adr"] = df["revenue"] / df["nights"]
    df.loc[df["nights"].isna() | (df["nights"] <= 0), "adr"] = None
    if pd.api.types.is_datetime64_any_dtype(df["arrival_date"]):
        df["arrival_day_of_week"] = df["arrival_date"].dt.day_name()
        df["arrival_year_month"] = df["arrival_date"].dt.strftime("%Y-%m")
    else:
        df["arrival_day_of_week"] = ""
        df["arrival_year_month"] = ""
    if "Status" in df.columns:
        st = df["Status"].astype(str)
        df = df.loc[~st.map(reservation_status_is_excluded)].copy()
    df = df.loc[df["revenue"].notna() & (df["revenue"] > 0)].copy()
    return df


def _guesty_map_to_canonical(raw: pd.DataFrame, property_id: Optional[str] = None) -> pd.DataFrame:
    df = raw.copy()
    df["arrival_date"] = pd.to_datetime(df.get("CHECK-IN DATE"), errors="coerce")
    df["departure_date"] = pd.to_datetime(df.get("CHECK-OUT DATE"), errors="coerce")
    booking_primary = pd.to_datetime(df.get("Booking date"), errors="coerce")
    booking_fallback = pd.to_datetime(df.get("CREATION DATE"), errors="coerce")
    df["booking_date"] = booking_primary.where(booking_primary.notna(), booking_fallback)
    unit_ser = df.get("Listing", "").astype(str).str.strip()
    if str(property_id or "").strip().lower() == "sos":
        df["unit_id"] = unit_ser.apply(lambda v: _normalize_sos_listing_name(v, property_id=property_id))
    else:
        df["unit_id"] = unit_ser
    df["guest_name"] = df.get("GUEST", "").astype(str).replace("nan", "").str.strip()
    df["revenue"] = df.get("Revenue").apply(_parse_currency)
    df["amount_paid"] = df.get("TOTAL PAID").apply(_parse_currency) if "TOTAL PAID" in df.columns else None
    df["channel"] = df.apply(_derive_guesty_channel, axis=1)
    df["nights"] = pd.to_numeric(df.get("NUMBER OF NIGHTS"), errors="coerce")
    inferred_nights = (df["departure_date"] - df["arrival_date"]).dt.days
    df["nights"] = df["nights"].where(df["nights"].notna() & (df["nights"] > 0), inferred_nights)
    res_id = (
        df.get("GUEST", "").astype(str).str.strip().str.lower().str.replace(r"[^a-z0-9]+", "-", regex=True)
        + "-"
        + df["arrival_date"].dt.strftime("%Y%m%d").fillna("na")
    )
    df["reservation_id"] = [f"guesty-{i + 1}-{rid}" for i, rid in enumerate(res_id)]
    df["booking_date"] = pd.to_datetime(df["booking_date"], errors="coerce")
    df["lead_time_days"] = (df["arrival_date"] - df["booking_date"]).dt.days
    df["adr"] = df["revenue"] / df["nights"]
    df.loc[df["nights"].isna() | (df["nights"] <= 0), "adr"] = None
    if pd.api.types.is_datetime64_any_dtype(df["arrival_date"]):
        df["arrival_day_of_week"] = df["arrival_date"].dt.day_name()
        df["arrival_year_month"] = df["arrival_date"].dt.strftime("%Y-%m")
    else:
        df["arrival_day_of_week"] = ""
        df["arrival_year_month"] = ""
    if "STATUS" in df.columns:
        st = df["STATUS"].astype(str)
        df = df.loc[~st.map(reservation_status_is_excluded)].copy()
    df = df.loc[df["revenue"].notna() & (df["revenue"] > 0)].copy()
    return df


def _ownerrez_map_to_canonical(raw: pd.DataFrame, property_id: Optional[str] = None) -> pd.DataFrame:
    """OwnerRez Oasi dashboard export: Property = unit; Revenue = canonical revenue."""
    df = raw.copy()
    df["unit_id"] = _apply_property_listing_name_map(
        df["Property"].astype(str).str.strip(), property_id
    )
    df["guest_name"] = df["Guest"].astype(str).replace("nan", "").str.strip()
    df["arrival_date"] = df["Arrival"].apply(_parse_date_resnexus)
    df["departure_date"] = df["Departure"].apply(_parse_date_resnexus)
    df["booking_date"] = df["Booked"].apply(_parse_date_resnexus)
    res_num = df["Booking #"].astype(str).str.strip()
    df["reservation_id"] = res_num.where(res_num != "", other=None)
    missing_res = df["reservation_id"].isna() | (df["reservation_id"].astype(str).str.strip() == "")
    df.loc[missing_res, "reservation_id"] = [
        f"ownerrez-{idx + 1}-{u}-{a.date() if pd.notna(a) else 'na'}"
        for idx, (u, a) in enumerate(zip(df.loc[missing_res, "unit_id"], df.loc[missing_res, "arrival_date"]))
    ]
    df["revenue"] = df["Revenue"].apply(_parse_currency)
    if "Net Payments" in df.columns:
        df["amount_paid"] = df["Net Payments"].apply(_parse_currency)
    elif "Paid" in df.columns:
        df["amount_paid"] = df["Paid"].apply(_parse_currency)
    else:
        df["amount_paid"] = df["revenue"]
    df["channel"] = df.apply(_derive_ownerrez_channel, axis=1)
    df["nights"] = pd.to_numeric(df.get("N"), errors="coerce")
    inferred_nights = (df["departure_date"] - df["arrival_date"]).dt.days
    df["nights"] = df["nights"].where(df["nights"].notna() & (df["nights"] > 0), inferred_nights)
    bw = pd.to_numeric(df.get("Booking Window"), errors="coerce")
    computed_lt = (df["arrival_date"] - df["booking_date"]).dt.days
    df["lead_time_days"] = bw.where(bw.notna() & (bw >= 0), computed_lt)
    df["adr"] = df["revenue"] / df["nights"]
    df.loc[df["nights"].isna() | (df["nights"] <= 0), "adr"] = None
    if pd.api.types.is_datetime64_any_dtype(df["arrival_date"]):
        df["arrival_day_of_week"] = df["arrival_date"].dt.day_name()
        df["arrival_year_month"] = df["arrival_date"].dt.strftime("%Y-%m")
    else:
        df["arrival_day_of_week"] = ""
        df["arrival_year_month"] = ""
    canceled_guest = df["Guest"].map(_guest_name_is_canceled)
    df = df.loc[~canceled_guest].copy()
    df = df.loc[df["revenue"].notna() & (df["revenue"] > 0)].copy()
    return df


def _hostaway_map_to_canonical(raw: pd.DataFrame, property_id: Optional[str] = None) -> pd.DataFrame:
    df = raw.copy()
    df["arrival_date"] = pd.to_datetime(df.get("Check-in date"), errors="coerce", format="mixed")
    df["departure_date"] = pd.to_datetime(df.get("Check-out date"), errors="coerce", format="mixed")
    df["booking_date"] = pd.to_datetime(df.get("Reservation date"), errors="coerce", format="mixed")
    unit_ser = _hostaway_listing_series(df)
    # Property-specific hostaway mapping hook:
    # FLOHOM uses "FLOHOM 01"/"FLOHOM 1" mixed labels, normalize to "FLOHOM <n>".
    if str(property_id or "").strip().lower() == "flohom":
        df["unit_id"] = unit_ser.apply(_normalize_flohom_listing_name)
    else:
        df["unit_id"] = _apply_property_listing_name_map(unit_ser, property_id)
    df["guest_name"] = df.get("Guest", "").astype(str).replace("nan", "").str.strip()
    df["revenue"] = df.get("rentalRevenue").apply(_parse_currency)
    df["amount_paid"] = df.get("Total paid").apply(_parse_currency) if "Total paid" in df.columns else None
    df["channel"] = df.apply(_derive_hostaway_channel, axis=1)

    df["nights"] = pd.to_numeric(df.get("Nights"), errors="coerce")
    inferred_nights = (df["departure_date"] - df["arrival_date"]).dt.days
    df["nights"] = df["nights"].where(df["nights"].notna() & (df["nights"] > 0), inferred_nights)

    df["reservation_id"] = [
        f"hostaway-{idx + 1}-{u}-{a.date() if pd.notna(a) else 'na'}"
        for idx, (u, a) in enumerate(zip(df["unit_id"], df["arrival_date"]))
    ]

    # Recompute booking window from reservation date to check-in date.
    df["lead_time_days"] = (
        (df["arrival_date"] - df["booking_date"]).dt.days
        if "booking_date" in df.columns else None
    )
    df["adr"] = df["revenue"] / df["nights"]
    df.loc[df["nights"].isna() | (df["nights"] <= 0), "adr"] = None

    if pd.api.types.is_datetime64_any_dtype(df["arrival_date"]):
        df["arrival_day_of_week"] = df["arrival_date"].dt.day_name()
        df["arrival_year_month"] = df["arrival_date"].dt.strftime("%Y-%m")
    else:
        df["arrival_day_of_week"] = ""
        df["arrival_year_month"] = ""

    if "Reservation status" in df.columns:
        st = df["Reservation status"].astype(str)
        df = df.loc[~st.map(reservation_status_is_excluded)].copy()

    # Business rule: exclude unknown/unpaid payment rows; keep paid and partially paid.
    # Flohom's live sheet tab often returns Payment status = Unknown for every row;
    # rentalRevenue > 0 (below) is the inclusion gate for that property.
    if "Payment status" in df.columns and str(property_id or "").strip().lower() != "flohom":
        payment_status = df["Payment status"].astype(str).str.strip().str.lower()
        df = df.loc[~payment_status.isin({"unknown", "unpaid"})].copy()

    df = df.loc[df["revenue"].notna() & (df["revenue"] > 0)].copy()
    df = _dedupe_hostaway_stay_rows(df)

    return df


def _resnexus_map_legacy_to_canonical(raw: pd.DataFrame, property_id: Optional[str] = None) -> pd.DataFrame:
    df = raw.copy()
    df["unit_id"] = df["Unit"].astype(str).str.strip()
    df["reservation_id"] = df["Res#"].astype(str).str.strip()
    df["guest_name"] = df["Guest"].astype(str).replace("nan", "")
    dates = df["Date"].apply(_parse_date_range_resnexus)
    df["arrival_date"] = [d[0] for d in dates]
    df["departure_date"] = [d[1] for d in dates]
    df["nights"] = [d[2] for d in dates]
    df["revenue"] = df["Amount"].apply(_parse_currency)
    df["amount_paid"] = df["Paid"].apply(_parse_currency) if "Paid" in df.columns else None
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


def _resnexus_map_sheet_to_canonical(raw: pd.DataFrame, property_id: Optional[str] = None) -> pd.DataFrame:
    """ResNexus-style LaFave sheet: Arrival/Departure, Listing Name, Reservation Date, # Nights."""
    df = raw.copy()
    df["unit_id"] = df["Listing Name"].astype(str).str.strip()
    df["reservation_id"] = df["Res#"].astype(str).str.strip()
    df["guest_name"] = df["Guest"].astype(str).replace("nan", "")
    df["arrival_date"] = df["Arrival"].apply(_parse_date_resnexus)
    df["departure_date"] = df["Departure"].apply(_parse_date_resnexus)
    df["nights"] = pd.to_numeric(df["# Nights"], errors="coerce")
    inferred = (df["departure_date"] - df["arrival_date"]).dt.days
    df["nights"] = df["nights"].where(df["nights"].notna() & (df["nights"] > 0), inferred)
    df["revenue"] = df["Amount"].apply(_parse_currency)
    if "Paid" in df.columns:
        df["amount_paid"] = df["Paid"].apply(_parse_currency)
    else:
        # No payment column: treat as fully collected for filtering / reporting.
        df["amount_paid"] = df["revenue"]
    df["booking_date"] = df["Reservation Date"].apply(_parse_date_resnexus)
    df["channel"] = df.apply(_derive_channel_resnexus_sheet, axis=1)
    if "Grouping" in df.columns:
        df["listing_group"] = df["Grouping"].astype(str).str.strip().replace("nan", "")
    computed_lt = None
    if pd.api.types.is_datetime64_any_dtype(df["arrival_date"]) and pd.api.types.is_datetime64_any_dtype(
        df["booking_date"]
    ):
        computed_lt = (df["arrival_date"] - df["booking_date"]).dt.days
    if "Booking window" in df.columns:
        bw = pd.to_numeric(df["Booking window"], errors="coerce")
        if computed_lt is not None:
            df["lead_time_days"] = bw.where(bw.notna() & (bw >= 0), computed_lt)
        else:
            df["lead_time_days"] = bw
    else:
        df["lead_time_days"] = computed_lt
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


def _resnexus_map_to_canonical(raw: pd.DataFrame, property_id: Optional[str] = None) -> pd.DataFrame:
    fmt = resnexus_raw_format(raw)
    if fmt == "sheet":
        return _resnexus_map_sheet_to_canonical(raw, property_id=property_id)
    if fmt == "legacy":
        return _resnexus_map_legacy_to_canonical(raw, property_id=property_id)
    raise ValueError(
        "Unrecognized ResNexus CSV layout: need either legacy columns "
        f"({', '.join(_RESNEXUS_REQUIRED)}) or sheet columns ({', '.join(_RESNEXUS_SHEET_REQUIRED)})."
    )


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
    "hostaway": {
        "required_columns": _HOSTAWAY_REQUIRED,
        "channel_columns": _HOSTAWAY_CHANNEL,
        "load_csv": _hostaway_load_csv,
        "map_to_canonical": _hostaway_map_to_canonical,
    },
    "cloudbeds": {
        "required_columns": _CLOUDBEDS_REQUIRED,
        "channel_columns": _CLOUDBEDS_CHANNEL,
        "load_csv": _cloudbeds_load_csv,
        "map_to_canonical": _cloudbeds_map_to_canonical,
    },
    "track": {
        "required_columns": _TRACK_REQUIRED,
        "channel_columns": _TRACK_CHANNEL,
        "load_csv": _track_load_csv,
        "map_to_canonical": _track_map_to_canonical,
    },
    "guesty": {
        "required_columns": _GUESTY_REQUIRED,
        "channel_columns": _GUESTY_CHANNEL,
        "load_csv": _guesty_load_csv,
        "map_to_canonical": _guesty_map_to_canonical,
    },
    "ownerrez": {
        "required_columns": _OWNERREZ_REQUIRED,
        "channel_columns": _OWNERREZ_CHANNEL,
        "load_csv": _ownerrez_load_csv,
        "map_to_canonical": _ownerrez_map_to_canonical,
    },
    "unknown_pms": {
        "required_columns": _UNKNOWN_PMS_REQUIRED,
        "channel_columns": _UNKNOWN_PMS_CHANNEL,
        "load_csv": _unknown_pms_load_csv,
        "map_to_canonical": _unknown_pms_map_to_canonical,
    },
}


def list_pms_ids() -> list[str]:
    """Return registered pms_id values (e.g. ['resnexus'])."""
    return list(_PMS_PARSERS.keys())


def get_required_columns(pms_id: str) -> list[str]:
    """Required columns in raw data for this PMS. Used by validation.

    For ``resnexus``, validation accepts either legacy or sheet layouts
    (see ``resnexus_raw_format``); this function still returns the legacy list
    for backward compatibility and tooling that expects a single list.
    """
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


def map_to_canonical(raw: pd.DataFrame, pms_id: str, property_id: Optional[str] = None) -> pd.DataFrame:
    """Map raw dataframe to canonical schema using the parser for this PMS."""
    if pms_id not in _PMS_PARSERS:
        raise ValueError(f"Unknown pms_id: {pms_id}. Available: {list_pms_ids()}")
    return _PMS_PARSERS[pms_id]["map_to_canonical"](raw, property_id=property_id)


def normalize(
    raw: pd.DataFrame,
    pms_id: str,
    property_id: Optional[str] = None,
    exclude_unpaid_below_amount: bool = True,
    exclude_invalid_revenue: bool = True,
) -> pd.DataFrame:
    """Map to canonical and exclude invalid/unpaid rows. Uses parser for this PMS."""
    df = map_to_canonical(raw, pms_id, property_id=property_id)
    if exclude_unpaid_below_amount and pms_id in {"resnexus"}:
        # Legacy exports include Paid; sheet exports may omit it (amount_paid filled with revenue in mapper).
        if df["amount_paid"].notna().any():
            mask = df["amount_paid"].notna() & (df["amount_paid"] >= df["revenue"].fillna(0))
            df = df.loc[mask].copy()
    if exclude_invalid_revenue:
        if pms_id == "cloudbeds":
            df = df.loc[df["revenue"].notna() & (df["revenue"] > 0)].copy()
        elif pms_id == "unknown_pms":
            # Keep zero-revenue No Shows; drop negative adjustment rows.
            df = df.loc[df["revenue"].notna() & (df["revenue"] >= 0)].copy()
        else:
            df = df.loc[df["revenue"].notna() & (df["revenue"] >= 0)].copy()
    df = df.loc[df["nights"].notna() & (df["nights"] >= 1)].copy()
    cols = [c for c in canonical_columns() if c in df.columns]
    return df[cols]


# Backward compatibility: ResNexus-specific names (delegate to pms_id)
def load_resnexus_csv(path) -> pd.DataFrame:
    """Deprecated: use load_csv(path, 'resnexus') or run with --property and property's pms_id."""
    return load_csv(path, "resnexus")
