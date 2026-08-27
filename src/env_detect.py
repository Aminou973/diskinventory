"""
env_detect — platform-neutral entry point for environment detection.

Selects between env_detect_windows.py (Windows) and the POSIX implementation
in this module (Linux/macOS) at runtime. Both expose the same function:
    detect_environment() -> dict

The returned shape is validated against spec/environment.schema.json in tests;
this module does NOT validate here (validation lives in tests + serve.py, so
a transient detection failure doesn't kill the run).
"""

from __future__ import annotations

import json
import os
import platform
import shutil
import socket
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Optional POSIX module; guarded so this file is importable on Windows.
try:
    import pwd  # type: ignore
    _HAS_PWD = True
except ImportError:  # pragma: no cover
    pwd = None  # type: ignore
    _HAS_PWD = False


def _now_utc() -> str:
    """ISO-8601 UTC timestamp with second precision and explicit 'Z' suffix."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _is_root() -> bool:
    """Return True if running with uid 0 (root). On non-Unix, False."""
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False


def _run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _read_os_release() -> dict:
    """Best-effort parse of /etc/os-release (PRETTY_NAME, VERSION, etc.)."""
    out = {"Caption": platform.system(), "Version": "", "Build": platform.release()}
    p = Path("/etc/os-release")
    if not p.is_file():
        return out
    try:
        data: dict[str, str] = {}
        for line in p.read_text(encoding="utf-8", errors="replace").splitlines():
            if "=" not in line or line.lstrip().startswith("#"):
                continue
            k, _, v = line.partition("=")
            data[k.strip()] = v.strip().strip('"').strip("'")
        if "PRETTY_NAME" in data:
            out["Caption"] = data["PRETTY_NAME"]
        if "VERSION_ID" in data:
            out["Version"] = data["VERSION_ID"]
        if "BUILD_ID" in data:
            out["Build"] = data["BUILD_ID"]
    except OSError:
        pass
    return out


def _list_drives_posix() -> list[dict]:
    """List mounted filesystems on Linux/macOS via /proc/mounts (Linux) or df (macOS)."""
    drives: list[dict] = []
    # Linux path
    mounts = Path("/proc/mounts")
    if mounts.is_file():
        try:
            for line in mounts.read_text(encoding="utf-8", errors="replace").splitlines():
                parts = line.split()
                if len(parts) < 3:
                    continue
                mount, fstype = parts[1], parts[2]
                # Skip pseudo-filesystems
                if fstype in ("tmpfs", "devtmpfs", "sysfs", "proc", "devpts",
                              "cgroup", "cgroup2", "overlay", "squashfs",
                              "autofs", "binfmt_misc", "fusectl", "configfs",
                              "debugfs", "tracefs", "mqueue", "pstore",
                              "ramfs", "rpc_pipefs", "hugetlbfs", "nsfs",
                              "fuse.gvfsd-fuse", "fuse.portal"):
                    continue
                try:
                    usage = shutil.disk_usage(mount)
                    total = usage.total
                    free = usage.free
                except OSError:
                    total = 0
                    free = 0
                drives.append({
                    "Mount": mount,
                    "FSType": fstype,
                    "TotalBytes": total,
                    "FreeBytes": free,
                })
            return drives
        except OSError:
            pass
    # macOS / generic fallback
    import subprocess
    try:
        proc = subprocess.run(["df", "-kP"], capture_output=True, text=True,
                              timeout=10, check=False)
    except (FileNotFoundError, OSError):
        return drives
    for line in proc.stdout.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 6:
            continue
        # df -kP: Filesystem 1024-blocks Used Available Capacity Mounted-on
        try:
            total = int(parts[1]) * 1024
            free = int(parts[3]) * 1024
        except ValueError:
            continue
        drives.append({
            "Mount": parts[5],
            "FSType": "unknown",
            "TotalBytes": total,
            "FreeBytes": free,
        })
    return drives


def _list_user_profiles_posix() -> list[dict]:
    """List home directories (skip system users)."""
    profiles: list[dict] = []
    home = Path("/home")
    if home.is_dir():
        for entry in sorted(home.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name.startswith("."):
                continue
            profiles.append({
                "User": entry.name,
                "Home": str(entry),
                "IsCurrent": str(entry) == str(Path.home()),
                "IsAdmin": _is_root(),
            })
    # macOS: /Users
    users = Path("/Users")
    if users.is_dir() and not profiles:
        for entry in sorted(users.iterdir()):
            if not entry.is_dir():
                continue
            if entry.name in ("Shared", ".localized"):
                continue
            if entry.name.startswith("."):
                continue
            profiles.append({
                "User": entry.name,
                "Home": str(entry),
                "IsCurrent": str(entry) == str(Path.home()),
                "IsAdmin": _is_root(),
            })
    return profiles


def _detect_heavy_caches_posix() -> list[dict]:
    """Probe well-known heavy cache locations and report what exists."""
    candidates = [
        ("Pip", "~/.cache/pip"),
        ("Conda", "~/.conda/pkgs"),
        ("Npm", "~/.npm"),
        ("Yarn", "~/.cache/yarn"),
        ("Pnpm", "~/.local/share/pnpm"),
        ("Go", "~/go/pkg/mod"),
        ("Cargo", "~/.cargo/registry"),
        ("Maven", "~/.m2/repository"),
        ("Gradle", "~/.gradle/caches"),
        ("Dotnet", "~/.nuget/packages"),
        ("VSCode", "~/.config/Code/Cache"),
        ("Brave", "~/.config/BraveSoftware/Brave-Browser/Default"),
        ("Chrome", "~/.config/google-chrome/Default"),
        ("Firefox", "~/.mozilla/firefox"),
        ("Steam", "~/.steam/steam"),
        ("Docker", "~/.docker"),
        ("Trash", "~/.local/share/Trash/files"),
    ]
    found: list[dict] = []
    for name, rel in candidates:
        p = Path(os.path.expanduser(rel))
        if not p.exists():
            continue
        try:
            total = 0
            file_count = 0
            for root, dirs, files in os.walk(p):
                # Prune to avoid huge recursive walks on cache dirs
                depth = root.count(os.sep) - str(p).count(os.sep)
                if depth > 4:
                    dirs[:] = []
                    continue
                for f in files:
                    try:
                        fp = os.path.join(root, f)
                        total += os.path.getsize(fp)
                        file_count += 1
                    except OSError:
                        continue
        except OSError:
            total = 0
            file_count = 0
        found.append({
            "Name": name,
            "Path": str(p),
            "SizeBytes": total,
            "FileCount": file_count,
        })
    return found


def _detect_project_roots_posix() -> list[dict]:
    """Look for project roots: ~/Projects, ~/Code, ~/src, ~/work."""
    roots: list[dict] = []
    for name in ("Projects", "Code", "src", "work", "dev"):
        p = Path.home() / name
        if p.is_dir():
            roots.append({"Name": name, "Path": str(p)})
    return roots


def _default_scan_roots_posix() -> list[dict]:
    """Default scan roots on POSIX: user's home + visible system data dirs."""
    roots: list[dict] = [{"Name": "Home", "Path": str(Path.home())}]
    if Path("/data").is_dir():
        roots.append({"Name": "Data", "Path": "/data"})
    return roots


