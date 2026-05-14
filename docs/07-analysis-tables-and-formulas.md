# Analysis: Table Schemas & Metric Formulas

This document defines the **exact tables** and **formulas** for the onboarding analysis. Implementation should follow this spec.

---

## 1. Analysis period (two full years)

**Rule:** Use the **two complete calendar years before the current year**.

- Today is March 2026 → analyse **2024** and **2025** (both Jan–Dec).
- Formula: `year_1 = current_year - 2`, `year_2 = current_year - 1`.
- **Filter:** Include only rows where `arrival_date` falls in [Jan 1, year_1, 00:00] through [Dec 31, year_2, 23:59] (inclusive). All metrics in the tables below are computed on this filtered set unless stated otherwise.
- **If data doesn’t cover both years:** Use whatever full years are available and add a note (e.g. “Only 2025 available; two full years not present”). For LaFave we assume 2024 and 2025 are present.

### Stays spanning year boundaries (Dec → Jan)

We decide **inclusion** and **month assignment** by **arrival date** only (we do not split stays across months).

| Stay example | In analysis window (2024 + 2025)? | Where it appears in monthly tables |
|--------------|-----------------------------------|------------------------------------|
| **Arrival Dec 2023, checkout Jan 2024** | **No** — arrival is in 2023, outside the two-year window. The stay is **excluded** from all analysis. | Not included. |
| **Arrival Dec 2024, checkout Jan 2025** | **Yes** — arrival is in 2024. The **entire stay** (all nights and revenue) is included. | Assigned to **December 2024** only (`arrival_year_month` = 2024-12). Room-nights that fall in Jan 2025 are still counted in Dec 2024. |
| **Arrival Dec 2025, checkout Jan 2026** | **Yes** — arrival is in 2025. The entire stay is included. | Assigned to **December 2025** only (2025-12). |

**Rule:** Include a row if and only if `arrival_date` is within [Jan 1, year_1] to [Dec 31, year_2]. For monthly (and listing-by-month) tables, assign the **whole** stay to the **arrival month** (`arrival_year_month`); we do not split revenue or room-nights across the months the stay spans.

---

## 2. Metric formulas (definitions)

Use these definitions everywhere. All revenue is **room revenue only** (`revenue` from canonical).

| Metric | Formula | Notes |
|--------|--------|--------|
| **Revenue** | Sum of `revenue` over the relevant rows (period, segment, or listing). | Room rate only. |
| **Room-nights** | Sum of `nights` over the relevant rows. | Each row = 1 unit × nights. |
| **Bookings (no of)** | Count of rows (unit-stays). | One row = one unit-stay; multi-room reservation = multiple rows. |
| **ADR** | `Revenue / Room-nights` for the same scope. If room-nights = 0, ADR = null or 0. | Average daily rate (room revenue per room-night). |
| **YoY change (%)** | For a metric M: `(M_year2 - M_year1) / M_year1 * 100` if M_year1 ≠ 0; else null. | Year1 = first of the two years, Year2 = second. |
| **Share of revenue (%)** | For a segment: `(Revenue in segment / Total revenue in scope) * 100`. | Scope = e.g. whole property or a given year. |
| **Share of bookings (%)** | For a segment: `(Bookings in segment / Total bookings in scope) * 100`. | Same scope as share of revenue when used together. |
| **Check-ins (no of)** | Count of rows where **arrival_date** falls on the relevant date/day. | Arrival-based: "check-in on Monday" = unit-stay arrived on a Monday. |
| **RevPAR** | `Revenue / Room-nights available`. Room-nights available = `room_count * number of nights in period` (property-level) or `1 * nights in period` (per unit). | Only where we have room_count (property or unit). |
| **Occupancy (%)** | `(Room-nights sold / Room-nights available) * 100`. | Property or per unit. |

**Booking window band:** A lead-time bucket. Bucket boundaries are **configurable** (e.g. in analysis or property config). **Default bands for implementation:** 0–7, 8–14, 15–30, 31–45, 46–60, 61–75, 76–90, 91–120, 121–180, 181–270, 271–365, 365+ days (finer granularity from 31+ days). Each row is assigned to one band based on `lead_time_days`.

---

## 3. Table 1: Overall summary (listings + property)

**Purpose:** Headline metrics for each listing and for the entire property across the two years, with YoY change.

**Grain:**
- One row per **listing** (unit_id) + one row for **entire property** (e.g. `unit_id = "PROPERTY"` or a separate “Property total” row).
- All metrics are for the **combined two years** (2024 + 2025).
- YoY columns compare year_1 vs year_2.

**Columns:**

