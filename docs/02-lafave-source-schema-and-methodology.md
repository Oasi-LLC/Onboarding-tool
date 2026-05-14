# LaFave Zion CSV: Source Schema & Onboarding Methodology

This document has **two layers**:

- **LaFave-specific example** (ResNexus export for LaFave Zion) — concrete column names, quirks, and mapping rules for this one property/PMS.
- **General methodology** — the pattern that applies to **any** property/PMS: map arbitrary exports into a **canonical schema**, then run **validation** on the normalized data.

We use the **real** LaFave export (ResNexus PMS) as **one example** of a source format. It defines (1) the source schema we receive *for this format*, (2) how we map it into the canonical analysis-ready format, and (3) validation rules that apply to every row and field once normalized.

**Important (general rule):** Do **not** assume every property we onboard will have this format or even a single CSV. Input can differ widely: different PMS, different number of files (one CSV, multiple CSVs, Excel, etc.), different column names and semantics. The **methodology** is the same everywhere: a **canonical schema** that all analyses use, plus a **per-source mapping** that turns whatever we receive into that schema, plus **validation** on the normalized data. This file is simply the first concrete format we support.

**Reference file (current pipeline):** `data/LAFAVE ZION/Lafave_data.csv` (ResNexus / LaFave **sheet** layout: `Arrival`, `Departure`, `Listing Name`, `Reservation Date`, `# Nights`, `Channel`, optional `Grouping`).  
**Legacy layout (still supported):** combined stay column `Date` plus `Unit` and `Reserved On` (see `config/pms/resnexus_mapping.yaml` and `resnexus_raw_format` in `src/parser.py`).  
**PMS:** ResNexus  
**Rows (Lafave_data.csv):** thousands of reservation rows + 1 header (counts change as the export is refreshed).  
**Date range:** depends on export refresh (arrivals from early 2023 through latest in file).

---

## 1. Source schema (what we actually get)

### 1.1 Column definitions

| Column name        | Sample values | Meaning / notes |
|--------------------|---------------|-----------------|
| **Res#**           | "130317", "124130" | Reservation ID. Can repeat: same Res# appears on multiple rows when one stay has multiple units (multi-room) or when export is one row per night. |
| **Date**           | "1/1/2023" or "1/1-1/3/2023" | Stay date(s). Either **single night** (M/D/YYYY) or **date range** (M/D-M/D/YYYY). Arrival and departure can be deduced from this column. ~6% single date, ~94% range in this file. |
| **Guest**          | "Amora Sun", "Chloe Grant" | Guest name. Same guest can appear on multiple rows (multi-room or multi-stay). |
| **Unit**           | "200 LaFave South: Angels Landing " | Room/unit identifier. Trailing spaces possible. |
| **Amount**         | "$1,075.00", "$2,195.00" | **Room rate only** for that unit, for all nights in the reservation (no cleaning, no taxes). When Date is single night, Amount = that night’s rate; when Date is a range, Amount = total room rate for the stay for that unit. |
| **Paid**           | "$4,706.65", "--", "$0.00" | **Total amount for the reservation** (including cleaning fees and taxes). "--" = missing/not applicable; "$0.00" = unpaid. **Business rule:** If Paid < Amount, do not count that reservation in analysis (incomplete payment or should be excluded). |
| **Booked Online**  | "X" or "" | Channel: use column header as channel name. |
| **GDS**            | "X" or "" | Channel: use column header as channel name. |
| **Trip Connect**   | "X" or "" | Channel: use column header as channel name. |
| **MyAllocator**    | "X" or "" | Channel: use column header as channel name. |
| **Direct Connect**  | "X" or "" | Channel: use column header as channel name. |
| **Reserved On**    | "12/30/2022" | Booking date (M/D/YYYY). Used for lead time. |

There is **no explicit cancellation or status column** in this export. Cancelled stays might be absent from the file or identifiable only by other means (e.g. amount zero, or external list).

### 1.2 Structural quirks

- **Grain:** Each row = one **unit-night** or one **unit-stay**:
  - **Single-date rows:** one row = one unit for one night; Amount = that night’s charge.
  - **Date-range rows:** one row = one unit for a contiguous stay; Amount = total for that stay; we must derive nights from the range and, for ADR, divide Amount by nights.
- **Multi-room reservations:** Same Res# + same Guest + same date range can appear on multiple rows with different Units. Each row is one unit in that reservation. See **§1.3 Example: multi-room reservation** below.
- **Channel:** Use the **column headers** as the channel values (no extra mapping). So channel is one of: "Booked Online", "GDS", "Trip Connect", "MyAllocator", "Direct Connect". If all five are empty for a row, use "Other". The data doesn't give more granular source info than that.
- **Missing/Paid:** "--" in Paid is the PMS way of “no value”; "$0.00" is explicit zero (e.g. comp, test, or unpaid). Rows where Paid < Amount should be excluded from analysis.

