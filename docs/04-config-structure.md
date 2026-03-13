# Config Structure: Property vs PMS

We have two kinds of config that serve different purposes. They stay **separate** but can be **linked** so the pipeline knows which to use together.

---

## 1. Two config types

| Config | Location | Purpose | Reuse |
|--------|----------|---------|--------|
| **PMS / source mapping** | `config/pms/resnexus_mapping.yaml` (and future `config/pms/mews_mapping.yaml`, etc.) | How to map **this PMS export format** into the canonical schema (columns, date format, channel columns). | One per **PMS** — reused for every property that uses that PMS. |
| **Property inventory** | `config/properties/lafave_zion.yaml` (one per property) | **This property’s** room count, room types (unit_ids), and optional metadata. Used for occupancy %, RevPAR, and room-level analysis. | One per **property** — only for that site. |

So:
- **ResNexus config** = “when the file is from ResNexus, map like this.” Any property using ResNexus uses the same mapping.
- **LaFave Zion config** = “this property has 32 rooms and these units.” Only for LaFave Zion.

---

## 2. Why keep them separate

- **Different lifecycles:** PMS mapping changes when we support a new PMS or a new export format. Property config changes when we onboard a new property or the property’s room list changes.
- **Reuse:** Many properties can share one ResNexus (or Mews, etc.) mapping. Each property has its own inventory.
- **Clear ownership:** Source mapping = data/format concern. Property config = site-specific business data.
- **Flexibility:** A new property might use Mews; we add `config/pms/mews_mapping.yaml` and a new `config/properties/other_property.yaml` without touching the ResNexus or LaFave files.

---

## 3. Linking them (optional)

So they stay **separate**, but we **link** them so the pipeline knows which mapping to use for a given run:

- In the **property** config, add a field that points to the PMS/source format, e.g.  
  **`pms_id: resnexus`** (or `source_format: resnexus`).
- When running ingestion or analysis for a property (e.g. `lafave_zion`):
  1. Load **property** config → get `room_count`, `room_types`, and `pms_id`.
  2. If you need to map raw data, load the **PMS** config for that `pms_id` (e.g. `config/pms/resnexus_mapping.yaml`).

So:
- **Property config** = “who am I, how many rooms, which PMS.”
- **PMS config** = “how to turn that PMS’s export into canonical.”

No need to duplicate column mappings inside each property file; one ResNexus mapping serves all ResNexus properties.

---

## 4. When integration might make sense

A single **combined** file per property (e.g. “lafave_zion has 32 rooms *and* uses this column mapping”) can be useful if:
- You rarely add new PMSs and often add new properties, and you want one file per property that contains everything, or
- A property has a **custom** export (e.g. ResNexus but with extra columns). Then you might have `lafave_zion.yaml` include an override or a property-specific mapping snippet.

Even then, keeping a **base** ResNexus mapping and letting the property config **extend or override** it is usually better than copying the full mapping into every property file.

---

## 5. Recommendation

- **Keep** PMS config and property config **separate** (current layout).
- **Add** `pms_id` (or `source_format`) to the **property** config so the pipeline can select the right mapping.
- **Document** in the run script or README: “For property X, load `config/properties/X.yaml`; use its `pms_id` to load the mapping from `config/pms/<pms_id>_mapping.yaml` (or the equivalent).”

That way the two configs stay integrated in **workflow** (linked by `pms_id`) but remain **separate files** for clarity and reuse.
