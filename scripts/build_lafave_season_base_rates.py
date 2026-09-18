#!/usr/bin/env python3
"""Build LaFave listing-group base rates by season (high / low / shoulder) from a daily pricing sheet."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

# Owner spreadsheet row order
ROOM_TYPES = [
    "House (6BR/4BA)",
    "Suite BIG (1BR/1BA)",
    "Suite SMALL (1BR/1BA)",
    "Deluxe Villa (2BR/1BA)",
    "Premium Villa (2BR/2BA)",
    "Villa Game (2BR/2BA)",
    "J.MT. Villa (3BR/2BA)",
    "Premier Villa (3BR/3BA)",
]

DOW_COLS = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
DOW_FULL = {
    "Monday": "Mon",
    "Tuesday": "Tue",
    "Wednesday": "Wed",
    "Thursday": "Thu",
    "Friday": "Fri",
    "Saturday": "Sat",
    "Sunday": "Sun",
}

SEASON_ORDER = ["high", "low", "shoulder"]
SEASON_LABELS = {
    "high": "SEASON: high",
    "low": "SEASON: low",
    "shoulder": "SEASON: shoulder (Jul–Aug)",
}


def _season_from_month(month: int) -> str:
    """Owner base-rate buckets: low = Nov–Feb, shoulder = Jul–Aug, high = Mar–Jun & Sep–Oct."""
    if month in (7, 8):
        return "shoulder"
    if month in (1, 2, 11, 12):
        return "low"
    return "high"


def _tier_to_season(tier_label: str) -> str | None:
    t = str(tier_label or "").strip()
    if t in ("High", "Peak"):
        return "high"
    if t in ("Low", "Soft"):
        return "low"
    if t == "Shoulder":
        return "shoulder"
    return None


def _build_tier_calendar(tier_blocks_path: Path) -> pd.DataFrame:
    tb = pd.read_csv(tier_blocks_path)
    tb["start_date"] = pd.to_datetime(tb["start_date"])
    tb["end_date"] = pd.to_datetime(tb["end_date"])
    tb["season"] = tb["tier_label"].map(_tier_to_season)

    # Extend 2027-06-17 → 2028-01-09 using 2026-06-17 → 2027-01-09 pattern (+1 year)
    tail = tb[(tb["start_date"] >= "2026-06-17") & (tb["end_date"] <= "2027-01-09")].copy()
    tail["start_date"] = tail["start_date"] + pd.DateOffset(years=1)
    tail["end_date"] = tail["end_date"] + pd.DateOffset(years=1)
    tb = pd.concat([tb, tail], ignore_index=True)

    rows: list[dict] = []
    for _, r in tb.iterrows():
        if not r["season"]:
            continue
        for d in pd.date_range(r["start_date"], r["end_date"], freq="D"):
            rows.append({"date": d.normalize(), "season": r["season"]})
    cal = pd.DataFrame(rows).drop_duplicates(subset=["date"], keep="last")
    return cal


def _typical_rate(s: pd.Series) -> int:
    """Median weekday rate within a season (robust to one-off holiday spikes)."""
    v = pd.to_numeric(s, errors="coerce").dropna().astype(int)
    if v.empty:
        return 0
    return int(v.median())


def load_daily_sheet(path: Path) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [str(c).strip() for c in df.columns]
    if "Date" in df.columns:
        df = df.rename(columns={"Date": "date", "Day": "day_of_week"})
    df["date"] = pd.to_datetime(df["date"], errors="coerce")
    df = df.dropna(subset=["date"])
    if "day_of_week" not in df.columns and "Day" in df.columns:
        df["day_of_week"] = df["Day"]
    df["dow"] = df["day_of_week"].map(DOW_FULL)
    group_cols = [c for c in df.columns if c not in {"date", "day_of_week", "Day", "dow", "notes"}]
    return df, group_cols


def build_season_tables(daily: pd.DataFrame, group_cols: list[str]) -> pd.DataFrame:
    long_rows: list[dict] = []
    merged = daily.copy()
    merged["season"] = merged["date"].dt.month.map(_season_from_month)

    for season in SEASON_ORDER:
        sub = merged.loc[merged["season"] == season]
        if sub.empty:
            print(f"Warning: no dates mapped to season '{season}'")
            continue
        for col in group_cols:
            room = col
            for dow in DOW_COLS:
                mask = sub["dow"] == dow
                rate = _typical_rate(sub.loc[mask, col])
                long_rows.append(
                    {
                        "season": season,
                        "season_label": SEASON_LABELS[season],
                        "room_type": room,
                        "dow": dow,
                        "rate": rate,
                    }
                )

    return pd.DataFrame(long_rows)


def to_wide_season_blocks(long_df: pd.DataFrame) -> list[pd.DataFrame]:
    blocks: list[pd.DataFrame] = []
    for season in SEASON_ORDER:
        s = long_df.loc[long_df["season"] == season]
        if s.empty:
            continue
        wide = (
            s.pivot(index="room_type", columns="dow", values="rate")
            .reindex(index=ROOM_TYPES, columns=DOW_COLS)
            .reset_index()
            .rename(columns={"room_type": "room type"})
        )
        label_row = pd.DataFrame([[SEASON_LABELS[season]] + [""] * len(DOW_COLS)], columns=["room type"] + DOW_COLS)
        header = pd.DataFrame([["room type"] + DOW_COLS], columns=["room type"] + DOW_COLS)
        block = pd.concat([label_row, header, wide], ignore_index=True)
        blocks.append(block)
        blocks.append(pd.DataFrame([[""] * (len(DOW_COLS) + 1)], columns=["room type"] + DOW_COLS))
    return blocks


def write_workbook(blocks: list[pd.DataFrame], path: Path) -> None:
    try:
        import openpyxl  # noqa: F401
    except ImportError:
        print("openpyxl not installed; skipping XLSX (CSV still written).")
        return
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        out = pd.concat(blocks, ignore_index=True) if blocks else pd.DataFrame()
        out.to_excel(writer, sheet_name="base_rates", index=False, header=False)


def main() -> None:
    parser = argparse.ArgumentParser(description="Build LaFave season base-rate tables from a daily group sheet.")
    parser.add_argument(
        "--input",
        default=str(
            PROJECT_ROOT
            / "output/lafave_zion/pricing/pricing_sheet_group_2027-03-08_2028-01-09_owner.csv"
        ),
        help="Daily pricing sheet (date, day, one column per listing group).",
    )
    parser.add_argument(
        "--tier-blocks",
        default=str(PROJECT_ROOT / "output/lafave_zion/analysis/tier_blocks.csv"),
    )
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "output/lafave_zion/pricing"),
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.exists():
        raise SystemExit(f"Input not found: {input_path}")

    daily, group_cols = load_daily_sheet(input_path)
    # Reorder columns to owner room-type order when present
    group_cols = [c for c in ROOM_TYPES if c in group_cols] + [c for c in group_cols if c not in ROOM_TYPES]

    long_df = build_season_tables(daily, group_cols)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    long_path = out_dir / "lafave_base_rates_by_season_long.csv"
    long_df.to_csv(long_path, index=False)

    wide_rows: list[dict] = []
    for season in SEASON_ORDER:
        s = long_df.loc[long_df["season"] == season]
        for _, r in s.iterrows():
            wide_rows.append(
                {
                    "season": season,
                    "room_type": r["room_type"],
                    "dow": r["dow"],
                    "rate": r["rate"],
                }
            )
    wide = pd.DataFrame(wide_rows).pivot_table(
        index=["season", "room_type"], columns="dow", values="rate", aggfunc="first"
    )
    wide = wide.reindex(columns=DOW_COLS)
    wide_path = out_dir / "lafave_base_rates_by_season.csv"
    wide.reset_index().to_csv(wide_path, index=False)

    blocks = to_wide_season_blocks(long_df)
    stacked_path = out_dir / "lafave_base_rates_by_season_stacked.csv"
    pd.concat(blocks, ignore_index=True).to_csv(stacked_path, index=False, header=False)

    xlsx_path = out_dir / "lafave_base_rates_by_season.xlsx"
    write_workbook(blocks, xlsx_path)

    print(f"Wrote {long_path}")
    print(f"Wrote {wide_path}")
    print(f"Wrote {stacked_path}")
    if xlsx_path.exists():
        print(f"Wrote {xlsx_path}")

    merged = daily.copy()
    merged["season"] = merged["date"].dt.month.map(_season_from_month)
    for season in SEASON_ORDER:
        n_days = int((merged["season"] == season).sum())
        print(f"  {season}: {n_days} daily rows in sheet")


if __name__ == "__main__":
    main()
