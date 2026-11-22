"""Single-file Leaflet template for the grower web map.

The map is deliberately lightweight: Leaflet comes from a CDN (two requests),
the basemap is a normal online tile layer, and the only embedded data is the
field geometry plus a small metadata object. Nothing raster or per-scene is
ever inlined here -- field posters, NDVI composites, and terrain rasters stay
on disk.

This module is dependency-free on purpose (no geopandas import) so the map
payload can be assembled and the HTML rendered without a geospatial stack.
"""

from __future__ import annotations

import json
from datetime import date

# Categorical stroke colours, one per farm inside a grower.
FARM_COLORS = (
    "#16a34a",
    "#2563eb",
    "#db2777",
    "#d97706",
    "#7c3aed",
    "#0891b2",
    "#65a30d",
    "#dc2626",
)

def prettify_slug(value: str | None) -> str:
    """Turn ``mo-grower`` into ``MO Grower`` for display.

    The pipeline stores slugs, not names. Grower metadata often carries the
    slug again as ``display_name``, which reads badly in a map popup, so the
    slug is the fallback and this function makes it presentable.
    """
    if not value:
        return "Unknown grower"
    words = [w for w in str(value).replace("_", "-").split("-") if w]
    if not words:
        return str(value)
    parts = []
    for index, word in enumerate(words):
        if index == 0 and len(word) == 2 and word.isalpha():
            parts.append(word.upper())
        else:
            parts.append(word[:1].upper() + word[1:])
    return " ".join(parts)


def display_name(metadata: dict | None, fallback_slug: str) -> str:
    """Prefer a real ``display_name`` over the slug."""
    if isinstance(metadata, dict):
        name = metadata.get("display_name")
        if isinstance(name, str) and name.strip() and name.strip() != fallback_slug:
            return name.strip()
    return prettify_slug(fallback_slug)


def farm_color(index: int) -> str:
    return FARM_COLORS[index % len(FARM_COLORS)]


def render_webmap_html(payload: dict, title: str | None = None) -> str:
    """Render the single-file map for an assembled payload.

    ``payload`` must provide ``grower``, ``farms``, ``fields``, ``geojson``,
    ``bounds`` and ``stats`` -- see ``generate_grower_webmap.py``.
    """
    page_title = title or payload.get("title") or "Grower field map"
    safe_json = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    # Keep a stray "</script>" inside a field name from closing the tag.
    safe_json = safe_json.replace("</", "<\\/")
    generated = payload.get("generated") or date.today().isoformat()
    return (
        _TEMPLATE.replace("__PAGE_TITLE__", _html_escape(page_title))
        .replace("__PAYLOAD__", safe_json)
        .replace("__GENERATED__", _html_escape(str(generated)))
    )


