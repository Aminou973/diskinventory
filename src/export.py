"""
export — CSV + Markdown + static HTML reports.

The HTML report embeds the data as JSON inside a single file (no external
JS, no CDN). The override UI posts back to /api/overrides when the engine
is running with `--serve`; otherwise the override form falls back to a
downloadable overrides.json.
"""

from __future__ import annotations

import csv
import html
import io
import json
import os
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .collect import INVENTORY_FIELDS


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def write_csv(rows: list[dict], path: Path | str) -> Path:
    p = Path(path) if not isinstance(path, Path) else path
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "w", encoding="utf-8", newline="") as fh:
        w = csv.writer(fh, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        w.writerow(INVENTORY_FIELDS)
        for r in rows:
            w.writerow([r.get(c, "") for c in INVENTORY_FIELDS])
    return p


def write_markdown(env: dict, rows: list[dict], path: Path | str) -> Path:
    p = Path(path) if not isinstance(path, Path) else path
    p.parent.mkdir(parents=True, exist_ok=True)
    cat_counts = Counter(r.get("Category", "Other") for r in rows)
    size_by_cat = defaultdict(int)
    for r in rows:
        try:
            size_by_cat[r.get("Category", "Other")] += int(r.get("SizeBytes") or 0)
        except (TypeError, ValueError):
            pass
    lines: list[str] = []
    lines.append(f"# DiskInventory Report — {env.get('RunId','')}")
    lines.append("")
    lines.append(f"- Generated: {env.get('TimestampUtc','')}")
    lines.append(f"- OS: {env.get('Os',{}).get('Caption','')}")
    lines.append(f"- Admin: **{'yes' if env.get('Admin') else 'no'}**")
    lines.append(f"- Items: **{len(rows)}**")
    lines.append("")
    lines.append("## Categories")
    lines.append("")
    lines.append("| Category | Items | Bytes |")
    lines.append("|---|---:|---:|")
    for cat, count in sorted(cat_counts.items(), key=lambda kv: -kv[1]):
        lines.append(f"| {cat} | {count} | {size_by_cat[cat]:,} |")
    lines.append("")
    lines.append("## Top 25 by size")
    lines.append("")
    lines.append("| Path | Size | Category |")
    lines.append("|---|---:|---|")
    by_size = sorted(rows, key=lambda r: -int(r.get("SizeBytes") or 0))[:25]
    for r in by_size:
        size = int(r.get("SizeBytes") or 0)
        lines.append(f"| `{r.get('Path','')}` | {size:,} | {r.get('Category','')} |")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return p


# --- HTML report ----------------------------------------------------------

_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>DiskInventory __RUN_ID__</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 14px/1.45 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         margin: 0; padding: 1.5rem; background: Canvas; color: CanvasText; }
  h1 { margin-top: 0; font-size: 1.5rem; }
  .tile-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
               gap: .75rem; margin: 1rem 0; }
  .tile { padding: .75rem 1rem; border: 1px solid #8884; border-radius: 8px;
          background: color-mix(in oklab, Canvas 92%, CanvasText 8%); }
  .tile .v { font-size: 1.4rem; font-weight: 600; }
  .tile .k { font-size: .8rem; opacity: .75; text-transform: uppercase; letter-spacing: .04em; }
  table { border-collapse: collapse; width: 100%; margin: .5rem 0 1.5rem; }
  th, td { padding: .35rem .55rem; border-bottom: 1px solid #8883; text-align: left;
           font-size: 13px; vertical-align: top; }
  th { background: color-mix(in oklab, Canvas 86%, CanvasText 14%); position: sticky; top: 0; }
  tr:hover td { background: color-mix(in oklab, Canvas 96%, CanvasText 4%); }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  details { margin: .5rem 0 1rem; }
  summary { cursor: pointer; font-weight: 600; padding: .25rem 0; }
  .pill { display: inline-block; padding: 0 .5em; border-radius: 999px;
          background: color-mix(in oklab, Canvas 85%, CanvasText 15%);
          font-size: .8rem; }
  .pager { margin: 1rem 0; }
  .pager button { padding: .25rem .75rem; margin-right: .5rem; cursor: pointer; }
  input[type="search"] { padding: .4rem .55rem; width: 100%; max-width: 360px;
                         border: 1px solid #8886; border-radius: 6px;
                         background: Field; color: FieldText; }
  .override-row { cursor: pointer; }
  .override-row.selected { outline: 2px solid Highlight; }
  .footer { margin-top: 2rem; font-size: .8rem; opacity: .7; }
  @media (prefers-color-scheme: dark) {
    body { background: #1b1b1b; }
    th { background: #2a2a2a; }
    tr:hover td { background: #232323; }
  }
</style>
</head>
<body>
<h1>DiskInventory — <span id="runId">__RUN_ID__</span></h1>
<p>Generated <span id="ts"></span> · OS <span id="os"></span> · Admin <span id="admin"></span></p>

<div class="tile-grid" id="tiles"></div>

<h2>Heavy caches</h2>
<div id="heavy"></div>

<h2>Categories</h2>
<div id="cats"></div>

<h2>Top <span id="topN">100</span> by size</h2>
<input type="search" id="filter" placeholder="Filter by path or category…" />
<div class="pager">
  <button id="prev">Prev</button>
  <button id="next">Next</button>
  <span id="pageInfo"></span>
</div>
<table id="big">
  <thead>
    <tr>
      <th>Path</th>
      <th class="num">Size</th>
      <th>Category</th>
      <th>Action</th>
      <th>Override</th>
    </tr>
  </thead>
  <tbody></tbody>
</table>

<details>
  <summary>Warnings</summary>
  <ul id="warns"></ul>
</details>

<div class="footer">DiskInventory v2.0 · offline · single file</div>

<script id="envData" type="application/json">__ENV_JSON__</script>
<script id="statsData" type="application/json">__STATS_JSON__</script>
<script id="heavyData" type="application/json">__HEAVY_JSON__</script>
<script id="warningsData" type="application/json">__WARNINGS_JSON__</script>
<script id="rowsData" type="application/json">__ROWS_JSON__</script>
<script>
(function () {
  'use strict';
  var $ = function (id) { return document.getElementById(id); };
  function jget(id) {
    try { return JSON.parse($(id).textContent); } catch (e) { return null; }
  }
  var env = jget('envData') || {};
  var stats = jget('statsData') || {};
  var heavy = jget('heavyData') || [];
  var warnings = jget('warningsData') || [];
  var rows = jget('rowsData') || [];

  $('runId').textContent = env.RunId || '(unknown)';
  $('ts').textContent = env.TimestampUtc || '';
  $('os').textContent = (env.Os && env.Os.Caption) || '';
  $('admin').textContent = env.Admin ? 'yes' : 'no';

  // Tiles
  var tileData = [
    ['k', 'Items', stats.items || rows.length],
    ['k', 'Categories', stats.categories || 0],
    ['k', 'Total bytes', (stats.totalBytes || 0).toLocaleString()],
    ['k', 'Heavy caches', heavy.length],
    ['k', 'Drives', (env.Drives || []).length],
    ['k', 'User profiles', (env.UserProfiles || []).length]
  ];
  var tiles = $('tiles');
  tileData.forEach(function (t) {
    var d = document.createElement('div');
    d.className = 'tile';
    d.innerHTML = '<div class="v">' + t[2] + '</div><div class="k">' + t[1] + '</div>';
    tiles.appendChild(d);
  });

  // Heavy caches
  if (heavy.length) {
    var t = document.createElement('table');
    t.innerHTML = '<thead><tr><th>Name</th><th>Path</th><th class="num">Size</th><th class="num">Files</th></tr></thead>';
    var tb = document.createElement('tbody');
    heavy.slice().sort(function(a,b){return (b.SizeBytes||0)-(a.SizeBytes||0);}).forEach(function(h){
      var tr = document.createElement('tr');
      tr.innerHTML = '<td>'+h.Name+'</td><td><code>'+h.Path+'</code></td><td class="num">'+(h.SizeBytes||0).toLocaleString()+'</td><td class="num">'+(h.FileCount||0).toLocaleString()+'</td>';
      tb.appendChild(tr);
    });
    t.appendChild(tb);
    $('heavy').appendChild(t);
  }

  // Categories
  var cats = {};
  rows.forEach(function (r) {
    var k = r.Category || 'Other';
    cats[k] = cats[k] || { count: 0, bytes: 0 };
    cats[k].count += 1;
    cats[k].bytes += (r.SizeBytes || 0);
  });
  var catRows = Object.keys(cats).sort(function (a, b) { return cats[b].bytes - cats[a].bytes; });
  var cTable = document.createElement('table');
  cTable.innerHTML = '<thead><tr><th>Category</th><th class="num">Items</th><th class="num">Bytes</th></tr></thead>';
  var cBody = document.createElement('tbody');
  catRows.forEach(function (k) {
    var tr = document.createElement('tr');
    tr.innerHTML = '<td>'+k+'</td><td class="num">'+cats[k].count+'</td><td class="num">'+cats[k].bytes.toLocaleString()+'</td>';
    cBody.appendChild(tr);
  });
  cTable.appendChild(cBody);
  $('cats').appendChild(cTable);

  // Big table (paginated, top N by size)
  var PAGE = 50;
  var top = rows.slice().sort(function (a, b) { return (b.SizeBytes || 0) - (a.SizeBytes || 0); }).slice(0, 500);
  var filtered = top;
  var page = 0;
  function render() {
    var body = $('big').getElementsByTagName('tbody')[0];
    body.innerHTML = '';
    var start = page * PAGE;
    var end = Math.min(filtered.length, start + PAGE);
    for (var i = start; i < end; i++) {
      var r = filtered[i];
      var tr = document.createElement('tr');
      tr.className = 'override-row';
      tr.dataset.path = r.Path || '';
      tr.innerHTML =
        '<td><code>' + (r.Path || '') + '</code></td>' +
        '<td class="num">' + (r.SizeBytes || 0).toLocaleString() + '</td>' +
        '<td>' + (r.Category || '') + '</td>' +
        '<td>' + (r.Action || '') + '</td>' +
        '<td><select data-act-for="' + (r.Path || '') + '">' +
          '<option value="">— keep default —</option>' +
          '<option value="keep">keep</option>' +
          '<option value="group">group</option>' +
          '<option value="archive">archive</option>' +
          '<option value="quarantine">quarantine</option>' +
          '<option value="delete">delete</option>' +
          '<option value="move">move</option>' +
        '</select></td>';
      body.appendChild(tr);
    }
    $('pageInfo').textContent = 'Showing ' + (start + 1) + '–' + end + ' of ' + filtered.length;
  }
  $('prev').onclick = function () { if (page > 0) { page--; render(); } };
  $('next').onclick = function () { if ((page + 1) * PAGE < filtered.length) { page++; render(); } };
  $('filter').oninput = function (e) {
    var q = (e.target.value || '').toLowerCase();
    filtered = q ? top.filter(function (r) {
      return ((r.Path || '').toLowerCase().indexOf(q) >= 0) ||
             ((r.Category || '').toLowerCase().indexOf(q) >= 0);
    }) : top;
    page = 0;
    render();
  };

  // Override download
  var overrides = [];
  document.addEventListener('change', function (e) {
    var t = e.target;
    if (!t || !t.dataset || !t.dataset.actFor) return;
    var p = t.dataset.actFor;
    var v = t.value;
    overrides = overrides.filter(function (o) { return o.path !== p; });
    if (v) overrides.push({ path: p, action: v });
  });
  var btn = document.createElement('button');
  btn.textContent = 'Download overrides.json';
  btn.style.margin = '0 0 1rem';
  btn.onclick = function () {
    var blob = new Blob([JSON.stringify({ items: overrides }, null, 2)],
                        { type: 'application/json' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'overrides.json';
    a.click();
    URL.revokeObjectURL(a.href);
  };
  $('big').parentNode.insertBefore(btn, $('big').nextSibling);

  // Warnings
  var ul = $('warns');
  warnings.forEach(function (w) {
    var li = document.createElement('li');
    li.textContent = w;
    ul.appendChild(li);
  });

  render();
})();
</script>
</body>
</html>
"""


def write_html(
    env: dict,
    rows: list[dict],
    path: Path | str,
    *,
    warnings: list[str] | None = None,
) -> Path:
    p = Path(path) if not isinstance(path, Path) else path
    p.parent.mkdir(parents=True, exist_ok=True)
    cat_counts: dict[str, int] = {}
    total_bytes = 0
    for r in rows:
        cat_counts[r.get("Category", "Other")] = cat_counts.get(r.get("Category", "Other"), 0) + 1
        try:
            total_bytes += int(r.get("SizeBytes") or 0)
        except (TypeError, ValueError):
            pass
    heavy = env.get("HeavyCaches", []) or []
    warnings = warnings or []

    body = _HTML
    body = body.replace("__RUN_ID__", html.escape(str(env.get("RunId", ""))))
    body = body.replace("__ENV_JSON__", json.dumps(env, ensure_ascii=False))
    body = body.replace("__STATS_JSON__", json.dumps({
        "items": len(rows),
        "categories": len(cat_counts),
        "totalBytes": total_bytes,
    }, ensure_ascii=False))
    body = body.replace("__HEAVY_JSON__", json.dumps(heavy, ensure_ascii=False))
    body = body.replace("__WARNINGS_JSON__", json.dumps(warnings, ensure_ascii=False))
    body = body.replace("__ROWS_JSON__", json.dumps(rows, ensure_ascii=False))
    p.write_text(body, encoding="utf-8")
    return p
