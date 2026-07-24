#!/usr/bin/env python3
"""
Run ingestion and validation for a given property.
Uses the property's config (pms_id, etc.) to select the correct parser — not hardcoded to ResNexus.

Usage (run from project root):
  python scripts/run_ingestion.py --property <property_id> <path_to_csv> [--output-canonical path] [--output-report path]

Example:
  python scripts/run_ingestion.py --property lafave_zion "data/LAFAVE ZION/Lafave_data.csv" --output-canonical output/ingestion/canonical.csv --output-report output/ingestion/validation_report.json
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


def _resolve_source_pms(path: Path, ingestion_sources: list[dict], default_pms_id: str) -> str:
    if default_pms_id != "multi":
        return default_pms_id
    file_name = path.name.lower()
    for src in ingestion_sources:
        if not isinstance(src, dict):
            continue
        token = str(src.get("match", "")).strip().lower()
        pms_id = str(src.get("pms_id", "")).strip().lower()
        if token and token in file_name and pms_id:
            return pms_id
    raise ValueError(
        f"Could not resolve pms_id for file '{path.name}'. "
        "Add ingestion_sources with {match, pms_id} in property config."
    )


def main():
    args = sys.argv[1:]
    property_id = None
    csv_paths: list[Path] = []
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
            csv_paths.append(Path(args[i]))
            i += 1
        else:
            i += 1

    if not property_id:
        print("Usage: python scripts/run_ingestion.py --property <property_id> <path_to_csv> [--output-canonical path] [--output-report path]")
        print("Example: python scripts/run_ingestion.py --property lafave_zion \"data/LAFAVE ZION/Lafave_data.csv\" --output-canonical output/lafave_zion/ingestion/canonical.csv")
        sys.exit(1)
    if not csv_paths:
        print("Error: at least one CSV file path is required.")
        sys.exit(1)
    missing_paths = [p for p in csv_paths if not p.exists()]
    if missing_paths:
        print(f"Error: CSV file(s) not found: {', '.join(str(p) for p in missing_paths)}")
        sys.exit(1)

    try:
        inv = load_property_inventory(property_id)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)
    pms_id = inv.get("pms_id")
    ingestion_sources = inv.get("ingestion_sources") or []
    if not pms_id:
        print(f"Error: Property '{property_id}' has no pms_id in config. Add pms_id (e.g. resnexus) to config/properties/{property_id}.yaml")
        sys.exit(1)

    print(f"Property: {inv.get('property_name', property_id)} (pms_id: {pms_id})")
    reports: list[dict] = []
    canonical_frames: list[pd.DataFrame] = []
    total_raw_rows = 0

    for csv_path in csv_paths:
        source_pms_id = _resolve_source_pms(csv_path, ingestion_sources, pms_id)
        print(f"Loading: {csv_path} (parser: {source_pms_id})")
        raw = load_csv(csv_path, source_pms_id)
        print(f"  Rows loaded: {len(raw)}")
        total_raw_rows += len(raw)

        print("\nRunning validation...")
        report = run_validation(raw, source_pms_id, property_id=property_id)
        reports.append({
            "file": str(csv_path),
            "pms_id": source_pms_id,
            "report": report,
        })
        text = validation_report_text(report)
        print(text)

        canonical_part = normalize(
            raw,
            source_pms_id,
            property_id=property_id,
            exclude_unpaid_below_amount=True,
            exclude_invalid_revenue=True,
        )
        canonical_part["source_file"] = csv_path.name
        canonical_part["source_pms_id"] = source_pms_id
        canonical_frames.append(canonical_part)

    allowed_units = None
    if inv.get("analysis_unit_filter") and inv.get("unit_ids"):
        allowed_units = {str(u).strip() for u in inv["unit_ids"] if str(u).strip()}

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
            if len(reports) == 1:
                payload = reports[0]["report"]
            else:
                payload = {
                    "multi_source": True,
                    "property_id": property_id,
                    "sources": reports,
                }
            json.dump(_serialize(payload), f, indent=2)
        print(f"Report written to: {output_report}")
    canonical = pd.concat(canonical_frames, ignore_index=True) if canonical_frames else pd.DataFrame()
    if allowed_units and not canonical.empty and "unit_id" in canonical.columns:
        before = len(canonical)
        canonical = canonical.loc[canonical["unit_id"].astype(str).isin(allowed_units)].copy()
        if before != len(canonical):
            print(
                f"Filtered to analysis listings ({', '.join(sorted(allowed_units))}): "
                f"{len(canonical)} rows kept ({before - len(canonical)} excluded)"
            )
    excluded = total_raw_rows - len(canonical)
    print(f"\nCanonical rows: {len(canonical)} (excluded {excluded} invalid or unpaid rows from {total_raw_rows} raw)")

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

    if not all(r["report"]["summary"].get("can_continue", False) for r in reports):
        print("\nValidation reported errors; invalid rows were excluded from the canonical output.")
        sys.exit(1)

    print("\nDone.")


if __name__ == "__main__":
    main()
