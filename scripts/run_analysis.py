#!/usr/bin/env python3
"""
Run onboarding analysis from canonical CSV per docs/07-analysis-tables-and-formulas.md.
Uses two full calendar years (current_year - 2, current_year - 1). Writes one CSV per table to output/analysis/.

Usage (run from project root):
  python scripts/run_analysis.py --property <property_id> [--canonical path] [--output-dir path]

Example:
  python scripts/run_analysis.py --property lafave_zion --canonical output/ingestion/canonical.csv --output-dir output/analysis
"""

import sys
from pathlib import Path
from typing import Optional

import pandas as pd

# Project root = parent of scripts/
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))

from src.analysis import resolve_analysis_window, run_analysis as run_analysis_tables
from src.property_config import load_property_inventory

OUTPUT_FILES = [
    "overall_summary.csv",
    "monthly_performance.csv",
    "channel_by_year.csv",
    "channel_summary.csv",
    "channel_by_listing.csv",
    "by_day_of_week.csv",
    "booking_window.csv",
    "adr_by_listing_by_month.csv",
]

def _audit_row(check_name: str, status: str, count: int, reason: str) -> dict:
    return {
        "check_name": check_name,
        "status": status,  # pass | fail | warn
        "count": int(count),
        "reason": reason,
    }


