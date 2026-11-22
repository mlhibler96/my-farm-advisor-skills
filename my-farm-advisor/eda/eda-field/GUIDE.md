---
name: eda-field
description: Field-level EDA that turns one farm's boundaries, CDL composition, and weather tables into ten static artifacts with the CSV behind every figure. Compares a field with itself over time, fields within a grower, and growers across the portfolio, and states which comparisons the data cannot support.
version: 0.1.0
author: Boreal Bytes
tags: [eda, fields, boundaries, cdl, weather, farm-scale, matplotlib]
---

# Workflow: eda-field

## Description

Answer "what does this farm look like?" from three tables every farm build already
produces — field boundaries, CDL class composition, and daily weather — without an
interactive dashboard. Each category emits two statistical views, one
comparison/correlation view, and one map:

| Category | Statistical view | Comparison / correlation | Map |
|---|---|---|---|
| Boundaries | area vs name token | shape quality; pairwise separation | field portfolio |
| CDL | rotation matrix by year | class purity, rotation outcome | — |
| Weather | GDD curves by year | monthly precipitation; variance partition | — |

The only map is the field portfolio. A CDL map is deliberately absent: the raster
is not ingested here, so a "CDL map" would be invented rather than derived.

Every PNG ships with the CSV that produced it, so a later report can quote numbers
instead of reading pixels. Report assembly is **out of scope** for this subskill.

## When to use this workflow

- A farm has boundaries plus derived CDL and weather tables and someone asks for
  a first, defensible field-level read.
- You need to check whether field geometry, crop history, and weather can support
  field-level claims before building a report or a model.
- You need a repeatable demo of the data foundation, not a one-off notebook.

Use the sibling `eda-*` skills instead when the question is generic (arbitrary
DataFrames, no farm tables) or when the answer must be interactive.

Do **not** use this workflow for soil analysis. Soil is out of scope by design.

## Inputs

Resolved from the canonical runtime layout when `--data-root` (or
`${DATA_PIPELINE_DATA_ROOT}`) is set:

| Input | Default path | Required |
|---|---|---|
| Boundaries | `growers/<grower>/farms/<farm>/boundary/field_boundaries.geojson` | yes |
| Weather | `growers/<grower>/farms/<farm>/derived/tables/*_weather_*.csv` | yes |
| CDL composition | `growers/<grower>/farms/<farm>/derived/tables/*_cdl_*_full_composition.csv` | yes |
| Planted crops | `growers/<grower>/farms/<farm>/manifests/*_derived_fields_provenance.json` | optional |

Expected columns:

- weather: `field_id, lat, lon, date, T2M, T2M_MAX, T2M_MIN, PRECTOTCORR, ...`
- CDL: `field_id, year, crop_code, crop_name, pixel_count, pct, crop_source`
- planted manifest: `fields[].field_slug` and `fields[].crop`; when an entry
  has no slug, the loader falls back to `fields[].field_name` matched against
  the boundary name (unique matches only)

An explicit `--boundaries`, `--weather`, `--cdl`, or `--planted` overrides the
default for that input, which is how the fixture runs.

## Quick start

```bash
# real farm (writes under the farm's derived/analysis/eda-field)
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  my-farm-advisor/eda/eda-field/scripts/field_eda.py \
  --data-root "${DATA_PIPELINE_DATA_ROOT}" \
  --grower-slug mo-grower --farm-slug hibler-farms

# one category only
python .../field_eda.py ... --category weather

# explicit fixture inputs (no runtime tree needed)
python .../field_eda.py \
  --boundaries /tmp/fx/boundaries.geojson \
  --weather /tmp/fx/weather.csv \
  --cdl /tmp/fx/cdl.csv \
  --planted /tmp/fx/planted.json \
  --out-dir /tmp/fx-out --no-cross-grower
```

Everything is written to `--out-dir`, defaulting to
`${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers/<grower>/farms/<farm>/derived/analysis/eda-field/`.
A run also writes `run_summary.json` with the findings and guard notes.

## What each artifact tells you

### Boundaries

| Artifact | Question | Story |
|---|---|---|
| `boundaries_area_ratio.png` | Does the measured acreage match the token in the field name? | One flagged bar means the name lies; the geometry is still the truth. |
| `boundaries_shape.png` | Is a field a clean parcel or a fragmented/sliver footprint? | Multipart or low-compactness fields distort every per-pixel share, so their CDL purity needs a caveat. |
| `boundaries_adjacency.png` | Are these independent fields or contiguous blocks? | The closest pairs are shown as gap lollipops (red where an edge is shared); the full pair table is in the CSV. Small gaps mean one block, large gaps a set of distant parcels. |
| `boundaries_portfolio_map.png` | Where is the portfolio, at true relative position? | Numbered footprints on local metre axes; a detail panel expands the largest contiguous cluster and the key carries ID, acreage, and planted crop. |

### CDL

| Artifact | Question | Story |
|---|---|---|
| `cdl_crop_matrix.png` | What did each field grow, year by year? | The rotation sequence; a solid row is continuous cropping, an alternating row is a corn/soybean rotation. |
| `cdl_purity.png` | How much of each field does the dominant class actually cover? | Low purity means edge mixing or a real split in management; compare it with field size, not with a target. |
| `cdl_rotation.png` | Did the planted crop follow a rotation break or repeat the last CDL year? | `flip` = the expected corn↔soybean change, `repeat` = same crop twice, `other` = anything else. |

### Weather

| Artifact | Question | Story |
|---|---|---|
| `weather_gdd_curves.png` | How did the seasons accumulate heat, and how do years rank? | Curves that separate early point to a warm spring; curves that converge still had different totals. |
| `weather_precip_heatmap.png` | When did the water arrive? | The same annual total can be a wet spring or a wet fall; the month pattern is what matters. |
| `weather_variance.png` | Is weather variation a field, year, or grower effect? | This decides the unit of analysis: if fields share one series, field-level weather claims are not identified. |

## Reading the numbers honestly

- **Boundary ratio** is only meaningful for geometry this run derived. Inherited
  field names are labels, not measurements; a 125 % ratio means the name is
  wrong, not the polygon.
- **CDL agreement is a rotation outcome, never a data disagreement.** `flip` and
  `repeat` describe what management did, not which source is correct.
- **Weather variance uses season-total GDD per field-year**, not daily values.
  Daily variance is dominated by the within-season shape and hides the year
  signal.
- **One weather series per grower** makes the field share 0 by construction. The
  run says so in `run_summary.json` and on the figure instead of reporting a
  meaningless p-value.
- **One grower in scope** skips the grower term with an explicit guard. The
  subskill never turns missing cross-grower evidence into a zero statistic.
- Tests (`flip` binomial, purity vs log area) are structural checks on one farm.
  Treat the p-value as a smell test, not agronomic evidence.

## Outputs

- 10 PNGs and 10 CSVs under `--out-dir`.
- `run_summary.json`: inputs, per-category artifacts, findings, and guards.

The four committed demo figures live in `examples/output/`; the other six are
generated on demand. See `examples/README.md` for the reproduction recipe.

## Dependencies

`geopandas`, `pandas`, `numpy`, `scipy`, `matplotlib` from the data-pipeline
virtualenv. The portfolio map imports `lib/field_frame.py` from
`data-pipeline/src/scripts`, so the repository checkout (or an installed runtime)
must be present.
