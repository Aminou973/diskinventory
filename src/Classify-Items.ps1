<#
.SYNOPSIS
    Classify-Items — applies ClassificationRules.json to inventory records.

.DESCRIPTION
    Each item is assigned (in order):
        Category   : one of the categories declared in the rules
        Action     : keep | group | archive | quarantine | delete
        RuleMatched: name of the rule that fired (for the report)
        Notes      : free-form string ("old folder" / "is OneDrive placeholder" / etc.)

    The classifier is rule-driven and first-match. Rules are checked in this order:
        1. System (path contains Windows\ / System32 / etc.) - never auto-acted
        2. App / Project (marker-file presence inside a directory)
        3. HeavyCache (path substring match)
        4. Archive (path-glob or extension + age)
        5. Junk (file-name or folder-name)
        6. Data (path is under Documents/Desktop/Downloads)
        7. Unknown (default)

    Marker files are detected by listing the parent directory's children (cheap).
    This avoids a second full walk.

.PARAMETER Items
    Array of records produced by Invoke-DiskInventoryCollect.

.PARAMETER Rules
    Parsed ClassificationRules.json.

.PARAMETER NowUtc
    Reference time for "older than N days" calculations. Defaults to UtcNow.
#>

function Invoke-DiskInventoryClassify {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory=$true)][object[]]$Items,
        [Parameter(Mandatory=$true)]$Rules,
        [datetime]$NowUtc
    )

    if ($PSBoundParameters.ContainsKey('NowUtc') -eq $false) {
        $NowUtc = (Get-Date).ToUniversalTime()
    }

    $out = New-Object System.Collections.Generic.List[object]
    $markerCache = @{}  # dir -> array of child file names

    function Get-MarkersCached($dir) {
        if ([string]::IsNullOrEmpty($dir)) { return @() }
        if ($markerCache.ContainsKey($dir)) { return $markerCache[$dir] }
        $m = Get-DiskInventoryMarkerFiles -DirPath $dir
        $markerCache[$dir] = $m
        return $m
    }

    function Test-MarkerPresent($dir, $patterns) {
        $markers = Get-MarkersCached -dir $dir
        if (-not $markers -or $markers.Count -eq 0) { return $false }
        foreach ($m in $markers) {
            foreach ($p in $patterns) {
                if ($m -like $p) { return $true }
            }
        }
        return $false
    }

    function Test-PathContainsAny($path, $list) {
        if (-not $list) { return $false }
        foreach ($sub in $list) {
            if ($path -like "*$sub*") { return $true }
        }
        return $false
    }

    function Test-NameMatchesAny($name, $list) {
        if (-not $list) { return $false }
        foreach ($p in $list) {
            if ($name -like $p) { return $true }
        }
        return $false
    }

    $catByName = @{}
    foreach ($c in $Rules.categories) { $catByName[$c.name] = $c }

    foreach ($item in $Items) {
        $path = $item.Path
        $name = $item.Name
        $parent = $item.Parent
        $isDir = ($item.Kind -eq 'Dir')

        $category = $null
        $action = $null
        $ruleMatched = $null
        $notes = ""

        # 1) System
        $sysRule = $catByName['System']
        if ($sysRule -and (Test-PathContainsAny -path $path -list $sysRule.match.pathContains)) {
            $category = 'System'
            $action = $sysRule.action
            $ruleMatched = 'System:pathContains'
        }

        # 2) App (path-based OR marker-based)
        if (-not $category) {
            $appRule = $catByName['App']
            if ($appRule) {
                $pathHit = Test-PathContainsAny -path $path -list $appRule.match.pathContains
                $markerHit = $false
                if (-not $pathHit -and $isDir -and $appRule.match.markerFiles) {
                    $markerHit = Test-MarkerPresent -dir $path -patterns $appRule.match.markerFiles
                } elseif ($appRule.match.markerFiles) {
                    # For files, also check sibling folder for markers
                    $markerHit = Test-MarkerPresent -dir $parent -patterns $appRule.match.markerFiles
                }
                if ($pathHit -or $markerHit) {
                    $category = 'App'
                    $action = $appRule.action
                    $ruleMatched = if ($pathHit) { 'App:pathContains' } else { 'App:markerFile' }
                }
            }
        }

        # 3) Project (VCS or build file marker)
        if (-not $category) {
            $projRule = $catByName['Project']
            if ($projRule -and $projRule.match.markerFiles) {
                $hit = $false
                if ($isDir) {
                    $hit = Test-MarkerPresent -dir $path -patterns $projRule.match.markerFiles
                } else {
                    $hit = Test-MarkerPresent -dir $parent -patterns $projRule.match.markerFiles
                }
                if ($hit) {
                    $category = 'Project'
                    $action = $projRule.action
                    $ruleMatched = 'Project:markerFile'
                }
            }
        }

        # 4) HeavyCache
        if (-not $category) {
            $hc = $catByName['HeavyCache']
            if ($hc -and (Test-PathContainsAny -path $path -list $hc.match.pathContains)) {
                $category = 'HeavyCache'
                $action = $hc.action
                $ruleMatched = 'HeavyCache:pathContains'
            }
        }

        # 5) Archive
        if (-not $category) {
            $arc = $catByName['Archive']
            if ($arc) {
                $isArchive = $false
                $why = ""
                if ($isDir -and $arc.match.pathLike) {
                    foreach ($pl in $arc.match.pathLike) {
                        if ($path -like $pl) { $isArchive = $true; $why = "pathLike:$pl"; break }
                    }
                }
                if (-not $isArchive -and $arc.match.fileExtensions) {
                    foreach ($ext in $arc.match.fileExtensions) {
                        if ($name -like "*$ext") { $isArchive = $true; $why = "extension:$ext"; break }
                    }
                }
                if ($isArchive) {
                    $category = 'Archive'
                    $action = $arc.action
                    $ruleMatched = "Archive:$why"
                    if ($Rules.archive -and $Rules.archive.olderThanDays) {
                        $cutoff = $NowUtc.AddDays(-1 * [int]$Rules.archive.olderThanDays)
                        $lw = $null
                        if ($item.LastWriteUtc) {
                            try { $lw = [datetime]::Parse($item.LastWriteUtc).ToUniversalTime() } catch { }
                        }
                        if ($lw -and $lw -lt $cutoff) {
                            $notes += "last write $(([math]::Round((($NowUtc - $lw).TotalDays))))d ago (>$($Rules.archive.olderThanDays)d threshold); "
                        } else {
                            $notes += "recently modified (under $($Rules.archive.olderThanDays)d); "
                        }
                    }
                }
            }
        }

        # 6) Junk
        if (-not $category) {
            $junk = $catByName['Junk']
            if ($junk) {
                $hit = $false
                $why = ""
                if (-not $isDir -and $junk.match.nameLike) {
                    foreach ($nl in $junk.match.nameLike) {
                        if ($name -like $nl) { $hit = $true; $why = "nameLike:$nl"; break }
                    }
                }
                if (-not $hit -and $isDir -and $junk.match.nameEquals) {
                    foreach ($ne in $junk.match.nameEquals) {
                        if ($name -eq $ne) { $hit = $true; $why = "nameEquals:$ne"; break }
                    }
                }
                if ($hit) {
                    $category = 'Junk'
                    $action = $junk.action
                    $ruleMatched = "Junk:$why"
                }
            }
        }

        # 7) Data
        if (-not $category) {
            $data = $catByName['Data']
            if ($data -and (Test-PathContainsAny -path $path -list $data.match.pathContains)) {
                $category = 'Data'
                $action = $data.action
                $ruleMatched = 'Data:pathContains'
            }
        }

        # 8) Unknown
        if (-not $category) {
            $category = if ($Rules.defaultCategory) { [string]$Rules.defaultCategory } else { 'Unknown' }
            $action = if ($Rules.defaultAction) { [string]$Rules.defaultAction } else { 'keep' }
            $ruleMatched = 'default'
        }

        # Safety net: never auto-anything for App / System / Project / Data / HeavyCache
        $safeCategories = @('App','System','Project','Data','HeavyCache')
        if ($safeCategories -contains $category) {
            $action = 'keep'
        }

        # Notes about OneDrive
        if ($item.IsOneDrivePlaceholder) {
            $notes += "OneDrive placeholder; "
        }

        $out.Add([pscustomobject]@{
            Path = $path
            Name = $name
            Parent = $parent
            Kind = $item.Kind
            SizeBytes = [int64]$item.SizeBytes
            LastWriteUtc = $item.LastWriteUtc
            CreatedUtc = $item.CreatedUtc
            IsHidden = $item.IsHidden
            IsSystem = $item.IsSystem
            IsOneDrivePlaceholder = $item.IsOneDrivePlaceholder
            Sha1 = $item.Sha1
            MarkerFiles = $item.MarkerFiles
            Category = $category
            Action = $action
            SuggestedAction = $action
            RuleMatched = $ruleMatched
            Notes = $notes.Trim()
        })
    }

    return $out.ToArray()
}

# NOTE: this file is dot-sourced by Invoke-Inventory.ps1, not imported as a module.
# No Export-ModuleMember needed.
