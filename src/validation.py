"""
Validation for PMS-sourced data.
Runs column-, cell-, row-, and dataset-level checks; returns a structured report.
Uses pms_id (from property config) to get required/channel columns and to map raw → canonical.
"""

from datetime import datetime
from typing import Any

import pandas as pd

from .parser import (
    get_channel_columns,
    get_required_columns,
    map_to_canonical,
)

# Severities
ERROR = "error"
WARNING = "warning"
INFO = "info"

# Config
DATE_MIN = datetime(2015, 1, 1)
DATE_MAX = datetime(2030, 12, 31)
ADR_OUTLIER_THRESHOLD = 50_000
REVENUE_ZERO_WARN_PCT = 0.10
MONTHS_MIN_FOR_SEASONALITY = 12


def validate_columns(raw: pd.DataFrame, pms_id: str) -> list[dict[str, Any]]:
    """Column-level checks on raw CSV (uses required/channel columns for this PMS)."""
    required = get_required_columns(pms_id)
    channel_cols = get_channel_columns(pms_id)
    results = []
    missing = [c for c in required if c not in raw.columns]
    results.append({
        "check": "required_columns_present",
        "severity": ERROR if missing else None,
        "passed": len(missing) == 0,
        "message": f"Missing required columns: {missing}" if missing else "All required columns present",
        "detail": {"missing": missing},
    })
    channel_ok = any(c in raw.columns for c in channel_cols)
    results.append({
        "check": "channel_columns_present",
        "severity": ERROR if not channel_ok else None,
        "passed": channel_ok,
        "message": "At least one channel column present" if channel_ok else "No channel columns found",
    })
    dupes = raw.columns[raw.columns.duplicated()].tolist()
    results.append({
        "check": "no_duplicate_columns",
        "severity": ERROR if dupes else None,
        "passed": len(dupes) == 0,
        "message": f"Duplicate columns: {dupes}" if dupes else "No duplicate columns",
        "detail": {"duplicates": dupes},
    })
    return results


def validate_cells(canonical: pd.DataFrame) -> list[dict[str, Any]]:
    """Cell-level checks on canonical (mapped) dataframe."""
    results = []
    n = len(canonical)

    # Res# not empty
    empty_res = canonical["reservation_id"].isna() | (canonical["reservation_id"].astype(str).str.strip() == "")
    count = empty_res.sum()
    results.append({
        "check": "reservation_id_not_empty",
        "severity": ERROR if count > 0 else None,
        "passed": count == 0,
        "message": f"Rows with empty reservation_id: {int(count)}",
        "count": int(count),
        "sample_rows": canonical.loc[empty_res].head(5).to_dict("records") if count else [],
    })

    # Date parseable and range
    bad_dates = canonical["arrival_date"].isna()
    count = int(bad_dates.sum())
    results.append({
        "check": "date_parseable",
        "severity": ERROR if count > 0 else None,
        "passed": count == 0,
        "message": f"Rows with unparseable or missing date: {count}",
        "count": count,
    })
    out_of_range = canonical["arrival_date"].notna() & (
        (canonical["arrival_date"] < DATE_MIN) | (canonical["arrival_date"] > DATE_MAX)
    )
    count_or = int(out_of_range.sum())
    results.append({
        "check": "date_in_reasonable_range",
        "severity": WARNING if count_or > 0 else None,
        "passed": count_or == 0,
        "message": f"Rows with arrival_date outside 2015–2030: {count_or}",
        "count": count_or,
    })

    # Departure > arrival (nights >= 1)
    bad_nights = canonical["nights"].notna() & (canonical["nights"] < 1)
    count = int(bad_nights.sum())
    results.append({
        "check": "departure_after_arrival",
        "severity": ERROR if count > 0 else None,
        "passed": count == 0,
        "message": f"Rows with nights < 1: {count}",
        "count": count,
    })

    # Unit not empty
    empty_unit = canonical["unit_id"].isna() | (canonical["unit_id"].astype(str).str.strip() == "")
    count = int(empty_unit.sum())
    results.append({
        "check": "unit_id_not_empty",
        "severity": ERROR if count > 0 else None,
        "passed": count == 0,
        "message": f"Rows with empty unit_id: {count}",
        "count": count,
    })

    # Amount parseable and >= 0
    bad_amount = canonical["revenue"].isna() | (canonical["revenue"] < 0)
    count = int(bad_amount.sum())
    results.append({
        "check": "amount_parseable_and_non_negative",
        "severity": ERROR if count > 0 else None,
        "passed": count == 0,
        "message": f"Rows with missing or negative revenue: {count}",
        "count": count,
    })

    # Reserved On parseable
    bad_booking = canonical["booking_date"].isna()
    count = int(bad_booking.sum())
    results.append({
        "check": "booking_date_parseable",
        "severity": ERROR if count > 0 else None,
        "passed": count == 0,
        "message": f"Rows with unparseable or missing booking_date: {count}",
        "count": count,
    })

    # Booking <= arrival
    lead_neg = canonical["lead_time_days"].notna() & (canonical["lead_time_days"] < 0)
    count = int(lead_neg.sum())
    results.append({
        "check": "booking_before_arrival",
        "severity": WARNING if count > 0 else None,
        "passed": count == 0,
        "message": f"Rows with booking_date after arrival_date: {count}",
        "count": count,
    })

    return results


