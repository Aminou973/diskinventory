# disk-inventory.ps1 - PowerShell launcher for DiskInventory v3.0.
#
#  -  Sets [Console]::OutputEncoding so non-ASCII paths print correctly.
#  -  Prefers python (Windows Launcher), then python3, then py -3.
#  -  Honors DISKINVENTORY_NOPAUSE / DISKINVENTORY_NO_PAUSE for CI.
#  -  Returns the same exit code as the underlying Python script.

[CmdletBinding()]
param(
    [Parameter(ValueFromRemainingArguments = $true)]
    [string[]]$Arguments
)

# PowerShell 5.1 ships utf-8 as the wrong .NET codepage; force UTF-8 in.
try {
    [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new($false)
    $OutputEncoding = [System.Text.UTF8Encoding]::new($false)
} catch { }

# Honor skip-pause env vars; PowerShell windows are typically persistent.
$NoPause = ($env:DISKINVENTORY_NOPAUSE -eq "1") -or
           ($env:DISKINVENTORY_NO_PAUSE -eq "1")

# Resolve script directory (PS 5.1 compatible; not depends on $PSScriptRoot
# being defined when called via -File).
$Here = if ($PSScriptRoot) { $PSScriptRoot } else { Split-Path -Parent $MyInvocation.MyCommand.Path }
Set-Location -LiteralPath $Here

function Find-Python3 {
    $candidates = @(
        @{ Cmd = "python"; Args = @() },
        @{ Cmd = "python3"; Args = @() },
        @{ Cmd = "py"; Args = @("-3") }
    )
    foreach ($cand in $candidates) {
        $cmd = Get-Command $cand.Cmd -ErrorAction SilentlyContinue
        if ($cmd) {
            $ver = & $cmd.Path @($cand.Args + @("-c", "import sys; print(f'{sys.version_info[0]}.{sys.version_info[1]}')")) 2>$null
            if ($ver -match '^(3\.(?:[89]|1[0-9]+))$') {
                return @{ Path = $cmd.Path; Args = $cand.Args }
            }
        }
    }
    return $null
}

$Py = Find-Python3
if (-not $Py) {
    Write-Error "[error] Python 3.8 or newer not found on PATH."
    if (-not $NoPause) { Read-Host "Press Enter to exit" }
    exit 127
}

$argList = @($Py.Args + @((Join-Path $Here "disk-inventory.py")) + $Arguments)
& $Py.Path @argList
$rc = $LASTEXITCODE

if (-not $NoPause -and $Host.Name -eq 'ConsoleHost' -and $Host.UI.RawUI.WindowTitle) {
    # Pause only when we own the console (not in a piped CI run).
    if (-not $args -contains '--no-pause') {
        Read-Host "Press Enter to close (rc=$rc)"
    }
}
exit $rc
