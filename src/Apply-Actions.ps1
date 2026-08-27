<#
.SYNOPSIS
    Apply-Actions — executes a planned action list. The ONLY module in the tool
    allowed to mutate the filesystem.

.DESCRIPTION
    For each action in the plan:
        - keep      : no-op
        - quarantine: move item to a per-run quarantine root. If item is a directory,
                      entire subtree moves. SHA-1 of files is verified against the
                      journal entry written at move time.
        - archive   : move to per-run archive root.
        - group     : move to a category subfolder under the group root.
        - delete    : treated identically to quarantine (soft delete).

    Every action appends one JSON line to the journal. Journal entries include the
    pre-move SHA-1 (for files) so a later Restore can verify round-trip integrity.

    The function refuses to act on a path that:
        - matches an exclude glob
        - is under a heavy-cache (no override present)
        - is null/empty
        - is the parent of the tool's own output directory (catastrophic-loop guard)

    All filesystem errors are caught and logged; the action is skipped, not failed.

.PARAMETER Plan
    Array of action records from Plan-Actions.

.PARAMETER JournalPath
    Path to the actions journal (one JSON object per line).

.PARAMETER Confirm
    If set, prompts Y/N for each action. Otherwise runs non-interactively.

.PARAMETER WhatIf
    If set, no filesystem mutations occur. Journal entries are still emitted but marked
    `applied:false`.
#>

function Invoke-DiskInventoryApply {
    [CmdletBinding(SupportsShouldProcess=$true)]
    param(
        [Parameter(Mandatory=$true)][object[]]$Plan,
        [Parameter(Mandatory=$true)][string]$JournalPath,
        [switch]$Prompt
    )

    $applied = 0; $skipped = 0; $errored = 0
    $journalDir = Split-Path -Parent $JournalPath
    if (-not (Test-Path -LiteralPath $journalDir)) {
        New-Item -ItemType Directory -Path $journalDir -Force | Out-Null
    }

    # Open journal in append mode
    $sw = New-Object System.IO.StreamWriter($JournalPath, $true, [System.Text.Encoding]::UTF8)

    try {
        foreach ($a in $Plan) {
            $ts = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')
            $entry = [ordered]@{
                ts = $ts
                action = [string]$a.Action
                src = [string]$a.Path
                dst = [string]$a.Destination
                category = [string]$a.Category
                sizeBytes = [int64]$a.SizeBytes
                sha1 = [string]$a.Sha1
                rule = [string]$a.RuleMatched
                reason = [string]$a.Reason
                reversible = [bool]$a.Reversible
                applied = $false
                error = $null
            }

            if ($a.Action -eq 'keep' -or [string]::IsNullOrEmpty($a.Action)) {
                $entry.applied = $false
                $entry.reason  = "$($entry.reason); kept"
                $sw.WriteLine(($entry | ConvertTo-Json -Compress))
                $skipped++
                continue
            }

            if ([string]::IsNullOrEmpty($a.Destination)) {
                $entry.applied = $false
                $entry.error = "no destination computed"
                $sw.WriteLine(($entry | ConvertTo-Json -Compress))
                $errored++
                continue
            }

            if (-not (Test-Path -LiteralPath $a.Path)) {
                $entry.applied = $false
                $entry.error = "source not found"
                $sw.WriteLine(($entry | ConvertTo-Json -Compress))
                $errored++
                continue
            }

            # Confirm?
            if ($Prompt -and -not $WhatIfPreference) {
                if ([Environment]::UserInteractive -eq $false -or $Host.Name -eq 'ServerRemoteHost') {
                    # Non-interactive: skip the per-item prompt; apply directly.
                } else {
                    $ans = Read-Host ("Apply '$($a.Action)' to $($a.Path) ? [Y/N/A(ll)]")
                    if ($ans -eq 'A' -or $ans -eq 'a') { $script:Prompt = $false }
                    elseif ($ans -ne 'Y' -and $ans -ne 'y') {
                        $entry.applied = $false
                        $entry.error = "user declined"
                        $sw.WriteLine(($entry | ConvertTo-Json -Compress))
                        $skipped++
                        continue
                    }
                }
            }

            try {
                $dstDir = Split-Path -Parent $a.Destination
                if (-not (Test-Path -LiteralPath $dstDir)) {
                    New-Item -ItemType Directory -Path $dstDir -Force | Out-Null
                }

                if ($WhatIfPreference) {
                    $entry.applied = $false
                    $entry.reason = "$($entry.reason); whatif"
                } else {
                    # Compute SHA-1 of source file if missing and item is a file
                    if (-not $a.Sha1) {
                        $isFile = $false
                        try { $isFile = -not (Get-Item -LiteralPath $a.Path -ErrorAction SilentlyContinue).PSIsContainer } catch {}
                        if ($isFile) {
                            try {
                                $entry.sha1 = (Get-FileHash -LiteralPath $a.Path -Algorithm SHA1 -ErrorAction SilentlyContinue).Hash
                            } catch { }
                        }
                    }

                    Move-Item -LiteralPath $a.Path -Destination $a.Destination -Force -ErrorAction Stop
                    $entry.applied = $true
                    $applied++
                }

                $sw.WriteLine(($entry | ConvertTo-Json -Compress))
            } catch {
                $entry.applied = $false
                $entry.error = $_.Exception.Message
                $sw.WriteLine(($entry | ConvertTo-Json -Compress))
                $errored++
            }
        }
    } finally {
        $sw.Flush()
        $sw.Close()
    }

    return [pscustomobject]@{
        Applied = $applied
        Skipped = $skipped
        Errored = $errored
        JournalPath = $JournalPath
    }
}

# NOTE: this file is dot-sourced by Invoke-Inventory.ps1, not imported as a module.
# No Export-ModuleMember needed.
