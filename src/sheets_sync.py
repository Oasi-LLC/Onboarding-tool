"""
Google Sheets sync for Onboarding EDA.

Pulls raw tab data from the same master "Dashboard revenue reporting" Google
Spreadsheet that Historical Snapshot already syncs from, and writes it to a
local CSV cache in the exact column shape analysts have always downloaded by
hand (File > Download > CSV) into data/<PROPERTY>/. Onboarding's existing
PMS parsers (src/parser.py) are not touched — the cache file is a drop-in
replacement for the manually-placed export, nothing more.

This module intentionally does NOT re-derive revenue, channel, listing name,
or grouping the way Historical Snapshot's io/google_sheets.py does — for
properties whose live tab already carries those as plain columns, raw
pass-through is all that's needed, and re-deriving them here would risk
silently diverging from the sheet's own numbers.

That assumption doesn't hold for every property, though: lafave_zion's live
tab is the raw 11-column ResNexus export (no computed Listing Name or
Channel — those only ever existed in a manually-downloaded, already-joined
CSV), and fbg/wmb's live tabs are narrow Cloudbeds dashboard exports with no
Revenue column at all. For those, and for spoon_mountain's ISO-vs-M/D/YYYY
date mismatch, `PROPERTY_SHAPERS` below adds the minimum a property's
existing parser (src/parser.py, untouched) needs — a multi-tab join, a
computed column, a reformatted date — ported from Historical Snapshot's own
working logic for the same properties (_normalize_lafave, _lafave_channel,
_fbg_wmb_room_revenue in its io/google_sheets.py). Everything else stays
untouched raw pass-through; ingestion still fails loudly on missing required
columns rather than silently producing wrong numbers — see src/parser.py's
required_columns checks.

Env vars (same names, same spreadsheet/service account as Historical Snapshot
— if that tool is already configured on this machine, no new setup needed):
  GOOGLE_SHEETS_SPREADSHEET_ID
  GOOGLE_APPLICATION_CREDENTIALS
  SYNC_TIMEZONE (optional, default Europe/Lisbon — only affects the daily
  staleness check, not the data itself)
"""

from __future__ import annotations

import csv
import io
import json
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from pathlib import Path
from zoneinfo import ZoneInfo

SPREADSHEET_ID_ENV = "GOOGLE_SHEETS_SPREADSHEET_ID"
CREDENTIALS_ENV = "GOOGLE_APPLICATION_CREDENTIALS"
DEFAULT_SYNC_TIMEZONE = "Europe/Lisbon"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_DATA_ROOT = PROJECT_ROOT / "data"


def _canonical_cloudbeds_source(source: str | None) -> str:
    """Map Cloudbeds Source strings (e.g. 'Airbnb (API) (Hotel Collect Booking)')
    to the same canonical channel labels parser.py uses elsewhere."""
    raw = _clean(source)
    if not raw:
        return "Unknown"
    key = raw.lower()
    if "airbnb" in key:
        return "Airbnb"
    if "vrbo" in key:
        return "VRBO"
    if "booking.com" in key:
        return "Booking.com"
    if "expedia" in key:
        return "Expedia"
    if "website" in key or key in {"manual", "direct", "pms", "email"}:
        return "Direct"
    return raw


# ---------------------------------------------------------------------------
# Auth + fetch (ported from Historical Snapshot's io/google_sheets.py —
# generic mechanics only, no per-property normalization)
# ---------------------------------------------------------------------------


def get_spreadsheet_id() -> str:
    spreadsheet_id = os.environ.get(SPREADSHEET_ID_ENV, "").strip()
    if not spreadsheet_id:
        raise ValueError(
            f"Missing {SPREADSHEET_ID_ENV} environment variable for Google Sheets sync."
        )
    return spreadsheet_id


def _require_gspread():
    try:
        import gspread
    except ImportError as exc:
        raise ImportError(
            "Google Sheets sync requires gspread and google-auth. "
            "Install with: pip install gspread google-auth"
        ) from exc
    return gspread