| Column | Formula / definition |
|--------|----------------------|
| `unit_id` | Listing identifier or `"PROPERTY"` for the total row. |
| `revenue` | Sum of `revenue` (two years combined). |
| `revenue_yoy_pct` | `(Revenue_year2 - Revenue_year1) / Revenue_year1 * 100`. |
| `room_nights` | Sum of `nights` (two years combined). |
| `bookings` | Count of rows (unit-stays). |
| `bookings_yoy_pct` | `(Bookings_year2 - Bookings_year1) / Bookings_year1 * 100`. |
| `adr` | Revenue / room_nights for the two years combined. |
| `adr_yoy_pct` | `(ADR_year2 - ADR_year1) / ADR_year1 * 100`. |
| `occupancy_pct` | (Room-nights sold / Room-nights available) * 100. Room-nights available: for property = `room_count * (365 + 365)`; for listing = 1 * 730. |
| `revpar` | Revenue / room-nights available (same denominator as occupancy). |

**Output:** e.g. `overall_summary.csv`.

---

## 4. Table 2: Monthly performance summary (revenue & ADR)

**Purpose:** Revenue and ADR by month across the two years (24 months).

**Grain:** One row per **month** (Jan 2024, Feb 2024, …, Dec 2025). Property-level only (all listings combined).

**Columns:**

| Column | Formula / definition |
|--------|----------------------|
| `year_month` | `arrival_year_month` (e.g. 2024-01, 2024-02, …, 2025-12). |
| `revenue` | Sum of `revenue` for that month. |
| `room_nights` | Sum of `nights` for that month. |
| `adr` | Revenue / room_nights for that month. |
| `bookings` | Count of rows for that month. |
| `occupancy_pct` | (Room-nights sold / Room-nights available) * 100. Available = room_count * days in month. |
| `revpar` | Revenue / room-nights available for that month. |

**Note:** The monthly performance score (1–10) is **not** in this table; it appears only in Table 2b (combined by calendar month).

**Output:** e.g. `monthly_performance.csv`.

---

## 4b. Table 2b: Monthly performance summary (combined across two years, by calendar month)

**Purpose:** Same as Table 2, but **aggregated across the two analysis years**, so you see one row per **calendar month** (Jan–Dec) instead of one row per year-month.

**Grain:** One row per calendar month (1–12), using **average values across the two analysis years** (e.g. Jan = average of Jan 2024 and Jan 2025).

**Columns:**

| Column | Formula / definition |
|--------|----------------------|
| `month_index` | Calendar month number 1–12. |
| `month_name` | Month label (January, February, …, December). |
| `revenue` | **Average** revenue per year for that calendar month (total revenue for that month across both years ÷ number of years with data). |
| `room_nights` | **Average** room-nights per year for that calendar month. |
| `adr` | Revenue / room_nights using the averaged values (average ADR for that calendar month). |
| `bookings` | **Average** bookings per year for that calendar month. |
| `occupancy_pct` | (Average room-nights sold / (room_count × days_in_month)) × 100, where days_in_month is the calendar month length (e.g. Jan = 31). |
| `revpar` | Average revenue per available room-night for that calendar month (average revenue ÷ (room_count × days_in_month)). |
| `performance_score_1_10` | Month “strength” on a **1–10 scale**: we rank the 12 calendar months by **revenue** and by **RevPAR** (averaged across both years), combine 50/50, and map to 1–10. 1 = weakest calendar month, 10 = strongest. |

**Output:** e.g. `monthly_performance_combined.csv`.

---

## 5. Table 3: Booking channel distribution (res, revenue; by years and by listings)

**Purpose:** Channel mix by number of reservations and revenue — at property level by year, and at listing level (combined years or by year as needed).

**3a) Property-level by year**

**Grain:** One row per **channel** per **year** (e.g. Booked Online × 2024, Booked Online × 2025, …).

**Columns:** `channel`, `year`, `bookings`, `revenue`, `room_nights`, `adr`, `share_of_revenue_pct` (within that year), `share_of_bookings_pct` (within that year).

**3b) Property-level combined (two years)**

**Grain:** One row per **channel**.

**Columns:** `channel`, `bookings`, `revenue`, `room_nights`, `adr`, `share_of_revenue_pct`, `share_of_bookings_pct`.

**3c) By listing (combined two years)**

**Grain:** One row per **channel** × **unit_id**.

**Columns:** `unit_id`, `channel`, `bookings`, `revenue`, `room_nights`, `adr`, `share_of_revenue_pct` (within that listing), `share_of_bookings_pct` (within that listing).

**Formulas:** Share of revenue = (revenue in segment / total revenue in scope) * 100. Scope = year for 3a, whole property for 3b, listing for 3c.

