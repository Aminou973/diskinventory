"""
env_detect_windows — Windows-only environment detection.

Calls out to PowerShell for WMI/CIM and ACL checks; the Python side just
shapes the JSON. We keep this in a separate module so the Linux/macOS
port doesn't import ctypes-only code or shell out to powershell.exe.

The shape returned is identical to env_detect.detect_environment().
"""

from __future__ import annotations

import json
import os
import shutil
import socket
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _is_admin() -> bool:
    """Best-effort admin check via ctypes (no PowerShell round-trip)."""
    if sys.platform != "win32":
        return False
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


_PS_ENV_SCRIPT = r"""
$ErrorActionPreference = 'SilentlyContinue'

$os = Get-CimInstance Win32_OperatingSystem
$caption = if ($os) { $os.Caption } else { [System.Environment]::OSVersion.VersionString }
$version = if ($os) { $os.Version } else { '' }
$build   = if ($os) { $os.BuildNumber } else { '' }

$drives = Get-CimInstance Win32_LogicalDisk -Filter 'DriveType=3' |
    Select-Object DeviceID, VolumeName, @{Name='SizeGB';Expression={[math]::Round($_.Size/1GB,1)}},
                              @{Name='FreeGB';Expression={[math]::Round($_.FreeSpace/1GB,1)}},
                              FileSystem

$profiles = Get-CimInstance Win32_UserProfile |
    Where-Object { -not $_.Special } |
    Select-Object @{Name='User';Expression={(Split-Path $_.LocalPath -Leaf)}},
                  @{Name='Home';Expression={$_.LocalPath}},
                  @{Name='IsCurrent';Expression={$_.LocalPath -eq $env:USERPROFILE}},
                  @{Name='Loaded';Expression={$_.Loaded}}

# Heavy caches we know to look for
$heavyNames = @(
    @{Name='Pip';     Path=Join-Path $env:USERPROFILE 'AppData\Local\pip\cache'},
    @{Name='Conda';   Path=Join-Path $env:USERPROFILE 'AppData\Local\conda\pkgs'},
    @{Name='Npm';     Path=JoinPathSafe $env:APPDATA 'npm-cache'},
    @{Name='Npm_alt'; Path=JoinPathSafe $env:LOCALAPPDATA 'npm-cache'},
    @{Name='Yarn';    Path=JoinPathSafe $env:LOCALAPPDATA 'Yarn\Cache'},
    @{Name='Pnpm';    Path=JoinPathSafe $env:LOCALAPPDATA 'pnpm-store'},
    @{Name='Go';      Path=JoinPathSafe $env:USERPROFILE 'go\pkg\mod'},
    @{Name='Cargo';   Path=JoinPathSafe $env:USERPROFILE '.cargo\registry'},
    @{Name='Maven';   Path=JoinPathSafe $env:USERPROFILE '.m2\repository'},
    @{Name='Gradle';  Path=JoinPathSafe $env:USERPROFILE '.gradle\caches'},
    @{Name='Nuget';   Path=JoinPathSafe $env:USERPROFILE '.nuget\packages'},
    @{Name='VSCode';  Path=JoinPathSafe $env:APPDATA 'Code\Cache'},
    @{Name='Chrome';  Path=JoinPathSafe $env:LOCALAPPDATA 'Google\Chrome\User Data\Default'},
    @{Name='Edge';    Path=JoinPathSafe $env:LOCALAPPDATA 'Microsoft\Edge\User Data\Default'},
    @{Name='Firefox'; Path=JoinPathSafe $env:APPDATA 'Mozilla\Firefox\Profiles'},
    @{Name='Steam';   Path='C:\Program Files (x86)\Steam'},
    @{Name='OneDrive';Path=JoinPathSafe $env:USERPROFILE 'OneDrive'},
    @{Name='Teams';   Path=JoinPathSafe $env:APPDATA 'Microsoft\Teams'},
    @{Name='Discord'; Path=JoinPathSafe $env:APPDATA 'discord\Cache'},
    @{Name='Temp';    Path=JoinPathSafe $env:TEMP ''}
)

function JoinPathSafe($a, $b) {
    if (-not $a) { return $null }
    if ($b) { return Join-Path $a $b }
    return $a
}

$heavy = foreach ($h in $heavyNames) {
    if ($h.Path -and (Test-Path -LiteralPath $h.Path)) {
        $size = (Get-ChildItem -LiteralPath $h.Path -Recurse -File -ErrorAction SilentlyContinue |
                 Measure-Object -Property Length -Sum).Sum
        $count = (Get-ChildItem -LiteralPath $h.Path -Recurse -File -ErrorAction SilentlyContinue |
                  Measure-Object).Count
        [pscustomobject]@{
            Name      = $h.Name
            Path      = $h.Path
            SizeBytes = [int64]$size
            FileCount = [int]$count
        }
    }
}

# Project roots
$projRoots = @('Projects','Code','src','work','dev','repos') | ForEach-Object {
    $p = Join-Path $env:USERPROFILE $_
    if (Test-Path -LiteralPath $p) { [pscustomobject]@{ Name=$_; Path=$p } }
}

$excludeNames = @('Windows','Program Files','Program Files (x86)','ProgramData','$Recycle.Bin','System Volume Information')
$excludeRoots = Get-PSDrive -PSProvider FileSystem |
    Where-Object { $excludeNames -contains $_.Description -or $_.Used -eq $null } |
    ForEach-Object { [pscustomobject]@{ Name=$_.Name; Path=$_.Root } }

$locale = [pscustomobject]@{
    Ui          = (Get-Culture).Name
    Culture     = (Get-Culture).Name
    DisplayName = (Get-Culture).DisplayName
}

[pscustomobject]@{
    Caption       = $caption
    Version       = $version
    Build         = $build
    PowerShell    = $PSVersionTable.PSVersion.ToString()
    Admin         = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
                        [Security.Principal.WindowsBuiltInRole]::Administrator)
    Drives        = $drives
    Profiles      = $profiles
    HeavyCaches   = $heavy
    ProjectRoots  = $projRoots
    Locale        = $locale
    ExcludedRoots = $excludeRoots
} | ConvertTo-Json -Depth 6 -Compress
"""


