<#
.SYNOPSIS
    Invoke-Inventory — entry point for the DiskInventory tool.

.DESCRIPTION
    Single command to:
        - Scan the current machine (auto-detected).
        - Classify every item.
        - Write CSV + HTML + Markdown reports.
        - Optionally dry-run, execute, restore, or purge the quarantine.

    Subcommands are selected with the first positional parameter:
        Mode <Report|DryRun|Auto>          (default: Report)
        -Restore <journalPath>             reverses a journal (default -WhatIf)
        -PurgeQuarantine [-OlderThanDays N] permanently deletes _Quarantine\<runId>
                                           contents older than N days (default 30).
                                           USE WITH CARE.

    The tool is self-discovering: it uses $PSScriptRoot to find its own location, then
    dot-sources the modules in src\. No install, no env vars, no registry.

.PARAMETER Mode
    Report (default, read-only), DryRun (writes journal, no disk changes), Auto (executes).

.PARAMETER OutputDir
    Where reports + journal + quarantine land. Default: <script dir>\out\<runId>.

.PARAMETER ConfigDir
    Override path to the config\ directory. Default: <script dir>\config.

.PARAMETER RootsOverride
    Optional list of paths to scan INSTEAD of auto-detected ones. Useful for a small test run.

.PARAMETER MaxItems
    Optional cap on total items collected. 0 = unlimited.

.PARAMETER ComputeHashes
    If set, compute SHA-1 of every file (slow). Default off.

.PARAMETER HonorOverrides
    Path to an overrides.json (produced by the HTML report) to apply on top of rules.

.PARAMETER Confirm
    Prompt before each action. Default: do not prompt.

.PARAMETER Restore
    Restore from a journal. When set, Mode is ignored.

.PARAMETER PurgeQuarantine
    Purge old quarantine contents. When set, Mode is ignored.

.PARAMETER OlderThanDays
    For -PurgeQuarantine. Default 30.

.PARAMETER Apply
    For -Restore: actually move files (default is -WhatIf).
#>

[CmdletBinding(DefaultParameterSetName="Run")]
param(
    [Parameter(ParameterSetName="Run", Position=0)][ValidateSet("Report","DryRun","Auto")][string]$Mode = "Report",

    [Parameter(ParameterSetName="Run")][string]$OutputDir,
    [Parameter(ParameterSetName="Run")][string]$ConfigDir,
    [Parameter(ParameterSetName="Run")][string[]]$RootsOverride,
    [Parameter(ParameterSetName="Run")][int]$MaxItems = 0,
    [Parameter(ParameterSetName="Run")][switch]$ComputeHashes,
    [Parameter(ParameterSetName="Run")][string]$HonorOverrides,
    [Parameter(ParameterSetName="Run")][switch]$Prompt,

    [Parameter(ParameterSetName="Restore", Mandatory=$true)][string]$Restore,
    [Parameter(ParameterSetName="Restore")][switch]$Apply,
    [Parameter(ParameterSetName="Restore")][int]$Sha1VerifyMaxMB = 1024,

    [Parameter(ParameterSetName="Purge", Mandatory=$true)][switch]$PurgeQuarantine,
    [Parameter(ParameterSetName="Purge")][int]$OlderThanDays = 30
)

$ErrorActionPreference = 'Stop'

# ---- Resolve tool location --------------------------------------------------
$ToolDir = $PSScriptRoot
if ([string]::IsNullOrEmpty($ToolDir)) {
    $ToolDir = Split-Path -Parent $MyInvocation.MyCommand.Definition
}
$SrcDir   = Join-Path $ToolDir 'src'
$ConfDir  = if ($ConfigDir) { $ConfigDir } else { Join-Path $ToolDir 'config' }

# Dot-source all modules
$modules = @(
    'Detect-Environment.ps1',
    'Collect-Inventory.ps1',
    'Classify-Items.ps1',
    'Plan-Actions.ps1',
    'Apply-Actions.ps1',
    'Export-Reports.ps1',
    'Restore-FromJournal.ps1'
)
foreach ($m in $modules) {
    $p = Join-Path $SrcDir $m
    if (-not (Test-Path -LiteralPath $p)) { throw "Missing module: $p" }
    . $p
}

# ---- Subcommand: Restore ----------------------------------------------------
if ($Restore) {
    Write-Host "=== Restore from journal: $Restore ===" -ForegroundColor Cyan
    $params = @{ JournalPath = $Restore; Sha1VerifyMaxMB = $Sha1VerifyMaxMB }
    if ($Apply) { $params.Apply = $true } else { $params.WhatIfPreview = $true }
    $r = Invoke-DiskInventoryRestore @params
    Write-Host ("Restored: {0}  Skipped: {1}  Errored: {2}  Verified: {3}  Mismatched: {4}" -f $r.Restored,$r.Skipped,$r.Errored,$r.Verified,$r.Mismatched)
    return
}

