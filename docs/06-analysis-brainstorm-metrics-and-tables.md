# Analysis Phase: Metrics & Summary Tables (Brainstorm)

This document brainstorms **what** to compute and **how** to summarize it for the onboarding analysis. No implementation yet — we nail down metrics, dimensions, and table shapes first.

---

## 1. Core metrics (definitions & why they matter)

### 1.1 Volume & capacity

| Metric | Definition | Why it matters for pricing |
|--------|------------|----------------------------|
| **Room-nights sold** | Sum of `nights` (each row = 1 unit × nights). Can be by date (each night) or by arrival period. | Base for occupancy; demand level. |
| **Room-nights available** | `room_count` × number of nights in period (from property config). | Denominator for occupancy %. |
| **Occupancy %** | Room-nights sold ÷ room-nights available (by period). | Core performance metric; informs rate strategy (push rate when occupancy high). |
| **Reservations (count)** | Count of rows, or count of distinct `reservation_id` (if we want “bookings” not “unit-stays”). | Volume of demand; mix of short vs long stays. |

**Open:** Do we report occupancy at **property level** only, or also by **unit/room type**? (Requires “available room-nights per unit type” if we have 1 unit per type.)

---

### 1.2 Revenue & rate

| Metric | Definition | Why it matters for pricing |
|--------|------------|----------------------------|
| **Revenue (total)** | Sum of `revenue` (room rate only, no taxes/fees). | Top-line performance. |
| **RevPAR** | Revenue ÷ room-nights available (by period). | Combines rate and occupancy; industry standard. |
| **ADR (average daily rate)** | Revenue ÷ room-nights sold (or mean of `adr` per row). Can be overall or by segment. | Rate level; compare across channel, unit, time. |
| **Revenue per reservation** | Total revenue ÷ reservation count (or per unit-stay). | Blends rate and LOS. |

**Open:** Do we need **TRevPAR** (total revenue including fees/taxes ÷ available room-nights) using `amount_paid`? Or keep focus on room revenue only?

---

### 1.3 Booking behavior

| Metric | Definition | Why it matters for pricing |
|--------|------------|----------------------------|
| **Lead time (days)** | Already in canonical: `lead_time_days` = arrival − booking_date. | Booking window: early vs last-minute; informs early-bird vs last-minute pricing. |
| **Lead time distribution** | Count or % of reservations in buckets (e.g. 0, 1–7, 8–14, 15–30, 31–60, 61–90, 91–180, 181–365, 365+ days). | Shape of demand curve over time. |
| **Length of stay (LOS)** | Already in canonical: `nights`. | Weekend vs week-long; min-stay and package opportunities. |
| **LOS distribution** | Count or % in buckets (e.g. 1, 2, 3, 4–7, 8+ nights). | Informs min-stay and length-based rules. |
| **Booking curve** | For a given arrival period, how many room-nights were booked at 90, 60, 30, 14, 7 days before arrival (or similar). | When demand materializes; pricing timing. |

**Open:** For “booking curve,” do we restrict to a **reference period** (e.g. last 12 months of arrivals) so the curve is comparable?

---

### 1.4 Mix & segmentation

| Metric | Definition | Why it matters for pricing |
|--------|------------|----------------------------|
| **Channel mix** | % of room-nights or revenue by `channel`. | Direct vs OTA; where to invest and how to price by channel. |
| **Channel ADR** | ADR (or revenue/room-nights) by channel. | Which channels deliver higher rate. |
| **Unit/room-type mix** | % of room-nights or revenue by `unit_id` (or grouped room type). | Which units drive revenue; upgrade paths. |
| **Unit/room-type ADR** | ADR by unit. | Rate positioning by product. |
| **Day-of-week mix** | % of room-nights or revenue by `arrival_day_of_week` (or weekend vs midweek). | Weekend premium; arrival patterns. |
| **Day-of-week ADR** | ADR by day of week. | Weekend vs midweek rate gap. |

**Open:** Do we define “weekend” as Fri–Sun arrivals, or Sat–Sun only? Do we need **arrival day** vs **stay night** (e.g. revenue by night of week the stay occurs)?

---

### 1.5 Time trends

| Metric | Definition | Why it matters for pricing |
|--------|------------|----------------------------|
| **Occupancy / RevPAR / ADR by month (or quarter)** | Aggregate by `arrival_year_month` or quarter. | Seasonality; peak vs shoulder vs low. |
| **YoY or period-over-period** | Same metric, same period (e.g. Jan 2024 vs Jan 2023). | Growth or decline; market shift. |
| **Index (e.g. base = 100)** | Normalize a series to a baseline period. | Visualize trend without scale. |

**Open:** Default **time grain** for “overview” tables: month, quarter, or both? Do we want **rolling** metrics (e.g. last 12 months) in addition to calendar periods?

---

## 2. Summary tables (dimensions & columns)

Think of each table as: **rows = one slice of the data** (e.g. one month, one channel), **columns = metrics**.

### 2.1 Property / period overview

**Purpose:** One-page view of performance over time.

