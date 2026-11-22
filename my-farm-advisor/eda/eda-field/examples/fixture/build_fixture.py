#!/usr/bin/env python3
"""Build a small deterministic fixture for the ``eda-field`` subskill.

Nothing here is real grower data: geometry comes from the committed Iowa example
boundaries already in this repository, and the weather/CDL tables are synthetic.
That keeps demo figures reviewable without putting a customer's fields in Git,
and makes every number in the committed examples reproducible.

    python examples/fixture/build_fixture.py --out /tmp/eda-field-fixture

Writes ``boundaries.geojson``, ``weather.csv``, ``cdl.csv``, ``planted.json``,
and ``fixture_manifest.json`` under ``--out``.

The tables are constructed to make each artifact's story visible:

* four fields alternate corn/soybean, one is continuous corn, one has a
  grass-dominated year (the 30 m edge-mixing case);
* two field names disagree with the measured acreage so the area-ratio figure
  has something to flag;
* CDL purity degrades on the smaller fields, giving the purity-vs-area
  Spearman a real (if synthetic) signal;
* every field in a grower shares one weather cell, so the variance partition
  reports a zero field share by construction — the honest case.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import box

SQM_PER_ACRE = 4046.8564224
REPO_ROOT = Path(__file__).resolve().parents[5]
DEFAULT_SOURCE = (
    REPO_ROOT / "my-farm-advisor" / "field-management" / "field-boundaries"
    / "examples" / "real_10_fields_iowa.geojson"
)

# Per-field CDL sequence design: (dominant crop per year, planted 2026).
# Four clean alternations, one continuous corn, one grass-anomaly field.
SEQUENCES = {
    "fx-01": (["Corn", "Soybeans", "Corn", "Soybeans", "Corn"], "Soybeans"),
    "fx-02": (["Soybeans", "Corn", "Soybeans", "Corn", "Soybeans"], "Corn"),
    "fx-03": (["Corn", "Soybeans", "Corn", "Soybeans", "Corn"], "Soybeans"),
    "fx-04": (["Soybeans", "Corn", "Soybeans", "Corn", "Soybeans"], "Corn"),
    "fx-05": (["Corn", "Corn", "Corn", "Corn", "Corn"], "Corn"),
    "fx-06": (["Soybeans", "Soybeans", "Grass/Pasture", "Soybeans", "Soybeans"], "Soybeans"),
}

YEARS = [2021, 2022, 2023, 2024, 2025]
YEAR_WETNESS = {2021: 1.35, 2022: 1.00, 2023: 0.72, 2024: 1.18, 2025: 1.05}


def build_boundaries(source: Path, count: int) -> gpd.GeoDataFrame:
    count = min(count, len(SEQUENCES))
    # The committed Iowa example spans ~40 km, which turns a portfolio map into
    # six specks. Use the densest six-field cluster (~5.6 x 3.2 km) so the demo
    # map reads like a real multi-parcel farm.
    cluster = [1, 3, 7, 9, 4, 5]
    fields = (
        gpd.read_file(source)
        .to_crs("EPSG:4326")
        .iloc[cluster[:count]]
        .reset_index(drop=True)
    )

    # Make the last field multipart (a farm road cuts it in two) so the shape
    # panel shows part_count > 1 instead of a flat row of 1s.
    geoms = list(fields.geometry)
    if count >= 2:
        split = geoms[-1]
        x0, y0, x1, y1 = split.bounds
        cut_y = y0 + 0.55 * (y1 - y0)
        band = box(x0 - 1, cut_y, x1 + 1, cut_y + 0.05 * (y1 - y0))
        geoms[-1] = split.difference(band)
    fields = fields.set_geometry(gpd.GeoSeries(geoms, index=fields.index, crs=fields.crs))

    projected = fields.to_crs(32615)
    acres = projected.geometry.area / SQM_PER_ACRE

    field_ids = [f"fx-{i:02d}" for i in range(1, count + 1)]
    # The field name leads with an acreage token. Two names deliberately
    # disagree with the measured acreage so the ratio figure has something to
    # flag (index 1 under-draws, index 4 over-draws).
    names = []
    for index, value in enumerate(acres):
        token = value if index not in {1, 4} else value * (0.80 if index == 1 else 1.25)
        # Letters after the token: trailing digits would be mistaken for part
        # of the acreage in names that do not separate the two.
        names.append(f"{round(float(token))}ac-{'abcdef'[index]}")

    return gpd.GeoDataFrame(
        {
            "field_id": field_ids,
            "field_name": names,
            # crop_name is the 2026 planted crop, matching pipeline boundaries.
            "crop_name": [SEQUENCES[field_id][1] for field_id in field_ids],
            "source": "eda-field fixture (synthetic attributes)",
            "state_fips": "19",
            "area_acres": [round(float(value), 3) for value in acres],
            "geometry": fields.geometry.values,
        },
        geometry="geometry",
        crs="EPSG:4326",
    )


def build_weather(boundaries: gpd.GeoDataFrame, *, temp_offset: float, seed: int) -> pd.DataFrame:
    """One shared weather cell for the whole grower, five growing seasons."""
    rng = np.random.default_rng(seed)
    centre = boundaries.to_crs("EPSG:4326").geometry.union_all().centroid
    dates = pd.date_range("2021-01-01", "2025-12-31", freq="D")
    doy = dates.day_of_year.to_numpy()
    seasonal = np.sin(2 * np.pi * (doy - 105) / 365.0)
    tmax = 12.0 + 18.0 * seasonal + rng.normal(0, 2.5, len(dates)) + temp_offset
    tmin = tmax - (10.0 + rng.normal(0, 1.5, len(dates)))

    month_weight = np.array([0.55, 0.6, 0.95, 1.45, 1.6, 1.35, 1.05, 0.9, 0.85, 0.8, 0.7, 0.6])
    wetness = dates.year.map(YEAR_WETNESS).to_numpy()
    amounts = rng.gamma(0.9, 5.0, len(dates)) * month_weight[dates.month.to_numpy() - 1] * wetness
    dry = rng.random(len(dates)) < 0.55
    precip = np.where(dry, 0.0, amounts)

    base = pd.DataFrame(
        {
            "lat": round(float(centre.y), 4),
            "lon": round(float(centre.x), 4),
            "date": dates.strftime("%Y-%m-%d"),
            "T2M": (tmax + tmin) / 2,
            "T2M_MAX": tmax,
            "T2M_MIN": tmin,
            "PRECTOTCORR": precip,
            "ALLSKY_SFC_SW_DWN": np.clip(14 + 8 * seasonal + rng.normal(0, 2, len(dates)), 0, None),
            "RH2M": np.clip(72 + rng.normal(0, 8, len(dates)), 20, 100),
            "WS10M": np.clip(rng.normal(3.5, 1.2, len(dates)), 0.2, None),
        }
    )
    blocks = []
    for field_id in boundaries["field_id"]:
        block = base.copy()
        block.insert(0, "field_id", field_id)
        blocks.append(block)
    return pd.concat(blocks, ignore_index=True)


def build_cdl(boundaries: gpd.GeoDataFrame, seed: int) -> pd.DataFrame:
    """Composition rows with purity that degrades on the smaller fields."""
    rng = np.random.default_rng(seed)
    acres = dict(zip(boundaries["field_id"], boundaries["area_acres"]))
    largest = max(acres.values())
    rows = []
    for field_id, (sequence, _planted) in SEQUENCES.items():
        if field_id not in acres:
            continue
        # Bigger field -> cleaner dominant class (the 30 m edge effect).
        size_factor = float(acres[field_id]) / largest
        for year, crop in zip(YEARS, sequence):
            purity = 96 - 26 * (1 - size_factor) + rng.normal(0, 1.5)
            purity = float(np.clip(purity, 62, 98))
            remainder = 100 - purity
            grass = remainder * (0.62 if crop != "Grass/Pasture" else 0.0)
            other = remainder - grass
            rows.append(
                {"field_id": field_id, "year": year, "crop_code": _code(crop),
                 "crop_name": crop, "pixel_count": int(round(purity)), "pct": round(purity, 2),
                 "crop_source": "synthetic-fixture"}
            )
            if grass > 0.5:
                rows.append(
                    {"field_id": field_id, "year": year, "crop_code": 176,
                     "crop_name": "Grass/Pasture", "pixel_count": int(round(grass)),
                     "pct": round(grass, 2), "crop_source": "synthetic-fixture"}
                )
            if other > 0.5:
                rows.append(
                    {"field_id": field_id, "year": year, "crop_code": 131,
                     "crop_name": "Fallow/Idle", "pixel_count": int(round(other)),
                     "pct": round(other, 2), "crop_source": "synthetic-fixture"}
                )
    return pd.DataFrame(rows)


def _code(crop: str) -> int:
    return {"Corn": 1, "Soybeans": 5, "Grass/Pasture": 176, "Winter Wheat": 24}.get(crop, 131)


def build_planted(boundaries: gpd.GeoDataFrame) -> dict:
    entries = []
    for _, row in boundaries.iterrows():
        field_id = str(row["field_id"])
        entries.append(
            {
                "field_slug": field_id,
                "field_name": str(row["field_name"]),
                "crop": SEQUENCES[field_id][1],
            }
        )
    return {"fields": entries, "note": "synthetic fixture planting record"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--out", required=True, type=Path)
    parser.add_argument("--source", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--fields", type=int, default=6)
    parser.add_argument("--temp-offset", type=float, default=0.0, help="Degrees C shift for a second grower")
    parser.add_argument("--seed", type=int, default=7)
    args = parser.parse_args(argv)

    out = args.out.expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)

    boundaries = build_boundaries(args.source, args.fields)
    weather = build_weather(boundaries, temp_offset=args.temp_offset, seed=args.seed)
    cdl = build_cdl(boundaries, seed=args.seed)
    planted = build_planted(boundaries)

    boundaries.to_file(out / "boundaries.geojson", driver="GeoJSON")
    weather.to_csv(out / "weather.csv", index=False)
    cdl.to_csv(out / "cdl.csv", index=False)
    (out / "planted.json").write_text(json.dumps(planted, indent=2) + "\n", encoding="utf-8")
    (out / "fixture_manifest.json").write_text(
        json.dumps(
            {
                "fields": int(len(boundaries)),
                "weather_rows": int(len(weather)),
                "cdl_rows": int(len(cdl)),
                "years": YEARS,
                "temp_offset_c": args.temp_offset,
                "seed": args.seed,
                "source_geometry": str(args.source),
                "synthetic": True,
            },
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    print(f"fixture written: {out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
