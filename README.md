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
│       └── Jan23-Jan27.csv
├── docs/                          # Design & methodology
├── src/                           # Core Python package
│   ├── parser.py                  # Ingestion + canonical mapping
│   ├── validation.py              # Validation & report
│   ├── analysis.py                # Analysis tables & scoring
│   └── property_config.py         # Property config loader
├── scripts/
│   ├── run_ingestion.py           # Entry point: ingestion + validation
│   ├── run_analysis.py            # Entry point: analysis from canonical CSV
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
    analysis/
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
  "data/LAFAVE ZION/Jan23-Jan27.csv"
```

If you don’t pass output paths, ingestion will default to:

- Canonical CSV: `output/lafave_zion/ingestion/canonical.csv`
- Validation report: `output/lafave_zion/ingestion/validation_report.json`

You can override these with:

```bash
python scripts/run_ingestion.py \
  --property lafave_zion \
  "data/LAFAVE ZION/Jan23-Jan27.csv" \
  --output-canonical output/lafave_zion/ingestion/canonical.csv \
  --output-report output/lafave_zion/ingestion/validation_report.json
```

See `docs/01-data-ingestion-and-validation-design.md` and  
`docs/02-lafave-source-schema-and-methodology.md` for the ingestion & validation design.

---

## 4. Analysis (CSV outputs)

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

---

## 5. Dashboard (Streamlit)

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

## 6. Adding a new property / PMS

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