| Table idea | Row dimension | Columns (metrics) | Notes |
|------------|----------------|-------------------|--------|
| **Monthly summary** | One row per month (arrival_year_month) | Room-nights sold, occupancy %, revenue, RevPAR, ADR, reservation count, avg LOS, avg lead time | Core trend; seasonality. |
| **Quarterly summary** | One row per quarter | Same as above | Higher-level seasonality. |
| **Overall summary (single row)** | Full date range | Total room-nights, total revenue, avg occupancy (if single period), overall ADR, avg LOS, avg lead time, distinct units, reservation count | Headline stats. |

**Open:** Do we restrict “monthly” to a **max history** (e.g. last 24 months) or show all data?

---

### 2.2 By channel

**Purpose:** Channel mix and performance.

| Table idea | Row dimension | Columns | Notes |
|------------|----------------|---------|--------|
| **Channel summary** | One row per channel | Room-nights, revenue, % of room-nights, % of revenue, ADR, reservation count, avg LOS | Current mix + rate by channel. |
| **Channel by month** | Channel × month | Room-nights, revenue, ADR (and optionally % of that month) | How channel mix and rate evolve. |

**Open:** Do we exclude or flag channels with very low volume (e.g. 3 reservations)?

---

### 2.3 By unit / room type

**Purpose:** Which units drive performance; rate by product.

| Table idea | Row dimension | Columns | Notes |
|------------|----------------|---------|--------|
| **Unit summary** | One row per unit_id | Room-nights, revenue, % of room-nights, % of revenue, ADR, reservation count, avg LOS | Unit-level performance. |
| **Unit by month** | Unit × month | Room-nights, revenue, ADR | Trend by unit (can be large if many units). |

**Open:** If there are many units (e.g. 32), do we also want a **grouped** view (e.g. by “building” or “tier” if we can derive from unit_id)? Or keep unit-level only?

---

### 2.4 By day of week

**Purpose:** Weekend vs midweek patterns.

| Table idea | Row dimension | Columns | Notes |
|------------|----------------|---------|--------|
| **Day-of-week summary** | One row per weekday (Mon–Sun) | Room-nights (arrival-based), revenue, ADR, reservation count | Weekend premium check. |
| **Weekend vs midweek** | Two rows: Weekend, Midweek | Same metrics | Quick comparison. |

**Open:** “Room-nights” by day of week: do we mean **arrivals** on that day (simpler) or **nights stayed** that fall on that weekday (needs expanding stays to nights)?

---

### 2.5 Lead time & LOS distributions

**Purpose:** Booking window and length-of-stay shape.

| Table idea | Row dimension | Columns | Notes |
|------------|----------------|---------|--------|
| **Lead time buckets** | One row per bucket (e.g. 0, 1–7, 8–14, … days) | Count of reservations, % of total, cumulative % | Distribution of how far out guests book. |
| **LOS buckets** | One row per bucket (1, 2, 3, 4–7, 8+ nights) | Count, % of reservations, % of room-nights | Distribution of stay length. |

**Open:** Bucket boundaries — fix in config or derive (e.g. percentiles)? Same buckets for every property?

---

### 2.6 Seasonality / calendar

**Purpose:** Identify peak, shoulder, low periods.

| Table idea | Row dimension | Columns | Notes |
|------------|----------------|---------|--------|
| **Month-of-year summary** | One row per month (1–12), across all years | Avg room-nights, avg revenue, avg occupancy, avg ADR (over years) | “Typical” January, February, etc. |
| **Heatmap-ready table** | Year × month (or week) | Occupancy %, RevPAR, or ADR | For visualization. |

**Open:** “Across all years” — do we require minimum 2 years for “typical” month, or show single year if that’s all we have?

---

### 2.7 Additional tables (optional)

| Table idea | Purpose |
|------------|--------|
| **ADR distribution** | Percentiles (P10, P25, P50, P75, P90) of ADR by segment or overall; or histogram buckets. |
| **First/last arrival date** | Min/max arrival in dataset; date range of data (for data-quality note). |
| **Excluded-row summary** | Count of rows excluded in ingestion (e.g. Paid &lt; Amount, null revenue) — from validation report, not canonical. |

---

## 3. Decisions to align on before building

*(See **§6 Decisions log** for full status: decided, configurable, open.)*

1. **Time grain for “main” overview:** → **Monthly** (decided). See §6.
2. **Reservation vs unit-stay:** When we say “reservation count,” do we mean distinct `reservation_id` (one per booking) or row count (one per unit-stay)? Multi-room = multiple rows.
3. **Occupancy scope:** → **Both property-level and by unit** (decided). See §6.
4. **Revenue definition:** → **Room revenue only** (decided). See §6.
5. **Day-of-week:** Arrival-based vs stay-night-based; definition of weekend.
6. **Buckets:** → **Configurable** per property/data. See §6.
7. **Segment filters:** Exclude or flag very small segments (e.g. channel with &lt; 10 reservations)?
8. **Date range:** → **Prefer last 24 months**; configurable. See §6.
9. **Output format:** CSV per table, single Excel with sheets, JSON, or all of the above?

