# eda-field artifact contract

A run is reproducible from four inputs and a category flag. This file is the
contract a caller (or a report builder) can code against; `GUIDE.md` explains
how to read the results.

## Run

```text
field_eda.py
  --data-root DIR                 # or DATA_PIPELINE_DATA_ROOT
  --grower-slug SLUG
  --farm-slug SLUG
  [--boundaries PATH] [--weather PATH] [--cdl PATH] [--planted PATH]
  [--out-dir DIR]
  [--category boundaries|cdl|weather|all]
  [--growers-root DIR] [--no-cross-grower]
```

- Default output: `<data-root>/data-pipeline/growers/<grower>/farms/<farm>/derived/analysis/eda-field/`
- Exit 0 on success. Missing inputs exit non-zero with the missing paths named.
- A category with no usable input is reported as a guard, not a crash (e.g. an
  empty CDL table).

## Artifacts

Every figure has a same-stem CSV. A caller should quote the CSV, not the PNG.

| Stem | PNG | CSV columns |
|---|---|---|
| `boundaries_area_ratio` | area vs name token, one row per field | `field_id, field_name, crop, area_acres, area_acres_geometric, perimeter_m, compactness, convex_hull_ratio, part_count, nominal_acres, ratio_to_name_pct` |
| `boundaries_shape` | compactness vs part count | same metrics table as above |
| `boundaries_adjacency` | gap matrix | `field_a, field_b, shared_edge_m, gap_m, overlap_acres` |
| `boundaries_portfolio_map` | framed portfolio map | `map_number, field_id, crop, area_acres, framed_x_m, framed_y_m` |
| `cdl_crop_matrix` | field × year dominant class | `field_id, year, dominant_crop, dominant_pct` |
| `cdl_purity` | dominant share per field-year | `field_id, year, dominant_crop, dominant_pct` |
| `cdl_rotation` | CDL last year → planted crop | `field_id, sequence, alternates, cdl_last, planted, outcome` |
| `weather_gdd_curves` | cumulative GDD by season | `field_id, year, date, gdd_f, cum_gdd, season_day` |
| `weather_precip_heatmap` | year × month precipitation | `year, 1..12` (mm) |
| `weather_variance` | variance shares | `term, levels, variance_share_pct` |

### Metric definitions

- `area_acres`: attribute if present, else geometric area.
- `area_acres_geometric`: always measured from the polygon.
- `compactness`: Polsby–Popper, `4πA / P²`; 1.0 is a circle.
- `convex_hull_ratio`: `A / A_hull`; lower means a more concave footprint.
- `part_count`: polygon parts after `MultiPolygon` expansion.
- `nominal_acres`: leading number in `field_name` (`21ac-clay-7` → 21).
- `ratio_to_name_pct`: `100 × measured_acres / nominal_acres`.
- `shared_edge_m`: length of `boundary ∩ boundary.buffer(1 m)`.
- `gap_m`: minimum edge-to-edge distance between the two polygons.
- `outcome`: `flip` (corn↔soybean between last CDL year and planting record),
  `repeat` (same class), `other` (anything else), or `no_planted_record`.
- `variance_share_pct`: sequential sum-of-squares share of **season-total GDD**
  per field-year, with the remainder in `residual (interaction)`.

## `run_summary.json`

Top level:

```json
{
  "generated_at": "ISO-8601 Z",
  "grower_slug": "...",
  "farm_slug": "...",
  "inputs": {"boundaries": "...", "weather": "...", "cdl": "...", "planted": "..."},
  "categories": {"boundaries": {}, "cdl": {}, "weather": {}}
}
```

### boundaries

`category, fields, total_acres, artifacts[]`, plus `findings`:
`fields_within_2m, max_shared_edge_m, min_gap_m, max_gap_m, overlap_pairs,
multipart_fields, names_off_by_over_15pct[]`.

### cdl

`category, fields, years[], alternating_fields, non_alternating_fields[],
transitions[{from_crop,to_crop,count}], flips_to_2026, repeats_to_2026,
comparable_2026, flip_binomial_p, purity_vs_log_area_spearman,
purity_vs_log_area_p, note, artifacts[]`.

`flip_binomial_p` is two-sided against 0.5 over `comparable_2026`; it is `null`
when nothing is comparable. `purity_vs_log_area_*` is `null` below four fields.

### weather

`category, unit, field_years, distinct_weather_cells, field_share_pct,
grower_levels, growers_available[], artifacts[]`, plus optional guards:

- `guard`: present when `grower_levels < 2`; the grower term is omitted.
- `guard_field`: present when `distinct_weather_cells == 1`; the field share is
  0 by construction.

## Verdict rules (must not drift)

1. **One grower → no grower term.** Never emit a grower row with a 0 % share and
   call it evidence. Emit `guard`.
2. **One weather series → field share is 0 by construction.** Emit
   `guard_field`, and put the note on `weather_variance.png`.
3. **Distinct weather series are counted from values**, not from `lat`/`lon`.
   Farm weather tables store the field centroid, so ten fields can share one
   grid cell and still show ten coordinate pairs.
4. **CDL is described as rotation outcome**, never as agreement or disagreement
   between sources.
5. **Field names are labels.** `ratio_to_name_pct` flags a naming error; it does
   not correct the geometry.
6. **One map only.** Do not render a CDL raster map from the composition table.

## Non-goals

- No soil ingestion or soil analysis.
- No interactive dashboard, web tiles, or client-side filtering.
- No report assembly, layout, or narrative generation.
- No raster ingestion (CDL, imagery, DEM); only the tabular composition.
- No writes into the repository; a run writes only under `--out-dir`.