def validate_rows(canonical: pd.DataFrame) -> list[dict[str, Any]]:
    """Row-level checks."""
    results = []
    key_cols = ["reservation_id", "unit_id", "arrival_date"]
    if all(c in canonical.columns for c in key_cols):
        dupes = canonical.duplicated(subset=key_cols, keep=False)
        count = int(dupes.sum())
        results.append({
            "check": "no_duplicate_key",
            "severity": WARNING if count > 0 else None,
            "passed": count == 0,
            "message": f"Rows with duplicate (reservation_id, unit_id, arrival_date): {count}",
            "count": count,
        })
    bad_nights = canonical["nights"].notna() & (canonical["nights"] < 1)
    results.append({
        "check": "nights_consistent",
        "severity": ERROR if bad_nights.any() else None,
        "passed": not bad_nights.any(),
        "message": f"Rows with nights < 1: {int(bad_nights.sum())}",
        "count": int(bad_nights.sum()),
    })
    adr_high = canonical["adr"].notna() & (canonical["adr"] > ADR_OUTLIER_THRESHOLD)
    count = int(adr_high.sum())
    results.append({
        "check": "adr_sanity",
        "severity": WARNING if count > 0 else None,
        "passed": count == 0,
        "message": f"Rows with ADR > ${ADR_OUTLIER_THRESHOLD:,}: {count}",
        "count": count,
    })
    return results


