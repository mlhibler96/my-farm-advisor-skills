#!/usr/bin/env python3
"""Build the one-time HTML report for the eda-field run.

This is deliberately outside the reusable subskill: it reads the runtime
artifact folder produced by ``scripts/field_eda.py``, inlines the PNGs as
base64 so the committed file is self-contained, and writes one HTML file.

Example:
    python build_field_eda_report.py \
        --data-root ~/my-farm-advisor-runtime \
        --grower-slug mo-grower --farm-slug hibler-farms \
        --out field-eda-hibler-farms.html
"""

from __future__ import annotations

import argparse
import base64
import html
import io
import json
from datetime import datetime
from pathlib import Path

import pandas as pd
from PIL import Image

MAX_WIDTH = 1200
FIGURE_WIDTHS = {
    "boundaries_adjacency.png": 900,
    "weather_variance.png": 900,
}


def embed(path: Path, width: int = MAX_WIDTH) -> str:
    with Image.open(path) as image:
        image = image.convert("RGB")
        if image.width > width:
            height = round(image.height * width / image.width)
            image = image.resize((width, height), Image.LANCZOS)
        buffer = io.BytesIO()
        # 256-colour palette keeps the committed HTML near half a megabyte
        # without visibly degrading the charts.
        image.convert("P", palette=Image.ADAPTIVE, colors=256).save(buffer, format="PNG", optimize=True)
    return "data:image/png;base64," + base64.b64encode(buffer.getvalue()).decode("ascii")


def figure(path: Path, caption: str, *, max_width: int | None = None) -> str:
    source = embed(path, max_width or FIGURE_WIDTHS.get(path.name, MAX_WIDTH))
    return (
        f'<figure><img src="{source}" alt="{html.escape(path.stem)}">'
        f"<figcaption>{caption}</figcaption></figure>"
    )


def table(headers: list[str], rows: list[list[str]], *, classes: str = "") -> str:
    head = "".join(f"<th>{html.escape(str(value))}</th>" for value in headers)
    body = []
    for row in rows:
        cells = "".join(f"<td>{html.escape(str(value))}</td>" for value in row)
        body.append(f"<tr>{cells}</tr>")
    class_attr = f' class="{classes}"' if classes else ""
    return f'<table{class_attr}><thead><tr>{head}</tr></thead><tbody>{"".join(body)}</tbody></table>'


