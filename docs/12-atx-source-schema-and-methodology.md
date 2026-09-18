# 12. ATX source schema and methodology

ATX (Oasi Austin) is onboarded with **two PMS exports**: **Track** (historical migration) and **Hostaway** (current operations). Files live under `data/ATX/`.

---

## 1. Files and timeline

| File | PMS | Rows (raw) | Stay-date range (arrival) | Role |
|------|-----|------------|---------------------------|------|
| `…ATX_track_data.csv` | Track | 231 | 2023-12-27 → 2024-10-17 | Pre–Hostaway history (`Tags`: Migrated Historical Reservation) |
| `…ATX_hostaway_data.csv` | Hostaway | 573 | 2024-10-21 → 2026-10-23 | Live PMS export |

Handoff is ~**2024-10-17–21**: Track ends 10/17; Hostaway starts 10/21. No material overlap on the five core homes.

---

## 2. Listings (analysis scope)

**In scope (only):** `Sunstrip`, `Malvern`.

All Malvern / MLVRN Hostaway labels map to **`Malvern`** via `listing_name_map` so Track history (single home) and post-split 4BR / 5BR listings roll up to **one performance series**. Pricing uses that series for the **4BR anchor**; **5BR** = 4BR × multiplier with a **$600** nightly floor (`pricing.malvern.five_br_min_rate_usd`).

**Out of scope:** Burnhill Dr, Coventry Ln, Romney (dropped when `analysis_unit_filter: true`).

---

## 3. Track export semantics

Key columns:

- **Stay:** `Check-In`, `Checkout`, `Nights`
- **Unit:** `Unit Name`
- **Revenue:** `Rev` (total stay revenue used for ADR/revpar)
- **Booking:** `Booking Date` / `booking date`, `booking window`
- **Channel:** `Channel` (all rows Airbnb in sample)
- **Status:** `Checked Out` only in historical file
- **ID:** `Res. #` (internal); `Alt Res. #` (Airbnb confirmation)
- **Tags:** `Migrated Historical Reservation` vs `Migrated Future Reservation`

Excluded at map: non-positive `Rev`, cancelled-style statuses (none in historical sample).

---

## 4. Hostaway export semantics

Key columns:

- **Stay:** `Check-in date`, `Check-out date`, `Nights`
- **Unit:** `Listing.1` (canonical listing name); avoid raw `Listing` (rate-plan / MLVRN shorthand)
- **Revenue:** `rentalRevenue` (accommodation rent; not `totalPrice`)
- **Booking:** `Reservation date`, `booking date`, `booking window`
- **Channel:** `Channel.1` (human-readable: Airbnb, Direct); `Channel` is API slug (`airbnbOfficial`)
- **Payment:** `Payment status` — only **Paid** / **Partially paid** kept
- **Reservation status:** inquiries, cancelled, declined, owner stays excluded

### Duplicate rows (paid stays)

~14 stays appear **twice** when paid: one row with **higher** `rentalRevenue` (gross), one with **lower** (accommodation-only, often `totalTaxes = 0`). Ingestion keeps the **max `rentalRevenue`** row per guest + dates + unit.

### Row mix (raw)

| Reservation status | Approx. count | Kept? |
|--------------------|---------------|-------|
| new / modified (paid) | ~325 | Yes |
| inquiry | ~174 | No |
| cancelled | ~45 | No |
| declined / inquiryNotPossible | ~24 | No |

---

## 5. Ingestion command

```bash
python scripts/run_ingestion.py --property atx \
  "data/ATX/Dashboard revenue reporting _ Oasi 2026 - ATX_track_data.csv" \
  "data/ATX/Dashboard revenue reporting _ Oasi 2026 - ATX_hostaway_data.csv"
```

Outputs: `output/atx/ingestion/canonical.csv`, `validation_report.json`.

---

## 6. Analysis window

Configured in `config/properties/atx.yaml`:

- **start_date:** `2023-12-01` (first Track arrivals)
- **end_date:** `2026-10-31` (config cap; actual stays in canonical may end earlier, e.g. ~2026-09)

`room_count: 2` (Sunstrip + Malvern). Channel mix is effectively **Airbnb-only**.
