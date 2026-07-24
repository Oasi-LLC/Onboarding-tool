"""Build Adventure Inn Durango Jul–Sep rate matrix from agreed DOW bands.

Standard Queen is the base. Room-type offsets come from 2025 summer ADR
deltas vs Standard Queen (rounded). Thu == Sun by policy (not data).

Output (wide matrix format, same grain as docs/08):
  output/adventure_inn_durango/pricing/pricing_matrix_draft.csv
  output/adventure_inn_durango/pricing/pricing_matrix_summer_jul_sep.csv
  output/adventure_inn_durango/pricing/pricing_matrix_summer_jul_sep_long.csv
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUT_DIR = PROJECT_ROOT / "output" / "adventure_inn_durango" / "pricing"

DAY_COLS = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]

# Base = Standard Queen. Exact rates from midpoints of agreed bands.
# July & September share one DOW shape; August is stronger on weekends.
BASE_RATES: dict[int, dict[str, int]] = {
    # Jul/Sep: MTW 155–165 → 160; Thu/Sun 160–170 → 165; Fri–Sat 185–205 → 195
    7: {
        "Monday": 160,
        "Tuesday": 160,
        "Wednesday": 160,
        "Thursday": 165,
        "Friday": 195,
        "Saturday": 195,
        "Sunday": 165,  # forced equal to Thursday
    },
    9: {
        "Monday": 160,
        "Tuesday": 160,
        "Wednesday": 160,
        "Thursday": 165,
        "Friday": 195,
        "Saturday": 195,
        "Sunday": 165,
    },
    # August: MTW 150–165 → 158; Thu/Sun 170–185 → 178; Fri–Sat 190–210 → 200
    8: {
        "Monday": 158,
        "Tuesday": 158,
        "Wednesday": 158,
        "Thursday": 178,
        "Friday": 200,
        "Saturday": 200,
        "Sunday": 178,
    },
}

# Fixed $ offsets vs Standard Queen from 2025 Jul–Sep ADR deltas (rounded).
ROOM_OFFSETS: dict[str, int] = {
    "Small Queen": -20,
    "Standard Queen": 0,
    "Standard King": 7,
    "Double Queen": 18,
    # No 2025 summer history; kitchen premium vs Queen.
    "Standard Queen with Kitchen": 10,
}

MONTH_NAMES = {7: "July", 8: "August", 9: "September"}


def build_matrix() -> tuple[pd.DataFrame, pd.DataFrame]:
    wide_rows: list[dict] = []
    long_rows: list[dict] = []

    for month, dow_rates in sorted(BASE_RATES.items()):
        assert dow_rates["Thursday"] == dow_rates["Sunday"], "Thu must equal Sun"
        for room, offset in ROOM_OFFSETS.items():
            row = {"unit_id": room, "month_index": month, "month_name": MONTH_NAMES[month]}
            for day in DAY_COLS:
                rate = int(round(dow_rates[day] + offset))
                row[day] = rate
                long_rows.append(
                    {
                        "unit_id": room,
                        "month_index": month,
                        "month_name": MONTH_NAMES[month],
                        "day_of_week": day,
                        "base_standard_queen": dow_rates[day],
                        "room_offset": offset,
                        "draft_adr": rate,
                        "notes": "Thu=Sun by policy; Jul/Sep share DOW shape; offsets from 2025 summer ADR vs Queen",
                    }
                )
            wide_rows.append(row)

    wide = pd.DataFrame(wide_rows)
    long = pd.DataFrame(long_rows)
    # Match standard matrix column order (docs/08 wide export)
    wide_std = wide[["unit_id", "month_index"] + DAY_COLS].copy()
    return wide_std, long


def main() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    wide, long = build_matrix()

    draft_path = OUT_DIR / "pricing_matrix_draft.csv"
    summer_path = OUT_DIR / "pricing_matrix_summer_jul_sep.csv"
    long_path = OUT_DIR / "pricing_matrix_summer_jul_sep_long.csv"
    labeled_path = OUT_DIR / "pricing_matrix_summer_jul_sep_labeled.csv"

    wide.to_csv(draft_path, index=False)
    wide.to_csv(summer_path, index=False)
    long.to_csv(long_path, index=False)

    # Human-readable labeled wide (includes month_name)
    labeled = wide.merge(
        pd.DataFrame({"month_index": [7, 8, 9], "month_name": ["July", "August", "September"]}),
        on="month_index",
        how="left",
    )
    labeled = labeled[["unit_id", "month_index", "month_name"] + DAY_COLS]
    labeled.to_csv(labeled_path, index=False)

    print(f"Wrote {draft_path}")
    print(f"Wrote {summer_path}")
    print(f"Wrote {labeled_path}")
    print(f"Wrote {long_path} ({len(long)} rows = {len(ROOM_OFFSETS)} rooms × 3 months × 7 days)")
    print()
    print(labeled.to_string(index=False))


if __name__ == "__main__":
    main()
