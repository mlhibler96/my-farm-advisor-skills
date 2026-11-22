#!/usr/bin/env python3
# pyright: reportMissingImports=false, reportAttributeAccessIssue=false, reportGeneralTypeIssues=false
"""Generate a lightweight interactive web map for each grower.

The map is grower-level: every farm and every field under
``growers/<grower>/`` is drawn on an online satellite basemap, with a click
popup carrying grower, farm, and field metadata. Field polygons come from the
same boundary file the pipeline writes; nothing raster is embedded, so the
output stays small (tens of KB for a normal grower).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

import geopandas as gpd
import pandas as pd

_LOCAL_LIB = Path(__file__).resolve().parents[1] / "lib"
sys.path.insert(0, str(_LOCAL_LIB))

from runtime_paths import resolve_runtime_paths  # noqa: E402

_RUNTIME_PATHS = resolve_runtime_paths()
_REPO = _RUNTIME_PATHS.runtime_base
_SCRIPTS = _RUNTIME_PATHS.runtime_scripts
_LIB = _RUNTIME_PATHS.runtime_scripts / "lib"
sys.path.insert(0, str(_SCRIPTS))
sys.path.insert(0, str(_LIB))

from paths import (  # noqa: E402
    GROWERS_ROOT,
    farm_cdl_preferred_full_composition_path,
    farm_dir,
    farm_boundary_path,
    farm_ssurgo_summary_path,
    grower_dir,
    grower_webmap_path,
)
from webmap_html import (  # noqa: E402
    display_name,
    farm_color,
    render_webmap_html,
)

# Coordinate precision: 6 decimals is ~0.1 m, far finer than any field
# boundary, and it keeps the embedded GeoJSON compact.
_COORD_PRECISION = 6
_SOFT_SIZE_BYTES = 1_500_000
_HARD_SIZE_WARN_BYTES = 2_000_000

_USPS_BY_FIPS = {
    "01": "AL", "02": "AK", "04": "AZ", "05": "AR", "06": "CA", "08": "CO",
    "09": "CT", "10": "DE", "11": "DC", "12": "FL", "13": "GA", "15": "HI",
    "16": "ID", "17": "IL", "18": "IN", "19": "IA", "20": "KS", "21": "KY",
    "22": "LA", "23": "ME", "24": "MD", "25": "MA", "26": "MI", "27": "MN",
    "28": "MS", "29": "MO", "30": "MT", "31": "NE", "32": "NV", "33": "NH",
    "34": "NJ", "35": "NM", "36": "NY", "37": "NC", "38": "ND", "39": "OH",
    "40": "OK", "41": "OR", "42": "PA", "44": "RI", "45": "SC", "46": "SD",
    "47": "TN", "48": "TX", "49": "UT", "50": "VT", "51": "VA", "53": "WA",
    "54": "WV", "55": "WI", "56": "WY", "60": "AS", "66": "GU", "69": "MP",
    "72": "PR", "78": "VI",
}


def _read_json(path: Path) -> dict:
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return payload if isinstance(payload, dict) else {}


def _round_coords(value):
    """Round every coordinate pair without touching the GeoJSON structure."""
    if isinstance(value, (list, tuple)):
        if (
            len(value) == 2
            and all(isinstance(v, (int, float)) and not isinstance(v, bool) for v in value)
        ):
            return [round(float(value[0]), _COORD_PRECISION), round(float(value[1]), _COORD_PRECISION)]
        return [_round_coords(item) for item in value]
    return value


def _as_float(value):
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return None if pd.isna(parsed) else parsed


def _cell(row, *names):
    for name in names:
        if name in row and pd.notna(row[name]):
            return row[name]
    return None


def _soil_lookup(grower_slug: str, farm_slug: str) -> dict[str, dict]:
    """Per-field soil summary from the farm SSURGO summary table."""
    path = farm_ssurgo_summary_path(grower_slug, farm_slug)
    if not path.exists():
        return {}
    try:
        frame = pd.read_csv(path)
    except (OSError, ValueError) as exc:
        print(f"  warning: could not read {path.name}: {exc}")
        return {}
    if "field_id" not in frame.columns:
        return {}
    lookup: dict[str, dict] = {}
    for row in frame.to_dict("records"):
        lookup[str(row["field_id"])] = {
            "dominant": _cell(row, "dominant_soil"),
            "drainage": _cell(row, "drainage_class"),
            "om": _as_float(_cell(row, "avg_om_pct")),
            "ph": _as_float(_cell(row, "avg_ph")),
            "aws": _as_float(_cell(row, "total_aws_inches")),
        }
    return lookup


def _crop_lookup(grower_slug: str, farm_slug: str) -> dict[str, dict[int, str]]:
    """Per-field, per-year crop name from the CDL composition table."""
    path = farm_cdl_preferred_full_composition_path(grower_slug, farm_slug)
    if not path.exists():
        return {}
    try:
        frame = pd.read_csv(path)
    except (OSError, ValueError) as exc:
        print(f"  warning: could not read {path.name}: {exc}")
        return {}
    required = {"field_id", "year", "crop_name"}
    if not required.issubset(frame.columns):
        return {}
    if "pct" in frame.columns:
        # One dominant crop per field-year; drop the scattered minority rows.
        frame = frame.sort_values("pct", ascending=False).drop_duplicates(
            ["field_id", "year"]
        )
    lookup: dict[str, dict[int, str]] = {}
    for row in frame.to_dict("records"):
        field_id = str(row["field_id"])
        try:
            year = int(row["year"])
        except (TypeError, ValueError):
            continue
        crop = row.get("crop_name")
        if not isinstance(crop, str) or not crop.strip():
            continue
        lookup.setdefault(field_id, {})[year] = crop.strip()
    return lookup


def _state_label(field_row, farm_meta: dict) -> str | None:
    fips = _cell(field_row, "state_fips")
    if fips is not None:
        key = str(fips).strip()
        if key.isdigit():
            key = key.zfill(2)
        if key in _USPS_BY_FIPS:
            return _USPS_BY_FIPS[key]
    state = farm_meta.get("state")
    if isinstance(state, str) and state.strip():
        return state.strip()
    return None


def _discover_growers(requested: list[str] | None) -> list[str]:
    if requested:
        return sorted({slug for slug in requested if slug})
    if not GROWERS_ROOT.exists():
        return []
    return sorted(
        path.name
        for path in GROWERS_ROOT.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )


def _discover_farms(grower_slug: str, only_farm: str | None) -> list[str]:
    farms_root = grower_dir(grower_slug) / "farms"
    if not farms_root.exists():
        return []
    slugs = sorted(
        path.name
        for path in farms_root.iterdir()
        if path.is_dir() and not path.name.startswith(".")
    )
    if only_farm:
        slugs = [slug for slug in slugs if slug == only_farm]
    return slugs


def _build_payload(
    grower_slug: str,
    farms_requested: list[str],
    title: str | None,
) -> dict | None:
    grower_meta = _read_json(grower_dir(grower_slug) / "grower.json")
    grower_label = display_name(grower_meta, grower_slug)

    farms_payload: list[dict] = []
    field_records: list[dict] = []
    features: list[dict] = []
    farm_slugs: list[str] = []

    for index, farm_slug in enumerate(farms_requested):
        boundary_path = farm_boundary_path(grower_slug, farm_slug)
        if not boundary_path.exists():
            print(f"  skip {grower_slug}/{farm_slug}: no {boundary_path.name}")
            continue
        try:
            fields = gpd.read_file(boundary_path)
        except Exception as exc:  # noqa: BLE001 - one bad farm must not kill the map
            print(f"  skip {grower_slug}/{farm_slug}: unreadable boundary ({exc})")
            continue
        if fields.empty:
            print(f"  skip {grower_slug}/{farm_slug}: boundary has no features")
            continue
        if fields.crs is not None and str(fields.crs).upper() not in {"EPSG:4326", "OGC:CRS84"}:
            fields = fields.to_crs("EPSG:4326")

        farm_meta = _read_json(farm_dir(grower_slug, farm_slug) / "farm.json")
        farm_label = display_name(farm_meta, farm_slug)
        color = farm_color(len(farm_slugs))
        farm_slugs.append(farm_slug)

        soil = _soil_lookup(grower_slug, farm_slug)
        crops = _crop_lookup(grower_slug, farm_slug)

        geojson = json.loads(fields.to_json(drop_id=True))
        farm_field_count = 0
        for feature in geojson.get("features", []):
            props = feature.get("properties") or {}
            field_id = str(props.get("field_id") or f"field-{len(field_records) + 1}")
            field_slug = str(props.get("field_slug") or field_id)
            field_name = _cell(props, "field_name") or field_slug.replace("-", " ").replace("_", " ")
            area = _as_float(_cell(props, "area_acres"))
            area = round(area, 2) if area is not None else None

            crop_years = crops.get(field_id, {})
            latest_year = max(crop_years) if crop_years else None
            latest_crop = crop_years.get(latest_year) if latest_year else _cell(props, "crop_name")

            key = f"f{len(field_records) + 1}"
            feature["properties"] = {"key": key, "farm": farm_slug}
            features.append(feature)
            field_records.append(
                {
                    "key": key,
                    "id": field_id,
                    "slug": field_slug,
                    "name": str(field_name),
                    "acres": area,
                    "crop": latest_crop if isinstance(latest_crop, str) else None,
                    "cropYear": latest_year,
                    "crops": {str(year): crop for year, crop in sorted(crop_years.items())},
                    "grower": grower_label,
                    "farm": farm_label,
                    "farmSlug": farm_slug,
                    "state": _state_label(props, farm_meta),
                    "soil": soil.get(field_id),
                }
            )
            farm_field_count += 1

        if not farm_field_count:
            continue
        farms_payload.append(
            {
                "slug": farm_slug,
                "label": farm_label,
                "color": color,
                "fieldCount": farm_field_count,
            }
        )
        print(f"  {farm_label}: {farm_field_count} fields")

    if not field_records:
        return None

    # Geometry is rounded, then simplified only if the payload would otherwise
    # be large (a grower with hundreds of detailed boundaries).
    geojson = {"type": "FeatureCollection", "features": _round_coords(features)}
    payload = {
        "title": title or f"{grower_label} field map",
        "generated": date.today().isoformat(),
        "grower": {"slug": grower_slug, "label": grower_label},
        "farms": farms_payload,
        "fields": field_records,
        "geojson": geojson,
        "bounds": _bounds_from_features(geojson["features"]),
        "stats": {
            "fields": len(field_records),
            "farms": len(farms_payload),
            "acres": round(
                sum(f["acres"] for f in field_records if isinstance(f["acres"], (int, float))),
                1,
            ),
        },
    }
    size = len(json.dumps(payload, separators=(",", ":")))
    if size > _SOFT_SIZE_BYTES:
        simplified = _simplify_features(geojson, tolerance=1e-5)
        if simplified is not None:
            payload["geojson"] = simplified
            payload["bounds"] = _bounds_from_features(payload["geojson"]["features"])
    return payload


def _bounds_from_features(features) -> list[list[float]]:
    xs: list[float] = []
    ys: list[float] = []
    for feature in features:
        for x, y in _iter_coords(feature.get("geometry") or {}):
            xs.append(x)
            ys.append(y)
    if not xs or not ys:
        return []
    return [[min(ys), min(xs)], [max(ys), max(xs)]]


def _iter_coords(geometry):
    kind = geometry.get("type")
    coords = geometry.get("coordinates") or []
    if kind == "Point":
        if len(coords) >= 2:
            yield coords[0], coords[1]
        return
    if kind in {"LineString", "MultiPoint"}:
        for point in coords:
            if len(point) >= 2:
                yield point[0], point[1]
        return
    if kind in {"Polygon", "MultiLineString"}:
        for ring in coords:
            for point in ring:
                if len(point) >= 2:
                    yield point[0], point[1]
        return
    if kind == "MultiPolygon":
        for polygon in coords:
            for ring in polygon:
                for point in ring:
                    if len(point) >= 2:
                        yield point[0], point[1]


def _simplify_features(geojson: dict, tolerance: float):
    """Return a simplified copy of the features, or ``None`` if unavailable."""
    try:
        from shapely.geometry import mapping, shape
    except ImportError:
        return None
    features = []
    for feature in geojson["features"]:
        geometry = feature.get("geometry")
        if not geometry:
            continue
        try:
            simplified = shape(geometry).simplify(tolerance, preserve_topology=True)
        except Exception:  # noqa: BLE001 - keep the original geometry
            continue
        if simplified.is_empty:
            continue
        features.append(
            {
                "type": "Feature",
                "properties": feature["properties"],
                "geometry": _round_coords(mapping(simplified)),
            }
        )
    return {"type": "FeatureCollection", "features": features} if features else None


def _write_map(payload: dict, output_path: Path, title: str | None) -> int:
    html = render_webmap_html(payload, title=title)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    # Write-and-replace so a half-written map never replaces a good one.
    temp_path = output_path.with_suffix(output_path.suffix + ".tmp")
    temp_path.write_text(html, encoding="utf-8")
    temp_path.replace(output_path)
    return len(html.encode("utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Generate an interactive field map for each grower"
    )
    parser.add_argument(
        "--grower-slug",
        action="append",
        default=None,
        help="Grower slug to map (repeatable). Defaults to every grower on disk.",
    )
    parser.add_argument(
        "--farm-slug",
        default=None,
        help="Limit the map to a single farm slug inside the selected grower(s).",
    )
    parser.add_argument(
        "--output",
        default=None,
        help="Output HTML path. Only valid with a single --grower-slug.",
    )
    parser.add_argument("--title", default=None, help="Override the map title.")
    args = parser.parse_args()

    if args.output and not (args.grower_slug and len(args.grower_slug) == 1):
        parser.error("--output requires exactly one --grower-slug")

    print("=" * 60)
    print("Grower interactive web map")
    print("=" * 60)

    grower_slugs = _discover_growers(args.grower_slug)
    if not grower_slugs:
        print(f"No growers found under {GROWERS_ROOT}")
        sys.exit(1)

    written = 0
    for grower_slug in grower_slugs:
        print(f"\nGrower: {grower_slug}")
        farms = _discover_farms(grower_slug, args.farm_slug)
        if not farms:
            print("  no farms found")
            continue
        payload = _build_payload(grower_slug, farms, args.title)
        if payload is None:
            print("  no field boundaries available; skipping")
            continue
        output_path = (
            Path(args.output).expanduser().resolve()
            if args.output
            else grower_webmap_path(grower_slug)
        )
        size = _write_map(payload, output_path, args.title)
        stats = payload["stats"]
        print(
            f"  {stats['farms']} farm(s), {stats['fields']} field(s), "
            f"{stats['acres']:.1f} acres"
        )
        print(f"  saved {output_path}")
        print(f"  size  {size / 1024:.1f} KB")
        if size > _HARD_SIZE_WARN_BYTES:
            print(
                "  warning: map is larger than expected; consider a coarser "
                "boundary source or --farm-slug to split it"
            )
        written += 1

    print("\n" + "=" * 60)
    print(f"Grower web maps complete: {written} file(s)")
    print("=" * 60)


if __name__ == "__main__":
    main()
