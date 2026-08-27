<#
.SYNOPSIS
    Export-Reports — writes CSV + HTML + Markdown from the same in-memory inventory +
    action-plan + environment-snapshot triple. The three outputs never disagree.

.DESCRIPTION
    The HTML report is fully self-contained: inline CSS, inline vanilla JS, no CDN, no
    network calls. Opens from file://. Has:
        - Environment header (everything detected about this machine)
        - Summary tiles
        - Filter bar (text + category + action)
        - Sortable table
        - Override panel: per-item dropdown to write overrides.json for the next Auto run
        - Caveats callout

    CSV columns:
        Path,Parent,Name,Kind,SizeBytes,LastWriteUtc,CreatedUtc,Category,Action,
        SuggestedAction,RuleMatched,IsHidden,IsSystem,IsOneDrivePlaceholder,Sha1,Notes

    Markdown:
        Headline summary, env table, top-10 by size, top-10 by age, category grouping,
        action plan, caveats.

.PARAMETER Classified
    Array of classified inventory records (from Classify-Items).

.PARAMETER Plan
    Array of action records (from Plan-Actions).

.PARAMETER Environment
    Environment snapshot (from Detect-Environment).

.PARAMETER Stats
    Collector stats object.

.PARAMETER Warnings
    Array of scan warnings.

.PARAMETER OutputDir
    Directory where reports will be written.

.PARAMETER ReportPrefix
    Filename prefix (e.g. "inventory"). Default: "inventory".

.PARAMETER Mode
    'Report' | 'DryRun' | 'Auto'. Used to gate override UI visibility.
#>

