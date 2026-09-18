## 8. Pricing matrix & draft ADRs (Step 2)

This document defines **Step 2** of the onboarding workflow: turning the analysis outputs into a **draft pricing matrix**.

Step 1 (docs/07) produces stable performance scores and historical ranges. Step 2 uses those scores and ranges to build a **rate scaffolding** that is:

- anchored to each listing’s historical ADR,
- adjusted for **listing strength**, **seasonality**, and **day-of-week demand**,
- clipped to realistic historical ADR bounds, and
- forced to respect a **fixed weekday price hierarchy**.

The result is a CSV that can be reviewed and adjusted into a final pricing sheet.

> **Scope note (general vs example):**  
> - Everything in this document is written to be **PMS- and property-agnostic** and should apply to **any property** that uses the canonical analysis tables from docs/07.  
> - Wherever LaFave Zion is mentioned, it is **only as an example** of how the generic logic looks for one specific property (e.g. 32 listings → 2688 rows). The formulas and structure do **not** change for other properties.

---

### 1. Matrix structure

**Grain:** one row per:

- `unit_id` (listing),
- `month_index` (1–12; calendar month), and
- `day_of_week` (Monday–Sunday, arrival day).

**Example (LaFave Zion):**

- 32 listings × 12 months × 7 days = **2688** rows.

**Columns (initial version):**

| Column | Definition |
|--------|-----------|
| `unit_id` | Listing identifier. |
| `month_index` | Calendar month number 1–12. |
| `day_of_week` | Monday, Tuesday, …, Sunday. |
| `base_adr_anchor` | Listing’s historical ADR across the full analysis period (two years combined). |
| `listing_score` | Listing strength indicator (e.g. RevPAR percentile band). |
| `month_score` | Calendar-month performance score (1–10) from the combined monthly table. |
| `dow_score` | Day-of-week performance score (1–10) from the DOW table. |
| `draft_adr` | Draft suggested ADR for that listing × month × DOW after all adjustments, bounds, and hierarchy enforcement. |

**Output:** e.g. `output/<property_id>/pricing/pricing_matrix_draft.csv`.

---

### 2. Inputs from analysis (Step 1)

Step 2 **never looks at raw reservations**. It reads only the Step‑1 outputs:

- `overall_summary.csv` (Table 1) – listing ADR and RevPAR.
- `monthly_performance_combined.csv` (Table 2b) – month-of-year scores.
- `by_day_of_week.csv` (Table 4) – day-of-week scores (and `dow_rank_provisional`, which neutralizes all 7 scores to 5 when there's too little history — see docs/07 §6).
- `adr_by_listing_by_month.csv` (Table 6) – min/max ADR per listing × year-month.

All formulas below assume the analysis period is the two full calendar years defined in docs/07.

---

### 3. Base ADR anchor (per listing)

**Source:** `overall_summary.csv`.

**Definition:**

- For each `unit_id`, take the **two-year combined ADR**:

\[
\text{base\_adr\_anchor(unit)} = \text{ADR\_overall\_summary(unit)}
\]

This keeps each listing’s **long-run price level** as the reference point and avoids rebuilding the ladder from scratch.

---

### 4. Listing strength factor (per listing)

We tilt each listing slightly up or down based on its **RevPAR performance** over the two years.

**Source:** `overall_summary.csv`.

1. For each `unit_id`, compute its **RevPAR percentile** among all listings (excluding the `PROPERTY` row).
2. Map that percentile into a **listing strength multiplier**, using a continuous linear interpolation from the 0th to the 100th percentile:

\[
\text{listing\_factor} = 0.90 + 0.20 \times \text{revpar\_percentile}
\]

| RevPAR percentile | `listing_factor` |
|--------------------|------------------|
| 0% | 0.90 |
| 25% | 0.95 |
| 50% | 1.00 |
| 75% | 1.05 |
| 100% | 1.10 |

(Earlier versions used a 5-step band table with the same anchor values at 0/20/40/60/80/100%, which produced a hard price jump for a listing sitting just above vs. just below a band cutoff — e.g. 79th vs. 81st percentile — despite being nearly identical in the underlying data. The continuous version removes those cliffs while keeping the same overall 0.90–1.10 range.)

3. This factor is **constant across all months and DOWs** for a listing.

We may store the underlying percentile or band label in `listing_score` for transparency, but the multiplier actually used is `listing_factor`.

---

### 5. Month factor (seasonality)

**Source:** `monthly_performance_combined.csv` (12 rows, one per calendar month).

Use the **month-of-year performance score** (1–10) as defined in docs/07.

For each calendar month:

- Let `month_score` = `performance_score_1_10` from the combined table (same for all listings).
- Convert to a **month factor**:

\[
\text{month\_factor} = 0.75 + 0.05 \times \text{month\_score}
\]

Examples:

| `month_score` | `month_factor` |
|---------------|----------------|
| 1 | 0.80 |
| 5 | 1.00 |
| 10 | 1.25 |

This reproduces the **seasonal shape** observed in the data:

- Lowest factors for **winter months** (Jan, Feb, Dec),
- Mid factors for summer shoulder,
- Highest factors for **spring and fall peaks** (Apr–Jun, Sep–Oct).

---

### 6. Day-of-week factor (weekday pattern)

**Source:** `by_day_of_week.csv` (7 rows, Mon–Sun).

    - Let `dow_score` = `dow_score_1_10` for that day-of-week (same for all listings and months). If `by_day_of_week.csv` flags the whole table `dow_rank_provisional` (too few weeks of stay-date history for a reliable weekday rank — see docs/07 §6), every day's score is neutralized to 5 before this step, same treatment as a provisional month.
    - Convert to a **weekday factor**:

\[
\text{dow\_factor} = 0.80 + 0.04 \times \text{dow\_score}
\]

Examples:

| `dow_score` | `dow_factor` |
|------------|--------------|
| 1 | 0.84 |
| 5 | 1.00 |
| 10 | 1.20 |

This widens the weekday spread so that high-score days (e.g. **Saturday, Thursday**) have a clearer uplift over weaker days.

> **Note:** this section previously documented a narrower `0.85 + 0.025 × dow_score` curve (0.875–1.10). That was a docs/code drift, not a change in behavior — `src/pricing_matrix.py` has always used `0.80 + 0.04 × dow_score` (0.84–1.20). This section now matches the shipped code.

---

### 7. Raw ADR calculation (before guardrails)

For each `unit_id × month_index × day_of_week`, compute the raw draft ADR using **additive adjustments**:

- Define:
  - `listing_adj = listing_factor - 1`
  - `month_adj = month_factor - 1`
  - `dow_adj = dow_factor - 1`

\[
\text{draft\_adr\_raw} =
\text{base\_adr\_anchor(unit)} \times
\bigl(1 + \text{listing\_adj} + \text{month\_adj} + \text{dow\_adj}\bigr)
\]

This reduces extreme compounding from multiplying three factors while still letting each score move the price up or down.

---

### 8. Historical ADR bounds (per listing × month)

To keep prices in a realistic range, we use the **ADR by listing × month** table.

**Source:** `adr_by_listing_by_month.csv`.

For each `unit_id × month_index`:

1. Look at all rows where:
   - `unit_id` matches, and
   - `year_month` has that `month_index` in either analysis year.
2. From those rows, read:
   - `min_adr` = historical minimum ADR,
   - `max_adr` = historical maximum ADR.
3. Compute **adaptive bounds** based on the calendar month’s strength score (`month_score` from the combined monthly table):

| `month_score` band | `floor_multiplier` | `ceiling_multiplier` |
|--------------------|--------------------|----------------------|
| 1–3 (weak) | 0.85 | 1.10 |
| 4–7 (mid) | 0.90 | 1.15 |
| 8–10 (strong) | 0.95 | 1.25 |

Then:

\[
\text{floor} = \text{min\_adr} \times \text{floor\_mult}, \quad
\text{ceiling} = \text{max\_adr} \times \text{ceil\_mult}
\]

4. Rather than clipping each day independently (which can flatten the week), we **scale the entire week** if needed:

   - After computing `draft_adr_raw` and enforcing the weekday hierarchy (next section), compute `week_min` and `week_max` across the 7 days.
   - If all days are below `floor`, scale the week up so that `week_min` hits `floor`.
   - If any day is above `ceiling`, scale the week down so that `week_max` hits `ceiling`.

This keeps all days within a realistic range **without destroying the weekday shape**.

---

### 9. Enforcing the weekday price hierarchy (config-driven)

We want a **weekday price ladder** that always holds for every `unit_id × month_index`, but the exact ladder
is **property-specific** and must come from config.

#### 9.1. Configuration (per property)

Add a `pricing.dow_hierarchy` section to each property config (see `config/properties/<property_id>.yaml`):

```yaml
pricing:
  dow_hierarchy:
    - ["Monday", "Tuesday"]   # lowest tier
    - ["Wednesday"]           # next tier
    - ["Sunday"]              # ...
    - ["Thursday", "Friday"]
    - ["Saturday"]            # highest tier
```

- Each inner list is a **tier**, ordered from **lowest** to **highest**.
- Within a tier, all days should end up at similar or identical price levels.
- Higher tiers must not price below lower tiers after enforcement.

**Example (LaFave Zion):**

```text
Mon = Tue
  < Wed
  < Sun
  < Thu = Fri
  < Sat
```

Other properties can use different ladders (e.g. Fri/Sat only, or stronger midweek).

#### 9.2. Implementation rule

For each `unit_id × month_index`, after computing `draft_adr_bounded` for all 7 days:

1. **Within each tier**, raise all days to at least the **maximum price in that tier** (so a weak Tuesday does not undercut Monday if they are paired).
2. **Across tiers**, ensure prices are **non-decreasing** from lower to higher tiers. If a higher tier ends up below a lower tier, raise that tier (and all days in it) up to the previous tier’s max.

The final stored `draft_adr` per row is the **post-hierarchy** price. This guarantees the configured ladder never breaks, even if the underlying scores are close or noisy.

---

### 10. Final matrix and interpretation

After all steps, the pricing matrix row has:

- `base_adr_anchor` – the long-run anchor for the listing.
- `listing_score` – band/percentile describing listing strength (optional field for display).
- `month_score` – 1–10 month-of-year score from analysis.
- `dow_score` – 1–10 day-of-week score from analysis.
- `draft_adr` – final suggested ADR that:
  - respects **listing strength**,
  - follows **seasonal** and **weekday** demand,
  - stays within **historical ADR ranges**, and
  - respects the **Mon/Tue < Wed < Sun < Thu/Fri < Sat** hierarchy.

This matrix is intended as a **draft pricing scaffold**. Human analysts can then:

- apply additional business rules (e.g. minimum weekend length, stay-through discounts),
- round or bucket ADRs as needed,
- and export to a final pricing sheet for the PMS or RMS.

