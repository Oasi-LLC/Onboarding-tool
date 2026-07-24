# 14. Adventure Inn Durango — source schema and methodology

Property id: `adventure_inn_durango`. PMS: **unknown** (`pms_id: unknown_pms`). Do **not** reuse the Cloudbeds mapper; column names are similar to some Cloudbeds reports but the source is treated as a distinct export format.

---

## 1. Source file

| Item | Value |
|------|--------|
| Export | “Reservations with Financials” Excel (`.xlsx`) |
| Location | `data/adventure_inn_durango/` |
| Header | Row 6 (0-index 5); 5 metadata rows above |
| Trailing junk | Drop totals row, blank row, and report-filter string row |
| Property name | Adventure Inn Durango |

Filter string in the export shows statuses limited to Confirmation Pending / Confirmed / No Show / Checked Out / In-House — **cancelled demand is not in this file**.

---

## 2. Business eras (affect interpretation, not raw formulas)

| Period | Context |
|--------|---------|
| Pre–Aug 2024 | Prior ownership |
| Aug–Oct 2024 | Ownership transition (refunds / adjustments possible) |
| Nov 2024 – May 2025 | Phased renovation (capacity constrained) |
| Jun 2025 – ~Mar 2026 | Post-reno ~**25** rooms |
| ~Apr 2026 onward | **27** rooms (+2) |

**Summer rate anchor:** 2025 Jul/Aug/**Sep** achieved ADR (not 2024, not AirDNA).

---

## 3. Canonical mapping rules

| Canonical | Source / rule |
|-----------|----------------|
| `reservation_id` | Reservation Number |
| `arrival_date` / `departure_date` | Check-In / Check-Out |
| `nights` | **LOS** = checkout − checkin (never use export “Room Nights” as LOS) |
| `unit_id` | Room type (from Room Numbers codes or Room Types); multi-room rows **expanded** one row per room |
| `guest_name` | Empty (not in export); Repeat Guest Flag is not mapped to canonical |
| `revenue` | Room Revenue Total, split evenly across expanded room rows |
| `amount_paid` | Reservation Paid Amount / room count |
| `booking_date` | Booking Date Time - UTC (date) |
| `lead_time_days` | arrival − booking, **clipped at 0** (UTC same-day artifact) |
| `adr` | revenue / nights (per room row after expand) |
| `channel` | Reservation Source (fallback: Reservation Source Category) |

**Excluded from canonical / analysis:** `In-House` status. Kept: Checked Out, Confirmed, No Show (and other non-excluded statuses present).

**Room code → type:** STQ → Standard Queen; STK → Standard King; DQ → Double Queen; SMQ → Small Queen; STQK → Standard Queen with Kitchen.

**Capacity:** `capacity_schedule` in property config (25 until 2026-03-31; 27 from 2026-04-01).

---

## 4. Metric catalog (dashboard)

### Headline KPIs
Revenue, ADR, Bookings, Occupancy %, RevPAR — capacity-aware when schedule is present.

### Core tables (standard pipeline)
Monthly, channel, day-of-week, booking window, ADR by listing × month, listing seasonality — formulas per `docs/07-analysis-tables-and-formulas.md`.

### Summer deep-dive (`output/.../analysis/summer/`)
Stay-month grain — **Jul / Aug / Sep never combined**. Dashboard month toggle filters every table.

| File | Purpose |
|------|---------|
| `summer_year_month_kpis.csv` | Jul/Aug/Sep KPIs by year-month |
| `summer_pace_asof.csv` | On-books by refreshed as-of date of stay year (+ vs LY on-books) |
| `summer_dow_adr.csv` | Arrival DOW ADR + weekend premium **per month** |
| `summer_weekday_weekend.csv` | Weekday vs Fri–Sat ADR + median/mean lead + lead-band shares |
| `summer_channel.csv` | Channel mix / ADR **per month** (insight; separate from rate bands) |
| `summer_room_type.csv` | Room-type ladder **per month** |
| `summer_lead_bands.csv` | Lead bands 0-6 … 91+ **per month** |
| `summer_daily_occupancy.csv` | Night-level occ / ADR |
| `summer_recommendations.csv` | This-summer bands from **2025 property** actuals |

### AirDNA (secondary)
`data/airdna/adventure_inn_durango/` — filter set: 0–1 BR, 1–2 bath, 1–4 guests, entire/private, economy–upscale.

| File | Purpose |
|------|---------|
| `output/.../benchmark/market_context_monthly.csv` | Market occ / ADR / RevPAR / revenue |
| `output/.../benchmark/market_summer_compare.csv` | Property vs market Jul–Sep gaps |
| `output/.../benchmark/market_booking_window.csv` | Market RevPAR by lead band (pace shape) |

**Property metrics govern pricing.** Label on dashboard: market context does not override property ADR/occ.

---

## 5. Ingestion / analysis commands

```bash
python scripts/run_ingestion.py --property adventure_inn_durango \
  "data/adventure_inn_durango/Reservations with Financials-14.xlsx"

python scripts/run_analysis.py --property adventure_inn_durango

python scripts/run_summer_analysis.py --property adventure_inn_durango

python scripts/build_durango_market_context.py --property adventure_inn_durango

streamlit run scripts/run_dashboard.py -- --property adventure_inn_durango
```
