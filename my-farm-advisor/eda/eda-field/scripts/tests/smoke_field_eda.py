#!/usr/bin/env python3
"""Offline smoke checks for the eda-field subskill.

Runs without DATA_PIPELINE_DATA_ROOT: the fixture builder generates synthetic
weather and CDL tables, and geometry comes from the committed Iowa example.
Requires the pipeline virtualenv (geopandas, scipy, matplotlib).

    "${DATA_PIPELINE_DATA_ROOT}/data-pipeline/.venv/bin/python" \
        scripts/tests/smoke_field_eda.py
"""

from __future__ import annotations

import importlib.util
import json
import subprocess
import sys
import tempfile
from pathlib import Path

SCRIPT_PATH = Path(__file__).resolve()
EDA_DIR = SCRIPT_PATH.parents[2]
MODULE_PATH = EDA_DIR / "scripts" / "field_eda.py"
BUILDER_PATH = EDA_DIR / "examples" / "fixture" / "build_fixture.py"
REPO_ROOT = EDA_DIR.parents[2]


class SmokeFailure(AssertionError):
    """Raised when a smoke check fails."""


def _load_module():
    spec = importlib.util.spec_from_file_location("field_eda", MODULE_PATH)
    if spec is None or spec.loader is None:
        raise SmokeFailure(f"cannot load {MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _build_fixture(out: Path, *, temp_offset: float = 0.0) -> None:
    result = subprocess.run(
        [sys.executable, str(BUILDER_PATH), "--out", str(out), "--temp-offset", str(temp_offset)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SmokeFailure(f"fixture builder failed: {result.stderr or result.stdout}")


def _paths(root: Path) -> dict[str, Path]:
    return {
        "boundaries": root / "boundaries.geojson",
        "weather": root / "weather.csv",
        "cdl": root / "cdl.csv",
        "planted": root / "planted.json",
    }


def check_nominal(module) -> None:
    cases = {
        "10acM&D": 10.0,
        "21ac-clay-7": 21.0,
        "14ac": 14.0,
        "12acr DebClem": 12.0,
        "230ac-a": 230.0,
        "7.5ac strip": 7.5,
    }
    for name, expected in cases.items():
        actual = module._nominal_acres(name)
        if actual != expected:
            raise SmokeFailure(f"nominal acres for {name!r}: expected {expected}, got {actual}")


def check_name_fallback(module, root: Path) -> None:
    """A manifest entry without field_slug must still match by field name."""
    doc = json.loads((root / "planted.json").read_text(encoding="utf-8"))
    for entry in doc["fields"]:
        entry.pop("field_slug", None)
    manifest = root / "planted_name_only.json"
    manifest.write_text(json.dumps(doc), encoding="utf-8")

    paths = _paths(root)
    paths["planted"] = manifest
    ds = module.load_dataset(paths, grower_slug="fx-grower", farm_slug="fx-farm")
    if len(ds.planted) != 6:
        raise SmokeFailure(f"name fallback should recover 6 crops, got {len(ds.planted)}")
    if ds.planted.get("fx-01") != "Soybeans":
        raise SmokeFailure(f"name fallback mapped fx-01 to {ds.planted.get('fx-01')!r}")


def check_boundaries(module, root: Path, out: Path) -> dict:
    ds = module.load_dataset(_paths(root), grower_slug="fx-grower", farm_slug="fx-farm")
    summary = module.run_boundaries(ds, out)
    if summary["fields"] != 6:
        raise SmokeFailure(f"expected 6 fields, got {summary['fields']}")
    if summary["findings"]["overlap_pairs"] != 0:
        raise SmokeFailure(f"fixture should have no overlaps: {summary['findings']}")
    if summary["findings"]["multipart_fields"] < 1:
        raise SmokeFailure("expected at least one multipart example boundary")

    metrics = __import__("pandas").read_csv(out / "boundaries_area_ratio.csv")
    flagged = set(metrics.loc[(metrics["ratio_to_name_pct"] - 100).abs() > 15, "field_id"])
    if "fx-02" not in flagged or "fx-05" not in flagged:
        raise SmokeFailure(f"expected fx-02/fx-05 name mismatches, flagged={sorted(flagged)}")
    return summary


def check_cdl(module, root: Path, out: Path) -> dict:
    ds = module.load_dataset(_paths(root), grower_slug="fx-grower", farm_slug="fx-farm")
    verdict = module.run_cdl(ds, out)
    if verdict["fields"] != 6:
        raise SmokeFailure(f"expected 6 fields in CDL verdict, got {verdict['fields']}")
    if verdict["alternating_fields"] != 4:
        raise SmokeFailure(f"expected 4 alternating fields, got {verdict['alternating_fields']}")
    if set(verdict["non_alternating_fields"]) != {"fx-05", "fx-06"}:
        raise SmokeFailure(f"unexpected non-alternating set: {verdict['non_alternating_fields']}")
    if verdict["flips_to_2026"] != 4 or verdict["repeats_to_2026"] != 2:
        raise SmokeFailure(
            f"expected 4 flips / 2 repeats, got {verdict['flips_to_2026']}/{verdict['repeats_to_2026']}"
        )
    per_field = __import__("pandas").read_csv(out / "cdl_rotation.csv")
    outcomes = dict(zip(per_field["field_id"], per_field["outcome"]))
    if outcomes.get("fx-05") != "repeat" or outcomes.get("fx-01") != "flip":
        raise SmokeFailure(f"unexpected rotation outcomes: {outcomes}")
    if verdict["purity_vs_log_area_spearman"] is None:
        raise SmokeFailure("expected a purity-vs-area correlation on the fixture")
    return verdict


def check_weather(module, root: Path, out: Path) -> dict:
    ds = module.load_dataset(_paths(root), grower_slug="fx-grower", farm_slug="fx-farm")
    verdict = module.run_weather(ds, out)
    if verdict["field_share_pct"] != 0.0:
        raise SmokeFailure(f"one weather cell must yield a 0 % field share, got {verdict['field_share_pct']}")
    if verdict["distinct_weather_cells"] != 1:
        raise SmokeFailure(f"expected one weather cell, got {verdict['distinct_weather_cells']}")
    if "guard" not in verdict:
        raise SmokeFailure("single grower must emit a cross-grower guard note")
    if "guard_field" not in verdict:
        raise SmokeFailure("one weather cell must emit a field-replication guard note")
    table = __import__("pandas").read_csv(out / "weather_variance.csv")
    total = float(table["variance_share_pct"].sum())
    if abs(total - 100.0) > 0.5:
        raise SmokeFailure(f"variance shares should sum to 100, got {total}")
    return verdict


def check_cross_grower(module, root: Path, other: Path, out: Path) -> dict:
    ds = module.load_dataset(_paths(root), grower_slug="fx-grower", farm_slug="fx-farm")
    other_ds = module.load_dataset(_paths(other), grower_slug="fx-other", farm_slug="fx-farm")
    verdict = module.run_weather(ds, out, extra_growers={"fx-other": other_ds.weather})
    if verdict["grower_levels"] != 2:
        raise SmokeFailure(f"expected 2 grower levels, got {verdict['grower_levels']}")
    if "guard" in verdict:
        raise SmokeFailure("cross-grower guard must not fire with two growers")
    shared = verdict["field_share_pct"]
    grower_share = float(
        __import__("pandas").read_csv(out / "weather_variance.csv").query("term == 'grower'")["variance_share_pct"].iloc[0]
    )
    if grower_share <= 0:
        raise SmokeFailure(f"two growers with different climates should share >0 variance, got {grower_share}")
    if shared != 0.0:
        raise SmokeFailure(f"field share should stay 0 within a grower, got {shared}")
    return verdict


def check_artifacts(module, root: Path, out: Path) -> None:
    expected = [
        "boundaries_area_ratio.png", "boundaries_shape.png",
        "boundaries_adjacency.png", "boundaries_portfolio_map.png",
        "cdl_crop_matrix.png", "cdl_purity.png", "cdl_rotation.png",
        "weather_gdd_curves.png", "weather_precip_heatmap.png", "weather_variance.png",
    ]
    missing = [name for name in expected if not (out / name).exists()]
    if missing:
        raise SmokeFailure(f"missing artifacts: {missing}")
    tiny = [name for name in expected if (out / name).stat().st_size < 8_000]
    if tiny:
        raise SmokeFailure(f"suspiciously small figures (blank panels?): {tiny}")


def check_cli(module, root: Path, out: Path) -> None:
    result = subprocess.run(
        [
            sys.executable, str(MODULE_PATH),
            "--boundaries", str(root / "boundaries.geojson"),
            "--weather", str(root / "weather.csv"),
            "--cdl", str(root / "cdl.csv"),
            "--planted", str(root / "planted.json"),
            "--out-dir", str(out),
            "--category", "all",
            "--no-cross-grower",
            "--grower-slug", "fx-grower",
            "--farm-slug", "fx-farm",
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        raise SmokeFailure(f"CLI failed: {result.stderr[-1500:]}")
    summary = json.loads((out / "run_summary.json").read_text(encoding="utf-8"))
    for category in ("boundaries", "cdl", "weather"):
        if category not in summary["categories"]:
            raise SmokeFailure(f"CLI summary missing category {category}")


def main() -> int:
    module = _load_module()
    check_nominal(module)
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        root, other = base / "grower-a", base / "grower-b"
        out = base / "out"
        _build_fixture(root)
        _build_fixture(other, temp_offset=1.4)

        check_name_fallback(module, root)
        check_boundaries(module, root, out)
        check_cdl(module, root, out)
        check_weather(module, root, out)
        check_cross_grower(module, root, other, base / "out-cross")
        check_artifacts(module, root, out)
        check_cli(module, root, base / "out-cli")

    print("PASS: eda-field smoke checks")
    return 0


if __name__ == "__main__":
    try:
        main()
    except SmokeFailure as exc:
        print(f"FAIL: {exc}")
        raise SystemExit(1)