def build(root: Path, grower: str, farm: str) -> str:
    summary = json.loads((root / "run_summary.json").read_text(encoding="utf-8"))
    boundaries = summary["categories"]["boundaries"]
    cdl = summary["categories"]["cdl"]
    weather = summary["categories"]["weather"]

    area = pd.read_csv(root / "boundaries_area_ratio.csv").sort_values("ratio_to_name_pct", ascending=False)
    purity = pd.read_csv(root / "cdl_purity.csv")
    rotation = pd.read_csv(root / "cdl_rotation.csv")
    gdd = pd.read_csv(root / "weather_gdd_curves.csv")
    precip = pd.read_csv(root / "weather_precip_heatmap.csv", index_col=0)

    total_acres = boundaries["total_acres"]
    field_count = boundaries["fields"]
    multipart = boundaries["findings"]["multipart_fields"]
    generated = datetime.fromisoformat(summary["generated_at"].replace("Z", "+00:00"))

    totals = gdd.groupby(["field_id", "year"])["cum_gdd"].max().reset_index(name="total")
    gdd_min = totals["total"].min()
    gdd_max = totals["total"].max()
    gdd_low_year = int(totals.loc[totals["total"].idxmin(), "year"])
    gdd_high_year = int(totals.loc[totals["total"].idxmax(), "year"])
    variance = pd.read_csv(root / "weather_variance.csv")
    variance_share = dict(zip(variance["term"], variance["variance_share_pct"]))
    portfolio = pd.read_csv(root / "boundaries_portfolio_map.csv")
    span_ew_km = (portfolio["framed_x_m"].max() - portfolio["framed_x_m"].min()) / 1000
    span_ns_km = (portfolio["framed_y_m"].max() - portfolio["framed_y_m"].min()) / 1000

    season_precip = precip.sum(axis=1)
    precip_min = season_precip.min()
    precip_max = season_precip.max()
    precip_min_year = int(season_precip.idxmin())
    precip_max_year = int(season_precip.idxmax())

    low_purity = purity[purity["dominant_pct"] < 70].sort_values("dominant_pct")
    median_purity = purity["dominant_pct"].median()

    off_name = area[(area["ratio_to_name_pct"] - 100).abs() > 15]
    off_name_text = ", ".join(
        f"{row.field_id} ({row.ratio_to_name_pct:.1f} %)" for row in off_name.itertuples()
    )
    inliers = area[(area["ratio_to_name_pct"] - 100).abs() <= 15]
    ratio_inlier_min = inliers["ratio_to_name_pct"].min()
    ratio_inlier_max = inliers["ratio_to_name_pct"].max()

    rotation_rows = []
    for row in rotation.sort_values("field_id").itertuples():
        rotation_rows.append([row.field_id, row.sequence, row.cdl_last, row.planted, row.outcome])

    low_purity_rows = [
        [row.field_id, row.year, row.dominant_crop, f"{row.dominant_pct:.1f} %"]
        for row in low_purity.itertuples()
    ]

    cdl_years = f"{cdl['years'][0]}–{cdl['years'][-1]}"
    weather_years = f"{int(precip.index.min())}–{int(precip.index.max())}"
    multipart_ids = ", ".join(area[area["part_count"] > 1]["field_id"])

    css = """
:root { color-scheme: light; }
* { box-sizing: border-box; }
body { margin: 0; background: #f6f5f1; color: #1d2520;
  font: 16px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; }
main { max-width: 1120px; margin: 0 auto; padding: 40px 28px 64px; }
header.hero { border-bottom: 4px solid #2f5d46; padding-bottom: 20px; margin-bottom: 28px; }
.kicker { text-transform: uppercase; letter-spacing: .12em; font-size: 12px; font-weight: 700; color: #2f5d46; }
h1 { font-size: 34px; line-height: 1.15; margin: 8px 0 10px; }
h2 { font-size: 23px; margin: 42px 0 12px; padding-top: 8px; border-top: 1px solid #d7d4cb; }
h3 { font-size: 18px; margin: 26px 0 8px; color: #2f5d46; }
p { margin: 8px 0; }
.lede { font-size: 18px; color: #46524b; max-width: 900px; }
.facts { display: flex; flex-wrap: wrap; gap: 8px; margin: 18px 0 6px; padding: 0; list-style: none; }
.facts li { background: #e7ece6; border: 1px solid #cfd8cf; border-radius: 999px; padding: 4px 12px; font-size: 14px; }
.meta { font-size: 13px; color: #68736c; }
table { width: 100%; border-collapse: collapse; margin: 14px 0 8px; background: #fff; font-size: 14px; }
th, td { text-align: left; padding: 8px 10px; border-bottom: 1px solid #e4e1d8; vertical-align: top; }
th { background: #eef1ec; font-size: 12px; text-transform: uppercase; letter-spacing: .05em; color: #46524b; }
tr:last-child td { border-bottom: none; }
figure { margin: 18px 0 26px; background: #fff; border: 1px solid #e0ddd4; border-radius: 8px; padding: 12px; }
figure img { display: block; width: 100%; height: auto; }
figcaption { margin-top: 10px; font-size: 14px; color: #46524b; }
figcaption strong { color: #1d2520; }
.grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(430px, 1fr)); gap: 0 22px; align-items: start; }
.note { background: #fff8e6; border-left: 4px solid #d8a33a; padding: 12px 16px; margin: 18px 0; }
.ok { background: #eaf3ec; border-left: 4px solid #2f7d3a; padding: 12px 16px; margin: 18px 0; }
ul.tight { margin: 8px 0; padding-left: 22px; }
ul.tight li { margin: 5px 0; }
code { background: #eceae2; padding: 1px 5px; border-radius: 3px; font-size: 13px; }
pre { background: #202a24; color: #e8efe9; padding: 14px 16px; border-radius: 6px; overflow-x: auto; font-size: 13px; }
footer { margin-top: 46px; padding-top: 16px; border-top: 1px solid #d7d4cb; font-size: 13px; color: #68736c; }
@media print {
  body { background: #fff; }
  main { max-width: none; padding: 0; }
  figure { break-inside: avoid; border: none; padding: 0; }
  h2 { break-after: avoid; }
  a { color: inherit; text-decoration: none; }
}
"""

    figures = [
        (
            "boundaries_area_ratio.png",
            f"<strong>Area vs. acreage token in the field name.</strong> Each bar is measured geometry area as a "
            "percentage of the number embedded in the field name. The dashed line is 100 %; red marks more than "
            f"15 % off. Nine fields sit at {ratio_inlier_min:.0f}–{ratio_inlier_max:.0f} %; only "
            f"<code>{off_name.iloc[0].field_id}</code> is far out at {off_name.iloc[0].ratio_to_name_pct:.0f} %. "
            "Field names are labels, not survey measurements, so this is a naming/geometry consistency check "
            "rather than an error budget.",
        ),
        (
            "boundaries_shape.png",
            "<strong>Boundary shape quality.</strong> Polsby–Popper compactness (1.0 = circle) against polygon part "
            "count; bubble area scales with acres and colour is the 2026 planted crop. Single-part fields cluster "
            "near the top-left. The multipart fields are less compact, and <code>17ac-iwillis</code> is the extreme "
            "case: four parts and compactness 0.14.",
        ),
        (
            "boundaries_adjacency.png",
            "<strong>Field separation (comparison).</strong> Edge-to-edge gap in metres for every field pair, with "
            "shared-edge length in the CSV. Only <code>23ac-north-brady</code> and <code>33ac-south</code> actually "
            "touch (4.54 m shared edge, 0.4 m gap). The next closest pair is <code>12ac-flot</code> and "
            "<code>21ac-clay-7</code> at 42 m. No pair overlaps.",
        ),
        (
            "cdl_crop_matrix.png",
            "<strong>CDL dominant class by field and year.</strong> One cell per field-year for 10 fields × 5 CDL "
            "years. Eight fields alternate Corn and Soybeans. <code>12ac-flot</code> is continuous corn, and "
            "<code>17ac-iwillis</code> carries a 2024 Grass/Pasture break.",
        ),
        (
            "cdl_purity.png",
            f"<strong>CDL class purity per field-year.</strong> Each dot is one year's share of the dominant CDL "
            f"class inside the field boundary; the red line marks 70 %. The median is {median_purity:.1f} %, so most "
            "field-years are cleanly one crop. Three low-purity cases are visible: <code>17ac-iwillis</code> in "
            "2024 (24.6 %, "
            "Grass/Pasture), 2021 (53.6 %) and <code>12ac-kev</code> in 2023 (57.7 %). Low purity can mean a real "
            "mixed year, a boundary that clips neighbouring land, or both.",
        ),
        (
            "cdl_rotation.png",
            "<strong>Rotation outcome (comparison).</strong> CDL's 2025 class on the left, the 2026 planted record "
            "on the right, one row per field. A row that changes crop is a <em>flip</em>; a row that stays is a "
            f"<em>repeat</em>. The result is {cdl['flips_to_2026']} flips and {cdl['repeats_to_2026']} repeats out "
            f"of {cdl['comparable_2026']} comparable fields.",
        ),
        (
            "weather_gdd_curves.png",
            f"<strong>Cumulative growing degree days.</strong> Season-to-date GDD (°F, base 50, cap 86) from 1 April "
            f"to 31 October for <code>10ac-m-d</code>, one line per year. The {gdd_low_year} season finishes lowest "
            f"at {gdd_min:,.0f} °F and {gdd_high_year} highest at {gdd_max:,.0f} °F; the spread is about "
            f"{(gdd_max - gdd_min) / gdd_max * 100:.0f} % of the seasonal total.",
        ),
        (
            "weather_precip_heatmap.png",
            f"<strong>Monthly precipitation.</strong> One field's monthly totals for {weather_years}, in millimetres, "
            "because every field samples the same weather cell. Season totals range from "
            f"{precip_min:,.0f} mm ({precip_min_year}) to {precip_max:,.0f} mm ({precip_max_year}). June 2021 is the "
            "wettest month at 218 mm; February 2024 is the driest at 7 mm.",
        ),
        (
            "weather_variance.png",
            f"<strong>Weather variance partition (comparison).</strong> Season-total GDD is split into field, year, "
            f"and residual terms. Year explains {variance_share.get('year', 0):.0f} % of the variance. The field term "
            f"is {variance_share.get('field', 0):.0f} % <em>by construction</em> because all ten fields share one "
            "weather series, not because fields were shown to be identical. The grower term is skipped: only one "
            "grower is in scope.",
        ),
        (
            "boundaries_portfolio_map.png",
            f"<strong>Field portfolio map.</strong> All ten fields in local metres (UTM zone 15N, EPSG:32615), with "
            "north arrow and numbered labels keyed to the legend. Colour is the 2026 planted crop and the legend "
            f"gives acres. The portfolio spans roughly {span_ew_km:.1f} km east–west and {span_ns_km:.1f} km "
            "north–south and forms several clusters rather than one contiguous block.",
        ),
    ]

    figures_html = []
    for name, caption in figures:
        max_width = 900 if name in {"boundaries_adjacency.png", "weather_variance.png"} else None
        figures_html.append(figure(root / name, caption, max_width=max_width))
    figures_html = "\n".join(figures_html)

    report = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Field-level EDA — Hibler Farms</title>
