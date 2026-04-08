from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from scripts.run_daily_market_context import _build_listing_day_stay_night
from scripts.run_daily_market_context import _resolve_metric_file, _resolve_submarket_dir
from scripts.run_market_benchmark import resolve_benchmark_years
from scripts.run_pricing_matrix import write_pricing_outputs
from src.analysis import build_daily_tier_outputs, get_analysis_years


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