**Output:** e.g. `channel_by_year.csv`, `channel_summary.csv`, `channel_by_listing.csv` (or one sheet/table per grain with clear naming).

---

## 6. Table 4: Day-of-week performance (night-of-week pricing view)

**Purpose:** Build day-of-week pricing signal from **actual occupied stay nights** (not arrival-day-only attribution).

**Grain:** One row per **day of week** (Monday, Tuesday, …, Sunday), based on exploded `stay_date` rows.

**Columns:**

| Column | Formula / definition |
|--------|----------------------|
| `day_of_week` | Monday, Tuesday, …, Sunday (from exploded stay dates). |
| `check_ins` | Count of arrivals on that weekday (context only; not used in score). |
| `revenue` | Sum of nightly-attributed revenue on that weekday, where each reservation contributes `revenue / nights` per occupied night. |
| `room_nights` | Count of occupied stay nights on that weekday (after stay-date explosion). |
| `adr` | Revenue / room_nights for that day of week. |
| `share_of_check_ins_pct` | (check_ins / total check-ins) * 100. |
| `share_of_revenue_pct` | (revenue / total revenue) * 100. |
| `share_of_room_nights_pct` | (room_nights / total room_nights) * 100. |
| `dow_score_1_10` | Day-of-week “strength” on a **continuous 1–10 scale**, combining **ADR**, **share_of_revenue_pct**, and **share_of_room_nights_pct**. For each metric we rank days across the 7 weekdays, convert ranks to 0–1, then combine **40% ADR**, **40% revenue share**, and **20% room-night share** and map to 1–10; values near 1 are weakest, near 10 strongest. |

**Output:** e.g. `by_day_of_week.csv`.

---

## 7. Table 5: Booking window analysis (share of revenue by band; two years)

**Purpose:** Share of revenue in each lead-time band across the two years.

**Grain:** One row per **booking window band** (defaults: 0–7, 8–14, 15–30, 31–45, 46–60, 61–75, 76–90, 91–120, 121–180, 181–270, 271–365, 365+ days). Bands are configurable.

**Columns:**

| Column | Formula / definition |
|--------|----------------------|
| `booking_window` | Band label (e.g. "0-7 days", "8-14 days"). |
| `lead_time_min` | Min days in band (for sorting). |
| `lead_time_max` | Max days in band. |
| `bookings` | Count of rows with `lead_time_days` in that band. |
| `revenue` | Sum of `revenue` for those rows. |
| `share_of_revenue_pct` | (revenue in band / total revenue) * 100. |
| `share_of_bookings_pct` | (bookings in band / total bookings) * 100. |
| `room_nights` | Sum of `nights` for those rows. |

**Output:** e.g. `booking_window.csv`.

---

## 8. Table 6: ADR by listing × year_month (two years)

**Purpose:** For each listing, ADR (and volume) by month with year (e.g. 2024-01, 2025-06).

**Grain:** One row per **unit_id** × **year_month** (e.g. 2024-01, 2024-02, …, 2025-12). So 32 listings × 24 months = up to 768 rows (or fewer if a listing has no data in a month).

**Columns:**

| Column | Formula / definition |
|--------|----------------------|
| `unit_id` | Listing. |
| `year_month` | `arrival_year_month` (e.g. 2024-01, 2025-06). Single column for period; no separate month number or month name. |
| `bookings` | Count of rows for that listing in that month. |
| `room_nights` | Sum of `nights`. |
| `revenue` | Sum of `revenue`. |
| `adr` | Revenue / room_nights for that listing × year_month (mean ADR). |
| `min_adr` | Minimum row-level ADR (`adr` in canonical) for stays arriving in that month. |
| `max_adr` | Maximum row-level ADR for stays arriving in that month. |

**Note:** Percentiles (P25, P50, P75) can be added later if needed.

**Output:** e.g. `adr_by_listing_by_month.csv`.

---

## 9. Table 7: Listing performance by season (High / Shoulder / Low)

**Purpose:** See how each listing performs in **High**, **Shoulder**, and **Low** seasons, and whether the top performers change with seasonality.

**Season definition (calendar months, based on combined monthly findings):**

- **High season:** April, May, June, September, October.
- **Low season:** January, February, December.
- **Shoulder:** March, July, August, November.

**Grain:** One row per **unit_id** × **season** (at most three rows per listing).

**Columns:**