function Invoke-DiskInventoryExport {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][object[]]$Classified,
        [Parameter(Mandatory=$true)][object[]]$Plan,
        [Parameter(Mandatory=$true)]$Environment,
        [Parameter(Mandatory=$true)]$Stats,
        [object[]]$Warnings = @(),
        [Parameter(Mandatory=$true)][string]$OutputDir,
        [string]$ReportPrefix = "inventory",
        [string]$Mode = "Report"
    )

    if ($null -eq $Warnings) { $Warnings = @() }

    if (-not (Test-Path -LiteralPath $OutputDir)) {
        New-Item -ItemType Directory -Path $OutputDir -Force | Out-Null
    }

    # Build a fast lookup: path -> action plan entry
    $planByPath = @{}
    foreach ($p in $Plan) { $planByPath[[string]$p.Path] = $p }

    $csvPath = Join-Path $OutputDir ($ReportPrefix + ".csv")
    $htmlPath = Join-Path $OutputDir ($ReportPrefix + ".html")
    $mdPath = Join-Path $OutputDir ($ReportPrefix + ".md")

    # ------------------------------ CSV -----------------------------------
    $csvRows = New-Object System.Collections.Generic.List[object]
    foreach ($c in $Classified) {
        $pl = $planByPath[[string]$c.Path]
        $row = [pscustomobject]@{
            Path = $c.Path
            Parent = $c.Parent
            Name = $c.Name
            Kind = $c.Kind
            SizeBytes = [int64]$c.SizeBytes
            LastWriteUtc = $c.LastWriteUtc
            CreatedUtc = $c.CreatedUtc
            Category = $c.Category
            Action = $c.Action
            SuggestedAction = $c.SuggestedAction
            PlannedDestination = if ($pl) { $pl.Destination } else { '' }
            PlanAction = if ($pl) { $pl.Action } else { '' }
            RuleMatched = $c.RuleMatched
            IsHidden = $c.IsHidden
            IsSystem = $c.IsSystem
            IsOneDrivePlaceholder = $c.IsOneDrivePlaceholder
            Sha1 = $c.Sha1
            Notes = $c.Notes
        }
        $csvRows.Add($row)
    }
    # Sort by size desc, then path
    $csvRows = $csvRows | Sort-Object -Property @{Expression='SizeBytes'; Descending=$true}, @{Expression='Path'; Descending=$false}
    $csvRows | Export-Csv -LiteralPath $csvPath -NoTypeInformation -Encoding utf8

    # ----------------------------- Markdown -------------------------------
    $md = New-Object System.Text.StringBuilder
    $null = $md.AppendLine("# Disk Inventory Report")
    $null = $md.AppendLine("")
    $null = $md.AppendLine("- **Run:** $($Environment.RunId) at $($Environment.TimestampUtc)")
    $null = $md.AppendLine("- **OS:** $($Environment.Os.Caption) ($($Environment.Os.Version) build $($Environment.Os.Build))")
    $null = $md.AppendLine("- **PowerShell:** $($Environment.PowerShell)")
    $null = $md.AppendLine("- **Locale:** $($Environment.Locale.Ui) (display: $($Environment.Locale.DisplayName))")
    $null = $md.AppendLine("- **Admin:** $($Environment.Admin)")
    $null = $md.AppendLine("- **Drives:** $(($Environment.Drives | ForEach-Object { "$($_.Name): $('{0:N1}' -f ($_.Total/1GB)) GB" }) -join ', ')")
    $null = $md.AppendLine("- **User profiles:** $(@($Environment.UserProfiles).Count)")
    $null = $md.AppendLine("- **Scan roots:** $(@($Environment.ScanRoots).Count) (excluded $(@($Environment.ExcludedRoots).Count))")
    $null = $md.AppendLine("- **Mode:** $Mode")
    $null = $md.AppendLine("- **Items:** $($Stats.TotalItems) (files: $($Stats.FilesScanned), dirs: $($Stats.DirsScanned))")
    $null = $md.AppendLine("- **Errors:** $($Stats.Errors) | **Cache hits:** $($Stats.CacheHits) | **misses:** $($Stats.CacheMisses)")

    $null = $md.AppendLine("")
    $null = $md.AppendLine("## Heavy caches detected")
    $null = $md.AppendLine("")
    if (@($Environment.HeavyCaches).Count -gt 0) {
        $null = $md.AppendLine("| Kind | Label | Size |")
        $null = $md.AppendLine("|---|---|---|")
        foreach ($h in ($Environment.HeavyCaches | Sort-Object -Property SizeBytes -Descending | Select-Object -First 30)) {
            $size = "{0:N2} MB" -f ($h.SizeBytes / 1MB)
            $null = $md.AppendLine("| $($h.Kind) | $($h.Label) | $size |")
        }
    } else {
        $null = $md.AppendLine("_(none detected)_")
    }

    $null = $md.AppendLine("")
    $null = $md.AppendLine("## Top 10 by size")
    $null = $md.AppendLine("")
    $null = $md.AppendLine("| Path | Size | Category |")
    $null = $md.AppendLine("|---|---|---|")
    foreach ($r in ($Classified | Sort-Object -Property SizeBytes -Descending | Select-Object -First 10)) {
        $size = "{0:N2} MB" -f ($r.SizeBytes / 1MB)
        $null = $md.AppendLine("| $($r.Path) | $size | $($r.Category) |")
    }

    $null = $md.AppendLine("")
    $null = $md.AppendLine("## Top 10 oldest")
    $null = $md.AppendLine("")
    $null = $md.AppendLine("| Path | LastWrite | Category |")
    $null = $md.AppendLine("|---|---|---|")
    foreach ($r in ($Classified | Where-Object { $_.LastWriteUtc } | Sort-Object -Property LastWriteUtc | Select-Object -First 10)) {
        $null = $md.AppendLine("| $($r.Path) | $($r.LastWriteUtc) | $($r.Category) |")
    }

    $null = $md.AppendLine("")
    $null = $md.AppendLine("## Counts by category")
    $null = $md.AppendLine("")
    $null = $md.AppendLine("| Category | Count | Total size |")
    $null = $md.AppendLine("|---|---|---|")
    foreach ($g in ($Classified | Group-Object Category | Sort-Object Count -Descending)) {
        $total = ($Classified | Where-Object Category -eq $g.Name | Measure-Object -Property SizeBytes -Sum).Sum
        $size = "{0:N2} MB" -f ($total / 1MB)
        $null = $md.AppendLine("| $($g.Name) | $($g.Count) | $size |")
    }

    $null = $md.AppendLine("")
    $null = $md.AppendLine("## Action plan (mode: $Mode)")
    $null = $md.AppendLine("")
    if (@($Plan).Count -gt 0) {
        $null = $md.AppendLine("| Action | Path | Destination | Reason |")
        $null = $md.AppendLine("|---|---|---|---|")
        foreach ($a in ($Plan | Sort-Object Action, Path | Select-Object -First 200)) {
            $null = $md.AppendLine("| $($a.Action) | $($a.Path) | $($a.Destination) | $($a.Reason) |")
        }
        $planCount = @($Plan).Count
        if ($planCount -gt 200) {
            $null = $md.AppendLine("")
            $null = $md.AppendLine("_(showing first 200 of $planCount - see CSV/HTML for the full list)_")
        }
    } else {
        $null = $md.AppendLine("_(no actions planned)_")
    }

    if (@($Warnings).Count -gt 0) {
        $null = $md.AppendLine("")
        $null = $md.AppendLine("## Scan warnings")
        $null = $md.AppendLine("")
        $null = $md.AppendLine("| Path | Reason |")
        $null = $md.AppendLine("|---|---|")
        foreach ($w in ($Warnings | Select-Object -First 50)) {
            $null = $md.AppendLine("| $($w.Path) | $($w.Reason) |")
        }
    }

    $null = $md.AppendLine("")
    $null = $md.AppendLine("## Caveats")
    $null = $md.AppendLine("- All timestamps are UTC ISO-8601; the HTML report renders them localized for your display locale.")
    $null = $md.AppendLine("- OneDrive files may appear as placeholders (`IsOneDrivePlaceholder: true`); their reported size is the on-disk size, not the cloud size.")
    $null = $md.AppendLine("- Items under `HeavyCache` are never auto-quarantined. Use overrides to act on them explicitly.")
    $null = $md.AppendLine("- This tool made no permanent deletions. Quarantined items are in `_Quarantine\<runId>\` and remain reversible until you run `-PurgeQuarantine`.")

    Out-File -LiteralPath $mdPath -InputObject $md.ToString() -Encoding utf8

    # ------------------------------ HTML ----------------------------------
    # Build rows array as JSON for the inline JS table
    $tableData = foreach ($c in $Classified) {
        $pl = $planByPath[[string]$c.Path]
        [pscustomobject]@{
            path = $c.Path
            name = $c.Name
            parent = $c.Parent
            kind = $c.Kind
            size = [int64]$c.SizeBytes
            lastWrite = $c.LastWriteUtc
            created = $c.CreatedUtc
            category = $c.Category
            action = $c.Action
            suggested = $c.SuggestedAction
            planAction = if ($pl) { $pl.Action } else { '' }
            planDest = if ($pl) { $pl.Destination } else { '' }
            rule = $c.RuleMatched
            reason = if ($pl) { $pl.Reason } else { '' }
            hidden = $c.IsHidden
            system = $c.IsSystem
            oneDrive = $c.IsOneDrivePlaceholder
            sha1 = $c.Sha1
            notes = $c.Notes
        }
    }
    $tableJson = ($tableData | ConvertTo-Json -Depth 4 -Compress)

    $envJson = ($Environment | ConvertTo-Json -Depth 6 -Compress)
    $heavyJson = (@($Environment.HeavyCaches) | ConvertTo-Json -Depth 4 -Compress)
    $warningsJson = (@($Warnings) | ConvertTo-Json -Depth 3 -Compress)
    $statsJson = ($Stats | ConvertTo-Json -Depth 3 -Compress)

    $html = @"
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Disk Inventory - $($Environment.RunId)</title>
<style>
  :root {
    --bg: #0e0f12; --panel: #161821; --panel-2: #1d2030; --fg: #e8eaf0; --muted: #8a92a6;
    --accent: #7aa2ff; --good: #57c785; --warn: #f0b347; --bad: #ff6b6b; --border: #262a39;
  }
  @media (prefers-color-scheme: light) {
    :root { --bg: #f6f7fb; --panel: #ffffff; --panel-2: #f0f2f7; --fg: #1a1d24; --muted: #5b6478; --accent: #2c5fcc; --good: #1f9c5b; --warn: #b86b00; --bad: #c4302b; --border: #d8dde7; }
  }
  * { box-sizing: border-box; }
  body { margin: 0; font: 14px/1.45 -apple-system, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--fg); }
  header { padding: 18px 22px; background: var(--panel); border-bottom: 1px solid var(--border); }
  h1 { font-size: 18px; margin: 0 0 8px; }
  .env { display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); gap: 8px 18px; font-size: 12px; color: var(--muted); }
  .env strong { color: var(--fg); }
  main { padding: 16px 22px; }
  .tiles { display: grid; grid-template-columns: repeat(auto-fit, minmax(180px, 1fr)); gap: 10px; margin-bottom: 18px; }
  .tile { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; padding: 12px 14px; }
  .tile .v { font-size: 22px; font-weight: 600; }
  .tile .k { color: var(--muted); font-size: 12px; text-transform: uppercase; letter-spacing: 0.05em; }
  .filters { display: flex; gap: 8px; flex-wrap: wrap; align-items: center; margin-bottom: 12px; }
  .filters input, .filters select { background: var(--panel); color: var(--fg); border: 1px solid var(--border); padding: 6px 10px; border-radius: 6px; font: inherit; }
  .filters input[type=search] { min-width: 280px; }
  .panel { background: var(--panel); border: 1px solid var(--border); border-radius: 8px; overflow: hidden; margin-bottom: 18px; }
  .panel h2 { font-size: 14px; margin: 0; padding: 10px 14px; background: var(--panel-2); border-bottom: 1px solid var(--border); }
  table { width: 100%; border-collapse: collapse; font-size: 12px; }
  th, td { padding: 6px 10px; text-align: left; border-bottom: 1px solid var(--border); }
  th { position: sticky; top: 0; background: var(--panel-2); cursor: pointer; user-select: none; }
  th:hover { background: var(--border); }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  tr.hidden { display: none; }
  .pill { display: inline-block; padding: 1px 8px; border-radius: 999px; font-size: 11px; background: var(--panel-2); border: 1px solid var(--border); }
  .pill.Junk { background: rgba(255,107,107,0.15); color: var(--bad); }
  .pill.Archive { background: rgba(240,179,71,0.15); color: var(--warn); }
  .pill.HeavyCache { background: rgba(122,162,255,0.15); color: var(--accent); }
  .pill.App, .pill.Project, .pill.System, .pill.Data { background: rgba(87,199,133,0.15); color: var(--good); }
  .pill.Unknown { background: var(--panel-2); }
  .ovr-select { background: var(--panel-2); color: var(--fg); border: 1px solid var(--border); border-radius: 4px; font-size: 11px; padding: 2px 4px; }
  details > summary { cursor: pointer; }
  pre { background: var(--panel-2); padding: 10px; border-radius: 6px; overflow: auto; font-size: 12px; }
  .caveat { background: var(--panel-2); border-left: 3px solid var(--warn); padding: 10px 14px; border-radius: 4px; font-size: 12px; color: var(--muted); margin-bottom: 12px; }
  .muted { color: var(--muted); }
