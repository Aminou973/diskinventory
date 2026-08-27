"""
export_reports — writes inventory.csv, inventory.md, inventory.html from one
in-memory model so the three views never disagree.

Mirrors src/Export-Reports.ps1 from the Windows tool. Same column order, same
HTML report layout (vanilla JS, no CDN), same override-UI that downloads
overrides.json. One Linux-specific tweak: the JS drive regex is replaced by a
simple path-prefix filter (since Linux paths start with /, not C:).
"""

import csv
import html
import json
from datetime import datetime


CSV_COLUMNS = [
    "Path", "Parent", "Name", "Kind", "SizeBytes",
    "LastWriteUtc", "CreatedUtc",
    "Category", "Action", "SuggestedAction",
    "PlannedDestination", "PlanAction",
    "RuleMatched", "IsHidden", "IsSystem", "IsOneDrivePlaceholder",
    "Sha1", "Notes",
]


def _format_size(n: int) -> str:
    n = int(n or 0)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < 1024:
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} {unit}"
        n /= 1024
    return f"{n:.1f} PB"


def _plan_for(item: dict, plan: list) -> dict | None:
    for p in plan:
        if p.get("Path") == item.get("Path"):
            return p
    return None


def _write_csv(classified, plan, outdir: str, prefix: str) -> str:
    """Write inventory.csv sorted by SizeBytes desc, then Path."""
    plan_by_path = {p.get("Path"): p for p in plan}
    rows = []
    for item in classified:
        p = plan_by_path.get(item.get("Path")) or {}
        row = {col: "" for col in CSV_COLUMNS}
        row["Path"] = item.get("Path", "")
        row["Parent"] = item.get("Parent", "")
        row["Name"] = item.get("Name", "")
        row["Kind"] = item.get("Kind", "")
        row["SizeBytes"] = item.get("SizeBytes", 0)
        row["LastWriteUtc"] = item.get("LastWriteUtc", "")
        row["CreatedUtc"] = item.get("CreatedUtc", "")
        row["Category"] = item.get("Category", "")
        row["Action"] = item.get("Action", "")
        row["SuggestedAction"] = item.get("SuggestedAction", "")
        row["PlannedDestination"] = p.get("Destination", "")
        row["PlanAction"] = p.get("Action", "")
        row["RuleMatched"] = item.get("RuleMatched", "")
        row["IsHidden"] = bool(item.get("IsHidden", False))
        row["IsSystem"] = bool(item.get("IsSystem", False))
        row["IsOneDrivePlaceholder"] = bool(item.get("IsOneDrivePlaceholder", False))
        row["Sha1"] = item.get("Sha1", "")
        row["Notes"] = item.get("Notes", "")
        rows.append(row)

    rows.sort(key=lambda r: (-int(r.get("SizeBytes") or 0), r.get("Path", "")))

    outpath = f"{outdir}/{prefix}.csv"
    with open(outpath, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=CSV_COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return outpath


def _write_markdown(classified, plan, environment, stats, warnings, outdir: str,
                    prefix: str, mode: str) -> str:
    """Write inventory.md: summary, heavy caches, top-N tables, counts, action plan, warnings."""
    lines = []

    # Header
    lines.append(f"# DiskInventory report")
    lines.append("")
    lines.append(f"- **Run id**: `{environment.get('RunId','')}`")
    lines.append(f"- **Timestamp (UTC)**: {environment.get('TimestampUtc','')}")
    lines.append(f"- **Mode**: `{mode}`")
    lines.append(f"- **OS**: {environment.get('Os',{}).get('Caption','?')} "
                 f"({environment.get('Os',{}).get('Version','')})")
    lines.append(f"- **Runtime**: {environment.get('PowerShell','')}")
    lines.append(f"- **Locale**: {environment.get('Locale',{}).get('Ui','')}")
    lines.append(f"- **Admin**: `{environment.get('Admin', False)}`")
    lines.append(f"- **Drives**: {len(environment.get('Drives', []))}")
    lines.append(f"- **User profiles**: {len(environment.get('UserProfiles', []))}")
    lines.append(f"- **Scan roots**: {len(environment.get('ScanRoots', []))}")
    lines.append(f"- **Items**: {stats.get('TotalItems', 0)}")
    lines.append(f"- **Errors / cache hits / misses**: "
                 f"{stats.get('Errors', 0)} / {stats.get('CacheHits', 0)} / {stats.get('CacheMisses', 0)}")
    lines.append("")

    # Heavy caches
    hc = environment.get("HeavyCaches", [])
    if hc:
        lines.append("## Heavy caches (auto-detected, NOT auto-quarantined)")
        lines.append("")
        lines.append("| Kind | Label | Path | Size |")
        lines.append("|---|---|---|---|")
        for h in hc:
            lines.append(f"| {h.get('Kind','')} | {h.get('Label','')} | "
                         f"`{h.get('Path','')}` | {_format_size(h.get('SizeBytes', 0))} |")
        lines.append("")

    # Top 10 by size
    by_size = sorted(classified, key=lambda i: -int(i.get("SizeBytes") or 0))[:10]
    if by_size:
        lines.append("## Top 10 by size")
        lines.append("")
        lines.append("| Path | Size | Category | Last write (UTC) |")
        lines.append("|---|---|---|---|")
        for it in by_size:
            lines.append(f"| `{it.get('Path','')}` | {_format_size(it.get('SizeBytes',0))} "
                         f"| {it.get('Category','')} | {it.get('LastWriteUtc','')} |")
        lines.append("")

    # Top 10 oldest
    by_age = sorted([i for i in classified if i.get("LastWriteUtc")],
                    key=lambda i: i.get("LastWriteUtc", ""))[:10]
    if by_age:
        lines.append("## Top 10 oldest")
        lines.append("")
        lines.append("| Path | Last write (UTC) | Category |")
        lines.append("|---|---|---|")
        for it in by_age:
            lines.append(f"| `{it.get('Path','')}` | {it.get('LastWriteUtc','')} "
                         f"| {it.get('Category','')} |")
        lines.append("")

    # Counts by category
    by_cat: dict[str, int] = {}
    by_cat_size: dict[str, int] = {}
    for it in classified:
        c = it.get("Category", "?")
        by_cat[c] = by_cat.get(c, 0) + 1
        by_cat_size[c] = by_cat_size.get(c, 0) + int(it.get("SizeBytes") or 0)
    if by_cat:
        lines.append("## Counts by category")
        lines.append("")
        lines.append("| Category | Count | Total size |")
        lines.append("|---|---|---|")
        for c in sorted(by_cat, key=lambda k: -by_cat[k]):
            lines.append(f"| {c} | {by_cat[c]} | {_format_size(by_cat_size[c])} |")
        lines.append("")

    # Action plan (first 200)
    if plan:
        lines.append("## Action plan (first 200)")
        lines.append("")
        lines.append("| Path | Action | Destination | Size | Category | Reason |")
        lines.append("|---|---|---|---|---|---|")
        for p in plan[:200]:
            lines.append(f"| `{p.get('Path','')}` | {p.get('Action','')} "
                         f"| `{p.get('Destination','')}` | {_format_size(p.get('SizeBytes',0))} "
                         f"| {p.get('Category','')} | {p.get('Reason','')} |")
        lines.append("")

    # Warnings
    if warnings:
        lines.append("## Scan warnings")
        lines.append("")
        lines.append("| Path | Reason |")
        lines.append("|---|---|")
        for w in warnings[:200]:
            lines.append(f"| `{w.get('Path','')}` | {w.get('Reason','')} |")
        lines.append("")

    # Caveats
    lines.append("## Caveats")
    lines.append("")
    lines.append("- **Timestamps are UTC ISO-8601** in CSV/JSON, regardless of system locale.")
    lines.append("- **OneDrive placeholder detection is not available on Linux** — the `IsOneDrivePlaceholder`")
    lines.append("  column is always `false` on this platform (the Linux OneDrive client serves files normally).")
    lines.append("- **Path separator**: Linux paths use `/`.")
    lines.append("")

    outpath = f"{outdir}/{prefix}.md"
    with open(outpath, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    return outpath


# --- HTML report ----------------------------------------------------------

_HTML_HEAD = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>DiskInventory — __RUNID__</title>
<meta name="viewport" content="width=device-width,initial-scale=1">
<style>
:root {
  --bg: #0f1115;
  --bg-elev: #181b22;
  --fg: #e6e6e6;
  --fg-mute: #9aa0a6;
  --accent: #6aa9ff;
  --border: #2a2f3a;
  --warn: #f0c674;
  --danger: #ff6a6a;
  --ok: #98c379;
}
@media (prefers-color-scheme: light) {
  :root {
    --bg: #fafafa;
    --bg-elev: #fff;
    --fg: #1a1a1a;
    --fg-mute: #555;
    --accent: #2563eb;
    --border: #e2e2e2;
    --warn: #b45309;
    --danger: #b91c1c;
    --ok: #15803d;
  }
}
* { box-sizing: border-box; }
body {
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Helvetica Neue", Arial, sans-serif;
  margin: 0;
  background: var(--bg);
  color: var(--fg);
}
header {
  padding: 1.25rem 1.5rem;
  background: var(--bg-elev);
  border-bottom: 1px solid var(--border);
}
h1 { margin: 0 0 0.5rem; font-size: 1.4rem; }
.subtitle { color: var(--fg-mute); font-size: 0.9rem; }
main { padding: 1.5rem; max-width: 1400px; margin: 0 auto; }
.tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr));
        gap: 0.75rem; margin-bottom: 1.5rem; }