def _open_spreadsheet():
    gspread = _require_gspread()
    from google.oauth2.service_account import Credentials

    credentials_path = os.environ.get(CREDENTIALS_ENV, "").strip()
    if not credentials_path:
        raise ValueError(
            f"Missing {CREDENTIALS_ENV} environment variable pointing to service account JSON."
        )
    creds = Credentials.from_service_account_file(
        credentials_path,
        scopes=["https://www.googleapis.com/auth/spreadsheets.readonly"],
    )
    client = gspread.authorize(creds)
    return client.open_by_key(get_spreadsheet_id())


def _open_worksheet(tab_name: str):
    spreadsheet = _open_spreadsheet()
    try:
        return spreadsheet.worksheet(tab_name)
    except Exception as exc:
        raise ValueError(f"Worksheet tab not found: {tab_name}") from exc


def fetch_tab_rows(tab_name: str) -> list[dict[str, str]]:
    """Fetch a worksheet tab as a list of row dicts, headers exactly as they
    appear in the sheet (same values a human would see downloading the tab
    as CSV — Sheets API returns rendered/computed cell values, not formulas).
    """
    worksheet = _open_worksheet(tab_name)
    values = worksheet.get_all_values()
    if not values:
        return []
    headers = [header.strip() for header in values[0]]
    if not any(headers):
        raise ValueError(f"Worksheet '{tab_name}' has no header row.")

    rows: list[dict[str, str]] = []
    for raw_row in values[1:]:
        if not any(cell.strip() for cell in raw_row):
            continue
        row = {
            headers[index]: (raw_row[index] if index < len(raw_row) else "")
            for index in range(len(headers))
            if headers[index]
        }
        rows.append(row)
    return rows


# ---------------------------------------------------------------------------
# Property data-source config (config/properties/<id>.yaml: data_source block)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SheetTabSource:
    tab: str
    # Cache file name written under data/.cache/sheets/. Defaults to a
    # slugified version of the tab name if not given.
    cache_name: str | None = None

    def resolved_cache_name(self) -> str:
        return self.cache_name or (self.tab.strip().lower().replace(" ", "_") + ".csv")


@dataclass(frozen=True)
class PropertyDataSource:
    type: str
    tabs: tuple[SheetTabSource, ...] = ()

    @property
    def is_google_sheets(self) -> bool:
        return self.type == "google_sheets"

    @classmethod
    def from_dict(cls, data: dict | None) -> "PropertyDataSource | None":
        if not data:
            return None
        tabs_data = data.get("tabs") or ([{"tab": data["tab"]}] if data.get("tab") else [])
        return cls(
            type=data["type"],
            tabs=tuple(
                SheetTabSource(tab=item["tab"], cache_name=item.get("cache_name"))
                for item in tabs_data
            ),
        )


def load_property_data_source(property_id: str, *, config_root: Path | None = None) -> PropertyDataSource | None:
    """Read the `data_source` block from config/properties/<property_id>.yaml, if present."""
    import yaml

    root = config_root or (PROJECT_ROOT / "config" / "properties")
    path = root / f"{property_id}.yaml"
    if not path.is_file():
        return None
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return PropertyDataSource.from_dict(data.get("data_source"))


# ---------------------------------------------------------------------------
# Cache read/write + staleness metadata (ported from Historical Snapshot's
# io/google_sheets.py + io/sync_policy.py)
# ---------------------------------------------------------------------------


def sheets_cache_dir(data_root: Path | str = DEFAULT_DATA_ROOT) -> Path:
    return Path(data_root) / ".cache" / "sheets"


def sheets_cache_path(cache_name: str, *, data_root: Path | str = DEFAULT_DATA_ROOT) -> Path:
    return sheets_cache_dir(data_root) / cache_name


def sheets_meta_path(cache_name: str, *, data_root: Path | str = DEFAULT_DATA_ROOT) -> Path:
    return sheets_cache_dir(data_root) / f"{Path(cache_name).stem}.meta.json"