| Column | Formula / definition |
|--------|----------------------|
| `unit_id` | Listing. |
| `season` | One of `High`, `Shoulder`, `Low` based on arrival month. |
| `season_revenue` | Sum of `revenue` for that listing over all arrivals in months belonging to that season. |
| `season_room_nights` | Sum of `nights` over those arrivals. |
| `season_bookings` | Count of rows (unit-stays) for that listing in that season. |
| `season_adr` | `season_revenue / season_room_nights` (average ADR for that listing in that season). |
| `season_score_1_10` | Listing’s performance in that season on a 1–10 scale (float), combining **season_revenue** and **season_revpar** (both ranked across listings within that season, with revenue weighted 70% and RevPAR 30%; 1 = weakest listing in that season, 10 = strongest). |
| `season_percentile` | Percentile rank of the listing within its season (0–100), derived from the same normalized score (e.g. 97.0 = 97th percentile among listings in that season). |

**Output:** e.g. `listing_season_performance.csv`.

---

## 9. Optional metrics to consider

- **Room-nights** in every table where revenue is shown (for context).
- **Occupancy %** and **RevPAR** in overall summary and monthly summary (already in Tables 1 and 2).
- **Avg LOS** (sum of nights / bookings) in overall summary and possibly by channel.
- **Avg lead time** in overall summary or in booking-window table as a summary row.
- **YoY for room-nights** in overall summary (same formula as for revenue/bookings/ADR).

If you want any of these added to specific tables, we can add columns to the schemas above.

---

## 10. Summary: tables and output files

| # | Table name | Grain | Main metrics | Output file (suggested) |
|---|------------|--------|---------------|-------------------------|
| 1 | Overall summary | Listing + property | Revenue, ADR, bookings, YoY %, occupancy, RevPAR | `overall_summary.csv` |
| 2 | Monthly performance | Month (24 rows) | Revenue, ADR, room_nights, bookings, occupancy, RevPAR | `monthly_performance.csv` |
| 3a | Channel by year | Channel × year | Bookings, revenue, share % | `channel_by_year.csv` |
| 3b | Channel summary | Channel | Same, combined years | `channel_summary.csv` |
| 3c | Channel by listing | Channel × unit_id | Same, per listing | `channel_by_listing.csv` |
| 4 | Day of week | Day of week | Room nights, ADR, share % | `by_day_of_week.csv` |
| 5 | Booking window | Lead-time band | Revenue, bookings, share of revenue % | `booking_window.csv` |
| 6 | ADR by listing × year_month | unit_id × year_month | ADR, revenue, room_nights, bookings | `adr_by_listing_by_month.csv` |

---

## 11. Two-year rule and filters (recap)

- **Analysis years:** `year_1 = current_year - 2`, `year_2 = current_year - 1` (e.g. 2024 and 2025 when current year is 2026).
- **Filter:** Restrict to rows with `arrival_date` in [Jan 1, year_1] through [Dec 31, year_2].
- **If two full years not available:** Use available full years and add a note; for this property we assume 2024 and 2025 are available.

All formulas in §2 apply to the filtered dataset unless a table specifies otherwise (e.g. “by year” within the two years).

---

## 12. Finalised checklist (implementation)

Use this to confirm everything is locked before coding.

| Item | Status | Reference |
|------|--------|-----------|
| Analysis period | **Finalised** | Two full years: current_year - 2 and current_year - 1 (§1). |
| Revenue definition | **Finalised** | Room revenue only; use canonical `revenue` (§2). |
| All metric formulas | **Finalised** | §2 (Revenue, room-nights, bookings, ADR, YoY %, share %, check-ins, RevPAR, occupancy). |
| Table 1: Overall summary | **Finalised** | §3 — listing + property row; columns as listed. |
| Table 2: Monthly performance | **Finalised** | §4 — 24 months; property-level. |
| Table 3: Channel (3a, 3b, 3c) | **Finalised** | §5 — by year, combined, by listing. |
| Table 4: Day of week | **Finalised** | §6 — stay-date exploded night-of-week attribution. |
| Table 5: Booking window | **Finalised** | §7 — bands configurable; default bands in §2. |
| Table 6: ADR by listing x year_month | **Finalised** | §8 — unit_id x year_month (e.g. 2024-01 … 2025-12). |
| Output files | **Finalised** | One CSV per table; names in §10. |
| Bookings definition | **Finalised** | Row count = unit-stays (one row per unit-stay). |
| Day-of-week definition | **Finalised** | Stay-date exploded night-of-week attribution; arrival check-ins retained as context only. |
| When data has &lt; 2 full years | **Finalised** | Use available full years; add a note. |
| Stays spanning year boundaries (e.g. Dec 2023 → Jan 2024, Dec 2024 → Jan 2025) | **Finalised** | Include only if **arrival_date** is in window. Assign whole stay to **arrival month**; do not split across months. See §1 "Stays spanning year boundaries". |
