# Canonical Columns & Analysis Readiness

This document confirms that every column needed for planned onboarding analyses is available in the canonical output (or can be derived), and notes any gaps.

---

## 1. Current canonical columns

| Column | Type | Description | Source (LaFave example) |
|--------|------|-------------|--------------------------|
| `reservation_id` | string | Reservation identifier | From Res# in LaFave export. |
| `arrival_date` | date | First night of stay | Parsed from Date column. |
| `departure_date` | date | Day after last night (exclusive) | Parsed from Date column. |
| `nights` | int | Length of stay (LOS) | departure − arrival |
| `unit_id` | string | Room/unit identifier | From Unit (trimmed). |
| `guest_name` | string | Guest name | From Guest. |
| `revenue` | float | Room rate for this row (unit, all nights) | From Amount (room revenue only). |
| `amount_paid` | float | Total paid (incl. fees/taxes) | From Paid. |
| `booking_date` | date | When reservation was made | From Reserved On. |
| `lead_time_days` | int | Days between booking and arrival | arrival − booking_date |
| `adr` | float | Average daily rate (revenue / nights) | Derived. |
| `channel` | string | Booking channel | From channel columns (e.g. \"Booked Online\", \"Direct Connect\"). |

---

## 2. Derived columns added for analysis

| Column | Type | Description | Used for |
|--------|------|-------------|----------|
| `arrival_day_of_week` | string | e.g. "Monday", "Tuesday" | Day-of-week patterns, weekend vs midweek |
| `arrival_year_month` | string | e.g. "2023-01" | Seasonality, monthly trends |

Room-nights for occupancy: use `nights` (each row = 1 unit × nights). No separate `room_nights` column.

---

## 3. Metric-by-metric checklist

| Analysis / metric | Required data | Available? | Notes |
|-------------------|----------------|------------|--------|
| **Occupancy %** | Room-nights sold by date; total room-nights available | ✓ | Room-nights sold from `nights` and date range. **Room count** from property config (e.g. `config/properties/lafave_zion.yaml`). |
| **RevPAR** | Revenue by period; available room-nights | ✓ | Revenue from canonical. **Room count** from property config for denominator. |
| **Occupancy & RevPAR trends (index/YoY)** | Same as above, by period | Partial | Same as above; room count needed for levels. |
| **Seasonality (monthly/quarterly)** | arrival_date, revenue, nights | ✓ | Aggregate by `arrival_year_month` or quarter. |
| **Booking window / lead time** | lead_time_days, booking_date, arrival_date | ✓ | Full distribution and stats. |
| **Length of stay (LOS)** | nights | ✓ | Distribution, averages, by segment. |
| **ADR by channel** | adr, channel | ✓ | By channel; also revenue by channel. |
| **ADR by room type** | adr, unit_id | ✓ | Use `unit_id` as room/unit type (or group units into types if needed). |
| **Day-of-week patterns** | arrival_date (or arrival_day_of_week) | ✓ | Derive or use added column. |
| **Channel mix over time** | channel, arrival_date | ✓ | Counts and share by period. |
| **Room-type performance** | unit_id, revenue, nights, adr | ✓ | By unit or grouped room type. |
| **Cancellation / no-show rate** | Cancelled or status flag | ✗ | Not in ResNexus export. Excluded rows (Paid < Amount) are not explicitly "cancelled"; would need different export or API. |

---

## 4. Gaps and workarounds

| Gap | Workaround |
|-----|------------|
| **Property room count** | Stored in `config/properties/<property_id>.yaml` (e.g. `lafave_zion.yaml`) with `room_count` and `room_types`. Load via `src.property_config.load_property_inventory(property_id)`. |
| **Cancellation status** | Document as not available from this export. Omit cancellation analysis or use a separate data source. |
| **Adults/children** | Not in current export. Omit or add if future PMS provides it. |

---

## 5. Summary

- **Available for analysis (general):** Seasonality, booking window/lead time, LOS, ADR by channel, ADR by room/unit, day-of-week patterns, channel mix, room-type performance, room-nights sold and revenue by date, and (with property config) occupancy % and RevPAR.
- **Property config (general):** Occupancy % and RevPAR use `room_count` from `config/properties/<property_id>.yaml` (e.g. LaFave Zion: 32 rooms, with room types listed as an example).
- **Not available from the LaFave/ResNexus export:** Cancellation/no-show rate (no status field); other PMSs may provide this.

Adding `arrival_day_of_week` and `arrival_year_month` to the canonical output ensures seasonality and day-of-week metrics can be computed without extra derivation for **any property**. For occupancy and RevPAR, always use the appropriate property config (`config/properties/<property_id>.yaml`) for `room_count` and optional `room_types`.
