# Onboarding EDA Tool

Automated, repeatable exploratory analysis for new hospitality properties using historical booking data from any PMS (e.g. ResNexus, Mews, Cloudbeds).  
This repo contains:

- A **config-driven ingestion + validation pipeline** (maps arbitrary PMS exports into a canonical schema).
- A **monthly and seasonal analysis engine** (CSV outputs).
- A **Streamlit dashboard** for interactive review.

The first fully wired example is **LaFave Zion** (`property_id = lafave_zion`), but the design is property- and PMS-agnostic.

---

## 1. Project structure

```text
Onboarding_EDA/
├── config/
│   ├── pms/                       # PMS/source format mappings (per PMS)
│   │   └── resnexus_mapping.yaml
│   └── properties/                # Property inventory (per property)
│       └── lafave_zion.yaml
├── data/
│   └── LAFAVE ZION/               # Example raw export for LaFave Zion
│       └── Lafave_data.csv
├── docs/                          # Design & methodology
├── src/                           # Core Python package
│   ├── parser.py                  # Ingestion + canonical mapping
│   ├── validation.py              # Validation & report
│   ├── analysis.py                # Analysis tables & scoring
│   └── property_config.py         # Property config loader
├── scripts/
│   ├── run_ingestion.py           # Entry point: ingestion + validation
│   ├── run_analysis.py            # Entry point: analysis from canonical CSV
│   ├── run_pricing_matrix.py      # Entry point: build draft pricing matrix from analysis
│   ├── run_pricing_sheet.py       # Entry point: build daily pricing sheet from matrix + events
│   └── run_dashboard.py           # Streamlit dashboard (frontend)
├── output/
│   └── <property_id>/             # Per-property outputs (see below)
├── requirements.txt
└── README.md
```

Per-property outputs are written to:

```text
output/
  <property_id>/
    ingestion/
      canonical.csv
      validation_report.json
    analysis/                      # Step 1: analysis & scores (see docs/07)
      overall_summary.csv
      monthly_performance.csv
      monthly_performance_combined.csv
      listing_season_performance.csv
      channel_by_year.csv
      channel_summary.csv
      channel_by_listing.csv
      by_day_of_week.csv
      booking_window.csv
      adr_by_listing_by_month.csv
    pricing/                       # Step 2: draft pricing matrix (see docs/08)
      pricing_matrix_draft.csv
```

For LaFave Zion, this is `output/lafave_zion/...`.

---

## 2. Setup

From the project root:

```bash
python -m venv .venv
source .venv/bin/activate          # macOS / Linux
# .venv\Scripts\activate           # Windows (PowerShell / CMD)

pip install -r requirements.txt
```

You should now have `pandas`, `PyYAML`, `streamlit`, and `plotly` installed in the virtualenv.

---

## 3. Ingestion & validation

For LaFave Zion (example):

```bash
source .venv/bin/activate

python scripts/run_ingestion.py \
  --property lafave_zion \
  "data/LAFAVE ZION/Lafave_data.csv"
```

If you don’t pass output paths, ingestion will default to:

- Canonical CSV: `output/lafave_zion/ingestion/canonical.csv`
- Validation report: `output/lafave_zion/ingestion/validation_report.json`

You can override these with:

```bash
python scripts/run_ingestion.py \
  --property lafave_zion \
  "data/LAFAVE ZION/Lafave_data.csv" \
  --output-canonical output/lafave_zion/ingestion/canonical.csv \
  --output-report output/lafave_zion/ingestion/validation_report.json
```

See `docs/01-data-ingestion-and-validation-design.md` and  
`docs/02-lafave-source-schema-and-methodology.md` for the ingestion & validation design.

---

## 3a. Google Sheets data source (replaces the manual CSV download)

