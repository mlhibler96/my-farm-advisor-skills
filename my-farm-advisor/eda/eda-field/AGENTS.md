# Local Instructions

## Purpose

This folder owns the field-level EDA workflow: farm boundaries, CDL composition,
and weather tables in, ten static figures plus their CSVs out. It is the
data-foundation EDA that other farm skills build on, not a report generator.

## Safe edit scope

Edits stay in this folder and its children unless the user asks for a broader
skill change. Do not change parent `SKILL.md`, sibling `eda-*` workflows, or root
policy from a subskill task. The router rows in `../INDEX.md` and
`../../SKILL.md` are the only parent files this subskill may touch, and only to
point at this folder.

## Read nearby docs first

Read `GUIDE.md` for the workflow and `SPEC.md` for the artifact contract before
changing a figure, CSV column, or summary key. Read `../INDEX.md` and
`../../SKILL.md` when routing context is needed.

## Local validation

```bash
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  my-farm-advisor/eda/eda-field/scripts/tests/smoke_field_eda.py
```

The smoke test is offline: it builds the synthetic fixture, runs all three
categories, exercises the cross-grower path with a second synthetic grower, and
checks the guard notes. Run it after any change to `field_eda.py`,
`build_fixture.py`, or a summary key.

Then run `./scripts/validate.sh` from the repository root.

## Figure gate

Four figures are committed under `examples/output/`. A change to
`field_eda.py` re-triggers vision review for all four per `../../../CONTRIBUTING.md`;
record the verdict in the commit message. Regenerate with:

```bash
"${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
  my-farm-advisor/eda/eda-field/examples/render_examples.py
```

## Boundaries of this subskill

- No soil analysis.
- No interactive dashboard.
- No CDL raster map (the raster is not ingested).
- Report assembly is out of scope; this folder produces numbers and figures.
- Runtime outputs stay under the farm's `derived/analysis/eda-field/`; only the
  four demo PNGs belong in Git.

## Local-delta-only reminder

This nested AGENTS.md records only what differs from the parent or root files.
Root policy on assets, vendoring, and validation is in `../../../AGENTS.md`.
