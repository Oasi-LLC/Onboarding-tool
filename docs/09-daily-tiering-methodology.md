# 9. Daily Tiering Methodology (Property-Specific)

This document defines the end-to-end process used to assign each calendar day to a performance tier for a property.

It reflects the current implemented workflow used for `flohom`, and is designed to generalize to future properties.

---

## 1. Goal

Create a practical, owner-communicable daily calendar where each day has a tier label (for pricing guidance), while preserving transparent diagnostics on how tiers were produced.

The system is intentionally:

- **data-first** (tries to discover natural structure),
- **robust** (falls back safely when natural breaks do not exist),
- **auditable** (records method and fallback reason),
- **property-specific** (all scoring is relative to each property's own history).

---

## 2. Analysis Window

Tiering uses the same analysis period as the main analysis pipeline:

- two full calendar years before current year,
- filtered by `arrival_date`.

Example (today in 2026): analyze `2024-01-01` through `2025-12-31`.

---

## 3. Signal Definition

### 3.1 Primary signal

Use **RevPAR** as the single primary signal:

- `daily_revpar = daily_revenue / room_count` (property-level).

### 3.2 Why RevPAR-only

- ADR was excluded as a primary scoring input because RevPAR already embeds price and occupancy effects.
- Booking pace was evaluated but deferred for now (directionally useful, not sufficiently reliable for this tier engine stage).

### 3.3 Smoothing

Use centered rolling smoothing to capture structure rather than daily noise:

- `revpar_smoothed = rolling_mean(daily_revpar, window = smoothing_window_days)`,
- default `smoothing_window_days = 14`.

---

## 4. Tier Detection Logic

The algorithm is two-stage:

1. try natural-gap detection,
2. if invalid, use quantile fallback.

### 4.1 Stage A: Gap detection

Attempt to detect meaningful gaps in score distribution:

- score basis is configurable per property:
  - `percentile_int` (integer-rounded percentile of smoothed RevPAR), or
  - `revpar_zscore` (z-score of smoothed RevPAR).
- detect boundaries where adjacent unique score gap exceeds threshold.

Constraints for a valid gap result:

- tier count in `[min_tiers, max_tiers]` (default `2..15`),
- every tier has at least `min_days_per_tier` days (default `14`).

### 4.2 Stage B: Quantile fallback

If gap output is invalid/out-of-bounds, fallback to quantiles:

- split by score percentile into `quantile_fallback_tiers` bands (property-configured),
- then apply same `min_days_per_tier` validation.

### 4.3 Final defensive fallback

If quantile assignment fails (rare), fallback to a median split (2 tiers) so output always exists.

---

## 5. FLOHOM Findings (Implemented Result)

For `flohom`, repeated diagnostics showed no meaningful natural gaps:

- percentile-int basis: all integer scores `0..100` appeared, so no large empty gaps,
- z-score basis: max adjacent gap remained very small (`~0.0515` with threshold `0.6`).

Conclusion:

- `flohom` behaves as a **smooth-gradient property** (no sharp structural breaks),
- gap detection correctly fails,
- quantile fallback is the correct and honest method.

Quantile tuning sweep (5..10 tiers) selected **6 tiers** as the best balance between separation and usability.

---

## 6. Property Config Keys

Tier behavior is controlled in `config/properties/<property_id>.yaml` under `tiering`.

Example (`flohom`):

```yaml
tiering:
  method: "gap_detection"
  gap_score_basis: "revpar_zscore"
  smoothing_window_days: 14
  gap_threshold_pct: 6
  gap_threshold_zscore: 0.6
  min_tiers: 2
  max_tiers: 15
  quantile_fallback_tiers: 6
  min_days_per_tier: 14
  tier_label_map:
    1: "Soft"
    2: "Low"
    3: "Shoulder Low"
    4: "Shoulder High"
    5: "High"
    6: "Peak"
```

Optional manual overrides:

- `tier_label_map`: rename tiers for owner-facing communication,
- `tier_merge_map`: merge source tiers into target tiers (e.g. `1->1`, `2->1`) when distinctions are not actionable.

---

## 7. Output Tables

Tiering writes these files in `output/<property_id>/analysis/`:

- `daily_tier_calendar.csv`
  - one row per date in analysis window,
  - includes both base and final tier fields:
    - `base_tier_id`, `base_tier_label`,
    - `tier_id`, `tier_label`,
    - plus `tier_method`.
- `tier_summary.csv`
  - one row per final tier,
  - includes:
    - `days`, `share_of_days_pct`,
    - `avg_revpar`, `min_revpar`, `max_revpar`,
    - `avg_adr`, `avg_occupancy_pct`,
    - percentile span fields.
- `tier_blocks.csv`
  - contiguous runs of the same tier over time (`start_date`, `end_date`, `length_days`).
- `tier_diagnostics.csv`
  - single-row run diagnostics:
    - selected method,
    - fallback reason,
    - configured thresholds,
    - candidate tier stats per score basis,
    - override usage details,
    - final tier count and min-days check.

---

## 8. How to Run

```bash
python scripts/run_analysis.py --property <property_id>
```

This regenerates all analysis outputs including tier tables.

To view in dashboard:

```bash
streamlit run scripts/run_dashboard.py -- --property <property_id>
```

Use the **Tier calendar** tab for diagnostics + tier tables.

---

## 9. Operating Model (Recommended)

Use the following workflow for each property:

1. Run auto tiering (gap-first + fallback).
2. Review `tier_diagnostics.csv` and `tier_summary.csv`.
3. If needed, apply property-level overrides:
   - rename tiers (`tier_label_map`),
   - merge adjacent tiers (`tier_merge_map`).
4. Re-run analysis and confirm final tier shape.

This keeps the system scalable and consistent while preserving pricing-manager judgment where required.

