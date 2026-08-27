<#
.SYNOPSIS
    Detect-Environment — runtime snapshot of the host machine for the DiskInventory tool.

.DESCRIPTION
    Discovers (never assumes): OS, PowerShell version, locale, admin status, fixed drives,
    user profiles, OneDrive presence per profile, per-profile heavy caches, OS-level heavy
    paths, and common project roots. Writes a structured snapshot as JSON to the run output
    directory and returns it as a PSCustomObject so the caller can pass it downstream.

    No machine-specific paths are hard-coded. Every probe is best-effort and failure-safe.

.PARAMETER OutputDir
    Directory where environment.json will be written. Must already exist.

.PARAMETER Config
    Parsed PathsToScan.json (PSCustomObject). Used to know which probes to run.

.OUTPUTS
    PSCustomObject with: RunId, TimestampUtc, Os, PowerShell, Locale, Admin, Drives,
    UserProfiles, HeavyCaches, ProjectRoots, ScanRoots (resolved), Notes, ExcludedRoots.
#>

function Get-DiskInventoryEnvironment {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)][string]$OutputDir,
        [Parameter(Mandatory = $true)]$Config
    )

    $runId = (Get-Date).ToUniversalTime().ToString('yyyyMMdd-HHmmss')
    $ts = (Get-Date).ToUniversalTime().ToString('yyyy-MM-ddTHH:mm:ssZ')

    # --- OS ----------------------------------------------------------------
    $osCaption = "Unknown"; $osVersion = ""; $osBuild = ""
    try {
        $os = Get-CimInstance -ClassName Win32_OperatingSystem -ErrorAction SilentlyContinue
        if ($os) {
            $osCaption = [string]$os.Caption
            $osVersion = [string]$os.Version
            $osBuild   = [string]$os.BuildNumber
        }
    } catch { }

    # --- PowerShell --------------------------------------------------------
    $psv = $PSVersionTable.PSVersion
    $psDesc = "$($psv.Major).$($psv.Minor) ($($PSVersionTable.PSEdition))"

    # --- Locale ------------------------------------------------------------
    $ui = [System.Globalization.CultureInfo]::CurrentUICulture
    $cu = [System.Globalization.CultureInfo]::CurrentCulture

    # --- Admin? ------------------------------------------------------------
    $isAdmin = $false
    try {
        $id = [Security.Principal.WindowsIdentity]::GetCurrent()
        $pr = New-Object Security.Principal.WindowsPrincipal($id)
        $isAdmin = $pr.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)
    } catch { }

    # --- Fixed drives ------------------------------------------------------
    $drives = @()
    try {
        $psdrives = Get-PSDrive -PSProvider FileSystem -ErrorAction SilentlyContinue
        foreach ($d in $psdrives) {
            if ($null -eq $d.Used -and $null -eq $d.Free) { continue }
            $drives += [pscustomobject]@{
                Name  = $d.Name
                Root  = $d.Root
                Used  = [int64]$d.Used
                Free  = [int64]$d.Free
                Total = [int64]$d.Used + [int64]$d.Free
                Description = $d.Description
            }
        }
    } catch { }

    # --- User profiles -----------------------------------------------------
    $profiles = @()
    $excludedRoots = @()
    $profileExcludeNames = @("Public", "Default", "Default User", "All Users", "desktop.ini")
    $profilesRoot = Join-Path $env:SystemDrive "Users"
    if (Test-Path -LiteralPath $profilesRoot) {
        try {
            $candidates = Get-ChildItem -LiteralPath $profilesRoot -Directory -Force -ErrorAction SilentlyContinue
            foreach ($p in $candidates) {
                $name = $p.Name
                if ($profileExcludeNames -contains $name) { continue }
                $docs = Join-Path $p.FullName "Documents"
                if (-not (Test-Path -LiteralPath $docs)) { continue }
                $profiles += [pscustomobject]@{
                    Name = $name
                    Path = $p.FullName
                }
            }
        } catch { }
    }

    # --- Per-profile probes (OneDrive + heavy caches) ----------------------
    $heavyCaches = New-Object System.Collections.Generic.List[object]

    # Inline helper as a scriptblock to keep heavyCaches in closure scope
    $probeScript = {
        param($p, $l, $k)
        if ([string]::IsNullOrWhiteSpace($p)) { return }
        if (-not (Test-Path -LiteralPath $p)) { return }
        $sz = 0
        try {
            $it = Get-ChildItem -LiteralPath $p -Recurse -Force -ErrorAction SilentlyContinue
            $ms = $it | Measure-Object -Property Length -Sum
            if ($ms -and $ms.Sum) { $sz = [int64]$ms.Sum }
        } catch { }
        $heavyCaches.Add([pscustomobject]@{
            Kind   = $k
            Label  = $l
            Path   = $p
            SizeBytes = $sz
        })
    }

    foreach ($prof in $profiles) {
        $base = $prof.Path
        $appLocal = Join-Path $base "AppData\Local"

        # OneDrive
        $oneDrivePath = Join-Path $base "OneDrive"
        $oneDriveRegKey = "HKCU:\Software\Microsoft\OneDrive\Accounts\Personal"
        $oneDrivePresent = (Test-Path -LiteralPath $oneDrivePath) -or (Test-Path -LiteralPath $oneDriveRegKey)
        if ($oneDrivePresent) {
            & $probeScript -p $oneDrivePath -l "OneDrive ($($prof.Name))" -k "OneDrive"
        }

        # Per-profile heavy caches
        & $probeScript -p (Join-Path $appLocal "OneDrive")         -l "OneDrive cache ($($prof.Name))" -k "OneDriveCache"
        & $probeScript -p (Join-Path $appLocal "CrashDumps")       -l "CrashDumps ($($prof.Name))"    -k "CrashDumps"

        # UWP LocalCache (any package, not just Claude)
        $packagesRoot = Join-Path $appLocal "Packages"
        if (Test-Path -LiteralPath $packagesRoot) {
            try {
                $pkgs = Get-ChildItem -LiteralPath $packagesRoot -Directory -Force -ErrorAction SilentlyContinue
                foreach ($pkg in $pkgs) {
                    $lc = Join-Path $pkg.FullName "LocalCache"
                    if (Test-Path -LiteralPath $lc) {
                        & $probeScript -p $lc -l "UWP LocalCache: $($pkg.Name)" -k "UWPLocalCache"
                    }
                }
            } catch { }
        }

        # Python pythoncore-*
        if (Test-Path -LiteralPath $appLocal) {
            try {
                $pys = Get-ChildItem -LiteralPath $appLocal -Directory -Filter "pythoncore-*" -Force -ErrorAction SilentlyContinue
                foreach ($py in $pys) {
                    & $probeScript -p $py.FullName -l "Python ($($py.Name))" -k "Python"
                }
            } catch { }
        }

        # ollama
        & $probeScript -p (Join-Path $appLocal "ollama\models") -l "ollama models ($($prof.Name))" -k "OllamaModels"
        & $probeScript -p (Join-Path $base ".ollama")           -l ".ollama ($($prof.Name))"      -k "OllamaRoot"

        # Thumbcache
        & $probeScript -p (Join-Path $appLocal "Microsoft\Windows\Explorer") -l "Explorer thumbcache ($($prof.Name))" -k "Thumbcache"
    }

    # --- OS-level heavy paths ---------------------------------------------
    $sysDrive = $env:SystemDrive
    & $probeScript -p (Join-Path $sysDrive "Windows\WinSxS")                     -l "WinSxS"              -k "WinSxS"
    & $probeScript -p (Join-Path $sysDrive "Windows\Installer")                  -l "Windows Installer"   -k "WindowsInstaller"
    & $probeScript -p (Join-Path $sysDrive "Windows\Temp")                       -l "Windows Temp"        -k "WindowsTemp"
    & $probeScript -p (Join-Path $sysDrive "ProgramData\Package Cache")          -l "ProgramData Package Cache" -k "ProgramDataPackageCache"

    # --- Common project roots (probed) -------------------------------------
    $projectRoots = @()
    if ($Config.scanRoots.commonProjectRoots) {
        foreach ($r in $Config.scanRoots.commonProjectRoots) {
            if (Test-Path -LiteralPath $r) { $projectRoots += $r }
        }
    }
    foreach ($prof in $profiles) {
        if ($Config.scanRoots.perUserProjectRoots) {
            foreach ($sub in $Config.scanRoots.perUserProjectRoots) {
                $p = Join-Path $prof.Path $sub
                if (Test-Path -LiteralPath $p) { $projectRoots += $p }
            }
        }
    }

    # --- Resolve scan roots (the actual list passed to Collect-Inventory) -
    $scanRoots = New-Object System.Collections.Generic.List[object]

    if ($Config.scanRoots.allFixedDrives) {
        foreach ($d in $drives) {
            $scanRoots.Add([pscustomobject]@{ Kind = "Drive"; Path = $d.Root; Note = "fixed drive" })
        }
    }

    if ($Config.scanRoots.allUserProfiles) {
        foreach ($prof in $profiles) {
            # Per-profile standard folders
            if ($Config.scanRoots.perProfileStandardFolders) {
                foreach ($sub in $Config.scanRoots.perProfileStandardFolders) {
                    $p = Join-Path $prof.Path $sub
                    if (Test-Path -LiteralPath $p) {
                        $scanRoots.Add([pscustomobject]@{ Kind = "ProfileFolder"; Path = $p; Note = "$($prof.Name)\$sub" })
                    }
                }
            }
            # Per-profile optional folders
            if ($Config.scanRoots.perProfileOptionalFolders) {
                foreach ($sub in $Config.scanRoots.perProfileOptionalFolders) {
                    $p = Join-Path $prof.Path $sub
                    if (Test-Path -LiteralPath $p) {
                        $scanRoots.Add([pscustomobject]@{ Kind = "ProfileOptional"; Path = $p; Note = "$($prof.Name)\$sub" })
                    }
                }
            }
        }
    }

    foreach ($r in $projectRoots) {
        $scanRoots.Add([pscustomobject]@{ Kind = "ProjectRoot"; Path = $r; Note = "project root" })
    }

    # Admin-only roots
    if ($isAdmin -and $Config.scanRoots.adminOnlyRoots) {
        foreach ($r in $Config.scanRoots.adminOnlyRoots) {
            if (Test-Path -LiteralPath $r) {
                $scanRoots.Add([pscustomobject]@{ Kind = "AdminOnly"; Path = $r; Note = "admin-visible" })
            }
        }
    } else {
        if ($Config.scanRoots.adminOnlyRoots) {
            foreach ($r in $Config.scanRoots.adminOnlyRoots) {
                $excludedRoots += [pscustomobject]@{ Path = $r; Reason = "Not running as Administrator" }
            }
        }
    }

    # --- Assemble & emit ---------------------------------------------------
    $envSnap = [pscustomobject]@{
        RunId        = $runId
        TimestampUtc = $ts
        Os           = [pscustomobject]@{ Caption = $osCaption; Version = $osVersion; Build = $osBuild }
        PowerShell   = $psDesc
        Locale       = [pscustomobject]@{ Ui = $ui.Name; Culture = $cu.Name; DisplayName = $cu.DisplayName }
        Admin        = $isAdmin
        Drives       = $drives
        UserProfiles = $profiles
        HeavyCaches  = $heavyCaches.ToArray()
        ProjectRoots = $projectRoots
        ScanRoots    = $scanRoots.ToArray()
        ExcludedRoots = $excludedRoots
    }

    $envPath = Join-Path $OutputDir "environment.json"
    try {
        $envSnap | ConvertTo-Json -Depth 6 | Out-File -LiteralPath $envPath -Encoding utf8 -Force
    } catch { }

    return $envSnap
}

# NOTE: this file is dot-sourced by Invoke-Inventory.ps1, not imported as a module.
# No Export-ModuleMember needed.