def _html_escape(value: str) -> str:
    return (
        str(value)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


_TEMPLATE = r"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8" />
<meta name="viewport" content="width=device-width, initial-scale=1.0" />
<title>__PAGE_TITLE__</title>
<link rel="stylesheet" href="https://unpkg.com/leaflet@1.9.4/dist/leaflet.css"
      integrity="sha256-p4NxAoJBhIIN+hmNHrzRCf9tD/miZyoHS5obTRR9BMY=" crossorigin="" />
<script src="https://unpkg.com/leaflet@1.9.4/dist/leaflet.js"
        integrity="sha256-20nQCchB9co0qIjJZRGuk2/Z9VM+kNiyxNV1lvTlZBo=" crossorigin=""></script>
<style>
  :root {
    --ink: #0f172a;
    --muted: #64748b;
    --line: #e2e8f0;
    --paper: #ffffff;
    --wash: #f8fafc;
    --accent: #166534;
  }
  * { box-sizing: border-box; }
  html, body { margin: 0; height: 100%; }
  body {
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
    color: var(--ink);
    background: var(--wash);
    display: flex;
    flex-direction: column;
  }
  header {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 16px;
    padding: 12px 18px;
    background: var(--paper);
    border-bottom: 1px solid var(--line);
    z-index: 1200;
  }
  .brand { display: flex; align-items: center; gap: 12px; min-width: 0; }
  .brand h1 { font-size: 1.05rem; margin: 0; letter-spacing: 0.01em; }
  .brand p { margin: 2px 0 0; font-size: 0.78rem; color: var(--muted); }
  .brand .logo { font-size: 1.4rem; }
  .head-actions { display: flex; align-items: center; gap: 8px; }
  button {
    font: inherit;
    font-size: 0.82rem;
    padding: 7px 12px;
    border-radius: 7px;
    border: 1px solid var(--line);
    background: var(--paper);
    color: var(--ink);
    cursor: pointer;
  }
  button:hover { border-color: #94a3b8; }
  #toggle-sidebar { display: none; font-size: 1rem; padding: 6px 10px; }
  main { position: relative; flex: 1; display: flex; min-height: 0; }
  #sidebar {
    width: 310px;
    flex: 0 0 310px;
    background: var(--paper);
    border-right: 1px solid var(--line);
    display: flex;
    flex-direction: column;
    min-height: 0;
  }
  .search { padding: 12px; border-bottom: 1px solid var(--line); }
  .search input {
    width: 100%;
    font: inherit;
    font-size: 0.85rem;
    padding: 8px 10px;
    border: 1px solid var(--line);
    border-radius: 7px;
    background: var(--wash);
  }
  .search input:focus { outline: 2px solid #bbf7d0; border-color: #86efac; }
  #field-list { flex: 1; overflow-y: auto; padding: 6px 0 14px; }
  .farm-head {
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 8px;
    padding: 10px 14px 6px;
  }
  .farm-head span {
    font-size: 0.7rem;
    font-weight: 700;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--muted);
  }
  .farm-head button {
    font-size: 0.7rem;
    padding: 3px 8px;
    border-radius: 6px;
  }
  .field-row {
    display: flex;
    align-items: center;
    gap: 10px;
    width: 100%;
    text-align: left;
    border: 0;
    border-left: 3px solid transparent;
    border-radius: 0;
    background: transparent;
    padding: 8px 14px;
  }
  .field-row:hover { background: var(--wash); }
  .field-row.active {
    background: #f0fdf4;
    border-left-color: var(--accent);
  }
  .field-row .dot {
    width: 10px;
    height: 10px;
    border-radius: 3px;
    flex: 0 0 10px;
  }
  .field-row .name { flex: 1; min-width: 0; }
  .field-row .name strong {
    display: block;
    font-size: 0.86rem;
    font-weight: 600;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }
  .field-row .name small { color: var(--muted); font-size: 0.72rem; }
  .field-row .acres { font-size: 0.75rem; color: var(--muted); white-space: nowrap; }
  .legend { border-top: 1px solid var(--line); padding: 10px 14px; }
  .legend h2 {
    font-size: 0.68rem;
    letter-spacing: 0.08em;
    text-transform: uppercase;
    color: var(--muted);
    margin: 0 0 6px;
  }
  .legend .row { display: flex; align-items: center; gap: 8px; font-size: 0.76rem; margin: 3px 0; }
  .legend .swatch { width: 14px; height: 10px; border-radius: 2px; }
  .sidebar-foot {
    border-top: 1px solid var(--line);
    padding: 9px 14px;
    font-size: 0.68rem;
    line-height: 1.5;
    color: var(--muted);
  }
  #map { flex: 1; min-width: 0; background: #dbeafe; }
  .leaflet-popup-content { margin: 12px 14px; }
  .pop h3 { margin: 0 0 2px; font-size: 1rem; }
  .pop .pop-sub { margin: 0 0 10px; color: var(--muted); font-size: 0.76rem; }
  .pop table { border-collapse: collapse; width: 100%; font-size: 0.78rem; }
  .pop th {
    text-align: left;
    font-weight: 500;
    color: var(--muted);
    padding: 3px 10px 3px 0;
    white-space: nowrap;
    vertical-align: top;
  }
  .pop td { padding: 3px 0; vertical-align: top; overflow-wrap: anywhere; }
  .pop .chips { margin-top: 10px; display: flex; flex-wrap: wrap; gap: 4px; }
  .pop .chip {
    font-size: 0.68rem;
    background: var(--wash);
    border: 1px solid var(--line);
    border-radius: 999px;
    padding: 2px 7px;
    white-space: nowrap;
  }
  .pop .chip.current { background: #dcfce7; border-color: #86efac; font-weight: 600; }
  .map-badge {
    background: rgba(255, 255, 255, 0.94);
    padding: 5px 8px;
    border-radius: 6px;
    font-size: 0.7rem;
    color: var(--muted);
    box-shadow: 0 1px 4px rgba(15, 23, 42, 0.18);
  }
  @media (max-width: 820px) {
    #toggle-sidebar { display: inline-block; }
    .leaflet-popup-content { margin: 10px 12px; }
    #sidebar {
      position: absolute;
      inset: 0 auto 0 0;
      z-index: 1100;
      width: min(86vw, 320px);
      box-shadow: 12px 0 30px rgba(15, 23, 42, 0.18);
      transform: translateX(-102%);
      transition: transform 160ms ease-out;
    }
    body.sidebar-open #sidebar { transform: translateX(0); }
  }
</style>
</head>
<body>
<header>
  <div class="brand">
    <span class="logo" aria-hidden="true">&#127806;</span>
    <div>
      <h1 id="page-title">__PAGE_TITLE__</h1>
      <p id="page-sub">Loading grower fields&hellip;</p>
    </div>
  </div>
  <div class="head-actions">
    <button id="fit-all" type="button">Fit grower</button>
    <button id="toggle-sidebar" type="button" aria-label="Toggle field list">&#9776;</button>
  </div>
</header>
<main>
  <aside id="sidebar">
    <div class="search">
      <input id="search" type="search" placeholder="Search fields&hellip;" autocomplete="off" />
    </div>
    <div id="field-list"></div>
    <div class="legend">
      <h2>Farms</h2>
      <div id="legend-rows"></div>
    </div>
    <div class="sidebar-foot">
      <div id="sidebar-foot-data"></div>
      <div>__GENERATED__</div>
      <div>Basemap tiles: Esri World Imagery / OpenStreetMap (online)</div>
      <div>Field polygons from the data pipeline. No imagery is embedded.</div>
    </div>
  </aside>
  <div id="map"></div>
</main>
<script>
(function () {
  "use strict";
  var DATA = __PAYLOAD__;
  var byKey = {};
  DATA.fields.forEach(function (field) { byKey[field.key] = field; });
  var fieldLayers = {};

  var imagery = L.tileLayer(
    "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
    {
      maxZoom: 19,
      attribution: "Tiles &copy; Esri &mdash; Source: Esri, Maxar, Earthstar Geographics"
    }
  );
  var streets = L.tileLayer("https://tile.openstreetmap.org/{z}/{x}/{y}.png", {
    maxZoom: 19,
    attribution: "&copy; OpenStreetMap contributors"
  });

  var map = L.map("map", { zoomControl: true, preferCanvas: true });
  imagery.addTo(map);
  L.control.layers({ "Satellite (Esri)": imagery, "Streets (OSM)": streets }, null, {
    position: "topright",
    collapsed: true
  }).addTo(map);
  L.control.scale({ position: "bottomleft", imperial: true }).addTo(map);

  function esc(value) {
    if (value === null || value === undefined) return "";
    return String(value)
      .replace(/&/g, "&amp;")
      .replace(/</g, "&lt;")
      .replace(/>/g, "&gt;")
      .replace(/"/g, "&quot;");
  }

  function num(value) {
    var parsed = Number(value);
    if (!isFinite(parsed) || value === null || value === "") return "";
    return parsed.toFixed(parsed >= 100 ? 0 : 1);
  }

  function popupHtml(field) {
    var rows = [];
    rows.push(["Grower", field.grower]);
    rows.push(["Farm", field.farm]);
    rows.push(["Field", field.name]);
    rows.push(["Field ID", field.id]);
    rows.push(["Area", num(field.acres) + " ac"]);
    if (field.crop) rows.push(["Crop (latest)", field.crop]);
    if (field.state) rows.push(["State", field.state]);
    if (field.soil) {
      if (field.soil.dominant) rows.push(["Dominant soil", field.soil.dominant]);
      if (field.soil.drainage) rows.push(["Drainage", field.soil.drainage]);
      if (field.soil.om !== null && field.soil.om !== undefined)
        rows.push(["Organic matter", num(field.soil.om) + " %"]);
      if (field.soil.ph !== null && field.soil.ph !== undefined)
        rows.push(["pH", Number(field.soil.ph).toFixed(2)]);
      if (field.soil.aws !== null && field.soil.aws !== undefined)
        rows.push(["Water storage", num(field.soil.aws) + " in"]);
    }
    var table = rows
      .map(function (row) {
        return "<tr><th>" + esc(row[0]) + "</th><td>" + esc(row[1]) + "</td></tr>";
      })
      .join("");

    var cropYears = field.crops ? Object.keys(field.crops).sort() : [];
    var chips = cropYears
      .map(function (year) {
        var current = field.cropYear !== null && String(field.cropYear) === String(year);
        return (
          '<span class="chip' + (current ? " current" : "") + '">' +
          esc(year) + " &middot; " + esc(field.crops[year]) +
          "</span>"
        );
      })
      .join("");
    var chipsBlock = chips ? '<div class="chips">' + chips + "</div>" : "";

    return (
      '<div class="pop">' +
      "<h3>" + esc(field.name) + "</h3>" +
      '<p class="pop-sub">' + esc(field.farm) + " &middot; " + esc(field.grower) + "</p>" +
      "<table>" + table + "</table>" +
      chipsBlock +
      "</div>"
    );
  }

  function highlight(key) {
    document.querySelectorAll(".field-row").forEach(function (row) {
      row.classList.toggle("active", row.dataset.key === key);
    });
  }

  function selectField(key) {
    // Sidebar action: always zoom to the field, then open its popup.
    var field = byKey[key];
    var layer = fieldLayers[key];
    if (!field || !layer) return;
    highlight(key);
    var opened = false;
    var open = function () {
      if (opened) return;
      opened = true;
      layer.openPopup();
    };
    // Opening during the zoom animation lets the popup auto-pan against a
    // moving map, which on phones pushes the title under the header. Fit
    // first, then open once the map has settled (with a timer fallback for
    // when the view already matches and no moveend fires).
    map.once("moveend", open);
    map.fitBounds(layer.getBounds(), { padding: [48, 48], maxZoom: 17 });
    window.setTimeout(open, 600);
    if (window.innerWidth <= 820) document.body.classList.remove("sidebar-open");
  }

  // A 340 px popup overflows a phone viewport; shrink it and give Leaflet
  // somewhere to pan the map so the table is never clipped.
  var popupOptions = {
    maxWidth: window.innerWidth < 600 ? 240 : 340,
    autoPan: true,
    autoPanPaddingTopLeft: [14, 78],
    autoPanPaddingBottomRight: [14, 20]
  };

  DATA.farms.forEach(function (farm) {
    L.geoJSON(DATA.geojson, {
      filter: function (feature) {
        return feature.properties.farm === farm.slug;
      },
      style: {
        color: farm.color,
        weight: 2.5,
        opacity: 0.95,
        fillColor: farm.color,
        fillOpacity: 0.2
      },
      onEachFeature: function (feature, layer) {
        var field = byKey[feature.properties.key];
        if (!field) return;
        fieldLayers[field.key] = layer;
        layer.bindPopup(popupHtml(field), popupOptions);
        layer.on("click", function () { highlight(field.key); });
        layer.on("mouseover", function () {
          layer.setStyle({ fillOpacity: 0.35, weight: 3 });
        });
        layer.on("mouseout", function () {
          layer.setStyle({ fillOpacity: 0.2, weight: 2.5 });
        });
      }
    }).addTo(map);
  });

  var listEl = document.getElementById("field-list");
  var searchEl = document.getElementById("search");

  function renderList(query) {
    var needle = (query || "").trim().toLowerCase();
    listEl.innerHTML = "";
    DATA.farms.forEach(function (farm) {
      var rows = DATA.fields.filter(function (field) {
        if (field.farmSlug !== farm.slug) return false;
        if (!needle) return true;
        return (
          String(field.name).toLowerCase().indexOf(needle) !== -1 ||
          String(field.id).toLowerCase().indexOf(needle) !== -1 ||
          String(field.crop || "").toLowerCase().indexOf(needle) !== -1
        );
      });
      if (!rows.length) return;

      var head = document.createElement("div");
      head.className = "farm-head";
      var label = document.createElement("span");
      label.textContent = farm.label;
      var fit = document.createElement("button");
      fit.type = "button";
      fit.textContent = "Zoom";
      fit.title = "Zoom to " + farm.label;
      fit.addEventListener("click", function () {
        var bounds = L.latLngBounds([]);
        rows.forEach(function (field) {
          var layer = fieldLayers[field.key];
          if (layer) bounds.extend(layer.getBounds());
        });
        if (bounds.isValid()) map.fitBounds(bounds, { padding: [40, 40], maxZoom: 17 });
      });
      head.appendChild(label);
      head.appendChild(fit);
      listEl.appendChild(head);

      rows.forEach(function (field) {
        var row = document.createElement("button");
        row.type = "button";
        row.className = "field-row";
        row.dataset.key = field.key;
        var dot = document.createElement("span");
        dot.className = "dot";
        dot.style.background = farm.color;
        var name = document.createElement("span");
        name.className = "name";
        var strong = document.createElement("strong");
        strong.textContent = field.name;
        var small = document.createElement("small");
        small.textContent = field.id + (field.crop ? " \u00b7 " + field.crop : "");
        name.appendChild(strong);
        name.appendChild(small);
        var acres = document.createElement("span");
        acres.className = "acres";
        acres.textContent = num(field.acres) + " ac";
        row.appendChild(dot);
        row.appendChild(name);
        row.appendChild(acres);
        row.addEventListener("click", function () {
          selectField(field.key);
        });
        listEl.appendChild(row);
      });
    });
    if (!listEl.children.length) {
      var empty = document.createElement("p");
      empty.style.cssText = "padding:16px;color:#64748b;font-size:0.82rem;";
      empty.textContent = "No fields match \u201c" + (query || "") + "\u201d.";
      listEl.appendChild(empty);
    }
  }

  var legendEl = document.getElementById("legend-rows");
  DATA.farms.forEach(function (farm) {
    var row = document.createElement("div");
    row.className = "row";
    var swatch = document.createElement("span");
    swatch.className = "swatch";
    swatch.style.background = farm.color;
    var text = document.createElement("span");
    text.textContent = farm.label + " \u00b7 " + farm.fieldCount + " field" + (farm.fieldCount === 1 ? "" : "s");
    row.appendChild(swatch);
    row.appendChild(text);
    legendEl.appendChild(row);
  });

  var stats = DATA.stats;
  var acreText = typeof stats.acres === "number"
    ? stats.acres.toLocaleString(undefined, { maximumFractionDigits: 1 })
    : "n/a";
  document.getElementById("page-sub").textContent =
    stats.farms + " farm" + (stats.farms === 1 ? "" : "s") + " \u00b7 " +
    stats.fields + " field" + (stats.fields === 1 ? "" : "s") + " \u00b7 " +
    acreText + " acres";
  document.getElementById("sidebar-foot-data").textContent =
    "Grower: " + DATA.grower.label + " (" + DATA.grower.slug + ")";

  if (DATA.bounds && DATA.bounds.length === 2) {
    map.fitBounds(DATA.bounds, { padding: [30, 30] });
  } else {
    map.setView([39.5, -94.5], 8);
  }

  document.getElementById("fit-all").addEventListener("click", function () {
    if (DATA.bounds && DATA.bounds.length === 2) map.fitBounds(DATA.bounds, { padding: [30, 30] });
  });
  document.getElementById("toggle-sidebar").addEventListener("click", function () {
    document.body.classList.toggle("sidebar-open");
  });
  searchEl.addEventListener("input", function () { renderList(searchEl.value); });

  renderList("");
})();
</script>
</body>
</html>
"""
