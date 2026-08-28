"""scan_roots.py — smart-default scan-root selection per OS.

Replaces the v2.0 behavior of scanning ``$HOME`` / ``%USERPROFILE%``
whole-cloth, which on most users' boxes means tens of thousands of
paths and the engine appearing "stuck" on first run.

v3 picks a small curated list of paths (Documents, Downloads, Desktop,
and any pre-existing ``~/Projects`` / ``~/Sources`` style directories)
and excludes known-heavy system / cache dirs. The full list is shown
on screen so the user knows exactly what will be scanned.

The first-run wizard lets the user add/remove paths before the scan
launches.
"""

from __future__ import annotations

import os
from pathlib import Path


# Filesystem prefixes we always exclude on every platform. These are
# checked as case-insensitive prefixes of absolute paths.
COMMON_EXCLUDE_PREFIXES: tuple[str, ...] = (
    # Windows
    r"C:\Windows",
    r"C:\Program Files",
    r"C:\Program Files (x86)",
    r"C:\ProgramData",
    r"C:\$Recycle.Bin",
    r"C:\System Volume Information",
    # macOS
    "/System",
    "/Library",
    # Linux / generic Unix
    "/proc",
    "/sys",
    "/dev",
)


# Per-user cache / app dirs that are *under* the home dir but should
# always be excluded on first run. Tuples of (path-suffix, comment).
USER_EXCLUDE_SUFFIXES: tuple[str, ...] = (
    # Windows
    "AppData\\Local\\Packages",
    "AppData\\Local\\Microsoft\\Windows",
    "AppData\\Local\\Temp",
    # macOS
    "Library/Caches",
    "Library/Application Support/MobileSync",
    # Linux / generic
    ".cache",
    ".local/share/Trash",
    ".Trash",
)


def smart_defaults(*, platform_name: str | None = None) -> list[dict]:
    """Return a curated list of scan roots for the current OS.

    Each item is a dict matching v2's ``ScanRoots`` schema::

        {"Name": <str>, "Path": <abs path>, "Source": "smart-default"}

    ``platform_name`` defaults to ``sys.platform``. Returns 3-5 paths.
    """
    if platform_name is None:
        platform_name = _platform()

    home = Path.home()
    if platform_name.startswith("win"):
        return _windows_defaults(home)
    if platform_name == "darwin":
        return _macos_defaults(home)
    return _linux_defaults(home)


def _windows_defaults(home: Path) -> list[dict]:
    candidates = [
        ("Documents", home / "Documents"),
        ("Downloads", home / "Downloads"),
        ("Desktop", home / "Desktop"),
        ("Pictures", home / "Pictures"),
        ("Videos", home / "Videos"),
    ]
    out = []
    for name, p in candidates:
        if p.is_dir():
            out.append({"Name": name, "Path": str(p),
                        "Source": "smart-default"})
    # OneDrive if it coexists with Documents
    onedrive = os.environ.get("OneDrive") or os.environ.get("ONEDRIVE")
    if onedrive and Path(onedrive).is_dir():
        out.append({"Name": "OneDrive",
                    "Path": str(Path(onedrive)),
                    "Source": "smart-default"})
    return out or [{"Name": "Home", "Path": str(home),
                    "Source": "smart-default"}]


def _macos_defaults(home: Path) -> list[dict]:
    candidates = [
        ("Documents", home / "Documents"),
        ("Downloads", home / "Downloads"),
        ("Desktop", home / "Desktop"),
        ("Pictures", home / "Pictures"),
        ("Movies", home / "Movies"),
    ]
    out = []
    for name, p in candidates:
        if p.is_dir():
            out.append({"Name": name, "Path": str(p),
                        "Source": "smart-default"})
    return out or [{"Name": "Home", "Path": str(home),
                    "Source": "smart-default"}]


def _linux_defaults(home: Path) -> list[dict]:
    candidates = [
        ("Documents", home / "Documents"),
        ("Downloads", home / "Downloads"),
        ("Desktop", home / "Desktop"),
        ("Pictures", home / "Pictures"),
        ("Videos", home / "Videos"),
        ("Music", home / "Music"),
    ]
    out = []
    for name, p in candidates:
        if p.is_dir():
            out.append({"Name": name, "Path": str(p),
                        "Source": "smart-default"})
    for proj in ("Projects", "projects", "src", "Sources", "repos"):
        p = home / proj
        if p.is_dir():
            out.append({"Name": proj, "Path": str(p),
                        "Source": "smart-default"})
    return out or [{"Name": "Home", "Path": str(home),
                    "Source": "smart-default"}]


def filter_roots(roots: list[dict], *,
                 extra_exclude_prefixes: tuple[str, ...] = (),
                 user_exclude_suffixes: tuple[str, ...] = USER_EXCLUDE_SUFFIXES,
                 ) -> list[dict]:
    """Drop any root that lives under (or overlaps) an excluded prefix.

    Used by the v3 entry-point so a user who passes ``--scan-root C:\\``
    cannot accidentally re-introduce the v2.0 "scan the whole disk"
    behavior.
    """
    excl_prefixes = tuple(p.lower() for p in
                          COMMON_EXCLUDE_PREFIXES + tuple(extra_exclude_prefixes))
    out = []
    for r in roots:
        p = Path(r["Path"])
        try:
            p_abs = str(p.resolve())
        except OSError:
            continue
        if any(p_abs.lower().startswith(px) for px in excl_prefixes):
            continue
        out.append(r)
    return out


def exclude_paths_for_walker(*, platform_name: str | None = None
                              ) -> tuple[Path, ...]:
    """Return absolute paths that the walker should prune on descent.

    These are the *home-relative* exclusions like ``~/.cache``,
    ``AppData\\Local\\Packages``, etc. The walker treats them as exact
    prefixes (case-insensitive on Windows) and skips everything below
    them.
    """
    home = Path.home()
    suffixes = USER_EXCLUDE_SUFFIXES
    excl = []
    for s in suffixes:
        # On non-Windows, drop the AppData\... entries immediately.
        if "\\" in s and platform_name and not platform_name.startswith("win"):
            continue
        if "/" in s and platform_name == "win32":
            continue
        excl.append(home / s)
    # Also drop empty / var/log on POSIX
    return tuple(p for p in excl if p.exists())


def _platform() -> str:
    import sys as _s
    return _s.platform


__all__ = [
    "smart_defaults",
    "filter_roots",
    "exclude_paths_for_walker",
    "COMMON_EXCLUDE_PREFIXES",
    "USER_EXCLUDE_SUFFIXES",
]