def build_tier_integrity_audit(
    tables: dict[str, pd.DataFrame],
    inv: dict,
    output_dir: Path,
) -> pd.DataFrame:
    rows = []
    daily = tables.get("daily_tier_calendar", pd.DataFrame()).copy()
    listing_daily = tables.get("listing_daily_tier_calendar", pd.DataFrame()).copy()
    tier_summary = tables.get("tier_summary", pd.DataFrame()).copy()
    tier_diag = tables.get("tier_diagnostics", pd.DataFrame()).copy()
    tier_cfg = inv.get("tiering") or {}

    # ---- Listing integrity checks ----
    if not listing_daily.empty and "start_date_source" in listing_daily.columns:
        inferred_units = listing_daily.loc[
            listing_daily["start_date_source"] != "config", "unit_id"
        ].dropna().nunique()
        rows.append(_audit_row(
            "listing_start_source_all_config",
            "pass" if inferred_units == 0 else "fail",
            inferred_units,
            "All listings should use config start dates.",
        ))
    if not listing_daily.empty and "out_of_window" in listing_daily.columns:
        oow = listing_daily.loc[listing_daily["out_of_window"] == True, "unit_id"].dropna().nunique()
        rows.append(_audit_row(
            "listing_out_of_window_units",
            "pass" if oow == 0 else "warn",
            oow,
            "Listings outside analysis window should be explicit.",
        ))
    if not listing_daily.empty and all(c in listing_daily.columns for c in ["active", "date", "listing_start_date"]):
        tmp = listing_daily.copy()
        tmp["date"] = pd.to_datetime(tmp["date"], errors="coerce")
        tmp["listing_start_date"] = pd.to_datetime(tmp["listing_start_date"], errors="coerce")
        bad = ((tmp["active"] == True) & (tmp["date"] < tmp["listing_start_date"])).sum()
        rows.append(_audit_row(
            "active_before_listing_start_date",
            "pass" if bad == 0 else "fail",
            int(bad),
            "No active days should exist before configured listing_start_date.",
        ))

    # ---- Portfolio signal checks ----
    units_total = listing_daily["unit_id"].dropna().nunique() if "unit_id" in listing_daily.columns else 0
    if not daily.empty and "contributing_units_count" in daily.columns:
        over = (pd.to_numeric(daily["contributing_units_count"], errors="coerce") > units_total).sum()
        rows.append(_audit_row(
            "contributing_units_not_exceed_total_units",
            "pass" if over == 0 else "fail",
            int(over),
            f"contributing_units_count must be <= units_total ({units_total}).",
        ))
    if not daily.empty and all(c in daily.columns for c in ["contributing_units_min_14d", "contributing_units_max_14d"]):
        delta = pd.to_numeric(daily["contributing_units_max_14d"], errors="coerce") - pd.to_numeric(daily["contributing_units_min_14d"], errors="coerce")
        big_shift = (delta > 2).sum()
        rows.append(_audit_row(
            "composition_shift_gt_2_units_in_14d",
            "warn" if big_shift > 0 else "pass",
            int(big_shift),
            "Large denominator shifts in smoothing window can bias signal interpretation.",
        ))
    # weighted mean denominator consistency check
    if not listing_daily.empty and not daily.empty and all(
        c in listing_daily.columns for c in ["date", "active", "insufficient_data", "listing_revpar_doy_avg", "contributing_year_count"]
    ) and all(c in daily.columns for c in ["date", "revpar"]):
        l = listing_daily.copy()
        l["date"] = pd.to_datetime(l["date"], errors="coerce")
        l = l[(l["active"] == True) & (l["insufficient_data"] == False)].copy()
        l["w"] = pd.to_numeric(l["contributing_year_count"], errors="coerce").fillna(1.0)
        l["x"] = pd.to_numeric(l["listing_revpar_doy_avg"], errors="coerce").fillna(0.0)
        chk = l.groupby("date", as_index=False).agg(wsum=("w", "sum"), xw=("x", lambda s: 0.0))
        # recompute xw with aligned weights
        l["xw"] = l["x"] * l["w"]
        chk = l.groupby("date", as_index=False).agg(wsum=("w", "sum"), xw=("xw", "sum"))
        chk["revpar_expected"] = chk["xw"] / chk["wsum"]
        d = daily.copy()
        d["date"] = pd.to_datetime(d["date"], errors="coerce")
        d["revpar"] = pd.to_numeric(d["revpar"], errors="coerce")
        merged = chk.merge(d[["date", "revpar"]], on="date", how="inner")
        diff = (merged["revpar_expected"] - merged["revpar"]).abs()
        mism = (diff > 1e-6).sum()
        rows.append(_audit_row(
            "weighted_revpar_denominator_consistency",
            "pass" if mism == 0 else "fail",
            int(mism),
            "Portfolio revpar should equal weighted listing DOY mean per day.",
        ))

    # ---- Tier output checks ----
    expected_tiers = int(tier_cfg.get("quantile_fallback_tiers", 5))
    selected_method = str(tier_diag.iloc[0]["selected_method"]) if not tier_diag.empty and "selected_method" in tier_diag.columns else ""
    final_tier_count = int(tier_diag.iloc[0]["final_tier_count"]) if not tier_diag.empty and "final_tier_count" in tier_diag.columns else None
    if final_tier_count is not None:
        status = "pass" if (selected_method == "quantile_fallback" and final_tier_count == expected_tiers) or selected_method != "quantile_fallback" else "fail"
        rows.append(_audit_row(
            "final_tier_count_matches_config_when_quantile",
            status,
            int(final_tier_count),
            f"Expected {expected_tiers} when quantile_fallback selected.",
        ))
    min_days = int(tier_cfg.get("min_days_per_tier", 14))
    if not tier_summary.empty and "days" in tier_summary.columns:
        under = (pd.to_numeric(tier_summary["days"], errors="coerce") < min_days).sum()
        rows.append(_audit_row(
            "all_tiers_meet_min_days_threshold",
            "pass" if under == 0 else "fail",
            int(under),
            f"Each tier should have >= {min_days} days.",
        ))
    if not tier_summary.empty and all(c in tier_summary.columns for c in ["tier_id", "avg_revpar"]):
        s = tier_summary.sort_values("tier_id")
        mono = s["avg_revpar"].is_monotonic_increasing
        rows.append(_audit_row(
            "tier_avg_revpar_monotonic_increasing",
            "pass" if mono else "warn",
            0 if mono else 1,
            "Tier 1 should be lowest avg_revpar and highest tier should be highest.",
        ))
    if not daily.empty and "tier_id" in daily.columns:
        nulls = daily["tier_id"].isna().sum()
        rows.append(_audit_row(
            "no_null_tier_id_in_daily_output",
            "pass" if nulls == 0 else "fail",
            int(nulls),
            "tier_id should be present on all daily rows.",
        ))

    # ---- AirDNA context checks ----
    market_path = output_dir.parent / "benchmark" / "daily_market_context.csv"
    if market_path.exists():
        m = pd.read_csv(market_path)
        cfg_submarkets = sorted({str(p.get("submarket", "")).strip() for p in (inv.get("airdna", {}).get("submarket_pulls", []) or []) if str(p.get("submarket", "")).strip()})
        got_submarkets = sorted(set(m.get("submarket", pd.Series(dtype=str)).dropna().astype(str).tolist()))
        missing = [s for s in cfg_submarkets if s not in got_submarkets]
        rows.append(_audit_row(
            "airdna_all_configured_submarkets_present",
            "pass" if len(missing) == 0 else "fail",
            len(missing),
            f"Missing submarkets: {missing}",
        ))
        if "market_reliability" in m.columns:
            nnull = m["market_reliability"].isna().sum()
            rows.append(_audit_row(
                "airdna_market_reliability_populated",
                "pass" if nnull == 0 else "fail",
                int(nnull),
                "market_reliability should be populated for all rows.",
            ))
        # known circular markets from config (reliability=unreliable)
        circular_cfg = {
            str(p.get("submarket", "")).strip()
            for p in (inv.get("airdna", {}).get("submarket_pulls", []) or [])
            if str(p.get("reliability", "")).strip().lower() == "unreliable"
        }
        if "submarket" in m.columns and "benchmark_warning" in m.columns:
            bad = m[(m["submarket"].isin(circular_cfg)) & (m["benchmark_warning"] != "circular_market")]
            rows.append(_audit_row(
                "airdna_circular_markets_warning_consistency",
                "pass" if len(bad) == 0 else "warn",
                int(len(bad)),
                "Rows in configured circular markets should carry circular_market warning.",
            ))
        if "rpi" in m.columns:
            r = pd.to_numeric(m["rpi"], errors="coerce")
            outlier = ((r > 5.0) | (r < 0.1)).sum()
            rows.append(_audit_row(
                "airdna_rpi_plausible_range",
                "warn" if outlier > 0 else "pass",
                int(outlier),
                "RPI outside [0.1, 5.0] may indicate join or benchmark mismatch.",
            ))
    else:
        rows.append(_audit_row(
            "airdna_context_file_present",
            "warn",
            1,
            f"Missing {market_path}; AirDNA checks skipped.",
        ))

    # ---- Schema checks ----
    if not daily.empty:
        expected_cols = [
            "date", "revpar", "revpar_smoothed", "tier_id", "tier_label", "tier_method",
            "active_units_count", "contributing_units_count", "excluded_units_count",
        ]
        missing_cols = [c for c in expected_cols if c not in daily.columns]
        rows.append(_audit_row(
            "daily_tier_calendar_expected_columns_present",
            "pass" if len(missing_cols) == 0 else "fail",
            len(missing_cols),
            f"Missing columns: {missing_cols}",
        ))
        req_null = daily[["date", "tier_id"]].isna().sum().sum() if all(c in daily.columns for c in ["date", "tier_id"]) else 0
        rows.append(_audit_row(
            "daily_tier_calendar_required_fields_non_null",
            "pass" if req_null == 0 else "fail",
            int(req_null),
            "Required fields (date, tier_id) should not be null.",
        ))
        if "date" in daily.columns:
            d = pd.to_datetime(daily["date"], errors="coerce").dropna().sort_values()
            expected = pd.date_range(d.min(), d.max(), freq="D")
            gap_count = len(expected.difference(pd.DatetimeIndex(d.unique())))
            rows.append(_audit_row(
                "daily_tier_calendar_date_spine_complete",
                "pass" if gap_count == 0 else "fail",
                int(gap_count),
                "Date spine should have no missing days in analysis window.",
            ))

    return pd.DataFrame(rows)