`lafave_zion`, `flohom`, `wmb`, `fbg`, `atx`, and `spoon_mountain` can pull
straight from the same master **"Dashboard revenue reporting"** Google
Spreadsheet, instead of someone downloading a tab as CSV by hand and dropping
it into `data/<PROPERTY>/`. Ingestion itself (`src/parser.py`) is unchanged;
`src/sheets_sync.py` fetches the tab(s) and writes a CSV cache that parser.py
consumes exactly like a manually-downloaded export. `lafave_zion`/`flohom`/
`wmb`/`fbg`/`atx` are also synced by Historical Snapshot from this
spreadsheet; `spoon_mountain` is Onboarding-EDA-only (Historical Snapshot
doesn't track that property), but its tab lives in the same spreadsheet.

For `flohom` and `atx`, the live tab already carries every column the parser
needs (raw pass-through, no changes). For `lafave_zion`, `fbg`, `wmb`, and
`spoon_mountain`, the live tab is narrower than what analysts used to
download by hand (a raw ResNexus/Cloudbeds/OwnerRez export, not a
pre-joined dashboard view) — `src/sheets_sync.py` shapes those into what the
existing parser already expects, ported from Historical Snapshot's own
working logic for the same properties. See "Per-property shaping" below.

### One-time setup

If Historical Snapshot is already configured on this machine, reuse the same
service account and spreadsheet — no new credentials needed. Otherwise:

1. Create a Google Cloud service account with read access to the spreadsheet
   (see Historical Snapshot's README for the full walkthrough).
2. Set the same two environment variables Historical Snapshot uses:

```bash
export GOOGLE_SHEETS_SPREADSHEET_ID="your-spreadsheet-id"
export GOOGLE_APPLICATION_CREDENTIALS="$HOME/.config/historical-snapshot/sheets-sa.json"
```

3. `pip install -r requirements.txt` (adds `gspread` + `google-auth`).

### Sync and ingest

```bash
# Sync one property's tab(s) into data/.cache/sheets/
python scripts/sync_sheets.py --property lafave_zion

# Sync everything wired to the sheet
python scripts/sync_sheets.py --all

# Ingest straight from the sheet (syncs automatically if not already synced today)
python scripts/run_ingestion.py --property lafave_zion --from-sheets
```

Add `--force-sync` to either command to refresh even if already synced today.
The manual CSV path still works exactly as before (`--csv`-style positional
path) — `--from-sheets` is additive, not a replacement requirement.

Which tab(s) a property reads is declared in `config/properties/<id>.yaml`:

```yaml
data_source:
  type: google_sheets
  tab: "Lafave_data"        # or `tabs: [{tab: "..."}, {tab: "..."}]` for multi-source properties like atx
```

### Per-property shaping

`lafave_zion`, `fbg`, `wmb`, and `spoon_mountain`'s live tabs don't match
what `src/parser.py` expects as raw pass-through, so `sync_property()` runs
each through a small shaping step (`PROPERTY_SHAPERS` in
`src/sheets_sync.py`) before writing the cache file — never touching
`src/parser.py` itself:

- **`lafave_zion`** — the live `Lafave_data` tab is the raw 11-column
  ResNexus export (no Listing Name or Channel). `config/properties/
  lafave_zion.yaml` declares `Lafave_data`, `Lafave_data2` (Res# → Unit),
  and `Lafave_OTA_data` (Reservation → Channel Name) as its three tabs; the
  shaper joins them into one output, derives Channel with the same
  By Phone / Booked Online → Direct, 3rd Party → OTA-name-or-Other logic,
  and adds Grouping via the same listing-name lookup Historical Snapshot
  uses (`_lafave_grouping`). Ingestion writes this to canonical as
  `listing_group`.
- **`fbg` / `wmb`** — the live `FBG_data` / `WMB_data` tabs are narrow
  Cloudbeds dashboard exports with no Revenue column. The shaper computes
  Revenue as `Accommodation Total + $35 × Nights` (the resort fee,
  Historical Snapshot's own formula), waived for Airbnb bookings only on
  `fbg` — `wmb` never waives it. It also fills in Listing Name (from Room
  Type) and blank Name/Property columns, which the tab genuinely doesn't
  have and which the Cloudbeds parser otherwise mishandles when totally
  absent.
- **`spoon_mountain`** — the live `SpoonMount_data` tab otherwise matches
  the OwnerRez parser's required columns; only Arrival/Departure/Booked come
  back as ISO (`YYYY-MM-DD`) dates instead of the `M/D/YYYY` the parser
  expects. The shaper reformats just those three columns.

A property with no registered shaper (flohom, atx) is unaffected — its
tab(s) are written to cache exactly as fetched, same as before this layer
existed.

`fbg` and `atx` are hybrids, matching how Historical Snapshot treats onera
and atx: the live tab syncs automatically, but pre-live-tab history is a
fixed, manually-maintained CSV (not automated — add it to `data/` yourself).
`--from-sheets` accepts extra manual paths alongside it — they're added to
the synced source(s), not replaced:

```bash
python scripts/run_ingestion.py --property fbg --from-sheets "data/FBG/Historical FBG (until 12_31_24).csv"
python scripts/run_ingestion.py --property atx --from-sheets "data/ATX/ATX_track_data.csv"
```

### Offboarded properties

`sos` (Spirit Of Sofia) is no longer an Oasi property. Its config
(`config/properties/sos.yaml`) is commented out rather than deleted —
`run_ingestion.py --property sos` fails cleanly ("no pms_id in config")
instead of silently running with stale data. Uncomment that file to
reactivate; nothing else needed to change elsewhere.

---

## 4. Analysis (CSV outputs – Step 1)

Once you have a canonical CSV for a property:

```bash
source .venv/bin/activate

python scripts/run_analysis.py --property lafave_zion
```

By default this reads:

- `output/lafave_zion/ingestion/canonical.csv`

and writes analysis tables to:

- `output/lafave_zion/analysis/*.csv`

Key tables include:

- `overall_summary.csv` – listing + property-level revenue, ADR, occupancy, RevPAR, YoY.
- `monthly_performance.csv` – 24 rows (2 years × 12 months) with revenue, ADR, occupancy, RevPAR, and a 1–10 **month score**.
- `monthly_performance_combined.csv` – 12 rows (Jan–Dec) with **average** per-month metrics across the two years and the same 1–10 score.
- `by_day_of_week.csv` – ADR, revenue share, check-in share, and a 1–10 **day-of-week score**.
- `booking_window.csv` – lead-time bands (0–7, 8–14, …, 365+ days) with revenue & share.
- `adr_by_listing_by_month.csv` – ADR per listing × year_month, plus `min_adr` / `max_adr` per month.
- `listing_season_performance.csv` – listing performance in **High / Shoulder / Low** seasons with a 1–10 **season score**.

Full table schemas and formulas are documented in `docs/07-analysis-tables-and-formulas.md`.
Daily tiering methodology (gap detection + quantile fallback, diagnostics, and overrides) is documented in `docs/09-daily-tiering-methodology.md`.

---

## 5. Draft pricing matrix (Step 2a)

Once analysis is done, you can build a **draft pricing matrix** for a property:

```bash
source .venv/bin/activate

PYTHONPATH=. python scripts/run_pricing_matrix.py --property lafave_zion
```

This reads the analysis tables from:

- `output/lafave_zion/analysis/`

and writes a draft rate matrix to:

- `output/lafave_zion/pricing/pricing_matrix_draft.csv`

The matrix has one row per `listing × calendar month` and one column per day of week (Mon–Sun), containing the suggested ADRs.  
The logic for how these rates are derived (anchors, scores, hierarchies, and bounds) is documented in `docs/08-pricing-matrix-and-draft-rates.md`.

---

## 6. Daily pricing sheet (Step 2b)

Once you have a pricing matrix for a property, you can generate a **daily pricing sheet** over a configurable date range:

```bash
source .venv/bin/activate

PYTHONPATH=. python scripts/run_pricing_sheet.py \
  --property lafave_zion \
  --start-date 2026-03-01 \
  --end-date 2026-12-31
```

This:

- Reads the matrix from `output/<property_id>/pricing/pricing_matrix_draft.csv`.
- Reads property‑specific events (with multipliers) from `config/properties/<property_id>.yaml` (see `pricing.events_YYYY`).
- Builds one row per calendar date with:
  - `date` and `day_of_week`
  - one column per listing (final ADR for that date)
  - `notes` describing any event/holiday that applies.

The sheet is written to:

- `output/<property_id>/pricing/pricing_sheet_<start>_<end>.csv`

Analysts can then review and tweak this sheet before loading rates into the PMS/RMS.

---

## 7. Dashboard (Streamlit)

To explore the outputs visually:

```bash
source .venv/bin/activate

streamlit run scripts/run_dashboard.py
```

By default the dashboard points at:

- `output/lafave_zion/analysis/`

You can change the analysis folder in the **sidebar** (e.g. to another property’s `output/<property_id>/analysis`).

The dashboard includes:

- **Overview KPIs** (property totals, YoY).
- **Overall summary** table.
- **Monthly performance** (per year-month + combined month-of-year view, with 1–10 scores).
- **Channel** views.
- **Day-of-week** performance (with 1–10 score).
- **Booking window** distribution.
- **Listing ADR by month**.
- **Listing seasonality** (per listing, per season with 1–10 score).

---

## 8. Adding a new property / PMS

High-level steps:

1. Create a property config in `config/properties/<property_id>.yaml`:
   - `property_id`, `property_name`
   - `pms_id` (e.g. `resnexus`)
   - `room_count`, optional `room_types` / `unit_ids`
2. Add or reuse a PMS mapping in `config/pms/<pms_id>_mapping.yaml`.
3. Run ingestion:
   ```bash
   python scripts/run_ingestion.py --property <property_id> <path_to_csv>
   ```
4. Run analysis:
   ```bash
   python scripts/run_analysis.py --property <property_id>
   ```
5. Point the dashboard sidebar at `output/<property_id>/analysis`.

In most cases you only need to add/adjust configs; the ingestion, validation, analysis, and dashboard logic stay the same.
For adding a brand-new PMS parser, use `docs/10-pms-parser-template.md`.

### Current PMS parser capabilities

| PMS (`pms_id`) | Ingestion parser available | Property-specific hook support | Notes |
|---|---|---|---|
| `resnexus` | Yes | Yes (via `property_id` in parser path) | Current default mapping is generic; no active property-specific branch required. |
| `hostaway` | Yes | Yes (via `property_id` in parser path) | Includes a FLOHOM-specific listing-name normalization branch only when `property_id = flohom`. |

---

## 9. AirDNA daily market context (extension)

To build listing-day market context from AirDNA submarket files (without changing tiering):

```bash
source .venv/bin/activate

python scripts/run_daily_market_context.py --property flohom
```

This script:

- Reads listing-day property performance from `output/<property_id>/ingestion/canonical.csv` (analysis-period filtered).
- Enriches each row with the property-day tier context from `output/<property_id>/analysis/daily_tier_calendar.csv`.
- Joins AirDNA monthly submarket benchmarks from `data/airdna/<property_id>/...`.
- Applies submarket reliability metadata from `config/properties/<property_id>.yaml` (`airdna.submarket_pulls`).

Output:

- `output/<property_id>/benchmark/daily_market_context.csv`

Key columns include:

- `date`, `unit_id`
- `tier_id`, `tier_label` (plus base tier columns)
- `submarket`, `market_reliability`, `benchmark_warning`
- `property_revpar`, `market_revpar_monthly`, `rpi`
- `market_condition` (submarket-relative terciles)
- optional context fields: `bedroom_revenue_benchmark`, `percentile_band`

