# Local Instructions

## Purpose

This folder owns the grower-level interactive web map workflow: the small,
single-file Leaflet map generated from field boundaries the data pipeline
already wrote.

## Safe edit scope

Edits should stay in this folder and in the files it owns under
`../src/scripts/` (`reporting/generate_grower_webmap.py`,
`lib/webmap_html.py`, and the grower map helpers in `lib/paths.py`). Do not
change sibling pipeline steps, parent `SKILL.md`, or root policy from a
subskill task unless explicitly requested.

## Read nearby docs first

Read `GUIDE.md` first for the workflow, inputs, and review checklist. Read
`examples/README.md` before changing what the generated page contains. If
routing context is needed, read `../README.md`, `../AGENTS.md`, and
`../../SKILL.md`.

## Runtime contract

- Runtime code is installed by `../scripts/install.sh`; it lives in
  `../src/scripts/` and is executed from
  `${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src`, never from this checkout.
- Generated maps belong under
  `${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers/<grower>/derived/reports/`.
  Never commit them: they embed real grower boundaries.
- `install.sh --force-refresh` replaces the whole runtime `src/` tree. Runtime
  `growers/` and `shared/` data are preserved. Edit the checkout, then install;
  never hand-edit the runtime copy as the fix.

## Local validation

```bash
# structure, provenance, links, asset policy
./scripts/validate.sh          # from the repository root

# real run against generated grower data
cd my-farm-advisor/data-pipeline
./scripts/install.sh --non-interactive --force-refresh
cd "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/src"
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  scripts/reporting/generate_grower_webmap.py --grower-slug <slug>
```

Then open the written HTML and run the review checklist in `GUIDE.md`.

## Operational gotchas

- **Keep it small.** No rasters, NDVI composites, posters, or per-scene data in
  the payload. Polygons and metadata only; if a grower map nears 2 MB, simplify
  geometry or split by `--farm-slug`.
- **Round coordinates.** 6 decimals is ~0.1 m and saves most of the file size
  on detailed boundaries.
- **Popups are sized for phones.** Popup `maxWidth` is computed from the
  viewport, and the popup opens after `fitBounds` settles; opening during the
  zoom animation makes auto-pan push the title under the header.
- **Field ids are not names.** Prefer `field_name` from the boundary, and fall
  back to a de-slugged id. Never tail-truncate ids such as `50ac-bttms`.
- **Grower metadata may be the slug.** `grower.json` often repeats the slug as
  `display_name`; `webmap_html.display_name` falls back to a prettified slug.
- **State comes from the fields.** Use the boundary `state_fips` for display;
  `farm.json` has carried a stale state (e.g. `IA` on a Missouri farm).

## Local-delta-only reminder

This nested AGENTS.md only records instructions that differ from the parent or
root files. Do not duplicate root-wide asset, vendor, or validation policy here
except this pointer to `../../../AGENTS.md`.
