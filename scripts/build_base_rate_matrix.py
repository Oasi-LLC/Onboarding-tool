#!/usr/bin/env python3
from __future__ import annotations

import argparse
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd
import yaml

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.analysis import resolve_analysis_window
from src.market_slug import load_market_slug_map
from src.property_config import load_property_inventory


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Build FLOHOM base rate matrix.")
    p.add_argument("--property", required=True, help="Property id, e.g. flohom")
    p.add_argument("--monthly-by-market", default=None, help="Optional monthly_performance_by_market.csv path")
    p.add_argument("--dow-by-market", default=None, help="Optional by_day_of_week_by_market.csv path")
    p.add_argument("--output-csv", default=None, help="Optional output matrix csv path")
    p.add_argument("--output-qa", default=None, help="Optional qa summary path")
    return p.parse_args()


def _load_pricing_config(property_id: str) -> dict:
    path = PROJECT_ROOT / "config" / "pricing_config.yaml"
    if not path.exists():
        raise ValueError(f"Missing pricing config: {path}")
    data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    cfg = ((data.get("pricing") or {}).get(property_id) or {})
    if not cfg:
        raise ValueError(f"Missing pricing.{property_id} section in {path}")
    return cfg


def _to_day_abbr(day: str) -> str:
    d = str(day or "").strip()
    if len(d) <= 3:
        return d.title()
    return d[:3].title()


def _round_rule(v: float, rule: str) -> float:
    if rule == "round_to_5":
        return float(round(v / 5.0) * 5.0)
    return float(round(v))


def _version_tag() -> str:
    try:
        return (
            subprocess.check_output(["git", "rev-parse", "--short", "HEAD"], cwd=PROJECT_ROOT)
            .decode("utf-8")
            .strip()
        )
    except Exception:
        return "unknown"


def _hard_assert(checks: list[tuple[str, bool]], errors: list[str]) -> None:
    for label, ok in checks:
        if not ok:
            errors.append(label)


