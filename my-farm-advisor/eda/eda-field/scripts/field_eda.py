#!/usr/bin/env python3
"""Field-level EDA for boundary, CDL, and weather artifacts.

This is the reusable half of the ``eda-field`` subskill: every function takes
plain DataFrames and writes a PNG plus the CSV behind it, so a report can be
assembled separately from the numbers rather than re-derived from pictures.

Three categories, three artifacts each, plus one map:

    boundaries  area_ratio, shape, adjacency        + portfolio map
    cdl         crop_matrix, purity, rotation_test
    weather     gdd_curves, precip_heatmap, variance_partition

Scopes: within field over time (CDL/weather), across fields within a grower
(all), across growers (variance partition, rotation consistency) with an
explicit guard when only one grower exists.

Runtime outputs go under
``${DATA_PIPELINE_DATA_ROOT}/data-pipeline/growers/<grower>/farms/<farm>/derived/analysis/eda-field/``.
Nothing is written into the repository by a run.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import geopandas as gpd
import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.transforms import Bbox, offset_copy
import pandas as pd
from scipy import stats

SQM_PER_ACRE = 4046.8564224
GDD_BASE_F = 50.0
GDD_CAP_F = 86.0
SEASON_START = (4, 1)
SEASON_END = (10, 31)

CROP_COLORS = {
    "Corn": "#e5a11f",
    "Soybeans": "#2f7d3a",
    "Grass/Pasture": "#9aa46b",
    "Winter Wheat": "#8c6bb1",
}
FALLBACK_COLOR = "#b9b9b9"


# ---------------------------------------------------------------------------
# environment / inputs
# ---------------------------------------------------------------------------


def pipeline_scripts_dir() -> Path:
    """Locate the data-pipeline ``src/scripts`` tree (runtime first, then repo)."""
    candidates: list[Path] = []
    data_root = os.environ.get("DATA_PIPELINE_DATA_ROOT")
    if data_root:
        candidates.append(Path(data_root).expanduser() / "data-pipeline" / "src" / "scripts")
    here = Path(__file__).resolve()
    # repo checkout layout: my-farm-advisor/eda/eda-field/scripts/field_eda.py
    candidates.append(here.parents[3] / "data-pipeline" / "src" / "scripts")
    for candidate in candidates:
        if (candidate / "lib" / "field_frame.py").exists():
            return candidate
    raise SystemExit(
        "cannot locate data-pipeline/src/scripts; set DATA_PIPELINE_DATA_ROOT "
        "or run from the repository checkout"
    )


def _field_frame_module():
    """Import the skill-locked static map frame helper."""
    lib = pipeline_scripts_dir() / "lib"
    if str(lib) not in sys.path:
        sys.path.insert(0, str(lib))
    from field_frame import crs_note, decorate, frame_for, view_extent  # noqa: PLC0415

    return frame_for, decorate, crs_note, view_extent


@dataclass
class Dataset:
    """Everything one farm-scope run needs, plus provenance for the summary."""

    boundaries: gpd.GeoDataFrame
    weather: pd.DataFrame
    cdl: pd.DataFrame
    planted: dict[str, str] = field(default_factory=dict)
    grower_slug: str = ""
    farm_slug: str = ""
    inputs: dict[str, str] = field(default_factory=dict)


def _latest(glob_dir: Path, pattern: str) -> Path | None:
    matches = sorted(glob_dir.glob(pattern))
    return matches[-1] if matches else None


def resolve_inputs(
    *,
    data_root: str | Path,
    grower_slug: str,
    farm_slug: str,
    boundaries: str | Path | None = None,
    weather: str | Path | None = None,
    cdl: str | Path | None = None,
    planted: str | Path | None = None,
) -> dict[str, Path]:
    """Resolve canonical runtime inputs, allowing explicit overrides."""
    farm_dir = Path(data_root).expanduser() / "data-pipeline" / "growers" / grower_slug / "farms" / farm_slug
    tables = farm_dir / "derived" / "tables"
    manifests = farm_dir / "manifests"

    resolved = {
        "boundaries": Path(boundaries) if boundaries else farm_dir / "boundary" / "field_boundaries.geojson",
        "weather": Path(weather) if weather else _latest(tables, "*_weather_*.csv"),
        "cdl": Path(cdl) if cdl else _latest(tables, "*_cdl_*_full_composition.csv"),
        # Optional: without a planting record the rotation outcome is reported
        # as no_planted_record instead of failing the run.
        "planted": Path(planted) if planted else _latest(manifests, "*_derived_fields_provenance.json"),
    }
    required = ("boundaries", "weather", "cdl")
    missing = [
        f"{key}={resolved[key]}"
        for key in required
        if resolved[key] is None or not Path(resolved[key]).exists()
    ]
    if missing:
        raise SystemExit("missing input(s): " + ", ".join(missing))
    return {key: Path(value) for key, value in resolved.items() if value is not None and Path(value).exists()}


def _normalise_name(value: str) -> str:
    """Match manifest names to boundaries: lowercase, non-alphanumerics -> '-'."""
    return re.sub(r"[^a-z0-9]+", "-", str(value).lower()).strip("-")


def load_dataset(paths: dict[str, Path], *, grower_slug: str = "", farm_slug: str = "") -> Dataset:
    boundaries = gpd.read_file(paths["boundaries"])
    weather = pd.read_csv(paths["weather"])
    cdl = pd.read_csv(paths["cdl"])
    planted: dict[str, str] = {}
    manifest = paths.get("planted")
    if manifest and manifest.exists():
        doc = json.loads(manifest.read_text(encoding="utf-8"))
        boundary_ids = {str(value) for value in boundaries.get("field_id", [])}
        # Some provenance manifests carry a crop but no field_slug (older
        # derive passes). Fall back to the field name, which is what both
        # sides share, and only when it matches exactly one boundary.
        name_lookup: dict[str, str] = {}
        if "field_name" in boundaries.columns:
            counts: dict[str, list[str]] = {}
            for field_id, field_name in zip(boundaries["field_id"], boundaries["field_name"]):
                counts.setdefault(_normalise_name(field_name), []).append(str(field_id))
            name_lookup = {key: ids[0] for key, ids in counts.items() if len(ids) == 1}
        priority: dict[str, int] = {}
        for entry in doc.get("fields", []):
            crop = entry.get("crop")
            slug = entry.get("field_slug")
            if not slug or str(slug) not in boundary_ids:
                slug = name_lookup.get(_normalise_name(entry.get("field_name", "")))
            if not slug or not crop:
                continue
            # If both a planting and a harvest record exist for one year, the
            # planting record wins; otherwise the entry order decides.
            rank = 2 if str(entry.get("operation", "")).lower() == "planting" else 1
            key = str(slug)
            if key not in planted or rank >= priority.get(key, 0):
                planted[key] = str(crop)
                priority[key] = rank
    return Dataset(
        boundaries=boundaries,
        weather=weather,
        cdl=cdl,
        planted=planted,
        grower_slug=grower_slug,
        farm_slug=farm_slug,
        inputs={key: str(value) for key, value in paths.items()},
    )


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def _projected(fields: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """Project to the UTM zone of the centroid — never measure in degrees."""
    lonlat = fields.to_crs("EPSG:4326")
    centre = lonlat.geometry.union_all().centroid
    zone = int((centre.x + 180) // 6) + 1
    epsg = (32600 if centre.y >= 0 else 32700) + zone
    return fields.to_crs(epsg)


def _parts(geom) -> list:
    if geom.geom_type == "MultiPolygon":
        return list(geom.geoms)
    return [geom]


def _nominal_acres(field_name: str) -> float:
    """Leading acreage token in a field name (``10acM&D`` -> 10).

    Only the LEADING number counts. Older field names put the token first
    (``21ac-clay-7``), so trailing digits belong to the name, not the acreage.
    """
    match = re.match(r"\s*(\d+(?:\.\d+)?)", str(field_name))
    return float(match.group(1)) if match else float("nan")


def _save(fig, png: Path) -> None:
    png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(png, bbox_inches="tight")
    plt.close(fig)


def _write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)


def _crop_color(crop: str) -> str:
    return CROP_COLORS.get(str(crop), FALLBACK_COLOR)


# ---------------------------------------------------------------------------
# boundaries
# ---------------------------------------------------------------------------


def boundary_metrics(fields: gpd.GeoDataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Per-field geometry metrics plus a pairwise adjacency table.

    The adjacency table separates two different questions: how much boundary two
    fields share (touching parcels) and how far apart they sit (isolated
    parcels). Reporting only "shared edge" makes a scattered portfolio look
    broken instead of distant.
    """
    projected = _projected(fields)
    rows = []
    for _, row in projected.iterrows():
        geom = row.geometry
        parts = _parts(geom)
        area_m2 = float(geom.area)
        perimeter = float(sum(part.length for part in parts))
        hull = float(geom.convex_hull.area) or np.nan
        nominal = _nominal_acres(row.get("field_name", ""))
        rows.append(
            {
                "field_id": str(row["field_id"]),
                "field_name": str(row.get("field_name", "")),
                "crop": str(row.get("crop_name", "")),
                "area_acres": round(
                    float(row["area_acres"]) if pd.notna(row.get("area_acres")) else area_m2 / SQM_PER_ACRE,
                    3,
                ),
                "area_acres_geometric": round(area_m2 / SQM_PER_ACRE, 3),
                "perimeter_m": round(perimeter, 1),
                "compactness": round(4 * np.pi * area_m2 / perimeter**2, 4) if perimeter else np.nan,
                "convex_hull_ratio": round(area_m2 / hull, 4) if hull else np.nan,
                "part_count": len(parts),
                "nominal_acres": nominal,
                "ratio_to_name_pct": (
                    round(100 * (area_m2 / SQM_PER_ACRE) / nominal, 1)
                    if np.isfinite(nominal) and nominal > 0
                    else np.nan
                ),
            }
        )
    metrics = pd.DataFrame(rows).sort_values("field_id").reset_index(drop=True)

    pairs = []
    ids = list(projected["field_id"])
    for i in range(len(projected)):
        for j in range(i + 1, len(projected)):
            left = projected.geometry.iloc[i]
            right = projected.geometry.iloc[j]
            shared = float(left.boundary.intersection(right.boundary.buffer(1.0)).length)
            pairs.append(
                {
                    "field_a": str(ids[i]),
                    "field_b": str(ids[j]),
                    "shared_edge_m": round(shared, 2),
                    "gap_m": round(float(left.distance(right)), 1),
                    "overlap_acres": round(float(left.intersection(right).area) / SQM_PER_ACRE, 4),
                }
            )
    return metrics, pd.DataFrame(pairs)