<style>{css}</style>
</head>
<body>
<main>
<header class="hero">
  <div class="kicker">My Farm Advisor · one-time report artifact</div>
  <h1>Field-level EDA: Hibler Farms</h1>
  <p class="lede">A field, field-year and within-grower look at the derived field boundaries, CDL crop history,
  and gridded weather for one Missouri farm, built from the reusable <code>eda-field</code> subskill's
  ten figures and ten CSV tables.</p>
  <p class="meta">Grower <strong>Hibler Farms</strong> (<code>{html.escape(grower)}</code>) ·
  farm <code>{html.escape(farm)}</code> · run generated {generated:%Y-%m-%d %H:%M} UTC ·
  this file is committed; the runtime artifacts are not.</p>
  <ul class="facts">
    <li>{field_count} fields</li>
    <li>{total_acres:.1f} derived acres</li>
    <li>{cdl_years} CDL</li>
    <li>{weather_years} weather</li>
    <li>1 grower</li>
  </ul>
</header>

<section id="scope">
<h2>1. Dataset scope and grower location</h2>
<p>The farm is in west-central Missouri, at roughly <strong>39.03° N, 94.04° W</strong> — about 30 miles east of
Kansas City. The ten field centroids sit within a few kilometres of each other, so the portfolio is one local
neighbourhood rather than a multi-county operation. All spatial work is done in local metres (UTM zone 15N) to
avoid degree-based distance errors.</p>
<p>The data are a single farm snapshot, not a longitudinal farm business record. The boundary layer describes
<strong>2026</strong> as-applied coverage; CDL gives the preceding five years of land cover; weather gives the same
five seasons. There is no yield, soil-test, machine-log, or management-record layer in this run.</p>

