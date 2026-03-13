# Data Ingestion & Validation Design

## Context

- **Reality:** Each client uses a different PMS (ResNexus, Mews, Cloudbeds, etc.) → export formats differ (column names, date formats, field semantics, file type). Do **not** assume every property will supply one CSV or the same structure; input can be one file, multiple files, different layouts, etc.
- **Current delivery:** Data is fed to the tool manually (files from client or exported by analyst). No API integration for now.
- **Requirement:** Once data is received, every row, column, and field/cell must be checked before any analysis runs.

This doc focuses on (1) how to ingest variable-format data and (2) what “thorough check” means in practice.

---

## Part 1: How to Ingest Variable-Format Data

We cannot rely on a single standard format. Two main approaches:

### Option A: Property-specific mapping (config-driven)

- **Idea:** For each PMS (or each client), we maintain a **mapping config** that says: “Their column X → our standard field Y” and “Their date format → our format.”
- **Flow:** User uploads a file → tool asks “Which PMS or template?” (or auto-detect, see below) → config is applied → data is reshaped into a **canonical schema**.
- **Pros:** Explicit, auditable, handles one-off client quirks.  
- **Cons:** New PMS = new mapping to build and maintain.

### Option B: Schema discovery + user-assisted mapping

- **Idea:** Tool reads the file and infers structure (column names, sample values, dtypes). It then asks the user to **map** each required concept (e.g. “Arrival Date”) to a column (e.g. “Check-in” or “Arrival”) and optionally to specify date/number formats.
- **Flow:** Upload file → tool shows columns and samples → user maps columns to standard fields (and saves mapping for reuse) → data is transformed to canonical schema.
- **Pros:** Works for any new PMS without pre-built configs; user teaches the tool once per format.  
- **Cons:** First-time setup per format; need a clear definition of “required” vs “optional” standard fields.

### Recommended direction: Hybrid

- **Canonical schema:** Define a single internal “analysis-ready” schema (e.g. `arrival_date`, `departure_date`, `room_type`, `adr`, `channel`, `booking_date`, `cancelled`, etc.). All analyses consume only this schema.
- **Per-PMS or per-client mapping:** Use **config files** (e.g. YAML/JSON) that describe for a given source:
  - Column name → canonical field
  - Datatype / format (date pattern, decimal separator, etc.)
  - Any transformations (e.g. “Total” = “Room Revenue” + “Tax” if that’s how the PMS exports)
- **When no config exists:** Fall back to **user-assisted mapping** (UI or CLI): show columns, let user assign to canonical fields and save as a new template for that PMS/client.
- **File format support:** Start with CSV and Excel; ensure encoding (UTF-8, etc.) and delimiter are detected or configurable.

So: we do **not** assume a standard format from clients; we assume we can **map** any reasonable export into one internal standard, either via pre-defined config or one-time user mapping.

---

## Part 2: What “Thorough Check” Means

After ingestion (and mapping into the canonical schema), every row, column, and field should be validated. Below is a structured way to think about it.

### 2.1 Column-level checks (schema / presence)

- **Required columns present:** All fields defined as “required” in the canonical schema exist after mapping.
- **No duplicate column names** (after mapping).
- **Expected data types:** Each column has a defined type (date, integer, float, string, boolean). Check that the column is parseable as that type; if not, flag.

### 2.2 Cell-level checks (per value)

- **Nulls / empties:** Count and report nulls or empty strings per column. Decide rules: e.g. “ADR null → exclude row from revenue metrics” vs “ADR null → hard fail.”
- **Ranges and domains:**
  - Dates: e.g. arrival/departure within a reasonable range (e.g. 2015–today), and **departure ≥ arrival**.
  - Numerics: ADR/revenue ≥ 0; number of rooms/adults/children non-negative; occupancy 0–100% if present.
  - Codes: e.g. channel/segment in allowed set (or flag “unknown”).