def main() -> None:
    args = _parse_args()
    property_id = args.property
    inv = load_property_inventory(property_id)
    pricing_cfg = _load_pricing_config(property_id)
    analysis_dir = PROJECT_ROOT / "output" / property_id / "analysis"
    outputs_dir = PROJECT_ROOT / "outputs" / property_id
    outputs_dir.mkdir(parents=True, exist_ok=True)

    monthly_path = Path(args.monthly_by_market) if args.monthly_by_market else outputs_dir / "monthly_performance_by_market.csv"
    dow_path = Path(args.dow_by_market) if args.dow_by_market else outputs_dir / "by_day_of_week_by_market.csv"
    matrix_path = Path(args.output_csv) if args.output_csv else outputs_dir / f"{property_id}_base_rate_matrix.csv"
    qa_path = Path(args.output_qa) if args.output_qa else outputs_dir / "qa_summary.txt"

    if not monthly_path.exists():
        raise SystemExit(f"Missing required input: {monthly_path}. Run compute_market_month_index.py first.")
    if not dow_path.exists():
        raise SystemExit(f"Missing required input: {dow_path}. Run compute_market_dow_index.py first.")

    monthly = pd.read_csv(monthly_path)
    _ = pd.read_csv(dow_path)  # reserved for calibration reporting, not used in formula composition yet.
    tier_summary = pd.read_csv(analysis_dir / "tier_summary.csv")
    listing_daily = pd.read_csv(analysis_dir / "listing_daily_tier_calendar.csv")
    overall = pd.read_csv(analysis_dir / "overall_summary.csv")

    unit_to_market = load_market_slug_map(property_id=property_id, inventory=inv)
    canonical_path = PROJECT_ROOT / "output" / property_id / "ingestion" / "canonical.csv"
    max_arrival = None
    if canonical_path.exists():
        cdf = pd.read_csv(canonical_path, usecols=["arrival_date"])
        cdf["arrival_date"] = pd.to_datetime(cdf["arrival_date"], errors="coerce")
        if cdf["arrival_date"].notna().any():
            max_arrival = pd.Timestamp(cdf["arrival_date"].max()).normalize()
    w_start, w_end = resolve_analysis_window(
        inv.get("analysis_window") or {}, canonical_max_arrival=max_arrival
    )
    room_types = inv.get("room_types") or []
    active_listings = []
    for r in room_types:
        if not isinstance(r, dict) or not r.get("unit_id"):
            continue
        unit = str(r["unit_id"])
        start = pd.to_datetime(r.get("listing_start_date"), errors="coerce")
        if pd.isna(start) or pd.Timestamp(start).normalize() <= w_end:
            active_listings.append(unit)
    active_listings = sorted(set(active_listings))
    if not active_listings:
        raise SystemExit("No active listings found for analysis window.")

    # Listing tier assignment from listing-day rows: tier with max revenue share for listing.
    ld = listing_daily.copy()
    ld = ld[ld["unit_id"].isin(active_listings)].copy()
    ld["property_revenue"] = pd.to_numeric(ld.get("property_revenue"), errors="coerce").fillna(0.0)
    listing_tier = (
        ld.groupby(["unit_id", "tier_label"], as_index=False).agg(revenue=("property_revenue", "sum"))
        .sort_values(["unit_id", "revenue"], ascending=[True, False])
        .drop_duplicates(subset=["unit_id"])
        .set_index("unit_id")["tier_label"]
        .to_dict()
    )
    tier_revpar = (
        tier_summary.set_index("tier_label")["avg_revpar"]
        .apply(pd.to_numeric, errors="coerce")
        .to_dict()
    )

    anchor_listing = str(pricing_cfg.get("anchor_listing", "")).strip()
    if not anchor_listing:
        raise SystemExit("pricing.anchor_listing is required")
    if anchor_listing not in active_listings:
        raise SystemExit(f"Anchor listing not active/missing: {anchor_listing}")
    if anchor_listing not in listing_tier:
        raise SystemExit(f"Anchor listing has no tier assignment: {anchor_listing}")
    anchor_tier = listing_tier[anchor_listing]
    anchor_tier_revpar = float(tier_revpar.get(anchor_tier, 0.0) or 0.0)
    if anchor_tier_revpar <= 0:
        raise SystemExit("Anchor tier RevPAR must be > 0")

    # Listing multipliers
    listing_multiplier_raw: dict[str, float] = {}
    listing_multiplier_capped: dict[str, bool] = {}
    for unit in active_listings:
        # New listings can have sparse/no listing-day rows yet.
        # Use Soft as deterministic fallback so multiplier remains meaningful.
        t = listing_tier.get(unit, "Soft")
        lr = float(tier_revpar.get(t, 0.0) or 0.0)
        raw = (lr / anchor_tier_revpar) if anchor_tier_revpar > 0 else 1.0
        capped = raw > 1.0
        if capped:
            raw = 1.0
        listing_multiplier_raw[unit] = float(raw)
        listing_multiplier_capped[unit] = bool(capped)

    # Market base rates: median ADR on Soft tier listing-day rows by market_slug.
    soft = ld[ld["tier_label"].astype(str) == "Soft"].copy()
    soft["market_slug"] = soft["unit_id"].astype(str).map(unit_to_market).fillna("unmapped")
    soft["adr_row"] = (
        pd.to_numeric(soft["property_revenue"], errors="coerce")
        / pd.to_numeric(soft["property_room_nights"], errors="coerce")
    )
    soft = soft[pd.to_numeric(soft["property_room_nights"], errors="coerce") > 0]
    soft = soft[pd.to_numeric(soft["adr_row"], errors="coerce") > 0]
    market_base_rate = soft.groupby("market_slug")["adr_row"].median().to_dict()
    if not market_base_rate:
        raise SystemExit("Could not compute market_base_rate from Soft tier rows.")

    # Month index lookup (market_slug × month)
    month_idx = (
        monthly.groupby(["market_slug", "month"], as_index=False)
        .agg(month_index=("month_index", "mean"), market_index_source=("market_index_source", "first"))
    )
    month_idx_lookup = {
        (str(r.market_slug), int(r.month)): (float(r.month_index), str(r.market_index_source))
        for r in month_idx.itertuples(index=False)
    }
    markets_with_full_market_index = {
        str(ms)
        for ms, g in month_idx.groupby("market_slug")
        if len(g) == 12 and set(g["market_index_source"].astype(str).unique().tolist()) == {"market"}
    }

    # DOW bands + indices
    dow_cfg = (pricing_cfg.get("dow_bands") or {}).get("bands") or {}
    band_to_idx = {}
    day_to_band = {}
    for band in ("base", "mid", "peak"):
        cfg = dow_cfg.get(band) or {}
        band_to_idx[band] = float(cfg.get("index", 1.0))
        for d in cfg.get("days") or []:
            day_to_band[_to_day_abbr(d)] = band
    overrides = (pricing_cfg.get("dow_bands") or {}).get("market_overrides") or {}
    floors = pricing_cfg.get("min_rate_floors") or {}
    rounding_rule = str(pricing_cfg.get("rounding_rule", "round_to_5"))

    rows = []
    warnings: list[str] = []
    excluded_listings: list[str] = []
    for unit in active_listings:
        market_slug = unit_to_market.get(unit, "unmapped")
        # User rule: skip listings that do not have complete market-native month indices.
        if market_slug not in markets_with_full_market_index:
            excluded_listings.append(unit)
            warnings.append(
                f"excluded_sparse_listing listing={unit} market_slug={market_slug}; reason=incomplete_market_month_index"
            )
            continue
        tier = listing_tier.get(unit, "Soft")
        listing_tier_revpar = float(tier_revpar.get(tier, 0.0) or 0.0)
        lmult = float(listing_multiplier_raw.get(unit, 1.0))
        capped = bool(listing_multiplier_capped.get(unit, False))
        if capped:
            warnings.append(f"multiplier_capped listing={unit}")
        if lmult <= 0:
            lmult = 0.01
            warnings.append(f"non_positive_multiplier listing={unit}; clamped_to=0.01")
        mb = float(market_base_rate.get(market_slug, 0.0) or 0.0)
        if mb <= 0:
            warnings.append(f"missing_market_base_rate market_slug={market_slug}; fallback to portfolio soft median")
            mb = float(pd.Series(list(market_base_rate.values())).median())
        if mb <= 0:
            mb = 1.0
            warnings.append(f"non_positive_market_base_rate market_slug={market_slug}; clamped_to=1.0")

        for month in range(1, 13):
            mi, mi_src = month_idx_lookup.get((market_slug, month), (None, None))
            if mi is None or float(mi) <= 0:
                warnings.append(f"skipped_cell listing={unit} market_slug={market_slug} month={month}; reason=missing_or_non_positive_month_index")
                continue

            for band in ("base", "mid", "peak"):
                idx = float(band_to_idx.get(band, 1.0))
                if market_slug in overrides and band in (overrides.get(market_slug) or {}):
                    idx = float((overrides.get(market_slug) or {}).get(band))

                pre_round = float(mb * lmult * float(mi) * idx)
                base_rate = _round_rule(pre_round, rounding_rule)
                # Listing-level floor (primary), then optional market-level fallback.
                floor = float(floors.get(unit, floors.get(market_slug, 0.0)) or 0.0)
                floored = max(base_rate, floor)
                floor_applied = floored > base_rate
                flags = []
                if capped:
                    flags.append("multiplier_capped")
                if mi_src == "portfolio_fallback":
                    flags.append("portfolio_fallback")
                if floor_applied:
                    flags.append("floor_applied")
                rows.append(
                    {
                        "run_timestamp": datetime.now(timezone.utc).isoformat(),
                        "version_tag": _version_tag(),
                        "listing_id": unit,
                        "market_slug": market_slug,
                        "tier": tier,
                        "month": month,
                        "dow_band": band,
                        "anchor_listing": anchor_listing,
                        "anchor_tier_revpar": anchor_tier_revpar,
                        "listing_tier_revpar": listing_tier_revpar,
                        "listing_multiplier": lmult,
                        "multiplier_capped": capped,
                        "market_base_rate": mb,
                        "month_index": float(mi),
                        "market_index_source": mi_src,
                        "dow_index": idx,
                        "base_rate_pre_round": pre_round,
                        "rounding_rule": rounding_rule,
                        "base_rate": base_rate,
                        "min_rate_floor": floor,
                        "base_rate_floored": floored,
                        "floor_applied": floor_applied,
                        "qa_flags": "|".join(sorted(flags)),
                    }
                )

    out = pd.DataFrame(rows)

    # QA assertions
    hard_errors: list[str] = []
    expected_rows = (len(active_listings) - len(set(excluded_listings))) * 12 * 3
    _hard_assert(
        [
            ("expected_row_count", len(out) == expected_rows),
            ("no_null_base_rate", out["base_rate"].notna().all()),
            ("base_rate_pre_round_positive", (pd.to_numeric(out["base_rate_pre_round"], errors="coerce") > 0).all()),
            (
                "dow_ordering",
                float(band_to_idx.get("base", 0)) < float(band_to_idx.get("mid", 0)) < float(band_to_idx.get("peak", 0)),
            ),
        ],
        hard_errors,
    )
    # tier monotonicity on tier RevPAR
    ts = tier_summary[["tier_id", "avg_revpar"]].copy()
    ts["avg_revpar"] = pd.to_numeric(ts["avg_revpar"], errors="coerce")
    ts = ts.sort_values("tier_id")
    _hard_assert([("tier_monotonicity", ts["avg_revpar"].is_monotonic_increasing)], hard_errors)

    warn_rows = []
    pre_cap_ratio = (
        pd.Series(
            [
                (float(tier_revpar.get(listing_tier.get(u, ""), 0.0) or 0.0) / anchor_tier_revpar)
                if anchor_tier_revpar > 0 else 1.0
                for u in active_listings
            ]
        )
    )
    if ((pre_cap_ratio <= 0.40) | (pre_cap_ratio > 1.10)).any():
        warn_rows.append("multiplier_range_warning")
    if ((out["month_index"] <= 0.40) | (out["month_index"] >= 2.00)).any():
        warn_rows.append("month_index_range_warning")
    # Floor binding QA should be inspected per listing, not portfolio-wide.
    floor_rate_by_listing = (
        out.groupby("listing_id", as_index=False)["floor_applied"]
        .mean()
        .rename(columns={"floor_applied": "floor_binding_rate"})
    )
    high_floor = floor_rate_by_listing[floor_rate_by_listing["floor_binding_rate"] >= 0.10]
    if not high_floor.empty:
        ids = ",".join(
            f"{r.listing_id}:{r.floor_binding_rate:.3f}"
            for r in high_floor.itertuples(index=False)
        )
        warn_rows.append(f"floor_binding_high_by_listing {ids}")
    cap_rate = float(pd.Series([listing_multiplier_capped[u] for u in active_listings]).mean()) if active_listings else 0.0
    if cap_rate >= 0.05:
        warn_rows.append(f"multiplier_cap_rate_high rate={cap_rate:.3f}")
    fallback_markets = out.loc[out["market_index_source"] == "portfolio_fallback", "market_slug"].nunique()
    if fallback_markets >= 2:
        warn_rows.append(f"fallback_market_count_high count={int(fallback_markets)}")

    # Market sum QA (room_nights aggregation should be close to portfolio table)
    try:
        market_room_nights = pd.to_numeric(monthly["room_nights"], errors="coerce").sum()
        portfolio_month = pd.read_csv(analysis_dir / "monthly_performance.csv")
        portfolio_room_nights = pd.to_numeric(portfolio_month["room_nights"], errors="coerce").sum()
        if portfolio_room_nights > 0:
            delta_pct = abs(market_room_nights - portfolio_room_nights) / portfolio_room_nights
            if delta_pct > 0.02:
                warn_rows.append(f"market_sum_qa_warning delta_pct={delta_pct:.4f}")
    except Exception:
        warn_rows.append("market_sum_qa_warning could_not_compute")

    if hard_errors:
        qa_path.write_text(
            "HARD_ERRORS\n" + "\n".join(hard_errors) + "\n\nWARNINGS\n" + "\n".join(sorted(set(warn_rows + warnings))) + "\n",
            encoding="utf-8",
        )
        raise SystemExit(f"QA hard errors: {hard_errors}. See {qa_path}")

    out.to_csv(matrix_path, index=False)
    qa_text = [
        f"run_timestamp={datetime.now(timezone.utc).isoformat()}",
        f"property_id={property_id}",
        f"active_listing_count={len(active_listings)}",
        f"included_listing_count={len(active_listings) - len(set(excluded_listings))}",
        f"excluded_listing_count={len(set(excluded_listings))}",
        f"excluded_listings={','.join(sorted(set(excluded_listings))) if excluded_listings else 'none'}",
        f"expected_rows={expected_rows}",
        f"actual_rows={len(out)}",
        f"hard_errors=0",
        "warnings:",
    ]
    qa_text.extend(sorted(set(warn_rows + warnings)) or ["none"])
    qa_text.append("")
    qa_text.append("floor_binding_rate_by_listing:")
    for r in floor_rate_by_listing.sort_values("listing_id").itertuples(index=False):
        qa_text.append(f"{r.listing_id}={r.floor_binding_rate:.4f}")
    qa_path.write_text("\n".join(qa_text) + "\n", encoding="utf-8")
    print(f"Wrote {matrix_path} ({len(out)} rows)")
    print(f"Wrote {qa_path}")


if __name__ == "__main__":
    main()
