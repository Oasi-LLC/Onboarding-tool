# 10. PMS Parser Template (Onboarding New PMS)

Use this template when adding a new PMS export format. Goal: keep ingestion property-agnostic and make property-specific quirks explicit.

---

## 1) Add PMS parser in `src/parser.py`

Implement:

- `<pms>_REQUIRED` columns list
- `<pms>_CHANNEL` columns list (or equivalent channel source)
- `_load_csv_<pms>(path)` loader
- `_map_to_canonical_<pms>(raw, property_id=None)` mapper

The mapper must output canonical columns:

- `reservation_id`
- `arrival_date`, `departure_date`
- `nights`
- `unit_id`
- `guest_name`
- `revenue`, `amount_paid`
- `booking_date`, `lead_time_days`
- `adr`
- `channel`
- `arrival_day_of_week`, `arrival_year_month`

Register parser in `_PMS_PARSERS`.

---

## 2) Property-specific mapping hook (required pattern)

Inside `_map_to_canonical_<pms>(..., property_id=None)`, keep defaults generic, then branch only for known property quirks:

```python
if property_id == "some_property":
    # apply property-specific unit normalization or edge-case mapping
```

Do **not** hardcode property naming logic globally in default mapping.

---

## 3) Thread property id through ingestion/validation

Ensure these calls pass `property_id`:

- `run_validation(raw, pms_id, property_id=...)`
- `normalize(raw, pms_id, property_id=...)`
- `map_to_canonical(raw, pms_id, property_id=...)`

This enables parser-level property-specific branches without breaking generic PMS behavior.

---

## 4) Add property config

Create `config/properties/<property_id>.yaml` with:

- `property_id`, `property_name`
- `pms_id` (new parser id)
- `room_count`, `room_types`
- optional `tiering` and `airdna` blocks

If listing activation matters, include `listing_start_date` under each `room_types` item.

---

## 5) Validation checklist for new PMS

1. `run_ingestion.py --property <id> <raw_file>`
2. Validate:
   - no missing required columns
   - date parse success
   - unit_id/channel sanity
3. Confirm canonical file shape + key columns.
4. `run_analysis.py --property <id>`
5. Check `tier_integrity_audit.csv` for pass/warn/fail statuses.

---

## 6) Anti-patterns to avoid

- Mixing property-specific quirks into generic PMS defaults.
- Using hardcoded listing labels that only work for one property.
- Skipping `property_id` wiring through validation/normalize paths.

