<#
.SYNOPSIS
    Collect-Inventory — full-recursion filesystem walk for the DiskInventory tool.

.DESCRIPTION
    Walks each scan root discovered by Detect-Environment. Honors exclude globs (path-substring
    and file-name/extension lists). For each file or directory encountered, emits a record
    with: Path, Name, Parent, Kind (File/Dir), SizeBytes, LastWriteUtc, CreatedUtc, IsHidden,
    IsSystem, IsOneDrivePlaceholder, Sha1 (files only, optional), and a list of marker files
    found inside the directory (first 50). Errors (access denied, reparse points) are
    collected, not raised.

    The result is a single PSCustomObject with:
        - Items: array of records
        - Warnings: array of {Path, Reason}
        - Cache: hashtable for the size cache (populated if cache enabled)
        - Stats: counters

.PARAMETER ScanRoots
    Array of objects produced by Detect-Environment (Kind/Path/Note).

.PARAMETER Config
    Parsed PathsToScan.json.

.PARAMETER ComputeHashes
    If set, compute SHA-1 of every file (slow). Off by default; collectors may enable it
    for small targeted runs.

.PARAMETER SizeCache
    Optional hashtable @{ path = @{ size = N; mtimeUtc = T; length = L } } loaded from a
    previous run. The caller owns persistence; this module just reads/writes entries.

.PARAMETER MaxItems
    Safety cap. Stops collecting after this many items. 0 = unlimited.

.PARAMETER ProgressSink
    Optional scriptblock invoked every ~500 items:  param($count,$currentPath).
#>