<h3>Data layers used</h3>
{table(
    ["Layer", "Source in this run", "What it contributes", "Coverage"],
    [
        ["Field boundaries", "AgFiniti as-applied point buffers, 30 ft swath, unioned by field",
         "Field footprint, acres, shape, multipart structure, adjacency", "10 fields, 2026"],
        ["CDL composition", "USDA Cropland Data Layer, summarised to class shares inside each boundary",
         "Dominant crop class and class purity per field-year", "10 fields × 5 years (50 field-years)"],
        ["Weather", "NASA POWER daily reanalysis, aggregated to season GDD and monthly precipitation",
         "Cumulative GDD curves, monthly precipitation, variance partition", "5 seasons (2021–2025) on one gridded cell"],
        ["Planted record", "AgFiniti derived-field provenance manifest (crop + operation)",
         "2026 planted crop for the rotation outcome check", "10 fields after name fallback"],
    ],
)}
<p class="meta">Boundaries are derived, not surveyed: the manifest warns that headlands not covered by as-applied
points are excluded and that a few percent area difference from nominal acreage is expected.</p>
</section>

<section id="levels">
<h2>2. Comparison levels</h2>
<p>The analysis uses the levels the data actually support, and states where a level is not identified.</p>
{table(
    ["Level", "What is compared", "Status in this run"],
    [
        ["Within field over time", "CDL class and purity 2021–2025; cumulative GDD by season",
         "Used for all 10 fields"],
        ["Across fields", "Area, shape, compactness, adjacency, 2026 crop",
         "Used for all 10 fields (45 field pairs)"],
        ["Field-year", "Dominant class, purity, season-total GDD, rotation outcome",
         f"Used: {len(purity)} CDL and {weather['field_years']} weather field-years"],
        ["Grower", "One grower's fields aggregated", "Used (single group)"],
        ["Across growers", "Field vs. year vs. grower variance shares",
         "Not identified: only one grower exists, so the grower term is skipped rather than reported as zero"],
    ],
)}
</section>