def _run_powershell_env() -> dict:
    """Invoke powershell.exe with the embedded script and parse its JSON."""
    if not shutil.which("powershell"):
        # Fallback to pwsh (PowerShell Core)
        if not shutil.which("pwsh"):
            return {}
    exe = "powershell" if shutil.which("powershell") else "pwsh"
    try:
        proc = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-Command", _PS_ENV_SCRIPT],
            capture_output=True, text=True, timeout=60, check=False,
        )
    except (FileNotFoundError, OSError, subprocess.TimeoutExpired):
        return {}
    if proc.returncode != 0 or not proc.stdout.strip():
        return {}
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {}


def detect_environment() -> dict:
    """Build the environment.json payload for Windows."""
    raw = _run_powershell_env()
    admin = _is_admin()
    os_block = raw.get("Os") or {}
    # Some PS versions nest Os under a different path; normalize.
    if not os_block and "Caption" in raw:
        os_block = {
            "Caption": raw.get("Caption", "Windows"),
            "Version": raw.get("Version", ""),
            "Build":   raw.get("Build", ""),
        }
    env = {
        "RunId": _run_id(),
        "TimestampUtc": _now_utc(),
        "Os": {
            "Caption": os_block.get("Caption", "Windows"),
            "Version": os_block.get("Version", ""),
            "Build":   os_block.get("Build", ""),
        },
        "PowerShell": raw.get("PowerShell") or "PowerShell 5.1",
        "Locale": raw.get("Locale") or {"Ui": "", "Culture": "", "DisplayName": ""},
        "Admin": admin or bool(raw.get("Admin", False)),
        "Drives": raw.get("Drives") or [],
        "UserProfiles": raw.get("Profiles") or [],
        "HeavyCaches": raw.get("HeavyCaches") or [],
        "ProjectRoots": raw.get("ProjectRoots") or [],
        "ScanRoots": [{"Name": "Home", "Path": os.environ.get("USERPROFILE", "")}],
        "ExcludedRoots": raw.get("ExcludedRoots") or [],
        "Hostname": socket.gethostname(),
    }
    return env


# --- dispatcher parity ---------------------------------------------------

def detect() -> dict:
    """Module-level entry, used by env_detect.detect()."""
    return detect_environment()
