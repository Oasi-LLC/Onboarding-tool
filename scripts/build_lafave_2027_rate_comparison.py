#!/usr/bin/env python3
"""Build LaFave 2027 draft matrix vs published override rates in owner season grid format."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path
from typing import Optional

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.property_config import load_property_inventory

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
DOW_MAP = {
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
    "shoulder": "SEASON: Summer pricing July, August 7/8",
}

SEASON_MONTHS = {
    "high": {3, 4, 5, 6, 9, 10},
    "low": {1, 2, 11, 12},
    "shoulder": {7, 8},
}

ALIASES = {
    "Suite SMALL (1BR/1BA) The Zion": "The Zion Suite",
    "(3BR/2BA) Johnson Mountain Villa": "Johnson Mountain",
    "(6BR/4BA) The Gallery House": "The Gallery House",
}


def _season_from_month(month: int) -> str:
    for season, months in SEASON_MONTHS.items():
        if month in months:
            return season
    return "high"


def _typical_rate(s: pd.Series) -> int:
    v = pd.to_numeric(s, errors="coerce").dropna()
    if v.empty:
        return 0
    return int(round(v.median()))


def _match_unit(suffix_str: str, unit_ids: list[str]) -> Optional[str]:
    needle = ALIASES.get(
        suffix_str,
        re.sub(
            r"^(Suite BIG|Suite SMALL|Premium Villa|Deluxe Villa|Premier Villa|Villa Game)\s*\([^)]+\)\s*",
            "",
            suffix_str,
        ).strip(),
    )
    for uid in unit_ids:
        part = uid.split(": ", 1)[-1]
        if part == needle or uid.endswith(needle) or needle.endswith(part):
            return uid
    return None


def _suffix(name: str) -> str:
    return name.split("--", 1)[1].strip() if "--" in name else name.strip()


def build_draft_season_tables(group_matrix_path: Path) -> pd.DataFrame:
    gm = pd.read_csv(group_matrix_path)
    day_cols = [c for c in gm.columns if c in DOW_MAP]
    rows: list[dict] = []
    for season in SEASON_ORDER:
        months = SEASON_MONTHS[season]
        sub = gm.loc[gm["month_index"].isin(months)]
        for group in ROOM_TYPES:
            gsub = sub.loc[sub["listing_group"] == group]
            if gsub.empty:
                continue
            for full_dow, short in DOW_MAP.items():
                if full_dow not in gsub.columns:
                    continue
                rate = _typical_rate(gsub[full_dow])
                rows.append(
                    {
                        "source": "draft_matrix",
                        "season": season,
                        "room_type": group,
                        "dow": short,
                        "rate": rate,
                    }
                )
    return pd.DataFrame(rows)


def build_published_season_tables(
    overrides_path: Path,
    *,
    year: int = 2027,
    start_date: str = "2027-01-04",
) -> pd.DataFrame:
    inv = load_property_inventory("lafave_zion")
    unit_ids = [r["unit_id"] for r in inv["room_types"]]
    unit_to_group = {u: g["name"] for g in inv["listing_groups"] for u in g["unit_ids"]}

    raw = pd.read_csv(overrides_path)
    raw["date"] = pd.to_datetime(raw["Start Date"], format="mixed", errors="coerce")
    raw["Price"] = pd.to_numeric(raw["Price"], errors="coerce")
    raw = raw.dropna(subset=["date", "Price"])
    raw = raw[(raw["date"].dt.year == year) & (raw["date"] >= pd.Timestamp(start_date))].copy()

    raw["suffix"] = raw["Listing Name"].map(_suffix)
    raw["unit_id"] = raw["suffix"].map(lambda s: _match_unit(s, unit_ids))
    raw = raw.dropna(subset=["unit_id"]).copy()
    raw["listing_group"] = raw["unit_id"].map(unit_to_group)
    raw = raw.dropna(subset=["listing_group"]).copy()
    raw["season"] = raw["date"].dt.month.map(_season_from_month)
    raw["dow"] = raw["date"].dt.day_name().map(DOW_MAP)

    rows: list[dict] = []
    for season in SEASON_ORDER:
        sub = raw.loc[raw["season"] == season]
        for group in ROOM_TYPES:
            gsub = sub.loc[sub["listing_group"] == group]
            if gsub.empty:
                continue
            for dow in DOW_COLS:
                rate = _typical_rate(gsub.loc[gsub["dow"] == dow, "Price"])
                rows.append(
                    {
                        "source": "published",
                        "season": season,
                        "room_type": group,
                        "dow": dow,
                        "rate": rate,
                    }
                )
    return pd.DataFrame(rows)


def _season_block(long_df: pd.DataFrame, season: str, source_label: str) -> pd.DataFrame:
    s = long_df.loc[long_df["season"] == season]
    wide = (
        s.pivot(index="room_type", columns="dow", values="rate")
        .reindex(index=ROOM_TYPES, columns=DOW_COLS)
        .reset_index()
        .rename(columns={"room_type": "room type"})
    )
    for c in DOW_COLS:
        wide[c] = wide[c].fillna(0).astype(int)
    label = pd.DataFrame([[f"{source_label} — {SEASON_LABELS[season]}"] + [""] * len(DOW_COLS)], columns=["room type"] + DOW_COLS)
    header = pd.DataFrame([["room type"] + DOW_COLS], columns=["room type"] + DOW_COLS)
    return pd.concat([label, header, wide], ignore_index=True)


def build_gap_block(draft: pd.DataFrame, published: pd.DataFrame, season: str) -> pd.DataFrame:
    d = draft.loc[draft["season"] == season].set_index(["room_type", "dow"])["rate"]
    p = published.loc[published["season"] == season].set_index(["room_type", "dow"])["rate"]
    rows: list[dict] = []
    for group in ROOM_TYPES:
        row: dict = {"room type": group}
        for dow in DOW_COLS:
            dv = d.get((group, dow), 0)
            pv = p.get((group, dow), 0)
            row[dow] = int(pv - dv) if pv and dv else (int(pv - dv) if pv or dv else 0)
        rows.append(row)
    wide = pd.DataFrame(rows)
    label = pd.DataFrame([[f"GAP (published − draft) — {SEASON_LABELS[season]}"] + [""] * len(DOW_COLS)], columns=["room type"] + DOW_COLS)
    header = pd.DataFrame([["room type"] + DOW_COLS], columns=["room type"] + DOW_COLS)
    return pd.concat([label, header, wide], ignore_index=True)


def _blank_row() -> pd.DataFrame:
    return pd.DataFrame([[""] * (len(DOW_COLS) + 1)], columns=["room type"] + DOW_COLS)


def write_stacked_comparison(
    draft_long: pd.DataFrame,
    pub_long: pd.DataFrame,
    out_dir: Path,
    *,
    year: int,
) -> None:
    blocks: list[pd.DataFrame] = []
    title = pd.DataFrame([[f"LaFave {year} — Draft pricing matrix (from analysis)"] + [""] * len(DOW_COLS)], columns=["room type"] + DOW_COLS)
    blocks.extend([title, _blank_row()])
    for season in SEASON_ORDER:
        blocks.append(_season_block(draft_long, season, "DRAFT MATRIX"))
        blocks.append(_blank_row())

    blocks.append(pd.DataFrame([[f"LaFave {year} — Published rates (listing-level overrides)"] + [""] * len(DOW_COLS)], columns=["room type"] + DOW_COLS))
    blocks.append(_blank_row())
    for season in SEASON_ORDER:
        blocks.append(_season_block(pub_long, season, "PUBLISHED"))
        blocks.append(_blank_row())

    blocks.append(pd.DataFrame([[f"LaFave {year} — Gap: published minus draft"] + [""] * len(DOW_COLS)], columns=["room type"] + DOW_COLS))
    blocks.append(_blank_row())
    for season in SEASON_ORDER:
        blocks.append(build_gap_block(draft_long, pub_long, season))
        blocks.append(_blank_row())

    stacked = pd.concat(blocks, ignore_index=True)
    csv_path = out_dir / f"lafave_{year}_draft_vs_published_stacked.csv"
    stacked.to_csv(csv_path, index=False, header=False)

    xlsx_path = out_dir / f"lafave_{year}_draft_vs_published.xlsx"
    with pd.ExcelWriter(xlsx_path, engine="openpyxl") as writer:
        stacked.to_excel(writer, sheet_name="comparison", index=False, header=False)
        draft_wide = draft_long.pivot_table(index=["season", "room_type"], columns="dow", values="rate", aggfunc="first")
        pub_wide = pub_long.pivot_table(index=["season", "room_type"], columns="dow", values="rate", aggfunc="first")
        draft_wide.reset_index().to_excel(writer, sheet_name="draft_long", index=False)
        pub_wide.reset_index().to_excel(writer, sheet_name="published_long", index=False)

    print(f"Wrote {csv_path}")
    print(f"Wrote {xlsx_path}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--property", default="lafave_zion")
    parser.add_argument("--year", type=int, default=2027)
    parser.add_argument("--start-date", default="2027-01-04", help="Published rate window start (YYYY-MM-DD)")
    parser.add_argument(
        "--overrides",
        default=str(Path.home() / "Downloads/listing-level-overrides (88).csv"),
    )
    parser.add_argument(
        "--group-matrix",
        default=str(PROJECT_ROOT / "output/lafave_zion/pricing/pricing_matrix_group_draft.csv"),
    )
    parser.add_argument(
        "--output-dir",
        default=str(PROJECT_ROOT / "output/lafave_zion/pricing"),
    )
    args = parser.parse_args()

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    draft_long = build_draft_season_tables(Path(args.group_matrix))
    pub_long = build_published_season_tables(
        Path(args.overrides), year=args.year, start_date=args.start_date
    )

    draft_long.to_csv(out_dir / f"lafave_{args.year}_draft_by_season_long.csv", index=False)
    pub_long.to_csv(out_dir / f"lafave_{args.year}_published_by_season_long.csv", index=False)

    write_stacked_comparison(draft_long, pub_long, out_dir, year=args.year)

    for source, df in [("Draft matrix", draft_long), ("Published", pub_long)]:
        print(f"\n{source} — median rate by season:")
        for season in SEASON_ORDER:
            sub = df.loc[df["season"] == season, "rate"]
            sub = sub[sub > 0]
            if sub.empty:
                print(f"  {season}: (no data)")
            else:
                print(f"  {season}: ${int(sub.median())} typical (median of group×DOW cells)")


if __name__ == "__main__":
    main()