<section id="figures">
<h2>3. What each visualization shows</h2>
<p>Each category has two statistical views and one comparison view. Numbers behind every panel are in the
matching CSV in the runtime artifact folder.</p>
<div class="grid">
{figures_html}
</div>
</section>

<section id="analysis">
<h2>4. What the comparison and correlation analysis shows</h2>
<ul class="tight">
  <li><strong>Rotation consistency.</strong> {cdl['flips_to_2026']} of {cdl['comparable_2026']} fields flip crop
  between the last CDL year and the 2026 planted record; {cdl['repeats_to_2026']} repeat. A two-sided binomial
  test gives p = {cdl['flip_binomial_p']:.4f}. With n = 10 this is a structural check, not evidence that the
  grower deliberately rotates.</li>
  <li><strong>CDL purity vs. field area.</strong> Spearman ρ = {cdl['purity_vs_log_area_spearman']:.3f}
  (p = {cdl['purity_vs_log_area_p']:.4f}) between dominant-class share and log area. There is no detectable
  monotonic relationship: small and large fields both reach high purity. The low-purity cases are specific
  field-years, not a size effect.</li>
  <li><strong>Name token vs. measured area.</strong> Nine of ten fields are within 15 % of the acreage number in
  their name (range {ratio_inlier_min:.1f}–{ratio_inlier_max:.1f} %). The exception is {off_name_text}. The name is a label;
  the derived geometry is the measurement.</li>
  <li><strong>Shape and fragmentation.</strong> {multipart} of {field_count} boundaries are multipart
  ({multipart_ids}). Multipart fields have lower compactness, and <code>17ac-iwillis</code> is the outlier at
  0.14 compactness with four parts.</li>
  <li><strong>Adjacency.</strong> One pair touches (<code>23ac-north-brady</code> / <code>33ac-south</code>,
  {boundaries['findings']['max_shared_edge_m']:.2f} m shared edge) and one pair is close but separate
  (<code>12ac-flot</code> / <code>21ac-clay-7</code>, 42 m). The remaining pairs are hundreds to thousands of
  metres apart, and there are no overlaps.</li>
  <li><strong>Weather variance.</strong> Season-total GDD varies far more by year than by field: year explains
  {variance_share.get('year', 0):.0f} % of the variance, field {variance_share.get('field', 0):.0f} % by
  construction, and the residual {variance_share.get('residual (interaction)', 0):.0f} %. This is a
  data-structure result, not an agronomic finding.</li>
</ul>

<h3>Rotation detail</h3>
{table(["Field", "CDL 2021–2025 sequence", "CDL 2025", "2026 planted", "Outcome"], rotation_rows)}
<p class="meta">The sequence is the dominant CDL class per year. “Flip” means the 2026 planted crop differs from
the 2025 CDL class; “repeat” means it matches. A CDL year is a land-cover observation, not a planting record.</p>

<h3>Low-purity field-years</h3>
{table(["Field", "Year", "Dominant CDL class", "Share"], low_purity_rows)}
<p class="meta">Median purity across all 50 field-years is {median_purity:.1f} %. These three cases fall below the
70 % reference line; they are the main places where a single dominant-class label hides a mixed or edge-affected
footprint.</p>
</section>

<section id="map">
<h2>5. What the geospatial map shows</h2>
<p>The portfolio map is the only map in this run. It places all ten derived boundaries on one local-metre frame
with a north arrow, numbers each field, colours by 2026 crop, and repeats acres in the legend. It shows that the
farm is a set of separated clusters, that five fields are multipart, and that only one pair is physically
adjacent. A CDL raster map was not produced because the run ingests the CDL composition table, not the source
raster; the map therefore carries crop as a field-level attribute rather than a per-pixel layer.</p>
</section>