.tile { background: var(--bg-elev); border: 1px solid var(--border);
        border-radius: 8px; padding: 0.9rem; }
.tile .label { color: var(--fg-mute); font-size: 0.8rem; }
.tile .value { font-size: 1.4rem; font-weight: 600; margin-top: 0.25rem; }
.tile.warn { border-color: var(--warn); }
.tile.danger { border-color: var(--danger); }
.tile.ok { border-color: var(--ok); }
.section { background: var(--bg-elev); border: 1px solid var(--border);
           border-radius: 8px; padding: 1rem; margin-bottom: 1rem; }
.section h2 { margin: 0 0 0.75rem; font-size: 1.1rem; }
table { width: 100%; border-collapse: collapse; font-size: 0.85rem; }
th, td { text-align: left; padding: 0.4rem 0.6rem; border-bottom: 1px solid var(--border);
         vertical-align: top; }
th { background: var(--bg-elev); position: sticky; top: 0; cursor: pointer; user-select: none; }
td.path { font-family: ui-monospace, SFMono-Regular, Menlo, Consolas, monospace; word-break: break-all; }
tr:nth-child(even) { background: rgba(127,127,127,0.05); }
.filterbar { display: flex; gap: 0.5rem; flex-wrap: wrap; margin-bottom: 0.75rem; }
.filterbar input, .filterbar select {
  background: var(--bg);
  color: var(--fg);
  border: 1px solid var(--border);
  border-radius: 4px;
  padding: 0.4rem 0.6rem;
  font-size: 0.9rem;
}
.filterbar input { flex: 1; min-width: 200px; }
.pill { display: inline-block; padding: 1px 6px; border-radius: 10px;
        font-size: 0.75rem; background: var(--border); color: var(--fg); }
