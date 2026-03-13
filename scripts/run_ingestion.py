#!/usr/bin/env python3
"""
Run ingestion and validation for a given property.
Uses the property's config (pms_id, etc.) to select the correct parser — not hardcoded to ResNexus.

Usage (run from project root):
  python scripts/run_ingestion.py --property <property_id> <path_to_csv> [--output-canonical path] [--output-report path]

Example:
  python scripts/run_ingestion.py --property lafave_zion "data/LAFAVE ZION/Jan23-Jan27.csv" --output-canonical output/ingestion/canonical.csv --output-report output/ingestion/validation_report.json
"""

import json
import sys
from pathlib import Path

import pandas as pd

# Project root = parent of scripts/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.parser import load_csv, normalize
from src.property_config import load_property_inventory
from src.validation import run_validation, validation_report_text


def main():
    args = sys.argv[1:]
    property_id = None
    csv_path = None
    output_canonical = None
    output_report = None

    i = 0
    while i < len(args):
        if args[i] == "--property" and i + 1 < len(args):
            property_id = args[i + 1]
            i += 2
        elif args[i] == "--output-canonical" and i + 1 < len(args):
            output_canonical = Path(args[i + 1])
            i += 2
        elif args[i] == "--output-report" and i + 1 < len(args):
            output_report = Path(args[i + 1])
            i += 2
        elif not args[i].startswith("--"):
            csv_path = Path(args[i])
            i += 1
        else:
            i += 1

    if not property_id:
        print("Usage: python scripts/run_ingestion.py --property <property_id> <path_to_csv> [--output-canonical path] [--output-report path]")
        print("Example: python scripts/run_ingestion.py --property lafave_zion \"data/LAFAVE ZION/Jan23-Jan27.csv\" --output-canonical output/lafave_zion/ingestion/canonical.csv")
        sys.exit(1)
    if not csv_path or not csv_path.exists():
        print(f"Error: CSV file required and must exist. Got: {csv_path}")
        sys.exit(1)

    try:
        inv = load_property_inventory(property_id)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)
    pms_id = inv.get("pms_id")
    if not pms_id:
        print(f"Error: Property '{property_id}' has no pms_id in config. Add pms_id (e.g. resnexus) to config/properties/{property_id}.yaml")
        sys.exit(1)

    print(f"Property: {inv.get('property_name', property_id)} (pms_id: {pms_id})")
    print(f"Loading: {csv_path}")
    raw = load_csv(csv_path, pms_id)
    print(f"  Rows loaded: {len(raw)}")

    print("\nRunning validation...")
    report = run_validation(raw, pms_id)
    text = validation_report_text(report)
    print(text)

    # Default output locations if not provided: per-property folders under output/
    if output_canonical is None:
        output_canonical = PROJECT_ROOT / "output" / property_id / "ingestion" / "canonical.csv"
    if output_report is None:
        output_report = PROJECT_ROOT / "output" / property_id / "ingestion" / "validation_report.json"

    if output_report:
        output_report = Path(output_report)
        output_report.parent.mkdir(parents=True, exist_ok=True)
        def _serialize(obj):
            if hasattr(obj, "isoformat"):
                return obj.isoformat()
            try:
                import numpy as np
                if isinstance(obj, np.bool_):
                    return bool(obj)
                if isinstance(obj, (np.integer, np.int64, np.int32)):
                    return int(obj)
                if isinstance(obj, (np.floating, np.float64, np.float32)):
                    return float(obj)
            except (ImportError, AttributeError):
                pass
            if isinstance(obj, dict):
                return {k: _serialize(v) for k, v in obj.items()}
            if isinstance(obj, list):
                return [_serialize(v) for v in obj]
            return obj
        with open(output_report, "w") as f:
            json.dump(_serialize(report), f, indent=2)
        print(f"Report written to: {output_report}")

    canonical = normalize(raw, pms_id, exclude_unpaid_below_amount=True, exclude_invalid_revenue=True)
    excluded = len(raw) - len(canonical)
    print(f"\nCanonical rows: {len(canonical)} (excluded {excluded} invalid or unpaid rows from {len(raw)} raw)")

    if output_canonical:
        output_canonical = Path(output_canonical)
        output_canonical.parent.mkdir(parents=True, exist_ok=True)
        out = canonical.copy()
        for col in ["arrival_date", "departure_date", "booking_date"]:
            if col not in out.columns:
                continue
            if pd.api.types.is_datetime64_any_dtype(out[col]):
                out[col] = out[col].dt.strftime("%Y-%m-%d")
            else:
                out[col] = out[col].astype(str)
        out.to_csv(output_canonical, index=False)
        print(f"Canonical CSV written to: {output_canonical}")

    if not report["summary"].get("can_continue", False):
        print("\nValidation reported errors; invalid rows were excluded from the canonical output.")
        sys.exit(1)

    print("\nDone.")


if __name__ == "__main__":
    main()
