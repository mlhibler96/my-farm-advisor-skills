---
name: grower-web-map
description: Build a lightweight interactive HTML field map for each grower in the data-pipeline runtime
version: 1.0.0
tags: [web-map, leaflet, field-boundaries, grower, interactive, basemap]
---

# Workflow: grower-web-map

## Description

Turn the field boundaries the pipeline already wrote into one shareable,
interactive HTML map per grower. The map draws every farm and every field on an
online satellite basemap (Esri World Imagery or OpenStreetMap), lets the user
zoom and pan, and shows grower / farm / field metadata when a field is clicked.

The output is deliberately small: field polygons plus a few metadata fields.
Nothing raster is embedded — no satellite scenes, no NDVI composites, no
terrain rasters, no field posters. A three-field grower lands around 25 KB; a
few hundred simplified fields stays in the low hundreds of KB.

## When to use this workflow

- A grower's data pipeline run is complete and the field boundaries exist.
- Someone needs to click through the farm on imagery rather than read a poster.
- The map will be emailed, dropped on a tablet, or hosted on any static server.
- A reviewer wants to confirm the pipeline drew the right polygons in the right
  place.

Do not use it to publish imagery products. Those stay as GeoTIFFs/PNGs under
the field tree; embedding them would blow up the file and the point of the map.

## What it reads (runtime contract)

All paths come from `scripts/lib/paths.py` and `DATA_PIPELINE_DATA_ROOT`:

| Input | Path | Used for |
| --- | --- | --- |
| Grower metadata | `growers/<grower>/grower.json` | display name |
| Farm metadata | `growers/<grower>/farms/<farm>/farm.json` | farm display name, state fallback |
| Field boundaries | `growers/<grower>/farms/<farm>/boundary/field_boundaries.geojson` | polygons, field id/name, acres, crop |
| Soil summary (optional) | `derived/tables/<farm>_ssurgo_summary.csv` | dominant soil, drainage, OM, pH, AWS |
| Crop history (optional) | `derived/tables/<farm>_cdl_2021_2025_full_composition.csv` | per-year crop chips |

Missing optional tables degrade gracefully: the popup simply omits those rows.

## Output

```
${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers/<grower>/derived/reports/<grower>_webmap.html
```

One self-contained file. It is generated runtime data: keep it out of Git.

## Commands

Run from the runtime source copy, like every other reporting step:

```bash
export DATA_PIPELINE_DATA_ROOT=/absolute/path/to/my-farm-advisor-runtime
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"

# every grower on disk
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  scripts/reporting/generate_grower_webmap.py

# one grower
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  scripts/reporting/generate_grower_webmap.py --grower-slug mo-grower

# one farm inside a grower, custom destination
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  scripts/reporting/generate_grower_webmap.py \
  --grower-slug mo-grower --farm-slug hibler-farms \
  --output /tmp/hibler-map.html
```

`--output` requires exactly one `--grower-slug`. `--title` overrides the page
title. The command prints farms, fields, acres, output path, and file size, and
warns above 2 MB.

## Map behaviour

- **Basemap**: Esri World Imagery by default, with a Streets (OSM) alternative
  in the layer switcher. Tiles load from the internet; the data does not.
- **All fields**: one Leaflet layer group per farm, distinct stroke colour per
  farm, map fitted to the whole grower on load.
- **Zoom/pan**: native Leaflet zoom control, scroll-wheel and pinch zoom, scale
  bar in imperial units.
- **Click a field**: popup with grower, farm, field name, field id, acres,
  latest crop, state, and — when available — dominant soil, drainage class,
  organic matter, pH, water storage, plus a 2021–2025 crop chip sequence.
- **Field list**: searchable sidebar grouped by farm; clicking a row zooms to
  that field and opens its popup; each farm header has a "Zoom" button.
- **Small screens**: the sidebar becomes a slide-over panel behind the header
  menu button, and popups are narrowed and auto-panned so the table is never
  clipped.

## Install into the runtime copy

The generator lives in the data-pipeline source tree, so the normal installer
copies it:

```bash
cd my-farm-advisor/data-pipeline
./scripts/install.sh --non-interactive --force-refresh
```

That replaces `${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src` with the checkout
source (growers and shared data are untouched) and installs:

- `src/scripts/reporting/generate_grower_webmap.py`
- `src/scripts/lib/webmap_html.py`
- `src/scripts/lib/paths.py` (adds `grower_reports_dir`, `grower_webmap_path`)

## Review checklist

Open the HTML in a browser (or screenshot it headlessly) and confirm:

1. All fields for the grower are drawn on imagery, in the right place.
2. Zoom and pan work; the scale bar changes with zoom.
3. Clicking a field shows grower, farm, and field name — the required minimum.
4. The sidebar list matches the map and its search filters rows.
5. No missing-tile holes; provider attribution is visible.
6. File size is reasonable (tens of KB, not tens of MB).

## Limitations

- The basemap needs internet. The HTML itself has no server dependency.
- Leaflet is loaded from unpkg, not vendored; offline use is out of scope.
- Boundary geometry is rounded to 6 decimals (~0.1 m). Growers large enough to
  exceed 1.5 MB get a topology-preserving simplification pass.
- The map is grower-level, not per-field: it is not a substitute for the field
  intelligence poster.
