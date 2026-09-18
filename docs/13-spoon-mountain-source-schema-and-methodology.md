# 13. Spoon Mountain source schema and methodology

Spoon Mountain uses **OwnerRez** (Oasi dashboard export). File: `data/SPM/Dashboard revenue reporting _ Oasi 2026 - SpoonMount_data.csv`.

## Source columns

| Column | Role |
|--------|------|
| `Booking #` | Reservation ID (e.g. `ORB9512818`) |
| `Property` | Unit / listing (`Kingfisher`, `Shaka`, `Chisum`) |
| `Guest` | Guest name; cancellations tagged `[CANCELED]` |
| `Listing Site` | OTA / direct source (`Website`, `Airbnb`) |
| `Booked` | Booking date (M/D/YYYY) |
| `Arrival` / `Departure` | Stay dates |
| `N` | Nights |
| `Rent` | Accommodation / rent component (not used as canonical `revenue`) |
| `Revenue` | Total booking revenue → canonical `revenue` |
| `Booking Window` | Lead time (days); matches `Arrival − Booked` |
| `Channel` | Normalized channel (`Direct`, `Airbnb`); may be empty |
| `Net Payments` | Amount collected (canonical `amount_paid`) |

## Canonical mapping

| Canonical | Source |
|-----------|--------|
| `reservation_id` | `Booking #` |
| `unit_id` | `Property` |
| `revenue` | `Revenue` |
| `amount_paid` | `Net Payments` |
| `channel` | `Channel`, else `Listing Site` (`Website` → Direct) |
| `lead_time_days` | `Booking Window` |

Excluded: guest names with `[CANCELED]`; rows with `Revenue` ≤ 0.

## Property config

`config/properties/spoon_mountain.yaml` — 3 units, `pms_id: ownerrez`. Analysis window: **2024-01-01 through 2025-12-31** (two full calendar years).

## Commands

```bash
source .venv/bin/activate

python scripts/run_ingestion.py --property spoon_mountain \
  "data/SPM/Dashboard revenue reporting _ Oasi 2026 - SpoonMount_data.csv"

python scripts/run_analysis.py --property spoon_mountain
```

Outputs: `output/spoon_mountain/ingestion/`, `output/spoon_mountain/analysis/`.

## Pricing hierarchies (`config/properties/spoon_mountain.yaml`)

**Day of week** (weak → strong): Wednesday → Tuesday → Thursday → Monday/Sunday → Friday/Saturday.

**Month** (score 1–10): Jan 1, Feb 2, Sep/Oct 3, Dec 4, Mar 5, Aug 6, Apr 7, Jul/Nov 8, May 9, Jun 10.

**Listings:** one tier — Kingfisher, Shaka, Chisum priced identically.

```bash
PYTHONPATH=. python scripts/run_pricing_matrix.py --property spoon_mountain
```

## PriceLabs overrides (OwnerRez)

Source: `data/SPM/2027pricingfull.csv` (daily rates in `Shaka/Chisum/Kingfisher` column).

| Listing | PriceLabs Id |
|---------|----------------|
| Chisum | 278915 |
| Kingfisher | 303587 |
| Shaka | 303588 |

```bash
python scripts/build_spm_pricelabs_sheet.py
# -> output/spoon_mountain/pricing/pricelabs_override_sheet_<start>_to_<end>.csv
```
