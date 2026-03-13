# Directory structure

All paths are relative to the **project root**. Run commands from the project root.

```
Onboarding_EDA/
├── config/
│   ├── pms/                          # PMS/source format mappings (one per PMS)
│   │   └── resnexus_mapping.yaml
│   └── properties/                   # Property inventory (one per property)
│       └── lafave_zion.yaml
├── data/                             # Raw input data (e.g. client exports)
│   └── LAFAVE ZION/
│       └── Jan23-Jan27.csv
├── docs/                             # Design and methodology docs
│   ├── 01-data-ingestion-and-validation-design.md
│   ├── 02-lafave-source-schema-and-methodology.md
│   ├── 03-canonical-columns-and-analysis-readiness.md
│   ├── 04-config-structure.md
│   └── 05-directory-structure.md
├── output/
│   └── <property_id>/                # Per-property outputs
│       ├── ingestion/                # Ingestion pipeline outputs for that property
│       │   ├── canonical.csv
│       │   └── validation_report.json
│       └── analysis/                 # Analysis outputs for that property (see docs/07)
│           ├── overall_summary.csv
│           ├── monthly_performance.csv
│           ├── monthly_performance_combined.csv
│           ├── listing_season_performance.csv
│           ├── channel_by_year.csv
│           ├── channel_summary.csv
│           ├── channel_by_listing.csv
│           ├── by_day_of_week.csv
│           ├── booking_window.csv
│           └── adr_by_listing_by_month.csv
├── scripts/                          # Entry-point scripts (run from project root)
│   ├── run_ingestion.py
│   ├── run_analysis.py
│   └── run_dashboard.py              # Streamlit dashboard to view results
├── src/                              # Package: parser, validation, property_config
│   ├── __init__.py
│   ├── parser.py
│   ├── property_config.py
│   └── validation.py
├── .venv/                            # Virtual environment (optional)
├── requirements.txt
└── README.md                         # (optional) Quick start
```

## Path reference

| Purpose | Path |
|--------|------|
| Run ingestion | `python scripts/run_ingestion.py --property <id> <csv_path>` |
| Property config | `config/properties/<property_id>.yaml` |
| PMS mapping | `config/pms/<pms_id>_mapping.yaml` |
| Canonical output | `output/<property_id>/ingestion/canonical.csv` |
| Validation report | `output/<property_id>/ingestion/validation_report.json` |
| Analysis outputs | `output/<property_id>/analysis/*.csv` (see docs/07) |
| View dashboard | `streamlit run scripts/run_dashboard.py` |
| Raw data | `data/<property_or_client>/` |

## Running from project root

```bash
# Ingestion (example)
python scripts/run_ingestion.py --property lafave_zion "data/LAFAVE ZION/Jan23-Jan27.csv" --output-canonical output/lafave_zion/ingestion/canonical.csv --output-report output/lafave_zion/ingestion/validation_report.json
```
