# Reports

One-time report artifacts assembled from runtime analysis outputs. These are
deliverables, not part of a reusable skill.

## `field-eda-hibler-farms.html`

Single-page, self-contained HTML report for the `mo-grower` / `hibler-farms`
run of the [`eda-field`](../my-farm-advisor/eda/eda-field/) subskill. The ten
gallery images are inlined as base64 PNGs, so the file opens on its own with no
runtime folder and no external assets.

It covers:

- dataset scope, grower location, and the four input layers
- comparison levels (field, field-year, grower, across-grower)
- all ten statistical / comparison figures, with captions
- the one geospatial portfolio map
- limitations, missing data, assumptions, and the planted-record fallback
- an explicit statement that soil analysis was out of scope

Rebuild it after a fresh `eda-field` run:

```bash
python my-farm-advisor/eda/eda-field/scripts/field_eda.py \
  --data-root ~/my-farm-advisor-runtime \
  --grower-slug mo-grower --farm-slug hibler-farms

python reports/build_field_eda_report.py \
  --data-root ~/my-farm-advisor-runtime \
  --grower-slug mo-grower --farm-slug hibler-farms
```

The script reads `run_summary.json` and the CSV tables from
`.../derived/analysis/eda-field/`, resizes each PNG, and writes the HTML beside
itself. Runtime PNGs and CSVs stay out of Git; only this report and its builder
are committed.
