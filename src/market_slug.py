from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
MARKET_MAP_PATH = PROJECT_ROOT / "config" / "market_slug_map.yaml"


def _slugify(value: str) -> str:
    s = (value or "").strip().lower()
    out = []
    prev_us = False
    for ch in s:
        if ch.isalnum():
            out.append(ch)
            prev_us = False
        else:
            if not prev_us:
                out.append("_")
                prev_us = True
    return "".join(out).strip("_")


def load_market_slug_map(property_id: str, inventory: dict[str, Any]) -> dict[str, str]:
    """
    Return {unit_id: market_slug} for a property.
    Prefers explicit map in config/market_slug_map.yaml, otherwise derives
    from property airdna.submarket_pulls.
    """
    property_id = str(property_id or "").strip().lower()
    sub_to_slug: dict[str, str] = {}
    listing_to_slug: dict[str, str] = {}
    if MARKET_MAP_PATH.exists():
        data = yaml.safe_load(MARKET_MAP_PATH.read_text(encoding="utf-8")) or {}
        prop_block = (data.get(property_id) or {})
        raw = (prop_block.get("submarket_to_market_slug") or {})
        sub_to_slug = {str(k).strip(): str(v).strip() for k, v in raw.items() if str(k).strip() and str(v).strip()}
        raw_listing = (prop_block.get("listing_to_market_slug") or {})
        listing_to_slug = {
            str(k).strip(): str(v).strip()
            for k, v in raw_listing.items()
            if str(k).strip() and str(v).strip()
        }

    out: dict[str, str] = {}
    pulls = ((inventory.get("airdna") or {}).get("submarket_pulls") or [])
    for p in pulls:
        submarket = str((p or {}).get("submarket", "")).strip()
        if not submarket:
            continue
        slug = sub_to_slug.get(submarket) or _slugify(submarket)
        for unit in (p or {}).get("listings") or []:
            u = str(unit).strip()
            if u:
                out[u] = slug
    # Explicit listing-level overrides take precedence over derived mapping.
    out.update(listing_to_slug)
    return out