def validate_dataset(canonical: pd.DataFrame) -> list[dict[str, Any]]:
    """Dataset-level checks."""
    results = []
    n = len(canonical)
    results.append({
        "check": "row_count",
        "severity": INFO,
        "passed": True,
        "message": f"Total rows: {n}",
        "count": n,
    })
    if canonical["arrival_date"].notna().any():
        min_arr = canonical["arrival_date"].min()
        max_arr = canonical["arrival_date"].max()
        months = (max_arr - min_arr).days / 30.44 if hasattr(max_arr - min_arr, "days") else 0
        results.append({
            "check": "date_range",
            "severity": WARNING if months < MONTHS_MIN_FOR_SEASONALITY else None,
            "passed": months >= MONTHS_MIN_FOR_SEASONALITY,
            "message": f"Arrival range: {min_arr} to {max_arr} (~{months:.1f} months)",
            "detail": {"min_arrival": str(min_arr), "max_arrival": str(max_arr), "months_approx": round(months, 1)},
        })
    zero_rev = (canonical["revenue"] == 0) | canonical["revenue"].isna()
    pct = zero_rev.sum() / n if n else 0
    results.append({
        "check": "revenue_coverage",
        "severity": WARNING if pct > REVENUE_ZERO_WARN_PCT else None,
        "passed": pct <= REVENUE_ZERO_WARN_PCT,
        "message": f"Rows with revenue 0 or null: {int(zero_rev.sum())} ({pct:.1%})",
        "count": int(zero_rev.sum()),
    })
    other_ch = (canonical["channel"] == "Other").sum()
    pct_other = other_ch / n if n else 0
    results.append({
        "check": "channel_mix",
        "severity": WARNING if pct_other > 0.5 else None,
        "passed": pct_other <= 0.5,
        "message": f"Rows with channel 'Other': {int(other_ch)} ({pct_other:.1%})",
        "count": int(other_ch),
    })
    null_paid = canonical["amount_paid"].isna().sum()
    results.append({
        "check": "null_amount_paid",
        "severity": INFO,
        "passed": True,
        "message": f"Rows with null amount_paid: {int(null_paid)}",
        "count": int(null_paid),
    })
    return results


def run_validation(raw: pd.DataFrame, pms_id: str) -> dict[str, Any]:
    """
    Run full validation: column checks on raw (for this PMS), then map to canonical and run cell/row/dataset checks.
    Returns a report with summary and per-check results.
    """
    report = {
        "column_checks": validate_columns(raw, pms_id),
        "cell_checks": [],
        "row_checks": [],
        "dataset_checks": [],
        "summary": {},
    }
    # If required columns missing, we might not be able to map
    if not all(r["passed"] for r in report["column_checks"] if r.get("severity") == ERROR):
        report["summary"] = {
            "can_continue": False,
            "error_count": sum(1 for r in report["column_checks"] if not r["passed"] and r.get("severity") == ERROR),
            "warning_count": 0,
            "message": "Column-level errors; fix before mapping.",
        }
        return report

    canonical = map_to_canonical(raw, pms_id)
    report["cell_checks"] = validate_cells(canonical)
    report["row_checks"] = validate_rows(canonical)
    report["dataset_checks"] = validate_dataset(canonical)

    all_checks = report["column_checks"] + report["cell_checks"] + report["row_checks"] + report["dataset_checks"]
    error_count = sum(1 for r in all_checks if not r["passed"] and r.get("severity") == ERROR)
    warning_count = sum(1 for r in all_checks if not r["passed"] and r.get("severity") == WARNING)

    report["summary"] = {
        "can_continue": error_count == 0,
        "error_count": error_count,
        "warning_count": warning_count,
        "total_rows": len(raw),
        "message": "Validation complete. Do not run analysis." if error_count > 0 else (
            "Validation complete. Review warnings before analysis." if warning_count > 0 else "All checks passed."
        ),
    }
    return report


def validation_report_text(report: dict[str, Any]) -> str:
    """Human-readable summary of the validation report."""
    lines = [
        "=== Validation Report ===",
        "",
        "Summary:",
        f"  Can proceed to analysis: {report['summary'].get('can_continue', False)}",
        f"  Errors: {report['summary'].get('error_count', 0)}",
        f"  Warnings: {report['summary'].get('warning_count', 0)}",
        f"  Total rows: {report['summary'].get('total_rows', 0)}",
        f"  {report['summary'].get('message', '')}",
        "",
    ]
    for section, key in [
        ("Column checks", "column_checks"),
        ("Cell checks", "cell_checks"),
        ("Row checks", "row_checks"),
        ("Dataset checks", "dataset_checks"),
    ]:
        lines.append(f"--- {section} ---")
        for r in report.get(key, []):
            status = "PASS" if r["passed"] else "FAIL"
            sev = r.get("severity", "")
            lines.append(f"  [{status}] {r['check']}: {r['message']}")
        lines.append("")
    return "\n".join(lines)
