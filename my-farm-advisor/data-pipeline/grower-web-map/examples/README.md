# Grower Web Map Examples

There is no committed sample map here, on purpose:

- the generated HTML embeds a real grower's field boundaries, and
- the artifact is runtime output, which stays under
  `${DATA_PIPELINE_DATA_ROOT}/data-pipeline`.

## Regenerate an example from real pipeline data

```bash
export DATA_PIPELINE_DATA_ROOT=~/my-farm-advisor-runtime
cd my-farm-advisor/data-pipeline
./scripts/install.sh --non-interactive --force-refresh
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  scripts/reporting/generate_grower_webmap.py --grower-slug mo-grower
```

Result (three-field Missouri grower):

```text
Grower: mo-grower
  Hibler Farms: 3 fields
  1 farm(s), 3 field(s), 75.1 acres
  saved .../growers/mo-grower/derived/reports/mo-grower_webmap.html
  size  24.6 KB
```

## What the page contains

- Esri World Imagery basemap with an OSM alternative and a layer switcher.
- Three field polygons (one layer group per farm), fitted to the grower extent.
- Sidebar field list: `12ac Kev · 12ac-kev · Soybeans · 11.3 ac`, and so on.
- Popup per field:

```text
12ac Kev
Hibler Farms · MO Grower
Grower          MO Grower
Farm            Hibler Farms
Field           12ac Kev
Field ID        12ac-kev
Area            11.3 ac
Crop (latest)   Soybeans
State           MO
Dominant soil   Higginsville
Drainage        Somewhat poorly drained
Organic matter  1.5 %
pH              6.12
Water storage   2.9 in
2021 · Soybeans  2022 · Corn  2023 · Soybeans  2024 · Corn  2025 · Soybeans
```

The whole file is under 25 KB because only the GeoJSON boundaries and a small
metadata object are embedded; map tiles are fetched by the browser.

## Suggested review capture

Screenshot the page at desktop width, then at phone width after opening the
field list, and confirm the checklist in `../GUIDE.md`. A dev-only Playwright
venv outside this repo is enough; nothing from that review tooling is a runtime
dependency.
