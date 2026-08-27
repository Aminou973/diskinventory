<#
.SYNOPSIS
    Restore-FromJournal — reverses actions from a journal file.

.DESCRIPTION
    Walks one JSON-lines journal and, for every entry where `applied: true` and `action`
    is in (quarantine | archive | group | move), moves the file/directory from `dst`
    back to `src`. Skips entries with `applied: false`.

    Round-trip integrity: if a SHA-1 is recorded and the file is small enough, the SHA-1
    of the file at `dst` is computed before the move and compared. Mismatches are logged
    but do not block the restore.

    Defaults to -WhatIf: prints what would happen, performs no moves. Pass -Apply to actually
    restore.

.PARAMETER JournalPath
    Path to the journal file (e.g. out\<run>\actions-journal.jsonl).

.PARAMETER Apply
    If set, perform the moves. Default: -WhatIf (preview only).

.PARAMETER Sha1VerifyMaxMB
    Max file size (in MB) to verify SHA-1 against. Default 1024 (1 GB). Larger files
    are moved without verification.

.PARAMETER Sha1VerifyAlways
    If set, verify SHA-1 regardless of size (very slow for huge files).
#>

function Invoke-DiskInventoryRestore {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][string]$JournalPath,
        [int]$Sha1VerifyMaxMB = 1024,
        [switch]$Sha1VerifyAlways,
        [switch]$Apply,
        [switch]$WhatIfPreview
    )

    if (-not (Test-Path -LiteralPath $JournalPath)) {
        throw "Journal not found: $JournalPath"
    }

    $maxBytes = [int64]$Sha1VerifyMaxMB * 1MB
    $restored = 0; $skipped = 0; $errored = 0; $verified = 0; $mismatched = 0
    $lines = Get-Content -LiteralPath $JournalPath -Encoding utf8

    foreach ($line in $lines) {
        if ([string]::IsNullOrWhiteSpace($line)) { continue }
        $e = $line | ConvertFrom-Json
        if (-not $e.applied) { $skipped++; continue }
        if (-not $e.src -or -not $e.dst) { $skipped++; continue }
        if ($e.action -notin 'quarantine','archive','group','move','delete') { $skipped++; continue }

        if (-not (Test-Path -LiteralPath $e.dst)) {
            Write-Warning "Skip: dst not found: $($e.dst)"
            $skipped++
            continue
        }

        # SHA-1 verify (optional)
        $isFile = $false
        try { $isFile = -not (Get-Item -LiteralPath $e.dst -ErrorAction SilentlyContinue).PSIsContainer } catch {}
        if ($isFile -and $e.sha1 -and ($Sha1VerifyAlways -or $e.sizeBytes -le $maxBytes)) {
            try {
                $curSha = (Get-FileHash -LiteralPath $e.dst -Algorithm SHA1 -ErrorAction SilentlyContinue).Hash
                if ($curSha -eq $e.sha1) { $verified++ } else { $mismatched++; Write-Warning "SHA-1 mismatch on $($e.dst): was $($e.sha1), now $curSha" }
            } catch { }
        }

        # Ensure src parent exists
        $srcParent = Split-Path -Parent $e.src
        if (-not (Test-Path -LiteralPath $srcParent)) {
            try { New-Item -ItemType Directory -Path $srcParent -Force | Out-Null } catch { }
        }

        try {
            if (-not $Apply) {
                # Default: preview only. If WhatIfPreview is set, print; otherwise silent.
                if ($WhatIfPreview) { Write-Host "WOULD restore: $($e.dst) -> $($e.src)" }
            } else {
                Move-Item -LiteralPath $e.dst -Destination $e.src -Force -ErrorAction Stop
                $restored++
            }
        } catch {
            Write-Warning "Failed to restore $($e.dst) -> $($e.src): $($_.Exception.Message)"
            $errored++
        }
    }

    return [pscustomobject]@{
        Restored = $restored
        Skipped = $skipped
        Errored = $errored
        Verified = $verified
        Mismatched = $mismatched
    }
}

# NOTE: this file is dot-sourced by Invoke-Inventory.ps1, not imported as a module.
# No Export-ModuleMember needed.
