#!/usr/bin/env python3
"""Offline checks for derive_fields_from_as_applied.

Runs without DATA_PIPELINE_DATA_ROOT: only the pure geometry, slug, merge, and
inventory helpers are exercised. Requires the runtime venv (geopandas/shapely).

    "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
        src/scripts/tests/smoke_derive_fields.py
"""

from __future__ import annotations

import importlib.util
import json
import sys
import tempfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.affinity import translate
from shapely.geometry import Point

SCRIPT_PATH = Path(__file__).resolve()
MODULE_PATH = SCRIPT_PATH.parents[1] / "ingest" / "derive_fields_from_as_applied.py"
FARM_METADATA_PATH = SCRIPT_PATH.parents[1] / "lib" / "farm_metadata.py"


class SmokeFailure(AssertionError):
    """Raised when a smoke check fails."""


def _load_module():
    spec = importlib.util.spec_from_file_location("derive_fields_from_as_applied", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise SmokeFailure(f"cannot load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _load_farm_metadata():
    spec = importlib.util.spec_from_file_location("farm_metadata", FARM_METADATA_PATH)
    if spec is None or spec.loader is None:
        raise SmokeFailure(f"cannot load {FARM_METADATA_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def check_farm_state() -> None:
    module = _load_farm_metadata()
    if module.state_code_from_fips("29") != "MO":
        raise SmokeFailure("state_code_from_fips('29') should be MO")
    if module.state_code_from_fips(None) != "":
        raise SmokeFailure("state_code_from_fips(None) should be empty")
    if module.infer_farm_state(Path("/nonexistent/boundary.geojson"), prior_state="ia") != "IA":
        raise SmokeFailure("existing state should win and be upper-cased")

    with tempfile.TemporaryDirectory() as tmp:
        boundary = Path(tmp) / "field_boundaries.geojson"
        boundary.write_text(
            json.dumps(
                {
                    "type": "FeatureCollection",
                    "features": [
                        {"type": "Feature", "properties": {"state_fips": "29"}, "geometry": None},
                        {"type": "Feature", "properties": {"state_fips": "29"}, "geometry": None},
                        {"type": "Feature", "properties": {}, "geometry": None},
                    ],
                }
            ),
            encoding="utf-8",
        )
        if module.infer_farm_state(boundary) != "MO":
            raise SmokeFailure("modal state_fips 29 should resolve to MO")
        if module.infer_farm_state(boundary, prior_state="IA") != "MO":
            raise SmokeFailure("boundary evidence should win over a stale prior state")

        empty = Path(tmp) / "empty.geojson"
        empty.write_text('{"type": "FeatureCollection", "features": []}', encoding="utf-8")
        if module.infer_farm_state(empty) != "":
            raise SmokeFailure("no state evidence should resolve to an empty string")
        if module.infer_farm_state(empty, prior_state="ia") != "IA":
            raise SmokeFailure("prior state should be the fallback when boundaries are empty")


def _grid_points(module, *, x_max: float, y_max: float, spacing: float = 6.0):
    coords = [
        (x, y)
        for x in range(0, int(x_max) + 1, int(spacing))
        for y in range(0, int(y_max) + 1, int(spacing))
    ]
    return gpd.GeoSeries([Point(x, y) for x, y in coords], crs="EPSG:32615")


def check_slugs(module) -> None:
    cases = {
        "23ac North Brady": "23ac-north-brady",
        "10acM&D": "10ac-m-d",
        "10acr DebClem": "10acr-debclem",
        "12ac FLot": "12ac-flot",
        "17ac IWillis": "17ac-iwillis",
        "12ac_Kev": "12ac-kev",
    }
    for raw, expected in cases.items():
        actual = module.slugify_field_name(raw)
        if actual != expected:
            raise SmokeFailure(f"slug for {raw!r}: expected {expected!r}, got {actual!r}")


def check_derive(module) -> None:
    points = _grid_points(module, x_max=100, y_max=60)
    geometry, metrics = module.derive_boundary(points, swath_ft=30.0, close_m=3.0, simplify_m=0.75)

    # 30 ft swath = 9.144 m, so the covered rectangle is ~(100+9.1) x (60+9.1) m.
    expected_m2 = (100 + 9.144) * (60 + 9.144)
    expected_acres = expected_m2 / module.SQM_PER_ACRE
    tolerance = 0.10
    if abs(metrics["final_area_acres"] - expected_acres) / expected_acres > tolerance:
        raise SmokeFailure(
            f"derived area {metrics['final_area_acres']:.3f} ac outside "
            f"{tolerance:.0%} of expected {expected_acres:.3f} ac"
        )
    if metrics["part_count"] != 1:
        raise SmokeFailure(f"expected a single part, got {metrics['part_count']}")
    if metrics["dropped_fragments_acres"]:
        raise SmokeFailure(f"unexpected fragment drops: {metrics['dropped_fragments_acres']}")
    if not geometry.is_valid:
        raise SmokeFailure("derived geometry is invalid")


def check_fragment_drop(module) -> None:
    points = _grid_points(module, x_max=60, y_max=60)
    isolated = pd.concat(
        [points, gpd.GeoSeries([Point(500, 500)], crs=points.crs)], ignore_index=True
    )
    points = gpd.GeoSeries(isolated, crs=points.crs)
    _, metrics = module.derive_boundary(
        points, swath_ft=30.0, close_m=3.0, simplify_m=0.75, min_part_acres=0.5
    )
    if metrics["part_count"] != 1:
        raise SmokeFailure(f"isolated point was not dropped: {metrics['part_count']} parts")
    expected_drop = (9.144 * 9.144 * 3.141592653589793 / 4) / module.SQM_PER_ACRE
    dropped = metrics["dropped_fragments_acres"]
    if len(dropped) != 1 or abs(dropped[0] - expected_drop) / expected_drop > 0.5:
        raise SmokeFailure(f"unexpected dropped fragment record: {dropped}")


def _feature(module, field_id: str, area_acres: float, *, x: float = 0.0):
    points = _grid_points(module, x_max=60, y_max=60)
    if x:
        points = points.apply(lambda geom: translate(geom, xoff=x))
    geometry, _ = module.derive_boundary(points, swath_ft=30.0)
    return {
        "field_id": field_id,
        "field_name": field_id,
        "area_acres": round(area_acres, 3),
        "crop_name": "Corn",
        "state_fips": "29",
        "country": "US",
        "source": "agfiniti",
        "agfiniti_farm": "Test",
        "geometry": geometry,
    }


def check_merge(module) -> None:
    existing = gpd.GeoDataFrame(
        [_feature(module, "aa-field", 1.5)], geometry="geometry", crs="EPSG:32615"
    )
    new = gpd.GeoDataFrame(
        [_feature(module, "aa-field", 1.5), _feature(module, "bb-field", 1.5, x=500)],
        geometry="geometry",
        crs="EPSG:32615",
    )

    merged = module.merge_boundary_features(existing, new)
    if len(merged) != 2:
        raise SmokeFailure(f"merge should yield 2 fields, got {len(merged)}")
    if merged["field_id"].duplicated().any():
        raise SmokeFailure("merge produced duplicate field_id values")

    again = module.merge_boundary_features(merged, new)
    if len(again) != 2:
        raise SmokeFailure(f"re-merge is not idempotent: {len(again)} fields")

    drifting = gpd.GeoDataFrame(
        [_feature(module, "aa-field", 3.0)], geometry="geometry", crs="EPSG:32615"
    )
    try:
        module.merge_boundary_features(existing, drifting)
    except module.DeriveError:
        pass
    else:
        raise SmokeFailure("expected DeriveError when replacing a field with a >2% area change")
    module.merge_boundary_features(existing, drifting, force=True)


def check_inventory(module) -> None:
    lines = ["field_id,field_slug", "14ac,14ac"]
    once = module.update_inventory_lines(lines, [("14ac", "14ac"), ("10ac-m-d", "10ac-m-d")])
    twice = module.update_inventory_lines(once, [("10ac-m-d", "10ac-m-d")])
    if once != ["field_id,field_slug", "14ac,14ac", "10ac-m-d,10ac-m-d"]:
        raise SmokeFailure(f"unexpected inventory rows: {once}")
    if once != twice:
        raise SmokeFailure("inventory update is not idempotent")


def check_overlaps(module) -> None:
    overlapping = gpd.GeoDataFrame(
        [
            _feature(module, "aa-field", 1.5),
            _feature(module, "bb-field", 1.5, x=10),
        ],
        geometry="geometry",
        crs="EPSG:32615",
    )
    if not module.find_overlaps(overlapping):
        raise SmokeFailure("expected an overlap between shifted copies")

    separate = gpd.GeoDataFrame(
        [
            _feature(module, "aa-field", 1.5),
            _feature(module, "bb-field", 1.5, x=5000),
        ],
        geometry="geometry",
        crs="EPSG:32615",
    )
    if module.find_overlaps(separate):
        raise SmokeFailure("did not expect an overlap between distant fields")


def main() -> int:
    module = _load_module()
    check_slugs(module)
    check_derive(module)
    check_fragment_drop(module)
    check_merge(module)
    check_inventory(module)
    check_overlaps(module)
    check_farm_state()
    print("PASS: derive_fields_from_as_applied smoke checks")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except SmokeFailure as exc:
        print(f"FAIL: {exc}")
        raise SystemExit(1)