- **Formats:** Dates parse consistently; decimals use expected separator; no obvious garbage (e.g. “N/A” in a numeric field, “TBD” in date).
- **Cross-field consistency:** e.g. “Total revenue” ≈ “ADR × nights × rooms” within tolerance, if both exist; “LOS” = departure − arrival when both are present.

### 2.3 Row-level checks (integrity)

- **Duplicate reservations:** Same reservation ID (if available) or same arrival + room + guest identifier appearing more than once → flag or dedupe with rule.
- **Logical consistency:** e.g. LOS derived from dates matches LOS column if both exist; booking date ≤ arrival date (lead time ≥ 0).
- **Orphan or invalid codes:** Room type IDs that don’t match a master list (if we have one); channel values not in the expected set.

### 2.4 Dataset-level checks (fitness for analysis)

- **Date range and coverage:** Min/max arrival dates; number of months; gaps (e.g. no data for a month). Warn if “less than 12 months” for seasonality.
- **Volume:** Row count; number of rooms/nights; enough data for segment or room-type breakdowns.
- **Balance:** e.g. proportion of cancelled vs not; proportion of nulls in key fields. Flag if one segment or room type dominates (might be OK, but analyst should know).

### 2.5 Output of the “thorough check”

- **Validation report:** One document/section that lists:
  - **Pass/fail per check** (or severity: error vs warning).
  - **Counts:** rows total, rows excluded, rows with warnings; nulls per column; duplicates; out-of-range values.
  - **Sample of problematic rows** (e.g. first 10 with errors, or rows where ADR is null).
- **Decision point:** If critical checks fail (e.g. missing required column, >X% null in ADR), **do not run analysis**; return the report and ask for data fix or mapping fix. If only warnings (e.g. “few nulls in channel”), run analysis but attach the report so the analyst can interpret.

So “every field/cell checked” means: defined checks at column, cell, row, and dataset level, with a structured report and a clear go/no-go for downstream analysis.

---

## Part 3: Suggested Workflow (Ingestion + Validation Only)

```
1. RECEIVE
   • User provides file(s) (CSV/Excel) + optional property metadata.
   • Tool detects or user specifies encoding, delimiter, sheet.

2. DISCOVER (if no mapping) or APPLY (if mapping exists)
   • Discover: show columns, sample rows; user maps to canonical schema; save as template.
   • Apply: load mapping for chosen PMS/client; transform to canonical schema.

3. VALIDATE
   • Run column-level checks → then cell-level → then row-level → then dataset-level.
   • Collect all errors and warnings; generate validation report with counts and sample rows.

4. DECIDE
   • If blocking errors: stop; return report; no analysis.
   • If only warnings: optionally proceed to analysis with report attached, or stop for analyst review.

5. (Later) ANALYZE
   • Input to analysis is validated, canonical-schema data only.
```

---

## Part 4: Open Decisions (to refine before building)

1. **Canonical schema:** Exact list of required vs optional fields (and their types) for “minimum viable” onboarding analysis.
2. **Storage of mappings:** Where do we keep PMS/client mapping configs? (e.g. repo config files vs. DB vs. sidecar next to the tool.)
3. **Strict vs. lenient:** Which validations are “blocking” (must fix) vs “warning” (report and maybe continue)?
4. **Multi-file uploads:** Can one property have multiple files (e.g. reservations + room types)? If so, how do we link and validate across files?
5. **Audit trail:** Do we need to store raw file + mapping + validation result for each run (for traceability)?

---

## Summary

- **Ingestion:** Do not rely on one standard format. Use a **canonical internal schema** and **per-PMS/per-client mapping** (config or user-assisted). Support at least CSV/Excel, with encoding and delimiter handled.
- **Thorough check:** Validate at **column** (presence, type), **cell** (nulls, ranges, formats, domains), **row** (duplicates, cross-field consistency), and **dataset** (coverage, volume) levels. Produce a **validation report** with pass/fail, counts, and sample bad rows, and a clear **go/no-go** before any analysis runs.

Next step could be to **define the canonical schema** (required + optional fields and types) and a **first version of the validation checklist** (concrete rules and severity) so ingestion and validation can be specified in detail before implementation.
