#!/usr/bin/env python3
"""Regenerate the committed ``eda-field`` demo figures.

Four figures are committed to ``examples/output/`` because they cover the
whole shape of the subskill — a map, a boundary check, a rotation table, and a
weather curve. The other six artifacts are produced on demand by a real run so
the gallery stays small.

    "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
        examples/render_examples.py

Everything runs from the deterministic synthetic fixture, so re-running this
script is the reproduction recipe for the committed PNGs:
``fixture/build_fixture.py`` writes the tables, ``scripts/field_eda.py`` renders
them, and this script copies the four chosen figures back here.
"""

from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

EXAMPLES_DIR = Path(__file__).resolve().parent
EDA_DIR = EXAMPLES_DIR.parent
BUILDER = EXAMPLES_DIR / "fixture" / "build_fixture.py"
MODULE = EDA_DIR / "scripts" / "field_eda.py"

COMMITTED = [
    "boundaries_portfolio_map.png",
    "boundaries_area_ratio.png",
    "cdl_crop_matrix.png",
    "weather_gdd_curves.png",
]


def run(command: list[str]) -> None:
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise SystemExit(
            "command failed: " + " ".join(command) + "\n" + (result.stderr or result.stdout)[-2000:]
        )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--keep", action="store_true", help="Keep the temporary fixture and full output")
    args = parser.parse_args()

    work = Path(tempfile.mkdtemp(prefix="eda-field-demo-"))
    fixture = work / "fixture"
    out = work / "out"
    try:
        run([sys.executable, str(BUILDER), "--out", str(fixture)])
        run(
            [
                sys.executable, str(MODULE),
                "--boundaries", str(fixture / "boundaries.geojson"),
                "--weather", str(fixture / "weather.csv"),
                "--cdl", str(fixture / "cdl.csv"),
                "--planted", str(fixture / "planted.json"),
                "--out-dir", str(out),
                "--grower-slug", "fixture-grower",
                "--farm-slug", "fixture-farm",
                "--no-cross-grower",
            ]
        )
        target = EXAMPLES_DIR / "output"
        target.mkdir(parents=True, exist_ok=True)
        for name in COMMITTED:
            shutil.copyfile(out / name, target / name)
            print(f"wrote {target / name}")
    finally:
        if args.keep:
            print(f"kept working directory: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
