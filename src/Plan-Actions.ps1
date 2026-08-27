<#
.SYNOPSIS
    Plan-Actions — turns classified inventory into a list of proposed actions,
    honoring per-item overrides written by the user via the HTML report.

.DESCRIPTION
    The classified items already have a `SuggestedAction` field. This module:
        1. Loads overrides.json (path -> action) if present.
        2. Applies them on top of the suggestions.
        3. Computes a destination path for each action (group / archive / quarantine).
        4. Returns the action plan as an array of objects:
            Path, Action, Destination, SizeBytes, Category, Reason, RuleMatched, Reversible

    The Apply-Actions module is the only consumer. Plan-Actions never touches the disk.

.PARAMETER Classified
    Array of classified items (from Classify-Items.ps1).

.PARAMETER Rules
    Parsed ClassificationRules.json.

.PARAMETER Config
    Parsed PathsToScan.json (for quarantine/archive/group root names).

.PARAMETER OverridesPath
    Path to overrides.json. If absent, all suggestions are used as-is.

.PARAMETER RunId
    Identifier of the current run (used to build the quarantine subfolder).

.PARAMETER OutputDir
    Where archives/quarantine live under the run.

.PARAMETER HeavyCaches
    Array of detected heavy-cache paths. Items inside these are never auto-quarantined
    even if a rule says so - they require an explicit override.
#>

function Invoke-DiskInventoryPlan {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][object[]]$Classified,
        [Parameter(Mandatory=$true)]$Rules,
        [Parameter(Mandatory=$true)]$Config,
        [string]$OverridesPath,
        [Parameter(Mandatory=$true)][string]$RunId,
        [Parameter(Mandatory=$true)][string]$OutputDir,
        [object[]]$HeavyCaches
    )

    $overrides = @{}
    if ($OverridesPath -and (Test-Path -LiteralPath $OverridesPath)) {
        try {
            $o = Get-Content -LiteralPath $OverridesPath -Raw -Encoding utf8 | ConvertFrom-Json
            if ($o -and $o.items) {
                foreach ($x in $o.items) {
                    if ($x.path -and $x.action) {
                        $overrides[[string]$x.path] = [string]$x.action
                    }
                }
            }
        } catch { }
    }

    $hcSet = @{}
    if ($HeavyCaches) { foreach ($h in $HeavyCaches) { if ($h.Path) { $hcSet[[string]$h.Path] = $true } } }

    $quarantineRoot = if ($Config.quarantineRootName) { $Config.quarantineRootName } else { '_Quarantine' }
    $archiveRoot    = if ($Config.archiveRootName)    { $Config.archiveRootName    } else { '_Archive' }
    $groupRoot      = if ($Config.groupRootName)      { $Config.groupRootName      } else { '_Grouped' }

    $qRoot = Join-Path $OutputDir ($quarantineRoot + "\" + $RunId)
    $aRoot = Join-Path $OutputDir ($archiveRoot + "\" + $RunId)
    $gRoot = Join-Path $OutputDir ($groupRoot + "\" + $RunId)

    $actions = New-Object System.Collections.Generic.List[object]

    foreach ($item in $Classified) {
        $action = $item.SuggestedAction
        $reason = "rule:$($item.RuleMatched)"

        # Apply override if any
        if ($overrides.ContainsKey($item.Path)) {
            $newAction = $overrides[$item.Path]
            $reason = "override:$newAction (was: $action)"
            $action = $newAction
        }

        # Safety: items inside heavy-caches cannot be auto-quarantined/deleted without override
        $inHeavyCache = $false
        foreach ($hc in $hcSet.Keys) {
            if ($item.Path -like "*$hc*") { $inHeavyCache = $true; break }
        }
        if ($inHeavyCache -and ($action -eq 'quarantine' -or $action -eq 'delete') -and -not $overrides.ContainsKey($item.Path)) {
            $action = 'keep'
            $reason = "in heavy-cache (would need explicit override)"
        }

        # Safety: refuse to act on excluded paths
        if ($Config.excludeGlobs) {
            foreach ($g in $Config.excludeGlobs) {
                if ($item.Path -like "*$g*") {
                    if ($action -ne 'keep') {
                        $action = 'keep'
                        $reason = "matches exclude glob '$g'"
                    }
                    break
                }
            }
        }

        # Compute destination
        $dest = $null
        switch ($action) {
            'quarantine' {
                $rel = $item.Path
                if ($rel.StartsWith($env:SystemDrive, [System.StringComparison]::OrdinalIgnoreCase)) {
                    $rel = $rel.Substring($env:SystemDrive.Length)
                }
                $rel = $rel.TrimStart('\','/')
                $dest = Join-Path $qRoot $rel
            }
            'archive' {
                $rel = $item.Path
                if ($rel.StartsWith($env:SystemDrive, [System.StringComparison]::OrdinalIgnoreCase)) {
                    $rel = $rel.Substring($env:SystemDrive.Length)
                }
                $rel = $rel.TrimStart('\','/')
                $dest = Join-Path $aRoot $rel
            }
            'group' {
                $cat = $item.Category
                $dest = Join-Path (Join-Path $gRoot $cat) $item.Name
            }
            default { $dest = $null }
        }

        $actions.Add([pscustomobject]@{
            Path = $item.Path
            Action = $action
            Destination = $dest
            SizeBytes = [int64]$item.SizeBytes
            Category = $item.Category
            Reason = $reason
            RuleMatched = $item.RuleMatched
            Reversible = ($action -in 'quarantine','archive','group','move')
            Sha1 = $item.Sha1
        })
    }

    return $actions.ToArray()
}

# NOTE: this file is dot-sourced by Invoke-Inventory.ps1, not imported as a module.
# No Export-ModuleMember needed.