<section id="limits">
<h2>6. Limitations, missing data, and assumptions</h2>
<ul class="tight">
  <li><strong>One grower only.</strong> Across-grower comparison is not identified. The variance partition skips
  the grower term and says so, instead of presenting a zero as evidence.</li>
  <li><strong>One weather series.</strong> All ten fields resolve to the same gridded weather cell (the tables
  store field centroids, but the daily values are identical). Field-level weather replication is therefore not
  identified; the field share of GDD variance is 0 % by construction.</li>
  <li><strong>Derived boundaries.</strong> Footprints come from buffered as-applied points, not survey or platform
  exports. Headlands can be missing, and area can differ from nominal acres by a few percent. Shape metrics
  describe the derived polygon, not the legal field.</li>
  <li><strong>Names are labels.</strong> The “10ac”, “21ac” tokens are not used as measurements. The area-ratio
  panel only flags where the label and the geometry disagree enough to be worth a look.</li>
  <li><strong>CDL is coarse land cover.</strong> A 30 m raster summarised inside a field boundary mixes edge
  pixels, strips, and neighbouring land. Low purity is ambiguous between a real mixed year and boundary
  contamination. CDL is also not a management record and does not prove what was planted.</li>
  <li><strong>Short panel, small n.</strong> Five CDL years and fifty field-years support description and
  structural checks, not causal inference. The rotation binomial p-value and the purity correlation are reported
  as diagnostics; neither is treated as agronomic evidence.</li>
  <li><strong>Planted-record fallback.</strong> Six of ten manifest entries had no <code>field_slug</code>.
  Those fields were resolved by a unique normalized <code>field_name</code> match against the boundary table, and
  a Planting record outranks a Grain Harvest record for the same field. All ten fields resolved; no field was
  left as “no planted record”.</li>
  <li><strong>Not in scope.</strong> No yield, irrigation, fertility, pest, economics, or soil-moisture layer.
  Weather is gridded reanalysis, not an on-farm station.</li>
</ul>
</section>

<section id="soil">
<h2>7. Soil analysis</h2>
<div class="ok"><strong>Soil analysis was not required and was not the focus of this EDA.</strong> No soil
sampling, texture, nutrient, pH, organic-matter, or plant-available-water analysis was performed, and no
conclusion in this report depends on soil properties. The CDL layer is crop and land-cover history; despite
living beside soil tooling in the repository, it is not a soil measurement.</div>
</section>

<section id="repro">
<h2>8. Reproducing the report</h2>
<p>The report is assembled from the runtime folder written by the reusable subskill. Nothing in that folder is
committed. To regenerate:</p>
<pre>python my-farm-advisor/eda/eda-field/scripts/field_eda.py \\
  --data-root ~/my-farm-advisor-runtime \\
  --grower-slug {html.escape(grower)} --farm-slug {html.escape(farm)}
python reports/build_field_eda_report.py \\
  --data-root ~/my-farm-advisor-runtime \\
  --grower-slug {html.escape(grower)} --farm-slug {html.escape(farm)}</pre>
<p>The run writes ten PNG figures, ten CSV tables, and <code>run_summary.json</code> under
<code>derived/analysis/eda-field/</code>. The CSVs are the source of every number in this HTML; the images are
inlined here so the committed file opens on its own.</p>
{table(
    ["Category", "Statistical views", "Comparison view", "Map"],
    [
        ["Boundaries", "Area vs. name token; shape quality",
         "Pairwise separation", "Field portfolio (the one map)"],
        ["CDL", "Crop matrix; class purity", "2025 CDL → 2026 planted rotation outcome", "—"],
        ["Weather", "Cumulative GDD; monthly precipitation", "Field / year variance partition", "—"],
    ],
)}
</section>

<footer>
  <p>One-time report artifact for the <code>eda-field</code> subskill · generated {generated:%Y-%m-%d} from the
  run at <code>{html.escape(str(root))}</code> · soil analysis out of scope.</p>
</footer>
</main>
</body>
</html>
"""
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data-root", type=Path, default=Path("~/my-farm-advisor-runtime").expanduser())
    parser.add_argument("--grower-slug", default="mo-grower")
    parser.add_argument("--farm-slug", default="hibler-farms")
    parser.add_argument("--out", type=Path, default=Path(__file__).with_name("field-eda-hibler-farms.html"))
    args = parser.parse_args()

    root = (
        args.data_root
        / "data-pipeline"
        / "growers"
        / args.grower_slug
        / "farms"
        / args.farm_slug
        / "derived"
        / "analysis"
        / "eda-field"
    )
    if not root.is_dir():
        raise SystemExit(f"artifact folder not found: {root}")

    document = build(root, args.grower_slug, args.farm_slug)
    args.out.write_text(document, encoding="utf-8")
    print(f"wrote {args.out} ({args.out.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()
