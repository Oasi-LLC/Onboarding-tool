from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from scripts.run_daily_market_context import _build_listing_day_stay_night
from scripts.run_daily_market_context import _resolve_metric_file, _resolve_submarket_dir
from scripts.run_market_benchmark import resolve_benchmark_years
from scripts.run_pricing_matrix import write_pricing_outputs
from src.analysis import (
    build_daily_tier_outputs,
    build_monthly_performance_combined,
    get_analysis_years,
    prepare_canonical_for_analysis,
)
from src.parser import reservation_status_is_excluded
from src.pricing_matrix import (
    _compute_month_factor,
    _month_factors_and_scores_merged,
    apply_within_tier_median_rates,
    coerce_month_score_by_index_config,
    listing_factors_from_price_hierarchy,
)


class PipelineContractTests(unittest.TestCase):
    def test_stay_night_context_splits_multinight_revenue(self) -> None:
        y1, _ = get_analysis_years()
        canonical = pd.DataFrame(
            [
                {
                    "unit_id": "FLOHOM 9",
                    "arrival_date": f"{y1}-12-05",
                    "departure_date": f"{y1}-12-07",
                    "revenue": 963.0,
                    "nights": 2,
                    "reservation_id": "r-1",
                }
            ]
        )
        out = _build_listing_day_stay_night(
            canonical,
            start_date=pd.Timestamp(year=y1, month=1, day=1),
            end_date=pd.Timestamp(year=y1, month=12, day=31),
        )
        self.assertEqual(len(out), 2)
        rev = sorted(out["property_revenue"].tolist())
        self.assertAlmostEqual(rev[0], 481.5, places=2)
        self.assertAlmostEqual(rev[1], 481.5, places=2)
        bookings_by_date = dict(zip(out["date"].dt.strftime("%Y-%m-%d"), out["bookings"]))
        self.assertEqual(bookings_by_date.get(f"{y1}-12-05"), 1.0)
        self.assertEqual(bookings_by_date.get(f"{y1}-12-06"), 0.0)

    def test_require_listing_start_dates_raises_when_missing(self) -> None:
        y1, _ = get_analysis_years()
        df = pd.DataFrame(
            [
                {
                    "unit_id": "U1",
                    "arrival_date": pd.Timestamp(year=y1, month=1, day=10),
                    "departure_date": pd.Timestamp(year=y1, month=1, day=12),
                    "revenue": 200.0,
                    "nights": 2,
                    "reservation_id": "r-1",
                },
                {
                    "unit_id": "U2",
                    "arrival_date": pd.Timestamp(year=y1, month=2, day=10),
                    "departure_date": pd.Timestamp(year=y1, month=2, day=12),
                    "revenue": 200.0,
                    "nights": 2,
                    "reservation_id": "r-2",
                },
            ]
        )
        with self.assertRaises(ValueError):
            build_daily_tier_outputs(
                df,
                room_count=2,
                tiering_cfg={"require_listing_start_dates": True},
                listing_start_dates={"U1": f"{y1}-01-01"},
            )

    def test_benchmark_years_match_analysis_years(self) -> None:
        y1, y2 = get_analysis_years()
        self.assertEqual(resolve_benchmark_years(), [y1, y2])

    def test_monthly_combined_min_arrival_excludes_pre_go_live_months(self) -> None:
        raw = pd.DataFrame(
            [
                {"arrival_date": "2025-01-15", "revenue": 300.0, "nights": 1, "unit_id": "U1"},
                {"arrival_date": "2025-08-15", "revenue": 100.0, "nights": 1, "unit_id": "U1"},
            ]
        )
        raw["arrival_date"] = pd.to_datetime(raw["arrival_date"])
        df = prepare_canonical_for_analysis(raw)
        starts = {"U1": "2024-01-01"}
        counts = {"U1": 1}
        unfiltered = build_monthly_performance_combined(df, room_count=2, listing_start_dates=starts, listing_unit_counts=counts)
        self.assertIn(1, set(unfiltered["month_index"].tolist()))
        filtered = build_monthly_performance_combined(
            df,
            room_count=2,
            listing_start_dates=starts,
            listing_unit_counts=counts,
            combined_min_arrival_date=pd.Timestamp("2025-07-01"),
        )
        self.assertNotIn(1, set(filtered["month_index"].tolist()))
        self.assertIn(8, set(filtered["month_index"].tolist()))

    def test_monthly_combined_provisional_flag_when_latest_year_thin(self) -> None:
        rows = [{"arrival_date": "2025-10-02", "revenue": 100.0, "nights": 1, "unit_id": "U1"} for _ in range(10)]
        rows += [{"arrival_date": "2026-10-05", "revenue": 50.0, "nights": 1, "unit_id": "U1"} for _ in range(2)]
        raw = pd.DataFrame(rows)
        raw["arrival_date"] = pd.to_datetime(raw["arrival_date"])
        df = prepare_canonical_for_analysis(raw)
        starts = {"U1": "2024-01-01"}
        counts = {"U1": 1}
        out = build_monthly_performance_combined(
            df,
            room_count=2,
            listing_start_dates=starts,
            listing_unit_counts=counts,
            provisional_bookings_ratio=0.6,
        )
        oct_row = out.loc[out["month_index"] == 10]
        self.assertEqual(len(oct_row), 1)
        self.assertTrue(bool(oct_row.iloc[0]["performance_rank_provisional"]))

    def test_apply_within_tier_median_rates(self) -> None:
        wide = pd.DataFrame(
            [
                {"unit_id": "A", "month_index": 1, "Monday": 100, "Tuesday": 200},
                {"unit_id": "B", "month_index": 1, "Monday": 110, "Tuesday": 220},
                {"unit_id": "C", "month_index": 1, "Monday": 130, "Tuesday": 240},
                {"unit_id": "Z", "month_index": 1, "Monday": 1, "Tuesday": 2},
            ]
        )
        hier = {"tiers": [{"order": 1, "id": "t1", "unit_ids": ["A", "B", "C"]}]}
        out = apply_within_tier_median_rates(wide, hier)
        self.assertEqual(int(out.loc[out["unit_id"] == "A", "Monday"].iloc[0]), 110)
        self.assertEqual(int(out.loc[out["unit_id"] == "B", "Monday"].iloc[0]), 110)
        self.assertEqual(int(out.loc[out["unit_id"] == "C", "Monday"].iloc[0]), 110)
        self.assertEqual(int(out.loc[out["unit_id"] == "A", "Tuesday"].iloc[0]), 220)
        self.assertEqual(int(out.loc[out["unit_id"] == "Z", "Monday"].iloc[0]), 1)

    def test_listing_factors_from_price_hierarchy_orders(self) -> None:
        raw = {
            "tiers": [
                {"order": 1, "id": "a", "unit_ids": ["U-weakest"]},
                {"order": 9, "id": "b", "unit_ids": ["U-strong"]},
            ]
        }
        fac = listing_factors_from_price_hierarchy(raw)
        self.assertIsNotNone(fac)
        assert fac is not None
        self.assertLess(fac["U-weakest"], fac["U-strong"])

    def test_month_score_yaml_coercion_and_jul_aug_tie(self) -> None:
        raw = {"7": 4, "8": 4.0, "1": 5}
        d = coerce_month_score_by_index_config(raw)
        self.assertEqual(d[7], 4.0)
        self.assertEqual(d[8], 4.0)
        monthly = pd.DataFrame(
            [{"month_index": 3, "performance_score_1_10": 9, "performance_rank_provisional": False}]
        )
        factors, scores = _month_factors_and_scores_merged(monthly, {7: 4.0, 8: 4.0})
        self.assertEqual(factors[7], factors[8])
        self.assertEqual(scores[7], scores[8])

    def test_reservation_status_excludes_cancel_variants(self) -> None:
        self.assertTrue(reservation_status_is_excluded("Cancelled"))
        self.assertTrue(reservation_status_is_excluded("canceled"))
        self.assertTrue(reservation_status_is_excluded("Cancellation pending"))
        self.assertTrue(reservation_status_is_excluded("confirmation pending"))
        self.assertTrue(reservation_status_is_excluded("Cancel pending"))
        self.assertFalse(reservation_status_is_excluded("Checked Out"))
        self.assertFalse(reservation_status_is_excluded("Confirmed"))
        self.assertFalse(reservation_status_is_excluded("In-House"))

    def test_prepare_canonical_drops_status_column_when_present(self) -> None:
        raw = pd.DataFrame(
            [
                {
                    "arrival_date": "2025-01-10",
                    "channel": "Direct",
                    "Status": "Cancelled",
                },
                {
                    "arrival_date": "2025-01-11",
                    "channel": "Direct",
                    "Status": "Checked Out",
                },
            ]
        )
        out = prepare_canonical_for_analysis(raw)
        self.assertEqual(len(out), 1)
        self.assertEqual(str(out.iloc[0]["arrival_date"].date()), "2025-01-11")

    def test_month_factor_treats_provisional_as_neutral_score_five(self) -> None:
        monthly = pd.DataFrame(
            [
                {"month_index": 10, "performance_score_1_10": 10, "performance_rank_provisional": True},
                {"month_index": 11, "performance_score_1_10": 10, "performance_rank_provisional": False},
            ]
        )
        factors = _compute_month_factor(monthly)
        # 0.75 + 0.05 * score; provisional month uses score 5 like a neutral month.
        self.assertAlmostEqual(factors[10], 1.0)
        self.assertAlmostEqual(factors[11], 1.25)

    def test_pricing_outputs_write_both_expected_files(self) -> None:
        listing_df = pd.DataFrame(
            [{"unit_id": "U1", "month_index": 1, "Monday": 100, "Tuesday": 100, "Wednesday": 100, "Thursday": 100, "Friday": 100, "Saturday": 100, "Sunday": 100}]
        )
        group_df = pd.DataFrame(
            [{"listing_group": "G1", "month_index": 1, "Monday": 110, "Tuesday": 110, "Wednesday": 110, "Thursday": 110, "Friday": 110, "Saturday": 110, "Sunday": 110}]
        )
        with tempfile.TemporaryDirectory() as tmp:
            out_dir = Path(tmp)
            write_pricing_outputs(out_dir, listing_df, group_df)
            self.assertTrue((out_dir / "pricing_matrix_draft.csv").exists())
            self.assertTrue((out_dir / "pricing_matrix_group_draft.csv").exists())

    def test_airdna_metric_resolution_raises_on_ambiguous_files(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            d = Path(tmp)
            (d / "revpar_last_3_years (1).csv").write_text("Date,Average RevPAR\n", encoding="utf-8")
            (d / "revpar_last_3_years (2).csv").write_text("Date,Average RevPAR\n", encoding="utf-8")
            with self.assertRaises(ValueError):
                _resolve_metric_file(d, "revpar_last_3_years")

    def test_submarket_resolution_raises_on_ambiguous_token_equivalent_dirs(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "downtown baltimore").mkdir()
            (root / "baltimore downtown").mkdir()
            with self.assertRaises(ValueError):
                _resolve_submarket_dir(root, "Baltimore (Downtown)")


if __name__ == "__main__":
    unittest.main()