.pill.cat-App { background: #3a3f4b; color: #cfe2ff; }
.pill.cat-System { background: #4b3838; color: #f8d7da; }
.pill.cat-Project { background: #2f4a36; color: #d4edda; }
.pill.cat-HeavyCache { background: #4a3a1c; color: #fff3cd; }
.pill.cat-Archive { background: #3b3b59; color: #d6d8db; }
.pill.cat-Junk { background: #5c2c2c; color: #f5c2c2; }
.pill.cat-Data { background: #2c3e50; color: #d6eaf8; }
.pill.act-quarantine { background: var(--danger); color: #fff; }
.pill.act-archive { background: var(--warn); color: #000; }
.pill.act-keep { background: var(--ok); color: #000; }
.pill.act-delete { background: var(--danger); color: #fff; }
details { margin-top: 0.5rem; }
summary { cursor: pointer; font-weight: 500; }
footer { padding: 1rem; text-align: center; color: var(--fg-mute); font-size: 0.85rem; }
.override-controls { margin-top: 1rem; }
.override-row { display: flex; gap: 0.5rem; align-items: center; margin-bottom: 0.3rem; }
.override-row .p { flex: 1; font-family: ui-monospace, monospace; font-size: 0.85rem; word-break: break-all; }
.raw { background: var(--bg); border: 1px solid var(--border); border-radius: 4px;
       padding: 0.6rem; font-family: ui-monospace, monospace; font-size: 0.8rem;
       overflow-x: auto; max-height: 240px; overflow-y: auto; }
.caveat { color: var(--warn); margin-bottom: 0.5rem; font-size: 0.9rem; }
</style>
</head>
<body>
<header>
  <h1>DiskInventory</h1>
  <div class="subtitle">Run <code>__RUNID__</code> · __TIMESTAMP__ · mode <code>__MODE__</code></div>
</header>
<main>
__TILES__
__HEAVY__
__FILTERBAR__
__TABLE__
__OVERRIDES__
__WARNINGS__
__CAVEATS__
</main>
<footer>
  Generated by DiskInventory · open <code>inventory.csv</code> for spreadsheet view ·
  edit <code>__CONFIG_RULES__</code> / <code>__CONFIG_PATHS__</code> to retune
</footer>
</body>
</html>
"""


def _build_tiles(env, stats) -> str:
    parts = ['<div class="tiles">']
    parts.append(f'<div class="tile"><div class="label">Items</div>'
                 f'<div class="value">{stats.get("TotalItems", 0):,}</div></div>')
    total_size = sum(int(it.get("SizeBytes") or 0) for it in env.get("__ITEMS__", []))
    parts.append(f'<div class="tile"><div class="label">Total size</div>'
                 f'<div class="value">{_format_size(total_size)}</div></div>')
    parts.append(f'<div class="tile"><div class="label">User profiles</div>'
                 f'<div class="value">{len(env.get("UserProfiles", []))}</div></div>')
    parts.append(f'<div class="tile"><div class="label">Fixed drives</div>'
                 f'<div class="value">{len(env.get("Drives", []))}</div></div>')
    parts.append(f'<div class="tile"><div class="label">Heavy caches</div>'
                 f'<div class="value">{len(env.get("HeavyCaches", []))}</div></div>')
    parts.append(f'<div class="tile"><div class="label">Scan roots</div>'
                 f'<div class="value">{len(env.get("ScanRoots", []))}</div></div>')
    parts.append(f'<div class="tile warn"><div class="label">Scan warnings</div>'
                 f'<div class="value">{stats.get("Errors", 0)}</div></div>')
    parts.append(f'<div class="tile ok"><div class="label">Cache hits</div>'
                 f'<div class="value">{stats.get("CacheHits", 0):,}</div></div>')
    parts.append('</div>')
    return "".join(parts)


def _build_heavy_table(heavy) -> str:
    if not heavy:
        return ""
    rows = ['<div class="section"><h2>Heavy caches (auto-detected, not auto-quarantined)</h2>',
            '<table><thead><tr><th>Kind</th><th>Label</th><th>Path</th><th>Size</th></tr></thead><tbody>']
    for h in heavy:
        rows.append(f'<tr><td>{html.escape(h.get("Kind",""))}</td>'
                    f'<td>{html.escape(h.get("Label",""))}</td>'
                    f'<td class="path">{html.escape(h.get("Path",""))}</td>'
                    f'<td>{_format_size(h.get("SizeBytes", 0))}</td></tr>')
    rows.append('</tbody></table></div>')
    return "".join(rows)


_HTML_TABLE_HEAD = """
<div class="section">
<h2>Items</h2>
<div class="filterbar">
  <input type="search" id="q" placeholder="Search path / name / notes...">
  <select id="cat"><option value="">All categories</option></select>
  <select id="act"><option value="">All actions</option></select>
  <select id="pathprefix"><option value="">All paths</option></select>
</div>
<table id="items">
<thead><tr>
  <th data-k="Name">Name</th>
  <th data-k="Path">Path</th>
  <th data-k="Kind">Kind</th>
  <th data-k="SizeBytes">Size</th>
  <th data-k="Category">Category</th>
  <th data-k="Action">Action</th>
  <th data-k="LastWriteUtc">Last write (UTC)</th>
  <th data-k="Notes">Notes</th>
</tr></thead>
<tbody></tbody>
</table>
<div style="margin-top:0.5rem;font-size:0.8rem;color:var(--fg-mute)">
  Showing up to 2000 rows. See <code>inventory.csv</code> for the full set.
</div>
</div>
"""

_HTML_OVERRIDE_PANEL = """
<div class="section override-controls">
<h2>Per-item overrides</h2>
<p style="color:var(--fg-mute);font-size:0.9rem">
  Pick an action for items you want to override. When done, click
  <b>Download overrides.json</b> and pass it via
  <code>--honor-overrides</code> on the next run.
</p>
<div id="override-list"></div>
<button id="download-overrides" style="margin-top:0.75rem;padding:0.5rem 1rem;background:var(--accent);color:#fff;border:0;border-radius:4px;cursor:pointer">
  Download overrides.json
</button>
</div>
"""

_HTML_WARNINGS_SECTION = """
<div class="section">
<h2>Scan warnings</h2>
__WARNINGS_BODY__
</div>
"""

_HTML_CAVEATS_SECTION = """
<div class="section">
<h2>Caveats</h2>
<div class="caveat">Timestamps are UTC ISO-8601 in CSV/JSON, regardless of system locale.</div>
<div class="caveat">OneDrive placeholder detection is not available on Linux &mdash; the <code>IsOneDrivePlaceholder</code> column is always <code>false</code> on this platform.</div>
<div class="caveat">Path separator: Linux paths use <code>/</code>.</div>
</div>
"""


def _build_warnings(warnings) -> str:
    if not warnings:
        body = '<div style="color:var(--ok);font-size:0.9rem">No scan warnings.</div>'
    else:
        rows = ['<table><thead><tr><th>Path</th><th>Reason</th></tr></thead><tbody>']
        for w in warnings[:200]:
            rows.append(f'<tr><td class="path">{html.escape(w.get("Path",""))}</td>'
                        f'<td>{html.escape(w.get("Reason",""))}</td></tr>')
        rows.append('</tbody></table>')
        body = "".join(rows)
    return _HTML_WARNINGS_SECTION.replace("__WARNINGS_BODY__", body)


_JS_BLOCK = """
<script>
const RAW_ENV = __ENV_JSON__;
const RAW_STATS = __STATS_JSON__;
const RAW_HEAVY = __HEAVY_JSON__;
const RAW_WARNINGS = __WARNINGS_JSON__;
const ROWS = __ROWS_JSON__;

function fmtSize(n) {
  n = Number(n) || 0;
  const u = ['B','KB','MB','GB','TB'];
  for (let i = 0; i < u.length; i++) {
    if (n < 1024 || i === u.length - 1) {
      return (i === 0 ? n.toFixed(0) : n.toFixed(1)) + ' ' + u[i];
    }
    n /= 1024;
  }
}

function uniqueValues(key) {
  const s = new Set();
  for (const r of ROWS) { if (r[key]) s.add(r[key]); }
  return [...s].sort();
}

function refreshSelectors() {
  const cats = uniqueValues('Category');
  const acts = uniqueValues('Action');
  const prefs = new Set();
  for (const r of ROWS) {
    const s = String(r.Path || '');
    if (s.startsWith('/')) {
      const i = s.indexOf('/', 1);
      if (i > 0) prefs.add(s.substring(0, i));
      else prefs.add('/');
    }
  }
  const $cat = document.getElementById('cat');
  const $act = document.getElementById('act');
  const $pp = document.getElementById('pathprefix');
  for (const c of cats) $cat.appendChild(new Option(c, c));
  for (const a of acts) $act.appendChild(new Option(a, a));
  for (const p of [...prefs].sort()) $pp.appendChild(new Option(p, p));
}

function renderRows() {
  const q = (document.getElementById('q').value || '').toLowerCase();
  const cat = document.getElementById('cat').value;
  const act = document.getElementById('act').value;
  const pp = document.getElementById('pathprefix').value;
  const tbody = document.querySelector('#items tbody');
  tbody.innerHTML = '';
  let n = 0;
  for (const r of ROWS) {
    if (q && !(r.Path || '').toLowerCase().includes(q)
            && !(r.Name || '').toLowerCase().includes(q)
            && !(r.Notes || '').toLowerCase().includes(q)) continue;
    if (cat && r.Category !== cat) continue;
    if (act && r.Action !== act) continue;
    if (pp && !(r.Path || '').startsWith(pp)) continue;
    const tr = document.createElement('tr');
    tr.innerHTML =
      '<td>' + (r.Name || '') + '</td>' +
      '<td class="path">' + (r.Path || '') + '</td>' +
      '<td>' + (r.Kind || '') + '</td>' +
      '<td>' + fmtSize(r.SizeBytes) + '</td>' +
      '<td><span class="pill cat-' + (r.Category || '') + '">' + (r.Category || '') + '</span></td>' +
      '<td><span class="pill act-' + (r.Action || '') + '">' + (r.Action || '') + '</span></td>' +
      '<td>' + (r.LastWriteUtc || '') + '</td>' +
      '<td>' + (r.Notes || '') + '</td>';
    tbody.appendChild(tr);
    if (++n >= 2000) break;
  }
}

document.getElementById('q').addEventListener('input', renderRows);
document.getElementById('cat').addEventListener('change', renderRows);
document.getElementById('act').addEventListener('change', renderRows);
document.getElementById('pathprefix').addEventListener('change', renderRows);

document.querySelectorAll('#items th').forEach(th => {
  th.addEventListener('click', () => {
    const k = th.dataset.k;
    const dir = th.dataset.dir === 'desc' ? 'asc' : 'desc';
    th.dataset.dir = dir;
    ROWS.sort((a, b) => {
      const av = a[k], bv = b[k];
      if (k === 'SizeBytes') return dir === 'asc' ? (av - bv) : (bv - av);
      return dir === 'asc'
        ? String(av).localeCompare(String(bv))
        : String(bv).localeCompare(String(av));
    });
    renderRows();
  });
});

function buildOverrideUI() {
  const $list = document.getElementById('override-list');
  const top = ROWS.slice(0, 200);
  for (const r of top) {
    const row = document.createElement('div');
    row.className = 'override-row';
    row.innerHTML =
      '<div class="p">' + (r.Path || '') + '</div>' +
      '<select data-path="' + (r.Path || '').replace(/"/g, '&quot;') + '">' +
      '<option value="">(no override)</option>' +
      '<option value="keep">keep</option>' +
      '<option value="group">group</option>' +
      '<option value="archive">archive</option>' +
      '<option value="quarantine">quarantine</option>' +
      '<option value="delete">delete</option>' +
      '</select>';
    $list.appendChild(row);
  }
}

document.getElementById('download-overrides').addEventListener('click', () => {
  const items = [];
  document.querySelectorAll('#override-list select').forEach(sel => {
    if (sel.value) {
      items.push({ path: sel.dataset.path, action: sel.value });
    }
  });
  const blob = new Blob(
    [JSON.stringify({ items: items }, null, 2)],
    { type: 'application/json' }
  );
  const url = URL.createObjectURL(blob);
  const a = document.createElement('a');
  a.href = url;
  a.download = 'overrides.json';
  document.body.appendChild(a);
  a.click();
  document.body.removeChild(a);
  URL.revokeObjectURL(url);
});

refreshSelectors();
renderRows();
buildOverrideUI();
</script>
"""


def _write_html(classified, plan, environment, stats, warnings, outdir: str,
                prefix: str, mode: str) -> str:
    """Write inventory.html with inline CSS + vanilla JS, no CDN."""
    env_for_js = {
        "RunId": environment.get("RunId", ""),
        "TimestampUtc": environment.get("TimestampUtc", ""),
        "Os": environment.get("Os", {}),
        "PowerShell": environment.get("PowerShell", ""),
        "Locale": environment.get("Locale", {}),
        "Admin": environment.get("Admin", False),
        "Drives": environment.get("Drives", []),
        "UserProfiles": environment.get("UserProfiles", []),
    }

    # Compact row shape for the JS table
    rows = []
    for it in classified:
        rows.append({
            "Path": it.get("Path", ""),
            "Name": it.get("Name", ""),
            "Kind": it.get("Kind", ""),
            "SizeBytes": int(it.get("SizeBytes") or 0),
            "Category": it.get("Category", ""),
            "Action": it.get("Action", ""),
            "LastWriteUtc": it.get("LastWriteUtc", ""),
            "Notes": it.get("Notes", ""),
        })

    js = (_JS_BLOCK
          .replace("__ENV_JSON__", json.dumps(env_for_js, ensure_ascii=False))
          .replace("__STATS_JSON__", json.dumps(stats, ensure_ascii=False))
          .replace("__HEAVY_JSON__", json.dumps(environment.get("HeavyCaches", []), ensure_ascii=False))
          .replace("__WARNINGS_JSON__", json.dumps(warnings, ensure_ascii=False))
          .replace("__ROWS_JSON__", json.dumps(rows, ensure_ascii=False)))

    head = (_HTML_HEAD
            .replace("__RUNID__", html.escape(str(environment.get("RunId", ""))))
            .replace("__TIMESTAMP__", html.escape(str(environment.get("TimestampUtc", ""))))
            .replace("__MODE__", html.escape(mode))
            .replace("__CONFIG_RULES__", "config/classification.linux.json")
            .replace("__CONFIG_PATHS__", "config/paths_to_scan.linux.json"))

    body = (
        head
        .replace("__TILES__", _build_tiles(env_for_js, stats))
        .replace("__HEAVY__", _build_heavy_table(environment.get("HeavyCaches", [])))
        .replace("__FILTERBAR____TABLE__", _HTML_TABLE_HEAD)
        .replace("__OVERRIDES__", _HTML_OVERRIDE_PANEL)
        .replace("__WARNINGS__", _build_warnings(warnings))
        .replace("__CAVEATS__", _HTML_CAVEATS_SECTION)
        + js
    )

    outpath = f"{outdir}/{prefix}.html"
    with open(outpath, "w", encoding="utf-8") as f:
        f.write(body)
    return outpath


def export_reports(classified, plan, environment, stats, warnings=None,
                   output_dir: str = "", report_prefix: str = "inventory",
                   mode: str = "report") -> dict:
    """Write CSV + HTML + Markdown. Returns {CsvPath, HtmlPath, MarkdownPath}."""
    if warnings is None:
        warnings = []
    csv_path = _write_csv(classified, plan, output_dir, report_prefix)
    md_path = _write_markdown(classified, plan, environment, stats, warnings,
                              output_dir, report_prefix, mode)
    html_path = _write_html(classified, plan, environment, stats, warnings,
                            output_dir, report_prefix, mode)
    return {"CsvPath": csv_path, "MarkdownPath": md_path, "HtmlPath": html_path}