### 1.3 Example: multi-room reservation

One reservation can have multiple rows (one per unit). Example from the file:

**Reservation:** Same guest, same dates, **three units** (three separate rows):

| Res#   | Date          | Guest      | Unit                                      | Amount     | Paid      | … | Reserved On |
|--------|---------------|------------|-------------------------------------------|------------|-----------|---|-------------|
| 127265 | 1/13-1/15/2023 | Halee Smith | 302 LaFave South: The Gallery House       | $2,535.00  | $2,734.00 | … | 11/24/2022  |
| 127290 | 1/13-1/15/2023 | Halee Smith | 206 LaFave South: Mountain of the Sun     | $1,035.00  | $1,180.00 | … | 11/24/2022  |
| 128862 | 1/13-1/15/2023 | Halee Smith | 208 LaFave South: Northgate Peaks        | $975.00    | $1,120.00 | … | 12/11/2022  |

So one booking (Halee Smith, Jan 13–15, 2023) has **three rooms** = three rows. Each row has its own Unit, Amount (room rate for that unit for those nights), and Paid. For analysis we keep each row separate; aggregation by reservation_id is done when needed.

---

## 2. Canonical schema (analysis-ready)

The tool should normalize every source into this internal schema. All downstream analysis and validation use only these fields.

### 2.1 Required fields (must exist after mapping)

| Canonical field   | Type   | Description | Notes |
|-------------------|--------|-------------|--------|
| `reservation_id`  | string | Unique or group identifier for the reservation | From Res#. |
| `arrival_date`     | date   | First night of stay | Parsed from Date (start of range or single date). |
| `departure_date`   | date   | Day after last night | Parsed from Date (end of range + 1, or single date + 1). |
| `unit_id`          | string | Room/unit identifier | From Unit; trim whitespace. |
| `revenue`          | float  | Room rate for this row (unit only, all nights) | From Amount; strip $ and commas; must be ≥ 0. |
| `booking_date`     | date   | When the reservation was made | From Reserved On. |
| `channel`          | string | Booking channel | Use **column header** as value: "Booked Online", "GDS", "Trip Connect", "MyAllocator", "Direct Connect"; if no "X" in any, use "Other". |

### 2.2 Optional but recommended

| Canonical field   | Type   | Description | Notes |
|-------------------|--------|-------------|--------|
| `guest_name`      | string | Guest name | From Guest. |
| `amount_paid`      | float  | Total paid for reservation (incl. cleaning, taxes) | From Paid; "--" → null, "$0.00" → 0. **Exclude from analysis** when amount_paid < revenue (or when amount_paid is null and we cannot verify). |
| `currency`        | string | e.g. "USD" | Default if not in file. |

### 2.3 Derived after normalization (computed by tool)

| Field        | Type  | Description |
|--------------|-------|-------------|
| `nights`     | int   | departure_date − arrival_date (number of nights). |
| `lead_time_days` | int | arrival_date − booking_date (in days). |
| `adr`        | float | revenue / nights (for this row). |

These are not in the source; they are computed once we have arrival, departure, booking_date, and revenue.

---

## 3. Mapping: LaFave CSV → canonical

| Source (LaFave)   | Canonical field   | Transform |
|-------------------|-------------------|-----------|
| Res#              | reservation_id    | As string (preserve leading zeros if any). |
| Date              | arrival_date, departure_date | Parse: if "M/D-M/D/YYYY" → start and end; if "M/D/YYYY" → arrival = that date, departure = that date + 1 day. |
| Unit              | unit_id           | Trim. |
| Amount            | revenue           | Strip "$", ","; parse as float. |
| Reserved On       | booking_date      | Parse M/D/YYYY. |
| Booked Online … Direct Connect | channel | Use the **column header** of the column that has "X" as the channel value (e.g. "Booked Online", "Direct Connect"); if none have "X", use "Other". |
| Guest             | guest_name        | As string. |
| Paid              | amount_paid       | "--" → null; "$0.00" → 0; else strip $ and commas, parse float. **After mapping:** exclude rows where amount_paid < revenue (do not count that reservation in analysis). |

**Date parsing rules:**

- Single date: `"1/1/2023"` → arrival_date = 2023-01-01, departure_date = 2023-01-02, nights = 1.
- Range: `"1/1-1/3/2023"` → arrival_date = 2023-01-01, last night = 2023-01-03, departure_date = 2023-01-04, nights = 3.

**ADR:** For each row, `adr = revenue / nights` (only for rows with nights > 0).

---

## 4. Validation rules (thorough check)

Every row and every field that feeds the canonical schema should be checked as below. The tool should produce a **validation report** with pass/fail per rule, counts of violations, and (where useful) sample bad rows.

### 4.1 Column-level (before mapping)