def plot_boundary_area_ratio(metrics: pd.DataFrame, png: Path, csv: Path) -> None:
    """Story: which boundaries under-draw their label, and which names lie."""
    data = metrics.dropna(subset=["ratio_to_name_pct"]).sort_values("ratio_to_name_pct")
    _write_csv(data, csv)
    if data.empty:
        # Names without a leading number are a naming gap, not a crash.
        fig, ax = plt.subplots(figsize=(8.6, 3.0), dpi=150)
        ax.text(0.5, 0.5, "No field name carries a leading acreage token,\n"
                          "so measured area cannot be compared with the name.",
                ha="center", va="center", fontsize=9, color="#4b5563", transform=ax.transAxes)
        ax.set_axis_off()
        ax.set_title("Boundary area vs field name", fontsize=10.5)
        _save(fig, png)
        return
    fig, ax = plt.subplots(figsize=(8.6, 0.42 * len(data) + 1.6), dpi=150)
    y = np.arange(len(data))
    colours = ["#b42318" if abs(v - 100) > 15 else "#4a4a4a" for v in data["ratio_to_name_pct"]]
    ax.hlines(y, 100, data["ratio_to_name_pct"], color=colours, linewidth=2.2, alpha=0.75)
    ax.scatter(data["ratio_to_name_pct"], y, s=70, color=colours, zorder=3)
    ax.axvline(100, color="#111827", linewidth=1.2, linestyle="--")
    ax.set_yticks(y)
    ax.set_yticklabels(data["field_id"], fontsize=8)
    right_edge = float(data["ratio_to_name_pct"].max())
    for yi, (_, row) in zip(y, data.iterrows()):
        # Put the value on the roomier side; a white pad keeps it legible over
        # the stem line and clear of long field names on the left.
        offset = -9 if row["ratio_to_name_pct"] > right_edge - 6 else 9
        ax.annotate(
            f"{row['ratio_to_name_pct']:.0f}%",
            (row["ratio_to_name_pct"], yi),
            xytext=(offset, 0),
            textcoords="offset points",
            va="center",
            ha="left" if offset > 0 else "right",
            fontsize=7.5,
            color="#333333",
            bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.85),
        )
    ax.set_xlim(float(data["ratio_to_name_pct"].min()) - 6, right_edge + 4)
    ax.set_xlabel("Measured area as % of the acreage token in the field name")
    ax.set_title("Boundary area vs field name\n(red = more than 15 % off the name)", fontsize=10.5)
    ax.grid(True, axis="x", color="#e6e6e6", linewidth=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    fig.tight_layout()
    _save(fig, png)


def plot_boundary_shape(metrics: pd.DataFrame, png: Path, csv: Path) -> None:
    """Story: fragmented or sliver-shaped fields distort any pixel statistic."""
    data = metrics.sort_values(["part_count", "compactness"]).reset_index(drop=True)
    _write_csv(data, csv)
    fig, ax = plt.subplots(figsize=(8.6, 6.0), dpi=150)
    # Bubble AREA carries acreage, but the range is compressed: raw acres make
    # 150 ac blobs that swallow the panel and hide the label positions.
    span = float(data["area_acres"].max() - data["area_acres"].min()) or 1.0
    sizes = 45 + 110 * (data["area_acres"] - data["area_acres"].min()) / span
    jitter = np.linspace(-0.12, 0.12, len(data))
    ax.scatter(
        data["part_count"] + jitter,
        data["compactness"],
        s=sizes,
        c=[_crop_color(c) for c in data["crop"]],
        edgecolor="#1f1f1f",
        linewidth=0.8,
        alpha=0.9,
        zorder=3,
    )
    # Place labels greedily: try eight corners, keep the first box that stays
    # inside the axes and clears every marker and earlier label. Two fields can
    # sit within a hundredth of each other, so a fixed offset is not enough.
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    point_px = fig.dpi / 72.0
    occupied = []
    for x, y, size in zip(data["part_count"] + jitter, data["compactness"], sizes):
        px, py = ax.transData.transform((x, y))
        radius = (np.sqrt(size) / 2 + 2) * point_px
        occupied.append(Bbox.from_extents(px - radius, py - radius, px + radius, py + radius))
    axes_box = ax.get_window_extent(renderer)
    # A spiral of offsets, nearest ring first. Forty fields put several markers
    # within a few pixels of each other, so a fixed eight-corner list runs out
    # of valid slots; longer offsets carry a hairline leader back to the dot.
    candidates = []
    for radius in (9, 17, 25, 34, 44):
        for angle in np.linspace(np.pi / 2, np.pi / 2 + 2 * np.pi, 8, endpoint=False):
            candidates.append((radius * np.cos(angle), radius * np.sin(angle)))

    def _overlap_area(left: Bbox, right: Bbox) -> float:
        width = min(left.x1, right.x1) - max(left.x0, right.x0)
        height = min(left.y1, right.y1) - max(left.y0, right.y0)
        return width * height if width > 0 and height > 0 else 0.0

    for (_, row), dx in zip(data.iterrows(), jitter):
        anchor = (row["part_count"] + dx, row["compactness"])
        best = None
        for ox, oy in candidates:
            text = ax.annotate(
                row["field_id"], anchor, xytext=(ox, oy), textcoords="offset points",
                ha="left" if ox > 0 else "right", va="center",
                fontsize=6.9, color="#222222", zorder=4,
                bbox=dict(boxstyle="round,pad=0.15", fc="white", ec="none", alpha=0.88),
                arrowprops=(
                    dict(arrowstyle="-", color="#9ca3af", lw=0.6, shrinkA=1, shrinkB=4)
                    if abs(ox) > 13 or abs(oy) > 13 else None
                ),
            )
            box = text.get_window_extent(renderer)
            inside = (axes_box.x0 <= box.x0 and box.x1 <= axes_box.x1
                      and axes_box.y0 <= box.y0 and box.y1 <= axes_box.y1)
            if not inside:
                text.remove()
                continue
            overlap = sum(_overlap_area(box, other) for other in occupied)
            if overlap == 0:
                if best is not None:
                    best[3].remove()
                    best = None
                occupied.append(box)
                break
            if best is None or overlap < best[0]:
                if best is not None:
                    best[3].remove()
                best = (overlap, ox, oy, text)
            else:
                text.remove()
        if best is not None:
            # No collision-free slot exists; keep the least-bad label.
            occupied.append(best[3].get_window_extent(renderer))
    ax.set_xlabel("Polygon part count (1 = single boundary)")
    ax.set_ylabel("Polsby–Popper compactness (1.0 = circle)")
    ax.set_xticks(sorted(data["part_count"].unique()))
    ax.set_xlim(0.5, float(data["part_count"].max()) + 0.5)
    ax.set_title("Boundary shape quality\n(bubble area ∝ acres)", fontsize=10.5)
    ax.grid(True, color="#ececec", linewidth=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    crops = sorted({str(c) for c in data["crop"]})
    ax.legend(
        handles=[
            plt.Line2D([], [], marker="o", linestyle="", markersize=8,
                       markerfacecolor=_crop_color(c), markeredgecolor="#1f1f1f", label=c)
            for c in crops
        ],
        loc="upper right", fontsize=7.5, frameon=False, title="2026 crop", title_fontsize=7.5,
    )
    fig.tight_layout()
    _save(fig, png)


def plot_boundary_adjacency(pairs: pd.DataFrame, png: Path, csv: Path) -> None:
    """Story: are these independent fields or one contiguous block?

    A full 40-field matrix is unreadable once the report scales it to page
    width, so the figure keeps the pair-level detail that actually decides the
    question — every pair within 250 m — and leaves the complete gap matrix in
    the CSV. Touch pairs get their shared-edge length printed next to the gap.
    """
    _write_csv(pairs, csv)
    threshold = 250.0
    near = pairs.loc[pairs["gap_m"] <= threshold].sort_values(
        ["gap_m", "shared_edge_m"], ascending=[True, False]
    )
    if near.empty:
        near = pairs.nsmallest(min(25, len(pairs)), "gap_m").sort_values("gap_m")

    labels = [f"{a}  ↔  {b}" for a, b in zip(near["field_a"], near["field_b"])]
    gaps = near["gap_m"].to_numpy(dtype=float)
    touches = near["shared_edge_m"].to_numpy(dtype=float) > 0
    colours = ["#b42318" if touch else "#6b7280" for touch in touches]
    y = np.arange(len(near))

    fig, ax = plt.subplots(figsize=(8.0, 0.27 * len(near) + 2.0), dpi=150)
    ax.hlines(y, 0, gaps, color=colours, linewidth=2.2, zorder=2)
    ax.scatter(gaps, y, s=34, color=colours, edgecolor="#1f1f1f", linewidth=0.6, zorder=3)
    for yi, gap, touch, shared in zip(y, gaps, touches, near["shared_edge_m"]):
        suffix = f"  ·  {shared:.1f} m shared edge" if touch else ""
        ax.text(gap + 0.012 * max(gaps.max(), 10.0), yi, f"{gap:.1f} m{suffix}",
                va="center", ha="left", fontsize=6.8,
                color="#b42318" if touch else "#374151", zorder=4)
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=7.0)
    ax.set_ylim(len(near) - 0.5, -0.5)
    ax.set_xlim(0, max(gaps.max(), 10.0) * 1.32)
    ax.set_xlabel("Edge-to-edge gap between derived polygons (m)", fontsize=8.5)
    ax.set_title(
        f"Closest field pairs — {len(near)} of {len(pairs)} are within {threshold:.0f} m",
        fontsize=10.5,
    )
    ax.grid(True, axis="x", color="#ececec", linewidth=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    touched = int(touches.sum())
    within_50 = int((pairs["gap_m"] < 50).sum())
    ax.text(
        0.99, 0.99,
        f"{touched} pairs share a boundary\n{within_50} pairs are under 50 m apart\n"
        f"{len(pairs) - len(near)} pairs are farther than {threshold:.0f} m",
        transform=ax.transAxes, ha="right", va="top", fontsize=7.2, color="#374151",
        bbox=dict(boxstyle="round,pad=0.35", fc="#f8f7f3", ec="#d7d4cb", lw=0.7),
    )
    fig.tight_layout()
    _save(fig, png)


def _largest_field_cluster(geoms: list, max_gap_m: float = 1000.0) -> list[int]:
    """Indices of the largest group whose members sit within ``max_gap_m``."""
    count = len(geoms)
    parent = list(range(count))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    for i in range(count):
        for j in range(i + 1, count):
            if geoms[i].distance(geoms[j]) <= max_gap_m:
                union(i, j)

    groups: dict[int, list[int]] = {}
    for index in range(count):
        groups.setdefault(find(index), []).append(index)
    return max(groups.values(), key=len)


def _fill_field_polygons(ax, geometry, crops, *, alpha: float = 0.65, linewidth: float = 0.9) -> None:
    for geom, crop in zip(geometry, crops):
        for part in _parts(geom):
            x, y = part.exterior.xy
            ax.fill(x, y, color=_crop_color(crop), alpha=alpha,
                    edgecolor="#1f1f1f", linewidth=linewidth, zorder=2)


def _number_fields(ax, geometry, orders, *, size: float, fontsize: float,
                   rings: tuple[float, ...] = (10.0, 16.0, 22.0)) -> None:
    # Number every part, so a split parcel does not look like an unlabelled
    # extra field. Point markers stay a fixed screen size: data-unit circles
    # swallow small parcels once the portfolio spans tens of kilometres.
    #
    # Adjacent fields put their representative points a handful of pixels
    # apart, so a centred number lands on top of its neighbour. Try a small
    # offset ring and keep the first slot that clears every earlier number;
    # a moved number gets a hairline leader back to its marker. The caller
    # sizes the ring: on the wide overview a large point offset is kilometres
    # of geography, while on the detail panel it is only tens of metres.
    fig = ax.figure
    fig.canvas.draw()
    renderer = fig.canvas.get_renderer()
    axes_box = ax.get_window_extent(renderer)
    occupied = []
    offsets = [(0.0, 0.0)]
    for radius in rings:
        for angle in np.linspace(np.pi / 2, np.pi / 2 + 2 * np.pi, 8, endpoint=False):
            offsets.append((radius * np.cos(angle), radius * np.sin(angle)))

    def _overlap_area(left: Bbox, right: Bbox) -> float:
        width = min(left.x1, right.x1) - max(left.x0, right.x0)
        height = min(left.y1, right.y1) - max(left.y0, right.y0)
        return width * height if width > 0 and height > 0 else 0.0

    for order, geom in zip(orders, geometry):
        for part in _parts(geom):
            point = part.representative_point()
            ax.scatter([point.x], [point.y], s=size, facecolor="white",
                       edgecolor="#111827", linewidth=0.9, zorder=5)
            best = None
            for ox, oy in offsets:
                text = ax.annotate(
                    str(order), (point.x, point.y), xytext=(ox, oy),
                    textcoords="offset points", ha="center", va="center",
                    fontsize=fontsize, fontweight="bold", zorder=6, clip_on=True,
                    arrowprops=(
                        dict(arrowstyle="-", color="#6b7280", lw=0.5,
                             shrinkA=1, shrinkB=2)
                        if (ox or oy) else None
                    ),
                )
                box = text.get_window_extent(renderer)
                inside = (axes_box.x0 <= box.x0 and box.x1 <= axes_box.x1
                          and axes_box.y0 <= box.y0 and box.y1 <= axes_box.y1)
                if not inside:
                    text.remove()
                    continue
                overlap = sum(_overlap_area(box, other) for other in occupied)
                if overlap == 0:
                    if best is not None:
                        best[1].remove()
                        best = None
                    occupied.append(box)
                    break
                if best is None or overlap < best[0]:
                    if best is not None:
                        best[1].remove()
                    best = (overlap, text)
                else:
                    text.remove()
            if best is not None:
                # No collision-free slot: keep the least-bad label.
                occupied.append(best[1].get_window_extent(renderer))


def plot_portfolio_map(fields: gpd.GeoDataFrame, png: Path, csv: Path) -> None:
    """Story: where the portfolio sits; the only spatial view in this subskill.

    A single overview stops working once forty fields span tens of kilometres:
    the dense core collapses into overlapping markers. When the largest
    connected cluster is a real subset of the portfolio, the figure adds a
    detail panel for that cluster and a field key below it.
    """
    frame_for, decorate, crs_note, view_extent = _field_frame_module()
    # force_rot=0.0: rotating a multi-field portfolio is meaningless, but the
    # frame contract still gives metre axes, a SW origin, and a true-north arrow.
    framed = frame_for(fields.to_crs("EPSG:4326"), panel_h_in=6.4, panel_w_in=9.4, force_rot=0.0)
    # Frame.geometry is the framed GeoDataFrame; its .geometry column keeps the
    # input row order, which is what the crop/ID zip below relies on.
    geometry = framed.geometry.geometry.reset_index(drop=True)
    crops = fields["crop_name"].astype(str).reset_index(drop=True)
    field_ids = fields["field_id"].astype(str).reset_index(drop=True)
    acres = pd.to_numeric(fields["area_acres"], errors="coerce").reset_index(drop=True)
    total_acres = float(acres.sum())

    bx0, by0, bx1, by1 = geometry.total_bounds  # (xmin, ymin, xmax, ymax)
    x_min, x_max, y_min, y_max = view_extent(bx0, bx1, by0, by1)

    handles = [
        plt.Line2D(
            [], [], marker="s", linestyle="", markersize=7,
            markerfacecolor=_crop_color(crop), markeredgecolor="#111827",
            label=f"{order}  {field_id}  {float(area):.1f} ac",
        )
        for order, (field_id, crop, area) in enumerate(zip(field_ids, crops, acres), start=1)
    ]

    cluster = _largest_field_cluster(list(geometry)) if len(geometry) >= 8 else []
    cx0, cy0, cx1, cy1 = (
        geometry.iloc[cluster].total_bounds if cluster else (bx0, by0, bx1, by1)
    )
    full_area = max((bx1 - bx0) * (by1 - by0), 1.0)
    show_detail = (
        len(cluster) >= 8
        and len(cluster) <= 0.85 * len(geometry)
        and (cx1 - cx0) * (cy1 - cy0) <= 0.55 * full_area
    )

    if not show_detail:
        fig, ax = plt.subplots(figsize=(7.4, 4.9), dpi=150)
        _fill_field_polygons(ax, geometry, crops)
        ax.set_xlim(x_min, x_max)
        ax.set_ylim(y_min, y_max)
        ax.set_aspect("equal", adjustable="box")
        decorate(ax, framed, callout=False)
        _number_fields(ax, geometry, range(1, len(geometry) + 1), size=58, fontsize=6.4)
        ax.set_xlabel(crs_note(framed), fontsize=8)
        ax.set_ylabel("metres from SW corner", fontsize=8)
        ax.tick_params(labelsize=7.5)
        ax.set_title(
            f"Field portfolio — {len(geometry)} fields, {total_acres:.0f} ac total", fontsize=10.5
        )
        ax.legend(
            handles=handles, loc="upper center", bbox_to_anchor=(0.5, -0.13),
            ncol=2 if len(handles) > 8 else 1, fontsize=6.6, frameon=False,
            handletextpad=0.4, labelspacing=0.3, columnspacing=1.2,
        )
        fig.tight_layout()
        _save(fig, png)
        _write_portfolio_csv(csv, geometry, fields)
        return

    fig = plt.figure(figsize=(7.2, 10.2), dpi=180)
    grid = fig.add_gridspec(
        3, 1, height_ratios=[1.0, 2.25, 0.55], hspace=0.34,
    )
    ax = fig.add_subplot(grid[0])
    detail_ax = fig.add_subplot(grid[1])
    key_ax = fig.add_subplot(grid[2])
    key_ax.axis("off")

    _fill_field_polygons(ax, geometry, crops, alpha=0.7, linewidth=0.8)
    ax.set_xlim(x_min, x_max)
    ax.set_ylim(y_min, y_max)
    ax.set_aspect("equal", adjustable="box")
    decorate(ax, framed, callout=False, arrow_xy=(0.87, 0.78))
    # Mark the detail extent; everything inside it is numbered in the panel
    # below, so the overview only numbers fields outside the box. Numbering a
    # field twice, once at full extent and once enlarged, reads as two fields.
    pad = 0.06 * max(cx1 - cx0, cy1 - cy0)

    def _inside_detail(geom) -> bool:
        point = geom.representative_point()
        return (cx0 - pad <= point.x <= cx1 + pad
                and cy0 - pad <= point.y <= cy1 + pad)

    visible = [
        i for i in range(len(geometry)) if not _inside_detail(geometry.iloc[i])
    ]
    _number_fields(
        ax, geometry.iloc[visible], [index + 1 for index in visible],
        size=42, fontsize=5.8, rings=(4.0, 7.0),
    )
    rect = plt.Rectangle(
        (cx0 - pad, cy0 - pad), (cx1 - cx0) + 2 * pad, (cy1 - cy0) + 2 * pad,
        fill=False, edgecolor="#111827", linewidth=1.1, linestyle=(0, (4, 3)), zorder=7,
    )
    ax.add_patch(rect)
    ax.annotate(
        "detail below", (cx1 + pad, cy0 - pad), xytext=(-3, 3), textcoords="offset points",
        ha="right", va="bottom", fontsize=6.6, color="#111827", zorder=8,
        bbox=dict(boxstyle="round,pad=0.18", fc="white", ec="none", alpha=0.85),
    )
    ax.set_xlabel(crs_note(framed), fontsize=7.5)
    ax.set_ylabel("metres from SW corner", fontsize=7.5)
    ax.tick_params(labelsize=7)
    # Left-align the title: the north-arrow box sits at the right of the
    # overview panel and a centred title runs underneath it.
    ax.set_title(
        f"Portfolio overview — {len(geometry)} fields · {total_acres:.0f} ac",
        fontsize=9.8, loc="left",
    )

    _fill_field_polygons(detail_ax, geometry, crops, alpha=0.7, linewidth=0.9)
    detail_ax.set_xlim(cx0 - pad, cx1 + pad)
    detail_ax.set_ylim(cy0 - pad, cy1 + pad)
    detail_ax.set_aspect("equal", adjustable="box")
    decorate(detail_ax, framed, callout=False, arrow_xy=(0.13, 0.80))
    _number_fields(detail_ax, geometry, range(1, len(geometry) + 1), size=52, fontsize=6.0)
    detail_ax.set_xlabel("metres from SW corner (same frame)", fontsize=7.5)
    detail_ax.set_ylabel("metres from SW corner", fontsize=7.5)
    detail_ax.tick_params(labelsize=7)
    detail_ax.set_title(
        f"Detail — largest contiguous cluster ({len(cluster)} fields)", fontsize=9.5
    )

    crop_handles = [
        plt.Line2D([], [], marker="s", linestyle="", markersize=7,
                   markerfacecolor=_crop_color(crop), markeredgecolor="#111827", label=crop)
        for crop in sorted(set(crops))
    ]
    crop_key = key_ax.legend(
        handles=crop_handles, loc="upper left", fontsize=6.4, frameon=False,
        ncol=len(crop_handles), handletextpad=0.4, labelspacing=0.3,
        borderaxespad=0.0,
    )
    key_ax.add_artist(crop_key)
    key_ax.legend(
        handles=handles, loc="upper left", bbox_to_anchor=(0.0, 0.82), ncol=3,
        fontsize=5.6, frameon=False, handletextpad=0.35, labelspacing=0.28,
        columnspacing=0.9, borderaxespad=0.0, title="Field key", title_fontsize=6.4,
    )

    _save(fig, png)
    _write_portfolio_csv(csv, geometry, fields)


def _write_portfolio_csv(csv: Path, geometry, fields: gpd.GeoDataFrame) -> None:
    _write_csv(
        pd.DataFrame(
            {
                "map_number": list(range(1, len(geometry) + 1)),
                "field_id": fields["field_id"].astype(str),
                "crop": fields["crop_name"].astype(str),
                "area_acres": fields["area_acres"].astype(float),
                "framed_x_m": [round(g.representative_point().x, 1) for g in geometry],
                "framed_y_m": [round(g.representative_point().y, 1) for g in geometry],
            }
        ),
        csv,
    )


def run_boundaries(ds: Dataset, out: Path) -> dict:
    metrics, pairs = boundary_metrics(ds.boundaries)
    plot_boundary_area_ratio(metrics, out / "boundaries_area_ratio.png", out / "boundaries_area_ratio.csv")
    plot_boundary_shape(metrics, out / "boundaries_shape.png", out / "boundaries_shape.csv")
    plot_boundary_adjacency(pairs, out / "boundaries_adjacency.png", out / "boundaries_adjacency.csv")
    plot_portfolio_map(ds.boundaries, out / "boundaries_portfolio_map.png", out / "boundaries_portfolio_map.csv")

    touching = pairs[pairs["shared_edge_m"] > 2.0]
    overlaps = pairs[pairs["overlap_acres"] > 0.01]
    return {
        "category": "boundaries",
        "fields": int(len(metrics)),
        "total_acres": round(float(metrics["area_acres"].sum()), 3),
        "artifacts": [
            "boundaries_area_ratio.png",
            "boundaries_shape.png",
            "boundaries_adjacency.png",
            "boundaries_portfolio_map.png",
        ],
        "findings": {
            "fields_within_2m": int(len(touching)),
            "max_shared_edge_m": round(float(pairs["shared_edge_m"].max()), 2) if len(pairs) else 0.0,
            "min_gap_m": round(float(pairs["gap_m"].min()), 1) if len(pairs) else None,
            "max_gap_m": round(float(pairs["gap_m"].max()), 1) if len(pairs) else None,
            "overlap_pairs": int(len(overlaps)),
            "multipart_fields": int((metrics["part_count"] > 1).sum()),
            "names_off_by_over_15pct": metrics.loc[
                (metrics["ratio_to_name_pct"] - 100).abs() > 15, "field_id"
            ].tolist(),
        },
    }


# ---------------------------------------------------------------------------
# CDL / cropland
# ---------------------------------------------------------------------------


def dominant_table(cdl: pd.DataFrame) -> pd.DataFrame:
    """One row per field-year: the class with the largest pixel share."""
    work = cdl.copy()
    work["year"] = work["year"].astype(int)
    idx = work.groupby(["field_id", "year"])["pct"].idxmax()
    dominant = work.loc[idx, ["field_id", "year", "crop_name", "pct"]].rename(
        columns={"crop_name": "dominant_crop", "pct": "dominant_pct"}
    )
    return dominant.sort_values(["field_id", "year"]).reset_index(drop=True)


def plot_cdl_crop_matrix(dominant: pd.DataFrame, png: Path, csv: Path) -> None:
    """Story: the rotation sequence per field, one row per field."""
    _write_csv(dominant, csv)
    years = sorted(dominant["year"].unique())
    fields = sorted(dominant["field_id"].unique())
    crops = sorted(dominant["dominant_crop"].unique())
    lookup = {(r.field_id, r.year): r.dominant_crop for r in dominant.itertuples(index=False)}

    fig, ax = plt.subplots(figsize=(1.5 + 0.95 * len(years), 0.42 * len(fields) + 1.9), dpi=150)
    for yi, field_id in enumerate(fields):
        for xi, year in enumerate(years):
            crop = lookup.get((field_id, year), "")
            ax.add_patch(
                plt.Rectangle((xi - 0.5, yi - 0.5), 1, 1, facecolor=_crop_color(crop),
                              edgecolor="white", linewidth=1.5)
            )
            ax.text(xi, yi, str(crop), ha="center", va="center", fontsize=6.6, color="#101010")
    ax.set_xlim(-0.5, len(years) - 0.5)
    ax.set_ylim(-0.5, len(fields) - 0.5)
    ax.set_xticks(range(len(years)))
    ax.set_xticklabels([str(y) for y in years], fontsize=8)
    ax.set_yticks(range(len(fields)))
    ax.set_yticklabels(fields, fontsize=7.5)
    ax.tick_params(length=0)
    for side in ("top", "right", "bottom", "left"):
        ax.spines[side].set_visible(False)
    ax.set_xlabel("CDL year", fontsize=8.5)
    ax.set_title("CDL dominant class by field and year", fontsize=10.5, pad=8)
    handles = [
        plt.Line2D([], [], marker="s", linestyle="", markersize=9,
                   markerfacecolor=_crop_color(c), markeredgecolor="white", label=c)
        for c in crops
    ]
    # Anchor in points, not axes fraction: with 40 rows the axes is ~17 in tall
    # and a -0.16 fraction would drop the key 2.7 in below the x label.
    ax.legend(
        handles=handles, loc="upper center", bbox_to_anchor=(0.5, 0),
        bbox_transform=offset_copy(ax.transAxes, fig=fig, y=-42, units="points"),
        ncol=min(len(crops), 4), fontsize=7.5, frameon=False,
    )
    fig.tight_layout()
    _save(fig, png)


def plot_cdl_purity(dominant: pd.DataFrame, png: Path, csv: Path) -> None:
    """Story: how much of each field the dominant class actually covers."""
    _write_csv(dominant, csv)
    fields = sorted(dominant["field_id"].unique())
    fig, ax = plt.subplots(figsize=(7.6, 0.46 * len(fields) + 1.8), dpi=150)
    for yi, field_id in enumerate(fields):
        block = dominant[dominant["field_id"] == field_id].sort_values("year")
        ax.hlines(yi, block["dominant_pct"].min(), block["dominant_pct"].max(),
                  color="#d5d5d5", linewidth=2.0, zorder=1)
        # Years cluster within a percent or two; a small vertical fan keeps
        # every year visible instead of drawing one dot on top of another.
        offsets = np.linspace(-0.18, 0.18, len(block)) if len(block) > 1 else [0.0]
        ax.scatter(block["dominant_pct"], yi + np.asarray(offsets),
                   c=[_crop_color(c) for c in block["dominant_crop"]],
                   s=46, edgecolor="#1f1f1f", linewidth=0.5, zorder=3)
    ax.axvline(70, color="#b42318", linestyle=":", linewidth=1.1)
    ax.annotate("70 %", (70, len(fields) - 0.5), xytext=(-5, 0),
                textcoords="offset points", ha="right", va="center",
                color="#b42318", fontsize=7)
    ax.set_yticks(range(len(fields)))
    ax.set_yticklabels(fields, fontsize=7.5)
    ax.set_ylim(-0.6, len(fields) - 0.4)
    ax.set_xlim(0, 105)
    ax.set_xlabel("Dominant class share of field pixels (%)", fontsize=8.5)
    ax.set_title("CDL class purity per field-year\n(one dot per year)", fontsize=10.5)
    ax.grid(True, axis="x", color="#ececec", linewidth=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    ax.legend(
        handles=[
            plt.Line2D([], [], marker="o", linestyle="", markersize=7,
                       markerfacecolor=_crop_color(c), markeredgecolor="#1f1f1f", label=c)
            for c in sorted(dominant["dominant_crop"].unique())
        ],
        loc="upper center", bbox_to_anchor=(0.5, 0),
        bbox_transform=offset_copy(ax.transAxes, fig=fig, y=-42, units="points"),
        ncol=4, fontsize=7.2, frameon=False, title="Dominant class",
        title_fontsize=7.2,
    )
    fig.tight_layout()
    _save(fig, png)


def rotation_test(
    dominant: pd.DataFrame,
    planted: dict[str, str],
    metrics: pd.DataFrame,
) -> tuple[pd.DataFrame, dict]:
    """Rotation consistency: transitions, alternation, and purity vs field size."""
    years = sorted(int(y) for y in dominant["year"].unique())
    wide = dominant.pivot(index="field_id", columns="year", values="dominant_crop")
    wide = wide.reindex(columns=years).sort_index()

    transitions: dict[tuple[str, str], int] = {}
    for year_a, year_b in zip(years, years[1:]):
        for field_id in wide.index:
            left, right = wide.loc[field_id, year_a], wide.loc[field_id, year_b]
            if pd.isna(left) or pd.isna(right):
                continue
            transitions[(str(left), str(right))] = transitions.get((str(left), str(right)), 0) + 1
    transition_rows = [{"from_crop": a, "to_crop": b, "count": n} for (a, b), n in sorted(transitions.items())]
    transition_df = pd.DataFrame(transition_rows)

    per_field = []
    for field_id, row in wide.iterrows():
        sequence = [str(row[year]) for year in years if not pd.isna(row[year])]
        alternates = all(a != b for a, b in zip(sequence, sequence[1:])) if len(sequence) > 1 else None
        planted_crop = planted.get(str(field_id), "")
        last_cdl = str(row[years[-1]]) if years and not pd.isna(row[years[-1]]) else ""
        if planted_crop and last_cdl:
            if planted_crop == last_cdl:
                outcome = "repeat"
            elif {planted_crop, last_cdl} == {"Corn", "Soybeans"}:
                outcome = "flip"
            else:
                outcome = "other"
        else:
            outcome = "no_planted_record"
        per_field.append(
            {
                "field_id": str(field_id),
                "sequence": "-".join(sequence),
                "alternates": alternates,
                "cdl_last": last_cdl,
                "planted": planted_crop,
                "outcome": outcome,
            }
        )
    per_field_df = pd.DataFrame(per_field)

    flips = int((per_field_df["outcome"] == "flip").sum())
    repeats = int((per_field_df["outcome"] == "repeat").sum())
    comparable = flips + repeats
    binomial_p = (
        float(stats.binomtest(flips, comparable, 0.5, alternative="two-sided").pvalue)
        if comparable
        else float("nan")
    )

    purity = dominant.groupby("field_id")["dominant_pct"].mean().rename("mean_purity")
    joined = purity.to_frame().join(metrics.set_index("field_id")["area_acres"], how="inner").dropna()
    if len(joined) >= 4:
        rho, rho_p = stats.spearmanr(np.log(joined["area_acres"]), joined["mean_purity"])
    else:
        rho, rho_p = float("nan"), float("nan")

    alternation = per_field_df["alternates"].dropna()
    verdict = {
        "years": years,
        "fields": int(len(wide)),
        "alternating_fields": int(alternation.sum()),
        "non_alternating_fields": per_field_df.loc[per_field_df["alternates"] == False, "field_id"].tolist(),  # noqa: E712
        "transitions": transition_df.to_dict("records"),
        "flips_to_2026": flips,
        "repeats_to_2026": repeats,
        "comparable_2026": comparable,
        "flip_binomial_p": None if np.isnan(binomial_p) else round(binomial_p, 4),
        "purity_vs_log_area_spearman": None if np.isnan(rho) else round(float(rho), 3),
        "purity_vs_log_area_p": None if np.isnan(rho_p) else round(float(rho_p), 4),
        "note": "n is one farm; treat tests as structural checks, not agronomic evidence.",
    }
    return per_field_df, verdict


def plot_cdl_rotation(per_field: pd.DataFrame, png: Path, csv: Path) -> None:
    """Story: which crop follows which, and who breaks the rotation."""
    _write_csv(per_field, csv)
    rows = per_field.sort_values("field_id").reset_index(drop=True)
    fig, ax = plt.subplots(figsize=(9.4, 0.42 * len(rows) + 1.9), dpi=150)
    y_positions = np.arange(len(rows))[::-1]
    for y, row in zip(y_positions, rows.itertuples(index=False)):
        left, right = str(row.cdl_last), str(row.planted or "")
        ax.plot([0, 1], [y, y], color="#c9c9c9", linewidth=5, solid_capstyle="round", alpha=0.35, zorder=1)
        for x, crop in ((0, left), (1, right)):
            if crop:
                ax.scatter([x], [y], s=150, color=_crop_color(crop),
                           edgecolor="#1f1f1f", linewidth=0.8, zorder=3)
        label = {"flip": "flip", "repeat": "repeat", "other": "other",
                 "no_planted_record": "no planting record"}[str(row.outcome)]
        ax.text(1.12, y, label, va="center", ha="left", fontsize=7.5, color="#333333")
    ax.set_yticks(y_positions)
    ax.set_yticklabels(rows["field_id"], fontsize=7.5)
    ax.set_xticks([0, 1])
    ax.set_xticklabels(["CDL last year", "Planted record"], fontsize=8)
    ax.set_xlim(-0.25, 1.95)
    ax.set_ylim(-0.8, len(rows) - 0.2)
    ax.tick_params(axis="x", length=0)
    for side in ("top", "right", "bottom"):
        ax.spines[side].set_visible(False)
    ax.grid(True, axis="y", color="#efefef", linewidth=0.6)
    ax.set_axisbelow(True)
    counts = rows["outcome"].value_counts()
    summary = f"{int(counts.get('flip', 0))} flip · {int(counts.get('repeat', 0))} repeat"
    if counts.get("other", 0):
        summary += f" · {int(counts['other'])} other"
    if counts.get("no_planted_record", 0):
        summary += f" · {int(counts['no_planted_record'])} no planting record"
    ax.set_title(
        "Rotation outcome: CDL last year → planted crop\n" + summary,
        fontsize=10.5, pad=8,
    )
    crops = sorted({c for c in list(rows["cdl_last"]) + list(rows["planted"]) if c})
    ax.legend(
        handles=[
            plt.Line2D([], [], marker="o", linestyle="", markersize=8,
                       markerfacecolor=_crop_color(c), markeredgecolor="#1f1f1f", label=c)
            for c in crops
        ],
        loc="lower right", fontsize=7.2, frameon=False, title="Crop", title_fontsize=7.2,
    )
    fig.tight_layout()
    _save(fig, png)


def run_cdl(ds: Dataset, out: Path) -> dict:
    metrics, _ = boundary_metrics(ds.boundaries)
    dominant = dominant_table(ds.cdl)
    plot_cdl_crop_matrix(dominant, out / "cdl_crop_matrix.png", out / "cdl_crop_matrix.csv")
    plot_cdl_purity(dominant, out / "cdl_purity.png", out / "cdl_purity.csv")
    per_field, verdict = rotation_test(dominant, ds.planted, metrics)
    plot_cdl_rotation(per_field, out / "cdl_rotation.png", out / "cdl_rotation.csv")
    verdict["artifacts"] = ["cdl_crop_matrix.png", "cdl_purity.png", "cdl_rotation.png"]
    verdict["category"] = "cdl"
    return verdict


# ---------------------------------------------------------------------------
# weather
# ---------------------------------------------------------------------------


def weather_daily(weather: pd.DataFrame) -> pd.DataFrame:
    """Add Fahrenheit temps and daily GDD, keeping the original rows."""
    frame = weather.copy()
    frame["date"] = pd.to_datetime(frame["date"])
    frame["year"] = frame["date"].dt.year
    frame["month"] = frame["date"].dt.month
    frame["tmax_f"] = frame["T2M_MAX"] * 9 / 5 + 32
    frame["tmin_f"] = frame["T2M_MIN"] * 9 / 5 + 32
    frame["gdd_f"] = np.maximum(
        (np.minimum(frame["tmax_f"], GDD_CAP_F) + np.maximum(frame["tmin_f"], GDD_BASE_F)) / 2 - GDD_BASE_F,
        0.0,
    )
    return frame


def season_slice(daily: pd.DataFrame) -> pd.DataFrame:
    start = (daily["month"] > SEASON_START[0]) | (
        (daily["month"] == SEASON_START[0]) & (daily["date"].dt.day >= SEASON_START[1])
    )
    end = (daily["month"] < SEASON_END[0]) | (
        (daily["month"] == SEASON_END[0]) & (daily["date"].dt.day <= SEASON_END[1])
    )
    return daily[start & end].copy()


def plot_weather_gdd(daily: pd.DataFrame, png: Path, csv: Path) -> None:
    """Story: season progress and how the years rank — within-field time."""
    season = season_slice(daily)
    field_id = sorted(season["field_id"].unique())[0]
    series = season[season["field_id"] == field_id].sort_values("date").copy()
    season_start = pd.to_datetime(series["year"].astype(str) + f"-{SEASON_START[0]:02d}-{SEASON_START[1]:02d}")
    series["season_day"] = (series["date"] - season_start).dt.days + 1
    series["cum_gdd"] = series.groupby("year")["gdd_f"].cumsum()
    _write_csv(series[["field_id", "year", "date", "gdd_f", "cum_gdd", "season_day"]], csv)

    fig, ax = plt.subplots(figsize=(9.6, 5.0), dpi=150)
    ramp = plt.get_cmap("viridis")
    years = sorted(series["year"].unique())
    handles = []
    for i, year in enumerate(years):
        block = series[series["year"] == year]
        (line,) = ax.plot(block["season_day"], block["cum_gdd"],
                          color=ramp(i / max(len(years) - 1, 1)), linewidth=1.9)
        handles.append((line, f"{year} · {block['cum_gdd'].iloc[-1]:,.0f}"))
    month_starts = pd.date_range("2001-04-01", "2001-11-01", freq="MS")
    ax.set_xticks([(stamp - pd.Timestamp("2001-04-01")).days + 1 for stamp in month_starts])
    ax.set_xticklabels([stamp.strftime("%b") for stamp in month_starts])
    ax.set_xlim(1, 215)
    ax.set_xlabel("Season (Apr 1 – Oct 31)", fontsize=8.5)
    ax.set_ylabel("Cumulative GDD (°F, base 50 / cap 86)", fontsize=8.5)
    ax.set_title(f"Growing degree days — {field_id}\n(one line per year)", fontsize=10.5)
    ax.grid(True, color="#ececec", linewidth=0.6)
    ax.set_axisbelow(True)
    ax.legend([line for line, _ in handles], [text for _, text in handles],
              title="Year", loc="upper left", bbox_to_anchor=(1.015, 1.0),
              fontsize=7.5, title_fontsize=7.5, frameon=False)
    fig.tight_layout()
    _save(fig, png)


def plot_weather_precip(daily: pd.DataFrame, png: Path, csv: Path) -> None:
    """Story: when the water arrived, not just how much in total."""
    # One field's daily series, NOT a sum over fields: ten fields sharing one
    # grid cell would otherwise report ten times the real rainfall.
    field_id = sorted(daily["field_id"].unique())[0]
    series = daily[daily["field_id"] == field_id]
    monthly = (
        series.groupby(["year", "month"], as_index=False)["PRECTOTCORR"].sum()
        .pivot(index="year", columns="month", values="PRECTOTCORR")
        .reindex(columns=range(1, 13))
    )
    _write_csv(monthly.reset_index().rename_axis(columns=None), csv)
    fig, ax = plt.subplots(figsize=(9.0, 0.5 * len(monthly) + 1.9), dpi=150)
    image = ax.imshow(monthly.to_numpy(), aspect="auto", cmap="YlGnBu")
    ax.set_xticks(range(12))
    ax.set_xticklabels(
        ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"],
        fontsize=7.5,
    )
    ax.set_yticks(range(len(monthly)))
    ax.set_yticklabels([str(y) for y in monthly.index], fontsize=8)
    values = monthly.to_numpy()
    threshold = np.nanmax(values) * 0.6
    for row in range(values.shape[0]):
        for col in range(values.shape[1]):
            value = values[row, col]
            if np.isnan(value):
                continue
            ax.text(col, row, f"{value:.0f}", ha="center", va="center", fontsize=7,
                    color="white" if value > threshold else "#1f1f1f")
    colorbar = fig.colorbar(image, ax=ax, pad=0.015)
    colorbar.set_label("Monthly precipitation (mm)", fontsize=8)
    ax.set_xlabel("Month", fontsize=8.5)
    ax.set_ylabel("Year", fontsize=8.5)
    ax.set_title(f"Monthly precipitation — {field_id}", fontsize=10.5)
    fig.tight_layout()
    _save(fig, png)


def variance_partition(daily: pd.DataFrame) -> tuple[pd.DataFrame, dict]:
    """Share of SEASON-TOTAL GDD variance explained by field, year, and grower.

    The unit is one field-year: total GDD over Apr 1 – Oct 31. Partitioning
    daily values instead hides the year signal, because every season has the
    same within-season shape and only the totals differ.

    Sequential sum-of-squares (field, then year, then grower), which a reader
    can reproduce by hand. When every field shares one weather grid cell the
    field share is zero by construction — that is the finding, not a failed
    estimate.
    """
    season = season_slice(daily).dropna(subset=["gdd_f"])
    keys = ["field_id", "year"] + (["grower"] if "grower" in season.columns else [])
    totals = season.groupby(keys, as_index=False)["gdd_f"].sum().rename(columns={"gdd_f": "season_gdd"})

    grand = float(totals["season_gdd"].mean())
    ss_total = float(((totals["season_gdd"] - grand) ** 2).sum())
    rows = []
    for label, column in (("field", "field_id"), ("year", "year"), ("grower", "grower")):
        if column not in totals.columns or totals[column].nunique() < 2:
            continue
        grouped = totals.groupby(column)["season_gdd"]
        ss_between = float(sum(len(block) * (block.mean() - grand) ** 2 for _, block in grouped))
        rows.append(
            {
                "term": label,
                "levels": int(totals[column].nunique()),
                "variance_share_pct": round(100 * ss_between / ss_total, 2) if ss_total else 0.0,
            }
        )
    explained = sum(row["variance_share_pct"] for row in rows)
    rows.append(
        {
            "term": "residual (interaction)",
            "levels": None,
            "variance_share_pct": round(max(0.0, 100.0 - explained), 2),
        }
    )
    table = pd.DataFrame(rows)

    # Count distinct weather SERIES, not distinct lat/lon: farm weather tables
    # store the field centroid per row, so ten fields can share one grid cell
    # and still show ten coordinate pairs. Ten series means real replication.
    value_cols = [c for c in ("T2M_MAX", "T2M_MIN", "PRECTOTCORR") if c in season.columns]
    distinct_cells = None
    if value_cols:
        id_cols = ["field_id"] + (["grower"] if "grower" in season.columns else [])
        ordered = season.sort_values("date")
        signatures = {}
        for key, block in ordered.groupby(id_cols):
            values = block[value_cols].to_numpy(dtype="float64")
            # NaN != NaN in a tuple, so map gaps to one sentinel: otherwise a
            # missing day per field would fake extra "distinct" series.
            values = np.where(np.isnan(values), -9999.0, values)
            signatures[key] = tuple(np.round(values, 6).ravel())
        distinct_cells = int(len(set(signatures.values())))
    field_row = table.loc[table["term"] == "field", "variance_share_pct"]
    grower_levels = int(totals["grower"].nunique()) if "grower" in totals.columns else 1
    verdict = {
        "unit": "season-total GDD per field-year (Apr 1 - Oct 31)",
        "field_years": int(len(totals)),
        "distinct_weather_cells": distinct_cells,
        "field_share_pct": float(field_row.iloc[0]) if len(field_row) else 0.0,
        "grower_levels": grower_levels,
        "growers_available": sorted(totals["grower"].unique().tolist()) if "grower" in totals.columns else [],
    }
    if grower_levels < 2:
        verdict["guard"] = (
            "cross-grower comparison not identified: 1 grower in scope, so the "
            "grower term is skipped rather than reported as zero evidence"
        )
    if distinct_cells == 1:
        verdict["guard_field"] = (
            "all fields share one weather series; the field share is 0 by "
            "construction and field-level weather replication is not identified"
        )
    return table, verdict


def plot_weather_variance(table: pd.DataFrame, png: Path, csv: Path, note: str = "") -> None:
    """Story: where weather variance lives determines the unit of analysis."""
    _write_csv(table, csv)
    ordered = table.iloc[::-1]
    fig, ax = plt.subplots(figsize=(7.4, 0.55 * len(table) + 1.8), dpi=150)
    colours = ["#b9b9b9" if str(term).startswith("residual") else "#2f6f9f" for term in ordered["term"]]
    ax.barh(ordered["term"], ordered["variance_share_pct"], color=colours, edgecolor="#1f1f1f", linewidth=0.6)
    for y, (_, row) in enumerate(ordered.iterrows()):
        ax.text(row["variance_share_pct"] + 1.5, y, f"{row['variance_share_pct']:.1f} %",
                va="center", fontsize=8)
    ax.set_xlim(0, 105)
    ax.set_xlabel("Share of seasonal GDD variance (%)", fontsize=8.5)
    ax.set_title(
        "Weather variance partition\n(season-total GDD, one row per field-year)",
        fontsize=10.5,
    )
    ax.grid(True, axis="x", color="#ececec", linewidth=0.6)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    if note:
        fig.text(0.01, 0.012, note, ha="left", va="bottom", fontsize=7, color="#4b5563",
                 wrap=True)
        fig.tight_layout(rect=(0, 0.055, 1, 1))
    else:
        fig.tight_layout()
    _save(fig, png)


def run_weather(ds: Dataset, out: Path, *, extra_growers: dict[str, pd.DataFrame] | None = None) -> dict:
    primary = weather_daily(ds.weather)
    primary["grower"] = ds.grower_slug or "grower"

    plot_weather_gdd(primary, out / "weather_gdd_curves.png", out / "weather_gdd_curves.csv")
    plot_weather_precip(primary, out / "weather_precip_heatmap.png", out / "weather_precip_heatmap.csv")

    combined = primary
    if extra_growers:
        blocks = [primary]
        for slug, frame in extra_growers.items():
            other = weather_daily(frame)
            other["grower"] = slug
            blocks.append(other)
        combined = pd.concat(blocks, ignore_index=True)

    table, verdict = variance_partition(combined)
    note = " · ".join(
        part for part in (verdict.get("guard"), verdict.get("guard_field")) if part
    )
    plot_weather_variance(
        table, out / "weather_variance.png", out / "weather_variance.csv", note=note
    )
    verdict["artifacts"] = ["weather_gdd_curves.png", "weather_precip_heatmap.png", "weather_variance.png"]
    verdict["category"] = "weather"
    return verdict


# ---------------------------------------------------------------------------
# cross-grower
# ---------------------------------------------------------------------------


def discover_other_growers(growers_root: Path, *, exclude: str) -> dict[str, pd.DataFrame]:
    """Load one weather table per other grower, if any exist.

    Deliberately best-effort: a second grower is optional, and the caller turns
    the empty result into an explicit "not identified" note rather than an error.
    """
    found: dict[str, pd.DataFrame] = {}
    if not growers_root.is_dir():
        return found
    for grower_dir in sorted(growers_root.iterdir()):
        if not grower_dir.is_dir() or grower_dir.name == exclude:
            continue
        matches = sorted(grower_dir.glob("farms/*/derived/tables/*_weather_*.csv"))
        if matches:
            found[grower_dir.name] = pd.read_csv(matches[-1])
    return found


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--grower-slug", default=os.environ.get("AG_GROWER_SLUG", "default-grower"))
    parser.add_argument("--farm-slug", default=os.environ.get("AG_FARM_SLUG", "default-farm"))
    parser.add_argument("--data-root", default=os.environ.get("DATA_PIPELINE_DATA_ROOT"))
    parser.add_argument("--boundaries", default=None, help="Override farm boundary GeoJSON")
    parser.add_argument("--weather", default=None, help="Override weather CSV")
    parser.add_argument("--cdl", default=None, help="Override CDL composition CSV")
    parser.add_argument("--planted", default=None, help="Override planting provenance JSON")
    parser.add_argument("--out-dir", default=None, help="Defaults to the farm's derived/analysis/eda-field")
    parser.add_argument("--category", choices=["boundaries", "cdl", "weather", "all"], default="all")
    parser.add_argument("--growers-root", default=None, help="Root scanned for other growers")
    parser.add_argument("--no-cross-grower", action="store_true", help="Skip scanning for other growers")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.data_root and not (args.boundaries and args.weather and args.cdl):
        raise SystemExit("set DATA_PIPELINE_DATA_ROOT or pass --boundaries/--weather/--cdl explicitly")

    data_root = Path(args.data_root).expanduser() if args.data_root else Path.cwd()
    paths = resolve_inputs(
        data_root=data_root,
        grower_slug=args.grower_slug,
        farm_slug=args.farm_slug,
        boundaries=args.boundaries,
        weather=args.weather,
        cdl=args.cdl,
        planted=args.planted,
    )
    ds = load_dataset(paths, grower_slug=args.grower_slug, farm_slug=args.farm_slug)

    out_dir = (
        Path(args.out_dir).expanduser()
        if args.out_dir
        else data_root / "data-pipeline" / "growers" / args.grower_slug / "farms" / args.farm_slug
        / "derived" / "analysis" / "eda-field"
    )
    out_dir.mkdir(parents=True, exist_ok=True)

    summary: dict = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "grower_slug": args.grower_slug,
        "farm_slug": args.farm_slug,
        "inputs": ds.inputs,
        "categories": {},
    }

    if args.category in {"boundaries", "all"}:
        if ds.boundaries.empty:
            summary["categories"]["boundaries"] = {
                "category": "boundaries",
                "guard": "no field boundaries for this farm",
            }
        else:
            summary["categories"]["boundaries"] = run_boundaries(ds, out_dir)
    if args.category in {"cdl", "all"}:
        if ds.cdl.empty:
            summary["categories"]["cdl"] = {"category": "cdl", "guard": "no CDL composition table for this farm"}
        else:
            summary["categories"]["cdl"] = run_cdl(ds, out_dir)
    if args.category in {"weather", "all"}:
        if ds.weather.empty:
            summary["categories"]["weather"] = {
                "category": "weather",
                "guard": "no weather table for this farm",
            }
        else:
            extra: dict[str, pd.DataFrame] = {}
            if not args.no_cross_grower:
                growers_root = (
                    Path(args.growers_root) if args.growers_root
                    else data_root / "data-pipeline" / "growers"
                )
                extra = discover_other_growers(growers_root, exclude=args.grower_slug)
            summary["categories"]["weather"] = run_weather(ds, out_dir, extra_growers=extra)

    summary_path = out_dir / "run_summary.json"
    summary_path.write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