# ---- Subcommand: PurgeQuarantine -------------------------------------------
if ($PurgeQuarantine) {
    $rootBase = if ($OutputDir) { $OutputDir } else { Join-Path $ToolDir 'out' }
    $quarantine = Join-Path $rootBase '_Quarantine'
    if (-not (Test-Path -LiteralPath $quarantine)) { Write-Host "No _Quarantine at $quarantine"; return }
    $cutoff = (Get-Date).AddDays(-1 * $OlderThanDays)
    Write-Host "Purging quarantine subdirs older than $OlderThanDays days ($($cutoff.ToString('yyyy-MM-dd')))..." -ForegroundColor Yellow
    $removed = 0
    Get-ChildItem -LiteralPath $quarantine -Directory -ErrorAction SilentlyContinue | ForEach-Object {
        if ($_.LastWriteTime -lt $cutoff) {
            Write-Host "  Removing $($_.FullName)"
            Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue
            $removed++
        }
    }
    Write-Host "Purged $removed quarantine run(s)."
    return
}

# ---- Main run ---------------------------------------------------------------
Write-Host "=== DiskInventory :: Mode = $Mode ===" -ForegroundColor Cyan

# Load configs
$rulesPath = Join-Path $ConfDir 'ClassificationRules.json'
$pathsPath = Join-Path $ConfDir 'PathsToScan.json'
if (-not (Test-Path -LiteralPath $rulesPath)) { throw "Missing: $rulesPath" }
if (-not (Test-Path -LiteralPath $pathsPath)) { throw "Missing: $pathsPath" }
$Rules = Get-Content -LiteralPath $rulesPath -Raw -Encoding utf8 | ConvertFrom-Json
$PathsConfig = Get-Content -LiteralPath $pathsPath -Raw -Encoding utf8 | ConvertFrom-Json