| Check | Rule | Severity |
|-------|------|----------|
| Required columns present | Res#, Date, Guest, Unit, Amount, Reserved On, and at least one channel column exist. | **Error** (block ingestion) |
| No duplicate column names | After mapping, no duplicate canonical field. | **Error** |
| Encoding / delimiter | File is readable (UTF-8 or declared encoding); delimiter consistent (comma for CSV). | **Error** if unreadable |

### 4.2 Cell-level (per value)

| Check | Rule | Severity |
|-------|------|----------|
| Res# not empty | Every row has non-empty Res#. | **Error** |
| Date parseable | Date parses as either single date or range; dates within reasonable range (e.g. 2015–2030). | **Error** if unparseable; **Warning** if outside range |
| Departure > arrival | For range: end date ≥ start date; derived departure_date > arrival_date. | **Error** |
| Guest | Can be empty (optional in canonical). | — |
| Unit not empty | Unit present and non-empty after trim. | **Error** |
| Amount parseable and ≥ 0 | Amount strips to valid number ≥ 0. Reject "$-100" or non-numeric. | **Error** |
| Paid | "--", "", "$0.00", or valid positive number. If present and not "--"/"", must parse. | **Warning** if unexpected value |
| Paid ≥ Amount (to count) | For analysis: only include rows where amount_paid ≥ revenue (and amount_paid is not null). Rows with Paid < Amount or missing Paid are excluded from counts. | **Business rule** (filter before analysis) |
| Reserved On parseable | M/D/YYYY, and date in reasonable range. | **Error** if unparseable |
| Booking ≤ arrival | booking_date ≤ arrival_date (lead time ≥ 0). | **Error** (or **Warning** for data quirks) |
| Channel | At least one of the five columns present; allow all blank → "other". | — |

### 4.3 Row-level (integrity)

| Check | Rule | Severity |
|-------|------|----------|
| Duplicate key | Define key: (reservation_id, unit_id, arrival_date). If same key appears twice, flag (could be duplicate export row). | **Warning** |
| Nights consistency | Derived nights = (departure_date − arrival_date); must be ≥ 1. | **Error** if 0 or negative |
| ADR sanity | adr = revenue / nights. Flag if adr is 0 or extremely high (e.g. > $50,000) for outlier review. | **Warning** |

### 4.4 Dataset-level (fitness for analysis)

| Check | Rule | Severity |
|-------|------|----------|
| Date range | Report min/max arrival_date; warn if < 12 months of data for seasonality. | **Warning** |
| Row count | Report total rows, rows with errors, rows with warnings. | Info |
| Revenue coverage | Count rows with revenue = 0; flag if large share (e.g. >10%). | **Warning** |
| Channel mix | Count "other"; flag if majority. | **Warning** |
| Null amount_paid | Count null/"--"; report for analyst. | Info |

### 4.5 Validation report contents

- **Summary:** Pass/fail per check; total rows; rows with at least one error; rows with only warnings.
- **Per-check:** Count of violations; optional sample of failing rows (e.g. first 10 per check).
- **Decision:** If any **Error** severity check fails → do not run analysis; return report. If only **Warning**/Info → analyst can choose to proceed with report attached.

---

## 5. Handling this file’s specific quirks (ResNexus)

- **Single vs range dates:** Parser must support both formats; derive nights and ADR accordingly. Arrival/departure are deduced from the Date column.
- **Amount = room rate only** for that unit, all nights. **Paid = total for the reservation** (cleaning, taxes included). If Paid < Amount, do not count that reservation in analysis.
- **Multi-unit same Res#:** Keep as separate rows (one row per unit-stay); aggregation by reservation_id is done in analysis if needed.
- **Channels:** Use the column headers as channel values (Booked Online, GDS, Trip Connect, MyAllocator, Direct Connect); no extra granularity in this export.
- **"--" in Paid:** Map to null; exclude from analysis when we require Paid ≥ Amount, or flag for analyst review.
- **"$0.00" in Paid:** Map to 0; if Amount > 0 then Paid < Amount → exclude from analysis.
- **Test / comp:** Rows like "Test Reservation" with $0 Paid can be kept or flagged; consider optional filter (e.g. exclude guest name "Test" or revenue 0) as config.
- **No cancellation flag:** This source does not provide cancellation status; cancellation analysis would require another export or API.

---

## 6. Next steps (implementation order)

1. **Implement parser** for this CSV: read header, apply LaFave mapping, output canonical rows (with derived nights, lead_time_days, adr).
2. **Implement validators** for each rule in §4; output validation report (e.g. JSON + human-readable summary).
3. **Run on** `LAFAVE_1:1:23-1:1:27.csv`: fix any mapping/parsing issues, then lock mapping as "LaFave / ResNexus-style" template.
4. **Document** this file as the first supported format; add a second PMS later by adding another mapping config and reusing the same canonical schema and validation.

This gives you a concrete methodology: one CSV per property, one mapping per source format, one canonical schema and one validation suite for all.