---

## 4. Suggested order of tables (for narrative flow)

A possible order for the analyst’s read-through:

1. **Overall summary** — headline stats and date range.
2. **Monthly (or quarterly) overview** — performance over time; seasonality.
3. **Channel summary** — mix and ADR by channel.
4. **Unit/room-type summary** — which units perform.
5. **Day-of-week summary** — weekend vs midweek.
6. **Lead time distribution** — booking window.
7. **LOS distribution** — length of stay.
8. **Channel × time** (optional) — how channel mix evolves.
9. **Month-of-year (typical)** (optional) — average January, February, etc.

---

## 5. What we’re not covering (without more data)

- **Cancellation / no-show rate** — no status in export.
- **Pace / booking curve by segment** — possible but needs clear definition of reference period and segments.
- **Competitive set or market benchmarks** — would need external data.
- **Forecast or targets** — out of scope for descriptive onboarding EDA.

---

## 6. Decisions log (as of brainstorm)

Record of choices and open points. Use this when implementing.

### Decided

| Decision | Choice | Implementation note |
|----------|--------|----------------------|
| **Time grain** | Monthly | Main overview and trend tables use month (e.g. `arrival_year_month`). |
| **Occupancy** | Both property-level and by unit/room type | Property: room_count from config. By unit: available room-nights = 1 per unit × nights in period (each unit has 1 room). |
| **Revenue** | Always room revenue only | Use `revenue` (canonical); do not use `amount_paid` for RevPAR/ADR metrics. |

### Variable / configurable

| Decision | Choice | Implementation note |
|----------|--------|----------------------|
| **Lead time / LOS buckets** | Depends on property data | Make bucket boundaries **configurable** (e.g. in property config or analysis config). Option: derive from data (percentiles) as fallback. |
| **Date range** | Two full calendar years | **Finalised.** Use current_year - 2 and current_year - 1 (Jan–Dec each). If data has fewer than two full years, use available full years and add a note. See **docs/07-analysis-tables-and-formulas.md** §1 and §12. |

### Open (options for later)

| Decision | Status | Options to choose from |
|----------|--------|-------------------------|
| **“Reservation” count** | Not sure | **(A)** Row count = unit-stays (each row one unit-stay; multi-room = multiple rows). **(B)** Distinct `reservation_id` = bookings (one per reservation). Recommendation: report **both** (e.g. “unit-stays” and “reservations”) so the analyst sees volume both ways. |
| **Day of week** | Not sure | **(A)** Arrival-based: “room-nights that *arrived* on Monday.” **(B)** Stay-night-based: “room-nights that *fell on* Monday” (requires expanding each stay to nights). **(C)** Weekend = Fri–Sun vs Sat–Sun. Recommendation: start with **arrival-based** (simpler); add stay-night view later if needed. Weekend = configurable (e.g. Fri–Sun default). |
| **Small segments** | Not sure | **(A)** Exclude segments below a threshold (e.g. &lt; 10 reservations). **(B)** Include all; flag or footnote “low volume.” **(C)** Include all with no flag. Recommendation: **include all, flag low volume** (e.g. “&lt; 10 reservations”) so nothing is hidden. |
| **Output format** | Unsure | **(A)** One CSV per summary table (e.g. `by_channel.csv`, `by_month.csv`). **(B)** Single Excel file with one sheet per table. **(C)** JSON (nested or one file per table). **(D)** Combination (e.g. CSV + optional Excel). Recommendation: **CSV per table** as default (simple, scriptable); add Excel export as optional later if needed. |

---

## 7. Context note (LaFave-specific vs general)

Many of the **examples and data comments** in this doc come from the initial LaFave Zion / ResNexus export (e.g. “no cancellation column here”). Those are **export-specific**.

What is **general and reusable for any property/PMS**:

- The list of **metrics** (occupancy, RevPAR, ADR, lead time, LOS, channel mix, unit performance, day-of-week, etc.).
- The **table shapes** (monthly overview, by channel, by unit, by day-of-week, booking-window distribution, etc.).
- The idea of using a **canonical schema + property config** (room_count, units) to drive all calculations.

What is **LaFave-specific**:

- Comments about missing fields in this export (e.g. no cancellation status).
- Specific room counts, unit names, and ad-hoc business rules derived from this file.

The analysis design should stay **flexible** so that when another PMS export includes additional fields (e.g. cancellation), we can extend the metrics and tables without redoing the overall structure.

---

## 8. Finalised for implementation

- **Two full years:** Use **(current year - 2)** and **(current year - 1)** (e.g. Mar 2026 → 2024 and 2025). If data doesn't cover both years, use available full years and add a note. See **docs/07-analysis-tables-and-formulas.md** §1.
- **Exact tables and formulas:** Defined in **docs/07-analysis-tables-and-formulas.md** (table schemas, columns, and metric formulas).
- **Open options (can defer):** Reservation count, day-of-week definition, small segments, output format — use defaults in v1; refine when you decide.


---

## 9. Next step

- Implement the analysis step per **docs/07-analysis-tables-and-formulas.md**.
