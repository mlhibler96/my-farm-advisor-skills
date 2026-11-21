#!/usr/bin/env python3
# ruff: noqa: E402
"""Derive field boundaries from as-applied planting/harvest point exports.

Operations-center exports (John Deere / AgFiniti style) arrive as point
shapefiles with one as-applied pass per file. They carry ``Swth_Wdth_`` (the
implement width in feet) but no field outline, so the boundary has to be
reconstructed:

1. buffer every point by half the documented swath width,
2. union the buffers,
3. close the union with ``buffer(+close).buffer(-close)`` to bridge pass gaps,
4. simplify by ``simplify_m`` metres,
5. drop disconnected fragments below ``min_part_acres``.

The method is deterministic: the same inputs and parameters reproduce the same
polygon. It excludes headlands the implement never covered, so derived acreage
runs a few percent under the nominal field name. That is expected and recorded
in the provenance manifest.

Writes the farm boundary GeoJSON (merged by ``field_id``), the field inventory
CSV, and a provenance manifest. Everything lands under
``${DATA_PIPELINE_DATA_ROOT}/data-pipeline``; nothing is written to the
checkout.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import sys
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import pandas as pd
from shapely.geometry import MultiPolygon, Polygon
from shapely.ops import unary_union

SQM_PER_ACRE = 4046.8564224
FEET_PER_METRE = 3.280839895013123
DEFAULT_WORKING_CRS = "EPSG:32615"
DEFAULT_SOURCE_LABEL = "agfiniti"

_ACREAGE_PREFIX = re.compile(r"^(?P<prefix>\d+acr?)(?P<rest>.*)$", re.IGNORECASE)
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


class DeriveError(RuntimeError):
    """Raised when an input cannot be turned into a trustworthy boundary."""


# --------------------------------------------------------------------------
# pure helpers (unit-tested without a runtime root)
# --------------------------------------------------------------------------


def slugify_field_name(name: str) -> str:
    """Return the house field slug, e.g. ``23ac North Brady`` -> ``23ac-north-brady``.

    Keeps the acreage prefix as its own token so ``10acM&D`` becomes
    ``10ac-m-d`` rather than ``10acm-d``.
    """
    text = str(name or "").strip()
    if not text:
        raise DeriveError("field name is empty")
    match = _ACREAGE_PREFIX.match(text)
    if match:
        prefix = match.group("prefix").lower()
        rest = match.group("rest")
        rest_slug = _NON_ALNUM.sub("-", rest.lower()).strip("-")
        return f"{prefix}-{rest_slug}" if rest_slug else prefix
    return _NON_ALNUM.sub("-", text.lower()).strip("-")


def modal_swath_ft(values: pd.Series) -> float:
    """Return the most common documented swath width in feet."""
    numeric = pd.to_numeric(values, errors="coerce").dropna()
    numeric = numeric[numeric > 0]
    if numeric.empty:
        raise DeriveError("no positive Swth_Wdth_ values; pass --swath-ft")
    return float(numeric.mode().iloc[0])


def acres(geom) -> float:
    return float(geom.area) / SQM_PER_ACRE


def _parts(geom) -> list[Polygon]:
    if geom.is_empty:
        return []
    if isinstance(geom, Polygon):
        return [geom]
    if isinstance(geom, MultiPolygon):
        return list(geom.geoms)
    return [g for g in getattr(geom, "geoms", []) if isinstance(g, Polygon)]


def derive_boundary(
    points: gpd.GeoSeries,
    *,
    swath_ft: float,
    close_m: float = 3.0,
    simplify_m: float = 0.75,
    min_part_acres: float = 0.5,
) -> tuple[Polygon | MultiPolygon, dict[str, float]]:
    """Reconstruct one field polygon from projected as-applied points."""
    if points.empty:
        raise DeriveError("no point geometry to derive a boundary from")
    if swath_ft <= 0:
        raise DeriveError(f"swath_ft must be positive, got {swath_ft}")

    half_swath_m = (swath_ft / FEET_PER_METRE) / 2.0
    buffered = unary_union([geom.buffer(half_swath_m) for geom in points if geom is not None])
    if buffered.is_empty:
        raise DeriveError("buffered point union is empty")
    buffered = buffered.buffer(0)

    closed = buffered.buffer(close_m).buffer(-close_m).buffer(0) if close_m > 0 else buffered

    simplified = closed.simplify(simplify_m, preserve_topology=True) if simplify_m > 0 else closed
    simplified = simplified.buffer(0)
    if simplified.is_empty:
        simplified = closed

    kept = [part for part in _parts(simplified) if acres(part) >= min_part_acres]
    dropped = [part for part in _parts(simplified) if acres(part) < min_part_acres]
    if not kept:
        # Never return an empty boundary: fall back to the largest part.
        kept = [max(_parts(simplified), key=lambda part: part.area)]

    geometry = unary_union(kept)
    metrics = {
        "points_area_acres": round(acres(buffered), 3),
        "closed_area_acres": round(acres(closed), 3),
        "final_area_acres": round(acres(geometry), 3),
        "dropped_fragments_acres": [round(acres(part), 3) for part in dropped],
        "part_count": len(_parts(geometry)),
        "vertex_count": int(len(getattr(geometry, "exterior", geometry).coords))
        if isinstance(geometry, Polygon)
        else int(sum(len(part.exterior.coords) for part in _parts(geometry))),
    }
    return geometry, metrics


def merge_boundary_features(
    existing: gpd.GeoDataFrame | None,
    new: gpd.GeoDataFrame,
    *,
    force: bool = False,
) -> gpd.GeoDataFrame:
    """Merge new field features into an existing farm boundary by ``field_id``."""
    if new.empty:
        raise DeriveError("no new boundary features to merge")
    if new["field_id"].duplicated().any():
        dupes = sorted(new.loc[new["field_id"].duplicated(), "field_id"].unique())
        raise DeriveError(f"duplicate field_id values in new features: {dupes}")

    if existing is None or existing.empty:
        merged = new.copy()
    else:
        prior = existing.set_index("field_id")
        rows = []
        for _, row in new.iterrows():
            field_id = str(row["field_id"])
            if field_id in prior.index:
                previous = prior.loc[field_id]
                previous_area = float(previous.get("area_acres") or 0.0)
                new_area = float(row.get("area_acres") or 0.0)
                if previous_area and abs(new_area - previous_area) / previous_area > 0.02 and not force:
                    raise DeriveError(
                        f"{field_id} already exists with {previous_area:.2f} ac but the "
                        f"derivation yields {new_area:.2f} ac; pass --force to replace it"
                    )
            rows.append(row)
        replaced_ids = {str(row["field_id"]) for row in rows}
        keep = existing[~existing["field_id"].astype(str).isin(replaced_ids)]
        merged = gpd.GeoDataFrame(
            pd.concat([keep, gpd.GeoDataFrame(rows, geometry="geometry", crs=new.crs)],
                      ignore_index=True),
            geometry="geometry",
            crs=new.crs,
        )

    if merged["field_id"].duplicated().any():
        raise DeriveError("merged boundary still has duplicate field_id values")
    return merged.sort_values("field_id").reset_index(drop=True)


def update_inventory_lines(existing_lines: list[str], additions: list[tuple[str, str]]) -> list[str]:
    """Append ``field_id,field_slug`` rows that are not already present."""
    header = existing_lines[0].strip() if existing_lines else "field_id,field_slug"
    body = [line.strip() for line in existing_lines[1:] if line.strip()] if existing_lines else []
    seen = {line.split(",")[0].strip() for line in body if "," in line}
    for field_id, field_slug in additions:
        if field_id in seen:
            continue
        body.append(f"{field_id},{field_slug}")
        seen.add(field_id)
    return [header, *body]


def build_provenance(
    *,
    grower: str,
    fields: list[dict],
    close_m: float,
    simplify_m: float,
    min_part_acres: float,
    field_count: int,
    total_area_acres: float,
    overlaps: list[dict],
) -> dict:
    return {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "grower": grower,
        "field_count": field_count,
        "total_area_acres": round(total_area_acres, 3),
        "parameters": {
            "close_m": close_m,
            "simplify_m": simplify_m,
            "min_part_acres": min_part_acres,
        },
        "method": (
            "as-applied point buffer union (half documented swath), "
            f"close {close_m} m, simplify {simplify_m} m"
        ),
        "metric_definitions": {
            "points_area_acres": "area of the raw half-swath point-buffer union",
            "closed_area_acres": "after buffer(+close).buffer(-close)",
            "final_area_acres": "after simplify and fragment removal",
        },
        "overlaps": overlaps,
        "warning": (
            "Boundaries are DERIVED from as-applied planting or harvest coverage, not surveyed "
            "or platform-exported field outlines. Expect a few percent area difference from "
            "nominal field acreage; headlands not covered are excluded."
        ),
        "fields": fields,
    }


def find_overlaps(features: gpd.GeoDataFrame) -> list[dict]:
    """Report pairwise polygon overlaps above a tenth of an acre."""
    overlaps: list[dict] = []
    ids = list(features["field_id"])
    for i, left_id in enumerate(ids):
        left = features.geometry.iloc[i]
        for j in range(i + 1, len(ids)):
            inter = left.intersection(features.geometry.iloc[j]).area
            if inter > 0.1 * SQM_PER_ACRE:
                overlaps.append(
                    {
                        "field_a": str(left_id),
                        "field_b": str(ids[j]),
                        "overlap_acres": round(inter / SQM_PER_ACRE, 3),
                    }
                )
    return overlaps


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--grower-slug", default=os.environ.get("AG_GROWER_SLUG", "default-grower"))
    parser.add_argument("--farm-slug", default=os.environ.get("AG_FARM_SLUG", "default-farm"))
    parser.add_argument("--farm-name", default=os.environ.get("AG_FARM_NAME", "Default Farm"))
    parser.add_argument(
        "--shp-dir",
        type=Path,
        required=True,
        help="Directory searched recursively for as-applied *.shp exports",
    )
    parser.add_argument("--inventory-csv", type=Path, default=None)
    parser.add_argument("--source-label", default=DEFAULT_SOURCE_LABEL)
    parser.add_argument("--working-crs", default=DEFAULT_WORKING_CRS)
    parser.add_argument("--close-m", type=float, default=3.0)
    parser.add_argument("--simplify-m", type=float, default=0.75)
    parser.add_argument("--min-part-acres", type=float, default=0.5)
    parser.add_argument("--swath-ft", type=float, default=None, help="Override the modal Swth_Wdth_")
    parser.add_argument("--state-fips", default=None, help="Two-digit state FIPS for new features")
    parser.add_argument("--country", default="US")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--force", action="store_true")
    return parser.parse_args(argv)


def _feature_rows(records: list[dict], working_crs: str) -> gpd.GeoDataFrame:
    rows = []
    for record in records:
        rows.append(
            {
                "field_id": record["field_slug"],
                "field_name": record["field_name"],
                "area_acres": record["final_area_acres"],
                "crop_name": record["crop"],
                "state_fips": record.get("state_fips"),
                "country": record.get("country"),
                "source": f"{record['source_label']}_as_applied_derived",
                "agfiniti_farm": record["agfiniti_farm"],
                "geometry": record["geometry"],
            }
        )
    frame = gpd.GeoDataFrame(rows, geometry="geometry", crs=working_crs)
    return frame.to_crs("EPSG:4326")


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    scripts_dir = Path(__file__).resolve().parent.parent
    sys.path.insert(0, str(scripts_dir))
    sys.path.insert(0, str(scripts_dir / "lib"))

    from paths import ensure_parent, farm_boundary_path, farm_manifest_dir
    from reporting_bootstrap import ensure_canonical_data_tree, field_slug_map_from_inventory

    shp_dir = args.shp_dir.expanduser().resolve(strict=False)
    if not shp_dir.is_dir():
        print(f"ERROR: --shp-dir is not a directory: {shp_dir}")
        return 2
    shapefiles = sorted(shp_dir.rglob("*.shp"))
    if not shapefiles:
        print(f"ERROR: no shapefiles under {shp_dir}")
        return 2

    inventory_path = (
        args.inventory_csv.expanduser()
        if args.inventory_csv
        else farm_manifest_dir(args.grower_slug, args.farm_slug) / "field-inventory.csv"
    )
    if not args.dry_run:
        ensure_canonical_data_tree(
            grower_slug=args.grower_slug,
            farm_slug=args.farm_slug,
            farm_name=args.farm_name,
            inventory_path=inventory_path if inventory_path.exists() else None,
        )

    boundary_path = farm_boundary_path(args.grower_slug, args.farm_slug)
    existing = gpd.read_file(boundary_path).to_crs("EPSG:4326") if boundary_path.exists() else None
    existing_slugs = (
        set(existing["field_id"].astype(str)) if existing is not None else set()
    )
    inventory_slugs = set(field_slug_map_from_inventory(inventory_path).values()) if inventory_path.exists() else set()

    records: list[dict] = []
    for shp in shapefiles:
        frame = gpd.read_file(shp)
        if frame.empty:
            print(f"  skip (empty): {shp.name}")
            continue
        if frame.crs is None:
            raise DeriveError(f"{shp.name} has no CRS; reproject before deriving")
        required = {"Field", "Crop_Type"}
        missing = required - set(frame.columns)
        if missing:
            raise DeriveError(f"{shp.name} is missing required columns: {sorted(missing)}")

        field_name = str(frame["Field"].iloc[0]).strip()
        field_slug = slugify_field_name(field_name)
        if field_slug in inventory_slugs - existing_slugs and field_slug not in existing_slugs:
            if not args.force:
                raise DeriveError(
                    f"{shp.name} slugs to '{field_slug}', which the inventory already maps "
                    "to another field; pass --force to replace it"
                )
        if field_slug in existing_slugs and not args.force:
            prior = existing.loc[existing["field_id"].astype(str) == field_slug].iloc[0]
            print(
                f"  note: {field_slug} already exists "
                f"({float(prior.get('area_acres') or 0):.2f} ac); re-deriving"
            )

        swath = args.swath_ft or modal_swath_ft(frame.get("Swth_Wdth_", pd.Series(dtype=float)))
        projected = frame.to_crs(args.working_crs)
        geometry, metrics = derive_boundary(
            projected.geometry,
            swath_ft=swath,
            close_m=args.close_m,
            simplify_m=args.simplify_m,
            min_part_acres=args.min_part_acres,
        )

        record = {
            "source_file": shp.name,
            "source_sha256": _sha256(shp),
            "source_crs": str(frame.crs),
            "grower": str(frame["Grower"].iloc[0]) if "Grower" in frame else args.farm_name,
            "agfiniti_farm": str(frame["Farm"].iloc[0]) if "Farm" in frame else "",
            "field_name": field_name,
            "field_slug": field_slug,
            "crop": str(frame["Crop_Type"].iloc[0]),
            "year": str(frame["Year"].iloc[0]) if "Year" in frame else "",
            "operation": str(frame["Operation"].iloc[0]) if "Operation" in frame else "",
            "point_count": int(len(frame)),
            "swath_ft": round(float(swath), 3),
            "method": (
                "as-applied point buffer union (half documented swath), "
                f"close {args.close_m} m, simplify {args.simplify_m} m"
            ),
            "provenance": "derived_from_as_applied_points",
            "source_label": args.source_label,
            "state_fips": args.state_fips,
            "country": args.country,
            "geometry": geometry,
            **metrics,
        }
        records.append(record)
        print(
            f"  {field_slug:20s} {record['crop']:9s} {metrics['final_area_acres']:7.2f} ac "
            f"({metrics['part_count']} part(s), {record['point_count']} pts, {record['swath_ft']} ft)"
        )

    if not records:
        print("ERROR: nothing derived")
        return 2

    new_features = _feature_rows(records, args.working_crs)
    merged = merge_boundary_features(existing, new_features, force=args.force)
    merged = merged.to_crs("EPSG:4326")
    overlaps = find_overlaps(merged.to_crs(args.working_crs))

    additions = [(record["field_slug"], record["field_slug"]) for record in records]
    existing_lines = (
        inventory_path.read_text(encoding="utf-8").splitlines() if inventory_path.exists() else []
    )
    inventory_lines = update_inventory_lines(existing_lines, additions)

    manifest_path = farm_manifest_dir(args.grower_slug, args.farm_slug) / (
        f"{args.source_label}_derived_fields_provenance.json"
    )
    prior_manifest: dict = {}
    if manifest_path.exists():
        try:
            prior_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            prior_manifest = {}
    prior_fields = {
        entry.get("source_file"): entry
        for entry in prior_manifest.get("fields", [])
        if isinstance(entry, dict) and entry.get("source_file")
    }
    merged_entries = dict(prior_fields)
    for record in records:
        merged_entries[record["source_file"]] = {
            key: value for key, value in record.items() if key != "geometry"
        }
    manifest = build_provenance(
        grower=args.farm_name,
        fields=sorted(merged_entries.values(), key=lambda item: str(item.get("field_slug"))),
        close_m=args.close_m,
        simplify_m=args.simplify_m,
        min_part_acres=args.min_part_acres,
        field_count=len(inventory_lines) - 1,
        total_area_acres=float(merged["area_acres"].sum()),
        overlaps=overlaps,
    )
    manifest["agfiniti_farms"] = sorted(
        {str(entry.get("agfiniti_farm", "")) for entry in merged_entries.values() if entry.get("agfiniti_farm")}
    )

    summary = {
        "boundary_path": str(boundary_path),
        "inventory_path": str(inventory_path),
        "provenance_path": str(manifest_path),
        "field_count": len(merged),
        "derived_fields": [record["field_slug"] for record in records],
        "total_area_acres": round(float(merged["area_acres"].sum()), 3),
        "overlaps": overlaps,
        "dry_run": bool(args.dry_run),
    }

    if args.dry_run:
        print(json.dumps(summary, indent=2, sort_keys=True))
        return 0

    ensure_parent(boundary_path)
    merged.to_file(boundary_path, driver="GeoJSON")
    ensure_parent(inventory_path)
    inventory_path.write_text("\n".join(inventory_lines) + "\n", encoding="utf-8")
    ensure_parent(manifest_path)
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    ensure_canonical_data_tree(
        grower_slug=args.grower_slug,
        farm_slug=args.farm_slug,
        farm_name=args.farm_name,
        inventory_path=inventory_path,
    )

    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())
