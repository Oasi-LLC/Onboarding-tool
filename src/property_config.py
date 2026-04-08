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

    return {
        "property_id": data.get("property_id", property_id),
        "property_name": data.get("property_name", property_id),
        "pms_id": pms_id,
        "room_count": room_count if isinstance(room_count, int) else None,
        "room_types": room_types,
        "unit_ids": unit_ids,
        "listing_groups": listing_groups,
        "pricing": pricing,
        "tiering": tiering,
        "airdna": airdna,
    }
