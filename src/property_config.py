"""
Load property inventory (room count, room types) for occupancy and RevPAR.
Config files live in config/properties/<property_id>.yaml
"""

from pathlib import Path
from typing import Any

# Project root (parent of src)
PROJECT_ROOT = Path(__file__).resolve().parent.parent
PROPERTIES_DIR = PROJECT_ROOT / "config" / "properties"


def load_property_inventory(property_id: str) -> dict[str, Any]:
    """
    Load property config by id (e.g. 'lafave_zion').
    Returns dict with: property_id, property_name, room_count, room_types (list of unit_id strings).
    """
    path = PROPERTIES_DIR / f"{property_id}.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Property config not found: {path}")

    try:
        import yaml
    except ImportError:
        raise ImportError("PyYAML required: pip install PyYAML")

    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}

    room_count = data.get("room_count")
    room_types = data.get("room_types") or []
    unit_ids = [r.get("unit_id") for r in room_types if isinstance(r, dict) and r.get("unit_id")]
    pms_id = data.get("pms_id")  # e.g. "resnexus" → links to config/pms/resnexus_mapping.yaml
    pricing = data.get("pricing") or {}
    listing_groups = data.get("listing_groups") or []  # [{ name, bedrooms?, bathrooms?, unit_ids: [...] }]
    tiering = data.get("tiering") or {}
    airdna = data.get("airdna") or {}
    analysis_window = data.get("analysis_window") or {}
    ingestion_sources = data.get("ingestion_sources") or []

    # listing_unit_counts: {unit_id: int} — number of physical units behind each listing type.
    # unit_count may be 0 for overlay SKUs that share another listing's inventory (see listing_unit_capacity_fallback).
    # Omitting unit_count means 1 physical unit.
    listing_unit_counts: dict[str, int] = {}
    for r in room_types:
        if isinstance(r, dict) and r.get("unit_id") and "unit_count" in r and r["unit_count"] is not None:
            try:
                listing_unit_counts[str(r["unit_id"])] = int(r["unit_count"])
            except (TypeError, ValueError):
                pass

    raw_fb = data.get("listing_unit_capacity_fallback") or {}
    listing_unit_capacity_fallback: dict[str, str] = {}
    if isinstance(raw_fb, dict):
        for k, v in raw_fb.items():
            ks, vs = str(k).strip(), str(v).strip()
            if ks and vs:
                listing_unit_capacity_fallback[ks] = vs

    # Optional: restrict combined month-of-year ranking (pricing signal) without changing other tables.
    monthly_performance_combined = data.get("monthly_performance_combined") or {}
    if not isinstance(monthly_performance_combined, dict):
        monthly_performance_combined = {}

    analysis_unit_filter = bool(data.get("analysis_unit_filter", False))

    capacity_schedule = data.get("capacity_schedule") or []
    if not isinstance(capacity_schedule, list):
        capacity_schedule = []
    summer = data.get("summer") or {}
    if not isinstance(summer, dict):
        summer = {}
    eras = data.get("eras") or []
    if not isinstance(eras, list):
        eras = []

    return {
        "property_id": data.get("property_id", property_id),
        "property_name": data.get("property_name", property_id),
        "pms_id": pms_id,
        "room_count": room_count if isinstance(room_count, int) else None,
        "room_types": room_types,
        "unit_ids": unit_ids,
        "analysis_unit_filter": analysis_unit_filter,
        "listing_groups": listing_groups,
        "pricing": pricing,
        "tiering": tiering,
        "airdna": airdna,
        "analysis_window": analysis_window,
        "ingestion_sources": ingestion_sources,
        "listing_unit_counts": listing_unit_counts,
        "listing_unit_capacity_fallback": listing_unit_capacity_fallback,
        "monthly_performance_combined": monthly_performance_combined,
        "capacity_schedule": capacity_schedule,
        "summer": summer,
        "eras": eras,
    }