</style>
</head>
<body>
<header>
  <h1>Disk Inventory &mdash; run $($Environment.RunId) ($($Environment.TimestampUtc))</h1>
  <div class="env" id="env"></div>
</header>
<main>

  <div class="caveat">
    <strong>Caveats.</strong> Timestamps are UTC ISO-8601 in CSV/JSON; the table below renders them in your locale. OneDrive files may be cloud-only placeholders (flagged <code>oneDrive</code>). Items under <code>HeavyCache</code> are never auto-quarantined; use the override dropdown to act on them explicitly. No permanent deletes &mdash; quarantined items remain reversible until you run <code>-PurgeQuarantine</code>.
  </div>

  <div class="tiles" id="tiles"></div>

  <div class="panel">
    <h2>Heavy caches detected</h2>
    <div id="heavy" style="padding: 8px 14px;"></div>
  </div>

  <div class="panel">
    <h2>Inventory ($Mode mode)</h2>
    <div class="filters">
      <input type="search" id="q" placeholder="Filter by path or name...">
      <select id="cat"><option value="">All categories</option></select>
      <select id="act"><option value="">All actions</option></select>
      <select id="drv"><option value="">All drives</option></select>
      <span class="muted" id="count"></span>
    </div>
    <div style="max-height: 70vh; overflow: auto;">
      <table id="tbl">
        <thead>
          <tr>
            <th data-k="name">Name</th>
            <th data-k="category">Category</th>
            <th data-k="action">Action</th>
            <th data-k="planAction">Planned</th>
            <th data-k="size" class="num">Size</th>
            <th data-k="lastWrite">Last write</th>
            <th data-k="path">Path</th>
            <th>Override</th>
          </tr>
        </thead>
        <tbody id="tbody"></tbody>
      </table>
    </div>
  </div>

  <div class="panel">
    <h2>Scan warnings</h2>
    <div id="warns" style="padding: 8px 14px;"></div>
  </div>

  <details class="panel">
    <summary><h2 style="display:inline">Raw environment snapshot (JSON)</h2></summary>
    <pre id="rawEnv"></pre>
  </details>

  <details class="panel">
    <summary><h2 style="display:inline">Collector stats (JSON)</h2></summary>
    <pre id="rawStats"></pre>
  </details>