function Invoke-DiskInventoryCollect {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][object[]]$ScanRoots,
        [Parameter(Mandatory=$true)]$Config,
        [switch]$ComputeHashes,
        [hashtable]$SizeCache,
        [int]$MaxItems = 0,
        [scriptblock]$ProgressSink
    )

    $items = New-Object System.Collections.Generic.List[object]
    $warnings = New-Object System.Collections.Generic.List[object]
    $statsHash = [ordered]@{
        FilesScanned = 0; DirsScanned = 0; CacheHits = 0; CacheMisses = 0
        HashesComputed = 0; Errors = 0; StartUtc = (Get-Date).ToUniversalTime()
    }

    # Build exclude sets for O(1) checks
    $excludeGlobs = @()
    if ($Config.excludeGlobs) { $excludeGlobs = @($Config.excludeGlobs) }
    $excludeFileNames = @()
    if ($Config.excludeFileNames) { $excludeFileNames = @($Config.excludeFileNames) }
    $excludeExts = @()
    if ($Config.excludeExtensions) { $excludeExts = @($Config.excludeExtensions) }

    function Test-ExcludedPath($path) {
        if ([string]::IsNullOrEmpty($path)) { return $true }
        $p = $path
        foreach ($g in $excludeGlobs) {
            if ($p -like "*$g*") { return $true }
        }
        return $false
    }

    function Test-ExcludedFile($fileInfo) {
        if ($excludeFileNames -contains $fileInfo.Name) { return $true }
        if ($excludeExts -contains $fileInfo.Extension.ToLower()) { return $true }
        return $false
    }

    # OneDrive placeholder detection
    function Test-OneDrivePlaceholder($fileInfo) {
        # A file is a placeholder if FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS (0x400000) is set
        try {
            $attrs = [System.IO.File]::GetAttributes($fileInfo.FullName)
            $recallFlag = [System.IO.FileAttributes]0x400000
            return (($attrs -band $recallFlag) -eq $recallFlag)
        } catch {
            return $false
        }
    }

    $count = 0
    $lastProgress = 0

    foreach ($root in $ScanRoots) {
        if ($MaxItems -gt 0 -and $count -ge $MaxItems) { break }
        # Accept either a string path or an object with a .Path property
        $rootPath = if ($root -is [string]) { $root } else { [string]$root.Path }
        if ([string]::IsNullOrEmpty($rootPath)) { continue }
        if (-not (Test-Path -LiteralPath $rootPath)) { continue }

        # Walk using Get-ChildItem -Recurse. -Force shows hidden/system. -ErrorAction SilentlyContinue
        # is essential because admin-locked subtrees would otherwise abort the whole walk.
        try {
            $all = Get-ChildItem -LiteralPath $rootPath -Recurse -Force -ErrorAction SilentlyContinue -ErrorVariable walkErrors
        } catch {
            $warnings.Add([pscustomobject]@{ Path = $rootPath; Reason = "Root walk failed: $($_.Exception.Message)" })
            continue
        }
        if ($walkErrors) {
            foreach ($we in $walkErrors) {
                $warnings.Add([pscustomobject]@{ Path = $we.TargetObject; Reason = $we.Exception.Message })
            }
        }

        # Also emit the root itself as a directory
        try {
            $rootItem = Get-Item -LiteralPath $rootPath -Force -ErrorAction SilentlyContinue
            if ($rootItem) {
                $items.Add([pscustomobject]@{
                    Path = $rootItem.FullName
                    Name = $rootItem.Name
                    Parent = Split-Path -Parent $rootItem.FullName
                    Kind = "Dir"
                    SizeBytes = 0   # dirs are not summed here
                    LastWriteUtc = $rootItem.LastWriteTimeUtc.ToString('yyyy-MM-ddTHH:mm:ssZ')
                    CreatedUtc   = $rootItem.CreationTimeUtc.ToString('yyyy-MM-ddTHH:mm:ssZ')
                    IsHidden = ($rootItem.Attributes -band [System.IO.FileAttributes]::Hidden) -ne 0
                    IsSystem = ($rootItem.Attributes -band [System.IO.FileAttributes]::System) -ne 0
                    IsOneDrivePlaceholder = $false
                    Sha1 = $null
                    MarkerFiles = @()
                })
                $statsHash.DirsScanned++
                $count++
            }
        } catch { }

        foreach ($it in $all) {
            if ($MaxItems -gt 0 -and $count -ge $MaxItems) { break }
            $full = $it.FullName
            if (Test-ExcludedPath -path $full) { continue }
            if (-not $it.PSIsContainer -and (Test-ExcludedFile -fileInfo $it)) { continue }

            $isDir = $it.PSIsContainer
            $size = 0
            $sha1 = $null
            $isOneDrivePH = $false

            if (-not $isDir) {
                $size = [int64]$it.Length
                $isOneDrivePH = Test-OneDrivePlaceholder -fileInfo $it

                # Cache lookup
                if ($SizeCache -and $SizeCache.ContainsKey($full)) {
                    $entry = $SizeCache[$full]
                    if ($entry.mtimeUtc -eq $it.LastWriteTimeUtc.ToString('o') -and $entry.length -eq $size) {
                        $sha1 = $entry.sha1
                        $statsHash.CacheHits++
                    } else {
                        $SizeCache.Remove($full)
                        $statsHash.CacheMisses++
                    }
                }

                if ($ComputeHashes -and [string]::IsNullOrEmpty($sha1)) {
                    try {
                        $sha1 = (Get-FileHash -LiteralPath $full -Algorithm SHA1 -ErrorAction SilentlyContinue).Hash
                        $statsHash.HashesComputed++
                        if ($SizeCache) {
                            $SizeCache[$full] = @{
                                size = $size
                                mtimeUtc = $it.LastWriteTimeUtc.ToString('o')
                                length = $size
                                sha1 = $sha1
                            }
                        }
                    } catch { }
                }
            }

            $items.Add([pscustomobject]@{
                Path = $full
                Name = $it.Name
                Parent = Split-Path -Parent $full
                Kind = if ($isDir) { "Dir" } else { "File" }
                SizeBytes = $size
                LastWriteUtc = $it.LastWriteTimeUtc.ToString('yyyy-MM-ddTHH:mm:ssZ')
                CreatedUtc   = $it.CreationTimeUtc.ToString('yyyy-MM-ddTHH:mm:ssZ')
                IsHidden = ($it.Attributes -band [System.IO.FileAttributes]::Hidden) -ne 0
                IsSystem = ($it.Attributes -band [System.IO.FileAttributes]::System) -ne 0
                IsOneDrivePlaceholder = $isOneDrivePH
                Sha1 = $sha1
                MarkerFiles = @()  # populated on demand by classifier
            })

            if ($isDir) { $statsHash.DirsScanned++ } else { $statsHash.FilesScanned++ }
            $count++

            if ($ProgressSink -and ($count - $lastProgress -ge 500)) {
                $lastProgress = $count
                try { & $ProgressSink $count $full } catch { }
            }
        }
    }

    $statsHash.EndUtc = (Get-Date).ToUniversalTime()
    $statsHash.TotalItems = $count
    $stats = [pscustomobject]$statsHash

    return [pscustomobject]@{
        Items = $items.ToArray()
        Warnings = $warnings.ToArray()
        Stats = $stats
        SizeCache = $SizeCache
    }
}

# Helper: pre-collect marker files for a directory (small set of names)
function Get-DiskInventoryMarkerFiles {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][string]$DirPath,
        [string[]]$Names = @('.git','.svn','.hg','.bzr','package.json','pyproject.toml',
                             'requirements.txt','Pipfile','Cargo.toml','go.mod','go.sum',
                             'pom.xml','build.gradle','build.gradle.kts','Makefile',
                             'CMakeLists.txt','docker-compose.yml','docker-compose.yaml',
                             'Dockerfile','setup.exe','Setup.exe','unins000.exe','*.exe','*.msi',
                             '*.iss','manifest.json','winget-manifest.yaml')
    )

    $found = @()
    if (-not (Test-Path -LiteralPath $DirPath)) { return $found }
    $literal = Convert-Path -LiteralPath $DirPath
    try {
        $children = Get-ChildItem -LiteralPath $literal -Force -ErrorAction SilentlyContinue
        foreach ($c in $children) {
            if ($c.PSIsContainer) { continue }
            foreach ($pattern in $Names) {
                if ($c.Name -like $pattern) {
                    $found += $c.Name
                    break
                }
            }
            if ($found.Count -ge 50) { break }
        }
    } catch { }
    return $found
}

# NOTE: this file is dot-sourced by Invoke-Inventory.ps1, not imported as a module.
# No Export-ModuleMember needed.