# Output dir
$runId = (Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmss')
if ($OutputDir) {
    $OutDir = $OutputDir
} else {
    $OutDir = Join-Path (Join-Path $ToolDir 'out') $runId
}
if (-not (Test-Path -LiteralPath $OutDir)) {
    New-Item -ItemType Directory -Path $OutDir -Force | Out-Null
}
Write-Host "Output dir: $OutDir"

# Step 1: Detect environment
Write-Host "[1/5] Detecting environment..." -ForegroundColor Yellow
$env = Get-DiskInventoryEnvironment -OutputDir $OutDir -Config $PathsConfig
Write-Host ("       Drives: {0}  Profiles: {1}  Scan roots: {2}" -f @($env.Drives).Count, @($env.UserProfiles).Count, @($env.ScanRoots).Count)

# Step 2: Collect inventory
if ($RootsOverride -and $RootsOverride.Count -gt 0) {
    $roots = [string[]]$RootsOverride
} else {
    $roots = @($env.ScanRoots | ForEach-Object { [string]$_.Path })
}
Write-Host "[2/5] Collecting inventory from $(@($roots).Count) root(s)..." -ForegroundColor Yellow

$sizeCache = $null
if ($PathsConfig.sizeCacheEnabled) {
    $cacheFile = Join-Path $OutDir ($PathsConfig.sizeCacheFileName)
    if (Test-Path -LiteralPath $cacheFile) {
        try {
            $sizeCache = Get-Content -LiteralPath $cacheFile -Raw -Encoding utf8 | ConvertFrom-Json
            $sizeCache = @{}
            foreach ($p in $sizeCache.PSObject.Properties) { $sizeCache[$p.Name] = $p.Value }
        } catch { $sizeCache = @{} }
    } else {
        $sizeCache = @{}
    }
}

$progress = {
    param($c, $p)
    Write-Host ("       ... {0} items (current: {1})" -f $c, $p)
}

$collected = Invoke-DiskInventoryCollect -ScanRoots $roots -Config $PathsConfig -ComputeHashes:$ComputeHashes -SizeCache $sizeCache -MaxItems $MaxItems -ProgressSink $progress
Write-Host ("       Items: {0} (files: {1}, dirs: {2})  Warnings: {3}  Cache hits: {4}" -f $collected.Stats.TotalItems, $collected.Stats.FilesScanned, $collected.Stats.DirsScanned, @($collected.Warnings).Count, $collected.Stats.CacheHits)

# Persist size cache
if ($PathsConfig.sizeCacheEnabled -and $sizeCache) {
    $cacheFile = Join-Path $OutDir ($PathsConfig.sizeCacheFileName)
    try {
        $sizeCache | ConvertTo-Json -Depth 4 -Compress | Out-File -LiteralPath $cacheFile -Encoding utf8 -Force
    } catch { }
}

# Step 3: Classify
Write-Host "[3/5] Classifying items..." -ForegroundColor Yellow
$classified = Invoke-DiskInventoryClassify -Items $collected.Items -Rules $Rules
$byCat = $classified | Group-Object Category | Sort-Object Count -Descending
Write-Host "       Categories:"
foreach ($g in $byCat) {
    Write-Host ("         - {0,-12} {1,8}" -f $g.Name, $g.Count)
}

# Step 4: Plan actions
Write-Host "[4/5] Planning actions..." -ForegroundColor Yellow
$plan = Invoke-DiskInventoryPlan -Classified $classified -Rules $Rules -Config $PathsConfig `
    -OverridesPath $HonorOverrides -RunId $runId -OutputDir $OutDir -HeavyCaches $env.HeavyCaches
$byAct = $plan | Group-Object Action | Sort-Object Count -Descending
Write-Host "       Planned actions:"
foreach ($g in $byAct) {
    Write-Host ("         - {0,-12} {1,8}" -f $g.Name, $g.Count)
}

# Step 5: Write reports (always)
Write-Host "[5/5] Writing reports..." -ForegroundColor Yellow
$reportPaths = Invoke-DiskInventoryExport -Classified $classified -Plan $plan -Environment $env -Stats $collected.Stats -Warnings $collected.Warnings -OutputDir $OutDir -ReportPrefix 'inventory' -Mode $Mode
Write-Host ("       CSV:      {0}" -f $reportPaths.CsvPath)
Write-Host ("       HTML:     {0}" -f $reportPaths.HtmlPath)
Write-Host ("       Markdown: {0}" -f $reportPaths.MarkdownPath)

# Write plan.json for transparency
try {
    $plan | ConvertTo-Json -Depth 4 -Compress | Out-File -LiteralPath (Join-Path $OutDir 'plan.json') -Encoding utf8 -Force
} catch { }

# Detect non-interactive (e.g. run from a non-tty context like Task Scheduler or a CI)
$NonInteractive = [Environment]::UserInteractive -eq $false -or $Host.Name -eq 'ServerRemoteHost'

# Apply actions if Auto
if ($Mode -eq 'Auto') {
    $journalPath = Join-Path $OutDir 'actions-journal.jsonl'
    if (-not $Prompt -and -not $NonInteractive) {
        Write-Host ""
        Write-Host "About to APPLY $($plan.Count) planned action(s). Type Y to proceed, anything else to abort." -ForegroundColor Red
        $ans = Read-Host "[Y/N]"
        if ($ans -ne 'Y' -and $ans -ne 'y') {
            Write-Host "Aborted. Reports already written to $OutDir" -ForegroundColor Yellow
            return
        }
    } elseif ($NonInteractive -and -not $Prompt) {
        Write-Host ""
        Write-Host "Non-interactive mode detected and -Prompt not set. Refusing to run Auto without explicit -Prompt." -ForegroundColor Red
        Write-Host "Reports have been written to $OutDir. Re-run with -Prompt to execute." -ForegroundColor Yellow
        return
    }
    Write-Host "Applying actions..." -ForegroundColor Yellow
    $applyParams = @{ Plan = $plan; JournalPath = $journalPath }
    if ($Prompt) { $applyParams.Prompt = $true }
    $result = Invoke-DiskInventoryApply @applyParams
    Write-Host ("       Applied: {0}  Skipped: {1}  Errored: {2}" -f $result.Applied, $result.Skipped, $result.Errored)
    Write-Host ("       Journal: {0}" -f $result.JournalPath)
} elseif ($Mode -eq 'DryRun') {
    $journalPath = Join-Path $OutDir 'dryrun-journal.jsonl'
    Write-Host "DryRun: writing dryrun-journal..." -ForegroundColor Yellow
    $applyParams = @{ Plan = $plan; JournalPath = $journalPath; WhatIf = $true }
    $result = Invoke-DiskInventoryApply @applyParams
    Write-Host ("       Journal entries: {0} (no disk changes)" -f ($result.Applied + $result.Skipped + $result.Errored))
    Write-Host ("       Journal: {0}" -f $result.JournalPath)
}

Write-Host ""
Write-Host "Done. Open $($reportPaths.HtmlPath) in a browser to review." -ForegroundColor Green