</main>
<script>
  const env = $envJson;
  const heavy = $heavyJson;
  const warnings = $warningsJson;
  const stats = $statsJson;
  const rows = $tableJson;
  const mode = "$Mode";

  function fmtSize(n) {
    if (n === 0) return "0 B";
    const u = ["B","KB","MB","GB","TB"];
    let i = 0; let v = n;
    while (v >= 1024 && i < u.length-1) { v /= 1024; i++; }
    return v.toFixed(2) + " " + u[i];
  }
  function fmtDate(s) {
    if (!s) return "";
    try { return new Date(s).toLocaleString(); } catch (e) { return s; }
  }
  function driveOf(p) { const m = (p||"").match(/^([A-Z]):/i); return m ? m[1].toUpperCase() : ""; }

  // Env header
  const envEl = document.getElementById("env");
  const envPairs = [
    ["OS", env.Os.Caption + " " + env.Os.Version + " (build " + env.Os.Build + ")"],
    ["PowerShell", env.PowerShell],
    ["Locale", env.Locale.Ui + " (" + env.Locale.DisplayName + ")"],
    ["Admin", String(env.Admin)],
    ["Drives", (env.Drives||[]).map(d => d.Name + ": " + fmtSize(d.Total)).join("  ")],
    ["Profiles", (env.UserProfiles||[]).map(p => p.Name).join(", ") || "(none)"],
    ["Scan roots", (env.ScanRoots||[]).length + " (excluded " + (env.ExcludedRoots||[]).length + ")"],
    ["Items", stats.TotalItems + " (" + stats.FilesScanned + " files, " + stats.DirsScanned + " dirs)"],
    ["Mode", mode]
  ];
  envEl.innerHTML = envPairs.map(([k,v]) => '<div><strong>'+k+'</strong>: '+v+'</div>').join("");

  // Tiles
  const byCat = {}, byAct = {};
  let totalSize = 0;
  rows.forEach(r => { byCat[r.category] = (byCat[r.category]||0)+1; byAct[r.action||"keep"] = (byAct[r.action||"keep"]||0)+1; totalSize += r.size; });
  const odCount = rows.filter(r => r.oneDrive).length;
  const tiles = [
    ["Total items", rows.length],
    ["Total size", fmtSize(totalSize)],
    ["OneDrive placeholders", odCount],
    ["Heavy caches", (heavy||[]).length],
    ["Top category", Object.entries(byCat).sort((a,b)=>b[1]-a[1])[0]?.join(": ") || "-"],
    ["Errors", stats.Errors || 0]
  ];
  document.getElementById("tiles").innerHTML = tiles.map(([k,v]) => '<div class="tile"><div class="v">'+v+'</div><div class="k">'+k+'</div></div>').join("");

  // Heavy caches
  const heavyEl = document.getElementById("heavy");
  if (!heavy || heavy.length === 0) { heavyEl.textContent = "(none)"; }
  else {
    heavyEl.innerHTML = "<table><thead><tr><th>Kind</th><th>Label</th><th>Path</th><th>Size</th></tr></thead><tbody>" +
      heavy.slice().sort((a,b)=>b.SizeBytes-a.SizeBytes).slice(0, 50).map(h =>
        '<tr><td><span class="pill '+h.Kind+'">'+h.Kind+'</span></td><td>'+h.Label+'</td><td class="muted">'+h.Path+'</td><td class="num">'+fmtSize(h.SizeBytes)+'</td></tr>'
      ).join("") + "</tbody></table>";
  }

  // Dropdowns
  const cats = [...new Set(rows.map(r => r.category))].sort();
  const acts = [...new Set(rows.map(r => r.action))].sort();
  const drvs = [...new Set(rows.map(r => driveOf(r.path)))].sort();
  const catSel = document.getElementById("cat"); cats.forEach(c => { const o = document.createElement("option"); o.value = c; o.textContent = c; catSel.appendChild(o); });
  const actSel = document.getElementById("act"); acts.forEach(a => { const o = document.createElement("option"); o.value = a; o.textContent = a; actSel.appendChild(o); });
  const drvSel = document.getElementById("drv"); drvs.forEach(d => { const o = document.createElement("option"); o.value = d; o.textContent = d || "(other)"; drvSel.appendChild(o); });

  // Body
  const tbody = document.getElementById("tbody");
  const ovrActions = ["keep","quarantine","archive","group","delete"];
  function rowHtml(r) {
    const sel = '<select class="ovr-select" data-path="'+r.path.replace(/"/g,"&quot;")+'">' +
      '<option value="">(no override)</option>' +
      ovrActions.map(a => '<option value="'+a+'"'+(r.planAction===a?' selected':'')+'>'+a+'</option>').join("") +
      '</select>';
    return '<tr data-row>' +
      '<td>'+r.name+'</td>' +
      '<td><span class="pill '+r.category+'">'+r.category+'</span></td>' +
      '<td>'+(r.action||"")+'</td>' +
      '<td>'+(r.planAction||"")+'</td>' +
      '<td class="num">'+fmtSize(r.size)+'</td>' +
      '<td>'+fmtDate(r.lastWrite)+'</td>' +
      '<td class="muted" title="'+r.path+'">'+r.path+'</td>' +
      '<td>'+sel+'</td>' +
      '</tr>';
  }
  tbody.innerHTML = rows.slice(0, 2000).map(rowHtml).join("");
  if (rows.length > 2000) {
    const note = document.createElement("tr");
    note.innerHTML = '<td colspan="8" class="muted">Showing first 2000 of '+rows.length+' rows. Use the CSV for the full list.</td>';
    tbody.appendChild(note);
  }

  // Sort
  let sortKey = "size", sortDesc = true;
  document.querySelectorAll("th[data-k]").forEach(th => {
    th.addEventListener("click", () => {
      const k = th.dataset.k;
      if (sortKey === k) sortDesc = !sortDesc; else { sortKey = k; sortDesc = (k === "size" || k === "lastWrite"); }
      apply();
    });
  });

  // Filter
  const q = document.getElementById("q");
  function apply() {
    const term = (q.value||"").toLowerCase();
    const c = catSel.value, a = actSel.value, d = drvSel.value;
    const visible = rows.filter(r =>
      (!term || (r.path||"").toLowerCase().includes(term) || (r.name||"").toLowerCase().includes(term)) &&
      (!c || r.category === c) &&
      (!a || (r.action||"") === a) &&
      (!d || driveOf(r.path) === d)
    );
    visible.sort((x, y) => {
      const vx = x[sortKey], vy = y[sortKey];
      if (typeof vx === "number" && typeof vy === "number") return sortDesc ? vy - vx : vx - vy;
      return sortDesc ? String(vy||"").localeCompare(String(vx||"")) : String(vx||"").localeCompare(String(vy||""));
    });
    tbody.innerHTML = visible.slice(0, 2000).map(rowHtml).join("");
    if (visible.length > 2000) {
      const note = document.createElement("tr");
      note.innerHTML = '<td colspan="8" class="muted">Showing first 2000 of '+visible.length+' visible rows. Use the CSV for the full list.</td>';
      tbody.appendChild(note);
    }
    document.getElementById("count").textContent = visible.length + " of " + rows.length + " items shown";
  }
  q.addEventListener("input", apply);
  catSel.addEventListener("change", apply);
  actSel.addEventListener("change", apply);
  drvSel.addEventListener("change", apply);
  apply();

  // Override panel: gather all dropdowns and write overrides.json
  document.body.addEventListener("change", (ev) => {
    const t = ev.target;
    if (!(t instanceof HTMLSelectElement) || !t.classList.contains("ovr-select")) return;
    collectOverrides();
  });
  function collectOverrides() {
    const items = [];
    document.querySelectorAll(".ovr-select").forEach(s => {
      const v = s.value;
      if (v) items.push({ path: s.dataset.path, action: v });
    });
    const blob = new Blob([JSON.stringify({ items: items }, null, 2)], { type: "application/json" });
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = "overrides.json";
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1000);
  }

  // Raw panels
  document.getElementById("rawEnv").textContent = JSON.stringify(env, null, 2);
  document.getElementById("rawStats").textContent = JSON.stringify(stats, null, 2);
  document.getElementById("warns").innerHTML = (!warnings || warnings.length === 0) ? "<span class='muted'>(none)</span>" :
    "<table><thead><tr><th>Path</th><th>Reason</th></tr></thead><tbody>" +
    warnings.slice(0, 200).map(w => '<tr><td class="muted">'+w.Path+'</td><td>'+w.Reason+'</td></tr>').join("") + "</tbody></table>";
</script>
</body>
</html>
"@

    Out-File -LiteralPath $htmlPath -InputObject $html -Encoding utf8

    return [pscustomobject]@{
        CsvPath = $csvPath
        HtmlPath = $htmlPath
        MarkdownPath = $mdPath
    }
}

# NOTE: this file is dot-sourced by Invoke-Inventory.ps1, not imported as a module.
# No Export-ModuleMember needed.