def write_cache_csv(path: Path, rows: list[dict[str, str]]) -> int:
    """Write fetched rows to CSV, headers exactly as returned by the sheet
    (raw pass-through — no column renaming or reordering)."""
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        path.write_text("", encoding="utf-8")
        return 0
    # Preserve header order as first-seen across all rows (handles the rare
    # case of a short trailing row from fetch_tab_rows).
    fieldnames: list[str] = []
    for row in rows:
        for key in row.keys():
            if key not in fieldnames:
                fieldnames.append(key)
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=fieldnames)
    writer.writeheader()
    for row in rows:
        writer.writerow(row)
    path.write_text(buffer.getvalue(), encoding="utf-8")
    return len(rows)


def write_sync_metadata(meta_path: Path, *, tab: str, row_count: int, synced_at: str) -> None:
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    meta_path.write_text(
        json.dumps({"tab": tab, "row_count": row_count, "synced_at": synced_at}, indent=2),
        encoding="utf-8",
    )


def read_sync_metadata(meta_path: Path) -> dict | None:
    if not meta_path.is_file():
        return None
    return json.loads(meta_path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Per-property shaping. Only registered for properties whose live tab(s)
# don't already satisfy their existing parser's required_columns as raw
# pass-through — see module docstring. Each shaper receives
# {tab_name: [row_dict, ...]} for every tab configured in that property's
# data_source and returns {cache_name: (source_tab_label, [row_dict, ...])}
# — one or more named outputs to write to data/.cache/sheets/. src/parser.py
# is never touched; these only ever add/reformat the specific columns a
# parser's required_columns check needs.
# ---------------------------------------------------------------------------

RESORT_FEE_PER_NIGHT = Decimal("35")

# Ported from Historical Snapshot io/google_sheets.py LAFAVE_GROUPINGS — same
# needle-in-listing-name → pricing group labels used in the master sheet.
LAFAVE_GROUPINGS: tuple[tuple[str, str], ...] = (
    ("Temple of Sinawava", "Suite BIG (1BR/1BA)"),
    ("Sentinel", "Suite BIG (1BR/1BA)"),
    ("Sundial", "Suite BIG (1BR/1BA)"),
    ("Watchman", "Suite SMALL (1BR/1BA)"),
    ("Zion", "Suite SMALL (1BR/1BA)"),
    ("Emerald Pools", "Villa Game (2BR/2BA)"),
    ("Subway", "Villa Game (2BR/2BA)"),
    ("Meridian Tower", "Villa Game (2BR/2BA)"),
    ("Lava Point", "Villa Game (2BR/2BA)"),
    ("Checkerboard Mesa", "Premium Villa (2BR/2BA)"),
    ("Echo Canyon", "Premium Villa (2BR/2BA)"),
    ("Mountain of the Sun", "Premium Villa (2BR/2BA)"),
    ("Mystery Falls", "Premium Villa (2BR/2BA)"),
    ("Big Springs", "Premium Villa (2BR/2BA)"),
    ("East Temple", "Premium Villa (2BR/2BA)"),
    ("Phantom Valley", "Premium Villa (2BR/2BA)"),
    ("Pine Creek", "Premium Villa (2BR/2BA)"),
    ("Hidden Canyon", "Deluxe Villa (2BR/1BA)"),
    ("Kolob Arch", "Deluxe Villa (2BR/1BA)"),
    ("Northgate Peaks", "Deluxe Villa (2BR/1BA)"),
    ("Orderville Canyon", "Deluxe Villa (2BR/1BA)"),
    ("Kayenta", "Deluxe Villa (2BR/1BA)"),
    ("Mount Kinesava", "Deluxe Villa (2BR/1BA)"),
    ("Weeping Rock", "Deluxe Villa (2BR/1BA)"),
    ("Angels Landing", "Premier Villa (3BR/3BA)"),
    ("Narrows", "Premier Villa (3BR/3BA)"),
    ("Cathedral Mountain", "Premier Villa (3BR/3BA)"),
    ("Virgin River", "Premier Villa (3BR/3BA)"),
    ("Johnson Mountain", "J.MT. Villa (3BR/2BA)"),
    ("Gallery House", "House (6BR/4BA)"),
)


def _lafave_grouping(listing_name: str) -> str:
    for needle, grouping in LAFAVE_GROUPINGS:
        if needle.lower() in listing_name.lower():
            return grouping
    return ""


def _clean(value: str | None) -> str:
    return (value or "").strip()


def _reservation_key(value: str | None) -> str:
    """Normalizes a reservation id for cross-tab joins (e.g. "130593" and
    "130593.0" both key to "130593"). Ported from Historical Snapshot's
    io/google_sheets.py — same join keys, same tabs."""
    raw = _clean(value)
    if not raw:
        return ""
    try:
        number = Decimal(raw)
    except InvalidOperation:
        return raw
    if number == number.to_integral_value():
        return str(int(number))
    return format(number.normalize(), "f")


def _rows_by_key(rows: list[dict[str, str]], key: str) -> dict[str, dict[str, str]]:
    lookup: dict[str, dict[str, str]] = {}
    for row in rows:
        normalized = _reservation_key(row.get(key))
        if normalized:
            lookup[normalized] = row
    return lookup


def _decimal_or_zero(value: str | None) -> Decimal:
    raw = _clean(value).replace("$", "").replace(",", "")
    if not raw:
        return Decimal("0")
    try:
        return Decimal(raw)
    except InvalidOperation:
        return Decimal("0")


def _shape_lafave_zion(rows_by_tab: dict[str, list[dict[str, str]]]) -> dict[str, tuple[str, list[dict[str, str]]]]:
    """Join the live 11-column Lafave_data (raw ResNexus export) with
    Lafave_data2 (Res# -> Unit) and Lafave_OTA_data (Reservation -> Channel
    Name) so the output satisfies Onboarding's _RESNEXUS_SHEET_REQUIRED
    (needs Listing Name + Reservation Date) plus an explicit Channel column.
    Same 3-tab join and same By Phone/Booked Online -> Direct, 3rd Party ->
    OTA-lookup logic as Historical Snapshot's _normalize_lafave /
    _lafave_channel (io/google_sheets.py) — this is the tab set Historical
    Snapshot already relies on for the same property.

    Only Lafave_data produces an ingestable output; Lafave_data2 and
    Lafave_OTA_data are pure lookup tables and are not written as their own
    cache files.
    """
    base_rows = rows_by_tab.get("Lafave_data", [])
    helper_rows = rows_by_tab.get("Lafave_data2", [])
    ota_rows = rows_by_tab.get("Lafave_OTA_data", [])

    listing_lookup = _rows_by_key(helper_rows, "Res#")
    ota_lookup = _rows_by_key(ota_rows, "Reservation")

    shaped_rows: list[dict[str, str]] = []
    for row in base_rows:
        reservation_id = _reservation_key(row.get("Res#"))
        helper_row = listing_lookup.get(reservation_id, {})
        listing_name = _clean(helper_row.get("Unit"))

        by_phone = _clean(row.get("By Phone")).upper() == "X"
        booked_online = _clean(row.get("Booked Online")).upper() == "X"
        if by_phone or booked_online:
            channel = "Direct"
        elif _clean(row.get("3rd Party")):
            ota_row = ota_lookup.get(reservation_id)
            channel = (_clean(ota_row.get("Channel Name")) if ota_row else "") or "Other"
        else:
            channel = "Other"

        # "Date & Time" e.g. "1/1/23 20:47" -> "1/1/23" (date portion only;
        # still M/D/YY, which Onboarding's _parse_date_resnexus already
        # accepts alongside M/D/YYYY).
        reservation_date = _clean(row.get("Date & Time")).split(" ")[0]

        shaped = dict(row)
        shaped["Listing Name"] = listing_name
        shaped["Reservation Date"] = reservation_date
        shaped["Channel"] = channel
        shaped["Grouping"] = _lafave_grouping(listing_name)
        shaped_rows.append(shaped)

    return {"lafave_data.csv": ("Lafave_data", shaped_rows)}


def _shape_fbg_or_wmb(
    rows_by_tab: dict[str, list[dict[str, str]]],
    *,
    tab_name: str,
    cache_name: str,
    waive_resort_fee_for_airbnb: bool,
) -> dict[str, tuple[str, list[dict[str, str]]]]:
    """The live FBG_data/WMB_data tabs are narrow Cloudbeds dashboard exports:
    no Revenue column, no guest-name column, and no "Property"-prefixed
    column at all. Onboarding's cloudbeds parser (_cloudbeds_map_to_canonical)
    requires Revenue, and separately assumes at least one "Property"/
    "Property.N" column exists (crashes comparing real NaN vs string when
    none do) and that "Name" is present (df.get("Name", "") on a fully
    missing column returns the bare default instead of a Series, which also
    crashes). Add: Revenue via Historical Snapshot's exact resort-fee formula
    (Accommodation Total + $35 x Nights, waived for Airbnb only on FBG, never
    on WMB — _fbg_wmb_room_revenue in its io/google_sheets.py), plus blank
    Name and Property columns so those two existing code paths see a normal
    string column instead of a missing one (Historical doesn't carry a guest
    name for these tabs either — it isn't in the source data, so there's
    nothing truthful to fill in; blank Property is a no-op since
    _cloudbeds_map_to_canonical only uses it when it varies per row). Also
    copies Room Type into a Listing Name column: the parser's own logic
    already says "use Listing Name if present, else Room Type" for unit_id,
    but that fallback silently resolves to blank rather than Room Type when
    Listing Name is a fully-missing column (same pandas NaN-through-astype(str)
    issue as above) -- pre-filling it here makes the parser's already-declared
    intent actually take effect instead of adding a new rule.

    Deliberately a single space, not "": pandas (3.x observed) round-trips a
    genuinely empty CSV cell back as float NaN rather than the string "",
    and part of _cloudbeds_map_to_canonical's Property-column handling calls
    .lower() on raw unique() values -- which crashes on an actual float NaN.
    A space is a non-empty cell (survives the CSV round-trip as a real
    string) and still normalizes to "" wherever the parser calls
    .str.strip().
    """
    rows = rows_by_tab.get(tab_name, [])
    shaped_rows: list[dict[str, str]] = []
    for row in rows:
        accommodation = _decimal_or_zero(row.get("Accommodation Total"))
        nights_raw = _clean(row.get("Nights"))
        try:
            nights = Decimal(nights_raw) if nights_raw else Decimal("0")
        except InvalidOperation:
            nights = Decimal("0")
        source = _clean(row.get("Source"))
        resort_fee = RESORT_FEE_PER_NIGHT * nights
        if waive_resort_fee_for_airbnb and _canonical_cloudbeds_source(source) == "Airbnb":
            resort_fee = Decimal("0")
        revenue = accommodation + resort_fee

        shaped = dict(row)
        shaped["Revenue"] = format(revenue, "f")
        shaped["Name"] = " "
        shaped["Property"] = " "
        shaped["Listing Name"] = _clean(row.get("Room Type"))
        shaped_rows.append(shaped)

    return {cache_name: (tab_name, shaped_rows)}


def _shape_fbg(rows_by_tab: dict[str, list[dict[str, str]]]) -> dict[str, tuple[str, list[dict[str, str]]]]:
    return _shape_fbg_or_wmb(
        rows_by_tab, tab_name="FBG_data", cache_name="fbg_data.csv", waive_resort_fee_for_airbnb=True
    )


def _shape_wmb(rows_by_tab: dict[str, list[dict[str, str]]]) -> dict[str, tuple[str, list[dict[str, str]]]]:
    return _shape_fbg_or_wmb(
        rows_by_tab, tab_name="WMB_data", cache_name="wmb_data.csv", waive_resort_fee_for_airbnb=False
    )


def _reformat_iso_date(value: str | None) -> str:
    """"YYYY-MM-DD" -> "M/D/YYYY" (what Onboarding's ownerrez parser /
    _parse_date_resnexus expects). Values that aren't ISO dates (already
    M/D/YYYY, blank, etc.) are left untouched."""
    raw = _clean(value)
    if not raw:
        return raw
    try:
        parsed = datetime.strptime(raw, "%Y-%m-%d")
    except ValueError:
        return raw
    return f"{parsed.month}/{parsed.day}/{parsed.year}"


def _shape_spoon_mountain(rows_by_tab: dict[str, list[dict[str, str]]]) -> dict[str, tuple[str, list[dict[str, str]]]]:
    """Live SpoonMount_data returns Arrival/Departure/Booked as ISO
    (YYYY-MM-DD) dates. Onboarding's ownerrez parser (_parse_date_resnexus)
    only accepts M/D/YYYY or M/D/YY, so every arrival/departure/booking date
    comes back null and downstream datetime math breaks. Reformat just those
    3 columns; the tab's other columns already match _OWNERREZ_REQUIRED as
    raw pass-through.
    """
    rows = rows_by_tab.get("SpoonMount_data", [])
    shaped_rows: list[dict[str, str]] = []
    for row in rows:
        shaped = dict(row)
        for col in ("Arrival", "Departure", "Booked"):
            if col in shaped:
                shaped[col] = _reformat_iso_date(shaped[col])
        shaped_rows.append(shaped)
    return {"spoonmount_data.csv": ("SpoonMount_data", shaped_rows)}


PROPERTY_SHAPERS = {
    "lafave_zion": _shape_lafave_zion,
    "fbg": _shape_fbg,
    "wmb": _shape_wmb,
    "spoon_mountain": _shape_spoon_mountain,
}


def shape_property_rows(
    property_id: str,
    rows_by_tab: dict[str, list[dict[str, str]]],
    data_source: "PropertyDataSource",
) -> dict[str, tuple[str, list[dict[str, str]]]]:
    """Returns {cache_name: (source_tab_label, rows)} — what actually gets
    written to data/.cache/sheets/ and what run_ingestion.py --from-sheets
    treats as ingestable CSVs. Default (no shaper registered for this
    property) is raw pass-through, one cache file per configured tab —
    unchanged behavior from before this module had any shaping logic."""
    shaper = PROPERTY_SHAPERS.get(property_id)
    if shaper:
        return shaper(rows_by_tab)
    return {
        tab.resolved_cache_name(): (tab.tab, rows_by_tab.get(tab.tab, []))
        for tab in data_source.tabs
    }


# ---------------------------------------------------------------------------
# Sync orchestration
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class TabSyncResult:
    property_id: str
    tab: str
    cache_path: Path
    row_count: int
    synced_at: str


def sync_tab(
    property_id: str,
    source: SheetTabSource,
    *,
    data_root: Path | str = DEFAULT_DATA_ROOT,
) -> TabSyncResult:
    """Fetch and cache a single tab, raw pass-through, no shaping. Not used by
    sync_property for properties with a registered shaper (see
    PROPERTY_SHAPERS) — kept as a building block for simple 1:1 tab->cache
    syncs."""
    rows = fetch_tab_rows(source.tab)
    cache_name = source.resolved_cache_name()
    cache_path = sheets_cache_path(cache_name, data_root=data_root)
    row_count = write_cache_csv(cache_path, rows)
    synced_at = datetime.now(timezone.utc).isoformat()
    write_sync_metadata(
        sheets_meta_path(cache_name, data_root=data_root),
        tab=source.tab,
        row_count=row_count,
        synced_at=synced_at,
    )
    return TabSyncResult(
        property_id=property_id,
        tab=source.tab,
        cache_path=cache_path,
        row_count=row_count,
        synced_at=synced_at,
    )


def sync_property(
    property_id: str,
    *,
    data_root: Path | str = DEFAULT_DATA_ROOT,
) -> list[TabSyncResult]:
    """Sync every sheet tab configured for this property, then shape into the
    output(s) actually written to data/.cache/sheets/ (see
    shape_property_rows — identity pass-through unless a shaper is
    registered for this property_id). Returns one result per output."""
    data_source = load_property_data_source(property_id)
    if data_source is None or not data_source.is_google_sheets:
        raise ValueError(
            f"Property '{property_id}' has no google_sheets data_source configured "
            f"in config/properties/{property_id}.yaml."
        )
    rows_by_tab = {tab.tab: fetch_tab_rows(tab.tab) for tab in data_source.tabs}
    shaped = shape_property_rows(property_id, rows_by_tab, data_source)

    results: list[TabSyncResult] = []
    for cache_name, (source_tab, rows) in shaped.items():
        cache_path = sheets_cache_path(cache_name, data_root=data_root)
        row_count = write_cache_csv(cache_path, rows)
        synced_at = datetime.now(timezone.utc).isoformat()
        write_sync_metadata(
            sheets_meta_path(cache_name, data_root=data_root),
            tab=source_tab,
            row_count=row_count,
            synced_at=synced_at,
        )
        results.append(
            TabSyncResult(
                property_id=property_id,
                tab=source_tab,
                cache_path=cache_path,
                row_count=row_count,
                synced_at=synced_at,
            )
        )
    return results


def cached_paths_for_property(
    property_id: str,
    *,
    data_root: Path | str = DEFAULT_DATA_ROOT,
) -> list[Path]:
    """Cache file paths this property's --from-sheets ingestion will read,
    without syncing. Matches sync_property's actual outputs (post-shaping) —
    not necessarily one path per configured tab (e.g. lafave_zion's 3 tabs
    join into a single output)."""
    data_source = load_property_data_source(property_id)
    if data_source is None or not data_source.is_google_sheets:
        return []
    empty_rows_by_tab = {tab.tab: [] for tab in data_source.tabs}
    shaped = shape_property_rows(property_id, empty_rows_by_tab, data_source)
    return [sheets_cache_path(cache_name, data_root=data_root) for cache_name in shaped.keys()]


# ---------------------------------------------------------------------------
# Daily staleness policy (ported from Historical Snapshot's io/sync_policy.py)
# ---------------------------------------------------------------------------


def sync_timezone(name: str | None = None) -> ZoneInfo:
    tz_name = (name or os.environ.get("SYNC_TIMEZONE") or DEFAULT_SYNC_TIMEZONE).strip()
    try:
        return ZoneInfo(tz_name)
    except Exception as exc:
        raise ValueError(f"Invalid SYNC_TIMEZONE: {tz_name}") from exc


def _parse_synced_at(synced_at_iso: str) -> datetime:
    raw = synced_at_iso.strip()
    if raw.endswith("Z"):
        raw = raw[:-1] + "+00:00"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def synced_on_date(synced_at_iso: str, *, day: date, tz: ZoneInfo) -> bool:
    synced_at = _parse_synced_at(synced_at_iso)
    return synced_at.astimezone(tz).date() == day


def needs_sync(
    property_id: str,
    *,
    data_root: Path | str = DEFAULT_DATA_ROOT,
    tz: ZoneInfo | None = None,
    today: date | None = None,
) -> tuple[bool, str]:
    """True if any of this property's tabs are missing from cache or weren't
    synced today (Lisbon time by default) — mirrors Historical Snapshot's
    once-a-day sync policy so both tools share the same refresh cadence."""
    zone = tz or sync_timezone()
    day = today or datetime.now(zone).date()
    data_source = load_property_data_source(property_id)
    if data_source is None or not data_source.is_google_sheets:
        return False, "not a google_sheets property"

    # Check staleness against actual outputs (post-shaping), not raw
    # configured tabs — e.g. lafave_zion's 3 source tabs join into 1 output,
    # and that 1 output cache file is what --from-sheets actually reads.
    empty_rows_by_tab = {tab.tab: [] for tab in data_source.tabs}
    shaped = shape_property_rows(property_id, empty_rows_by_tab, data_source)

    stale: list[str] = []
    for cache_name in shaped.keys():
        cache_path = sheets_cache_path(cache_name, data_root=data_root)
        meta_path = sheets_meta_path(cache_name, data_root=data_root)
        if not cache_path.is_file():
            stale.append(f"{cache_name}: missing cache")
            continue
        meta = read_sync_metadata(meta_path)
        if meta is None:
            stale.append(f"{cache_name}: missing sync metadata")
            continue
        synced_at = meta.get("synced_at")
        if not synced_at or not isinstance(synced_at, str):
            stale.append(f"{cache_name}: invalid synced_at")
            continue
        try:
            if not synced_on_date(synced_at, day=day, tz=zone):
                stale.append(f"{cache_name}: last synced {synced_at}")
        except ValueError:
            stale.append(f"{cache_name}: unparseable synced_at")

    if stale:
        return True, "; ".join(stale)
    return False, f"all {len(shaped)} output(s) synced today ({zone.key})"