def _default_excluded_roots_posix() -> list[dict]:
    """Default exclusions: system + caches + mounts we don't want to walk."""
    excludes = [
        "/proc", "/sys", "/dev", "/run", "/snap", "/var/lib",
        "/var/cache", "/var/log", "/var/tmp", "/tmp",
        "/boot", "/lost+found", "/.snapshots",
    ]
    return [{"Name": Path(p).name or p, "Path": p} for p in excludes]


def detect_environment() -> dict:
    """Build the environment.json payload for POSIX (Linux/macOS)."""
    locale = {
        "Ui": "",
        "Culture": "",
        "DisplayName": "",
    }
    lang = os.environ.get("LANG", "")
    if lang:
        locale["Ui"] = lang
        locale["Culture"] = lang.split(".")[0]
    try:
        locale["DisplayName"] = f"{platform.system()} {platform.release()}"
    except Exception:
        pass

    env = {
        "RunId": _run_id(),
        "TimestampUtc": _now_utc(),
        "Os": _read_os_release(),
        "PowerShell": f"Python {platform.python_version()}",
        "Locale": locale,
        "Admin": _is_root(),
        "Drives": _list_drives_posix(),
        "UserProfiles": _list_user_profiles_posix(),
        "HeavyCaches": _detect_heavy_caches_posix(),
        "ProjectRoots": _detect_project_roots_posix(),
        "ScanRoots": _default_scan_roots_posix(),
        "ExcludedRoots": _default_excluded_roots_posix(),
        "Hostname": socket.gethostname(),
    }
    return env


# --- dispatcher -----------------------------------------------------------

def detect() -> dict:
    """Top-level entry point: chooses Windows vs POSIX implementation."""
    if sys.platform.startswith("win"):
        from . import env_detect_windows as win  # type: ignore
        return win.detect_environment()
    return detect_environment()
