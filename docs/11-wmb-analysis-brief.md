# WMB Analysis Brief (Current Clean Run)

This brief documents the current WMB analysis state, decisions made, caveats, and follow-up actions.

## Scope and Data Rules

- Property: `wmb` / Onera Wimberley
- PMS: `cloudbeds`
- Analysis window override: `2024-08-01` to `2026-03-31`
- Canonical revenue field: `Revenue`
- Revenue filter: `revenue > 0`
- Status exclusions: `Cancelled`, `Confirmation Pending`
- Channel mapping: `Channel Group`, fallback to column `Y`
- Unit identity: listing type (not physical room number)

## Data Integrity State

- Canonical ingestion passes validation (`can_continue: true`)
- Remaining warning: 2 rows have `booking_date > arrival_date`
- Tier integrity checks are clean for start-date governance:
  - `require_listing_start_dates: true`
  - `units_inferred_start_date: 0`
  - `listing_start_source_all_config: pass`
  - `composition_shift_days: 0`

## Tiering Method Outcome

- Selected method: `quantile_fallback`
- Natural gap detection did not trigger:
  - `max_gap_percentile_int = 1.0` (vs threshold 6.0)
  - `max_gap_zscore = 0.1284` (vs threshold 0.6)
- Final tier count: 5
- Tier labels: `Soft`, `Low`, `Shoulder`, `High`, `Peak`

## Tier Count Validation (Automated)

The system now runs an automated sweep during analysis and writes:

- `output/<property>/analysis/tier_sensitivity_sweep.csv`
- `output/<property>/analysis/tier_validation_summary.csv`

For WMB, the recommendation is:

- `recommended_quantile_fallback_tiers = 5`
- Selection basis: maximum positive minimum adjacent tier step (`min_adjacent_step_revpar`)
- `natural_gap_detected_any_sweep = false`

## Portfolio Signal and Reporting Caveats

- YoY values are mathematically correct but not decision-grade for ramp periods.
- WMB started in Aug 2024, so YoY should be treated as directional only.
- The dashboard should avoid presenting YoY as a mature baseline KPI for WMB.

## Listing-Type vs Physical Unit Caveat

WMB canonical unit ids represent listing types, not physical units.

Known physical-unit distribution:

- `Greenhouse`: 9
- `Greenhouse (Accessible)`: 1
- `Greenhouse (Pet Friendly)`: 6
- `Spyglass`: 5
- `Spyglass (Accessible)`: 1
- `Spyglass (Pet Friendly)`: 6

Total units = 28.

Implication:

- Listing-level occupancy/RevPAR rows can be misread if interpreted as single-unit utilization.
- Treat listing-level metrics as listing-type performance aggregates unless normalized by physical unit count.

## Pricing Integrity Check (Tier x Booking Window)

Question examined:

- Are `High` / `Peak` days filling at discounted rates via last-minute bookings?

Result from current run (arrival-date join to tier calendar):

- `High` tier:
  - Revenue share booked in 0-7 days: 46.57%
  - ADR for 0-7 days: 373.40
  - ADR for >7 days: 515.93
- `Peak` tier:
  - Revenue share booked in 0-7 days: 39.88%
  - ADR for 0-7 days: 449.26
  - ADR for >7 days: 544.85

Interpretation:

- High/Peak demand still arrives substantially inside 7 days.
- Last-minute ADR is materially lower than >7-day ADR in both high bands.
- This suggests potential late-fill discounting pressure on premium-tier days and is a pricing operations follow-up item.

## Recommended Follow-ups

1. Add dashboard caveat for ramp-period YoY interpretation on WMB.
2. Add listing-type normalization option using physical unit counts (for listing-level occupancy/RevPAR clarity).
3. Add recurring monitoring table: `tier_label x lead_band x ADR` to track premium-day pricing discipline over time.