def build_tier_validation_outputs(
    canonical_df: pd.DataFrame,
    room_count: Optional[int],
    base_tiering_cfg: dict,
    listing_start_dates: dict[str, str],
    analysis_window_cfg: dict,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """
    Build automated tier sensitivity outputs so per-property tuning is reproducible.
    Returns:
      - tier_sensitivity_sweep (many rows)
      - tier_validation_summary (condensed recommendation table)
    """
    if not base_tiering_cfg:
        return pd.DataFrame(), pd.DataFrame()

    threshold_values = base_tiering_cfg.get("sweep_gap_thresholds") or [3, 4, 5, 6, 7, 8, 10]
    try:
        threshold_values = [float(x) for x in threshold_values]
    except Exception:
        threshold_values = [3, 4, 5, 6, 7, 8, 10]
    quantile_min = int(base_tiering_cfg.get("sweep_quantile_min_tiers", 5))
    quantile_max = int(base_tiering_cfg.get("sweep_quantile_max_tiers", 10))
    quantile_values = list(range(max(2, quantile_min), max(2, quantile_max) + 1))
    basis_values = ["percentile_int", "revpar_zscore"]

    rows: list[dict] = []
    for basis in basis_values:
        for gap in threshold_values:
            cfg = dict(base_tiering_cfg)
            cfg["gap_score_basis"] = basis
            cfg["gap_threshold_pct"] = float(gap)
            cfg["gap_threshold_zscore"] = float(gap) / 10.0
            tables = run_analysis_tables(
                canonical_df,
                room_count=room_count,
                tiering_cfg=cfg,
                listing_start_dates=listing_start_dates,
                analysis_window=analysis_window_cfg,
            )
            diag = tables.get("tier_diagnostics", pd.DataFrame())
            summ = tables.get("tier_summary", pd.DataFrame()).sort_values("tier_id")
            d = diag.iloc[0].to_dict() if not diag.empty else {}
            if not summ.empty and "avg_revpar" in summ.columns:
                steps = pd.to_numeric(summ["avg_revpar"], errors="coerce").diff().dropna()
                min_step = float(steps.min()) if len(steps) else None
                avg_step = float(steps.mean()) if len(steps) else None
            else:
                min_step, avg_step = None, None
            rows.append(
                {
                    "sweep_type": "gap_threshold",
                    "gap_score_basis": basis,
                    "gap_threshold_value": float(gap),
                    "quantile_fallback_tiers": int(cfg.get("quantile_fallback_tiers", 5)),
                    "selected_method": d.get("selected_method"),
                    "fallback_used": bool(d.get("fallback_used", False)),
                    "fallback_reason": d.get("fallback_reason"),
                    "final_tier_count": int(d.get("final_tier_count", 0) or 0),
                    "candidate_tiers_percentile_int": int(d.get("candidate_tiers_percentile_int", 0) or 0),
                    "candidate_tiers_zscore": int(d.get("candidate_tiers_zscore", 0) or 0),
                    "max_gap_percentile_int": float(d.get("max_gap_percentile_int", 0.0) or 0.0),
                    "max_gap_zscore": float(d.get("max_gap_zscore", 0.0) or 0.0),
                    "min_adjacent_step_revpar": min_step,
                    "avg_adjacent_step_revpar": avg_step,
                }
            )

    for q in quantile_values:
        cfg = dict(base_tiering_cfg)
        cfg["quantile_fallback_tiers"] = int(q)
        tables = run_analysis_tables(
            canonical_df,
            room_count=room_count,
            tiering_cfg=cfg,
            listing_start_dates=listing_start_dates,
            analysis_window=analysis_window_cfg,
        )
        diag = tables.get("tier_diagnostics", pd.DataFrame())
        summ = tables.get("tier_summary", pd.DataFrame()).sort_values("tier_id")
        d = diag.iloc[0].to_dict() if not diag.empty else {}
        if not summ.empty and "avg_revpar" in summ.columns:
            steps = pd.to_numeric(summ["avg_revpar"], errors="coerce").diff().dropna()
            min_step = float(steps.min()) if len(steps) else None
            avg_step = float(steps.mean()) if len(steps) else None
        else:
            min_step, avg_step = None, None
        rows.append(
            {
                "sweep_type": "quantile_tier_count",
                "gap_score_basis": str(cfg.get("gap_score_basis", "percentile_int")),
                "gap_threshold_value": float(cfg.get("gap_threshold_pct", 6)),
                "quantile_fallback_tiers": int(q),
                "selected_method": d.get("selected_method"),
                "fallback_used": bool(d.get("fallback_used", False)),
                "fallback_reason": d.get("fallback_reason"),
                "final_tier_count": int(d.get("final_tier_count", 0) or 0),
                "candidate_tiers_percentile_int": int(d.get("candidate_tiers_percentile_int", 0) or 0),
                "candidate_tiers_zscore": int(d.get("candidate_tiers_zscore", 0) or 0),
                "max_gap_percentile_int": float(d.get("max_gap_percentile_int", 0.0) or 0.0),
                "max_gap_zscore": float(d.get("max_gap_zscore", 0.0) or 0.0),
                "min_adjacent_step_revpar": min_step,
                "avg_adjacent_step_revpar": avg_step,
            }
        )

    sweep = pd.DataFrame(rows)
    if sweep.empty:
        return sweep, pd.DataFrame()

    quant = sweep[sweep["sweep_type"] == "quantile_tier_count"].copy()
    if quant.empty:
        return sweep, pd.DataFrame()
    quant["monotonic_ok"] = quant["min_adjacent_step_revpar"].notna() & (quant["min_adjacent_step_revpar"] > 0)
    quant = quant.sort_values(["monotonic_ok", "min_adjacent_step_revpar", "quantile_fallback_tiers"], ascending=[False, False, True])
    best = quant.iloc[0]
    summary = pd.DataFrame(
        [
            {
                "recommended_quantile_fallback_tiers": int(best["quantile_fallback_tiers"]),
                "selection_rule": "max positive min_adjacent_step_revpar",
                "recommended_min_adjacent_step_revpar": float(best["min_adjacent_step_revpar"]) if pd.notna(best["min_adjacent_step_revpar"]) else None,
                "recommended_avg_adjacent_step_revpar": float(best["avg_adjacent_step_revpar"]) if pd.notna(best["avg_adjacent_step_revpar"]) else None,
                "natural_gap_detected_any_sweep": bool((sweep["selected_method"] == "gap_detection").any()),
                "max_observed_gap_percentile_int": float(pd.to_numeric(sweep["max_gap_percentile_int"], errors="coerce").max()),
                "max_observed_gap_zscore": float(pd.to_numeric(sweep["max_gap_zscore"], errors="coerce").max()),
                "notes": "If natural gaps are absent across threshold sweeps, quantile fallback is the defensible path.",
            }
        ]
    )
    return sweep, summary


def main():
    args = sys.argv[1:]
    property_id = None
    canonical_path = None
    output_dir = None

    i = 0
    while i < len(args):
        if args[i] == "--property" and i + 1 < len(args):
            property_id = args[i + 1]
            i += 2
        elif args[i] == "--canonical" and i + 1 < len(args):
            canonical_path = Path(args[i + 1])
            i += 2
        elif args[i] == "--output-dir" and i + 1 < len(args):
            output_dir = Path(args[i + 1])
            i += 2
        else:
            i += 1

    if not property_id:
        print("Usage: python scripts/run_analysis.py --property <property_id> [--canonical path] [--output-dir path]")
        print("Example: python scripts/run_analysis.py --property lafave_zion")
        sys.exit(1)

    if canonical_path is None:
        canonical_path = PROJECT_ROOT / "output" / property_id / "ingestion" / "canonical.csv"
    if output_dir is None:
        output_dir = PROJECT_ROOT / "output" / property_id / "analysis"

    if not canonical_path.exists():
        print(f"Error: Canonical CSV not found: {canonical_path}")
        print("Run ingestion first: python scripts/run_ingestion.py --property <id> <csv_path>")
        sys.exit(1)

    try:
        inv = load_property_inventory(property_id)
    except FileNotFoundError as e:
        print(f"Error: {e}")
        sys.exit(1)

    room_count = inv.get("room_count")
    analysis_window_cfg = inv.get("analysis_window") or {}

    df = pd.read_csv(canonical_path)
    for col in ("arrival_date", "departure_date", "booking_date"):
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
    max_arrival = None
    if "arrival_date" in df.columns and df["arrival_date"].notna().any():
        max_arrival = pd.Timestamp(df["arrival_date"].max()).normalize()
    start_date, end_date = resolve_analysis_window(
        analysis_window_cfg, canonical_max_arrival=max_arrival
    )
    print(f"Property: {inv.get('property_name', property_id)}")
    print(f"Analysis period: {start_date.date()} to {end_date.date()}")
    mc_print = inv.get("monthly_performance_combined") or {}
    if mc_print:
        print(f"  monthly_performance_combined (rank table only): {mc_print}")
    print(f"Room count: {room_count or 'not set (occupancy/RevPAR at property level will be null)'}")
    print(f"Loading: {canonical_path}")

    room_types = inv.get("room_types") or []
    listing_start_dates = {}
    for r in room_types:
        if isinstance(r, dict) and r.get("unit_id") and r.get("listing_start_date"):
            listing_start_dates[str(r["unit_id"])] = str(r["listing_start_date"])

    # listing_unit_counts is populated by property_config when unit_count is set in room_types.
    listing_unit_counts = inv.get("listing_unit_counts") or {}
    if listing_unit_counts:
        print(f"Listing unit counts: { {k: v for k, v in listing_unit_counts.items()} }")

    listing_capacity_fallback = inv.get("listing_unit_capacity_fallback") or {}
    if listing_capacity_fallback:
        print(f"Listing capacity fallback (overlay → base): {listing_capacity_fallback}")

    tables = run_analysis_tables(
        df,
        room_count=room_count,
        tiering_cfg=inv.get("tiering"),
        listing_start_dates=listing_start_dates,
        listing_unit_counts=listing_unit_counts,
        analysis_window=analysis_window_cfg,
        listing_capacity_fallback=listing_capacity_fallback,
        monthly_performance_combined_cfg=inv.get("monthly_performance_combined") or {},
    )
    n_rows = len(tables["overall_summary"]) - 1  # exclude PROPERTY row for listing count
    if n_rows == 0:
        print("Warning: No data in analysis period; tables may be empty or only PROPERTY row.")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    for name, table_df in tables.items():
        out_file = output_dir / f"{name}.csv"
        table_df.to_csv(out_file, index=False)
        print(f"  Wrote {out_file} ({len(table_df)} rows)")

    audit_df = build_tier_integrity_audit(tables=tables, inv=inv, output_dir=output_dir)
    audit_path = output_dir / "tier_integrity_audit.csv"
    audit_df.to_csv(audit_path, index=False)
    print(f"  Wrote {audit_path} ({len(audit_df)} checks)")

    tiering_cfg = inv.get("tiering") or {}
    if tiering_cfg:
        sweep_df, summary_df = build_tier_validation_outputs(
            canonical_df=df,
            room_count=room_count,
            base_tiering_cfg=tiering_cfg,
            listing_start_dates=listing_start_dates,
            analysis_window_cfg=analysis_window_cfg,
        )
        if not sweep_df.empty:
            sweep_path = output_dir / "tier_sensitivity_sweep.csv"
            sweep_df.to_csv(sweep_path, index=False)
            print(f"  Wrote {sweep_path} ({len(sweep_df)} rows)")
        if not summary_df.empty:
            val_path = output_dir / "tier_validation_summary.csv"
            summary_df.to_csv(val_path, index=False)
            print(f"  Wrote {val_path} ({len(summary_df)} rows)")

    print(f"\nDone. All tables written to {output_dir}")


if __name__ == "__main__":
    main()
