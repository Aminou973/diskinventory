"""
collect_inventory — POSIX filesystem walk for Linux/macOS.

Mirrors src/Collect-Inventory.ps1 on Windows but uses pathlib/os.walk/os.scandir:
  - Excludes by substring match against excludeGlobs, exact-name match against
    excludeFileNames, and case-insensitive suffix match against excludeExtensions.
  - Detects hidden files by filename prefix '.' (POSIX convention). System files
    don't have a separate bit; we treat .files starting with . as both hidden
    AND system (closest POSIX analog; user can edit this if needed).
  - No OneDrive placeholder detection (no FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS
    analog on Linux; the Linux OneDrive sync client serves files normally).
  - SHA-1 via hashlib, gated by compute_hashes.
  - Size cache: dict keyed by full path, value {size, mtime, length, sha1};
    invalidated when mtime or length changes.
"""

import hashlib
import os
from datetime import datetime, timezone
from pathlib import Path


SHA1_CHUNK = 1024 * 1024  # 1 MB


def _is_hidden(path: Path) -> bool:
    """A POSIX file is hidden if any of its name parts starts with '.'."""
    return any(part.startswith(".") for part in path.parts if part)


def _compute_sha1(path: Path) -> str | None:
    """Compute SHA-1 of a file's content. Returns None on error."""
    try:
        h = hashlib.sha1()
        with path.open("rb") as f:
            while True:
                chunk = f.read(SHA1_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest().upper()
    except OSError:
        return None


def _is_excluded(full_path: str, exclude_globs: list, exclude_file_names: set,
                 exclude_extensions_lower: set) -> bool:
    """Substring-match the path against exclude globs/names/extensions."""
    for g in exclude_globs:
        if g in full_path:
            return True
    name = os.path.basename(full_path)
    if name in exclude_file_names:
        return True
    # case-insensitive suffix match
    name_lower = name.lower()
    for ext in exclude_extensions_lower:
        if name_lower.endswith(ext):
            return True
    return False


def _make_item(path: Path, root_label: str | None = None) -> dict:
    """Build an item dict from a Path."""
    try:
        stat = path.stat(follow_symlinks=False)
    except OSError:
        stat = None

    if stat is None:
        # Could not stat; emit a stub so the user sees the path at least
        return {
            "Path": str(path),
            "Parent": str(path.parent),
            "Name": path.name,
            "Kind": "Unknown",
            "SizeBytes": 0,
            "LastWriteUtc": "",
            "CreatedUtc": "",
            "IsHidden": _is_hidden(path),
            "IsSystem": False,
            "IsOneDrivePlaceholder": False,
            "Sha1": None,
            "MarkerFiles": "",
        }

    is_dir = stat.S_ISDIR(stat.st_mode) if hasattr(stat, "S_ISDIR") else False
    # pathlib's is_dir() doesn't follow symlinks by default
    if not is_dir:
        try:
            is_dir = path.is_dir()
        except OSError:
            is_dir = False

    return {
        "Path": str(path),
        "Parent": str(path.parent),
        "Name": path.name,
        "Kind": "Dir" if is_dir else "File",
        "SizeBytes": 0 if is_dir else stat.st_size,
        "LastWriteUtc": datetime.fromtimestamp(stat.st_mtime, tz=timezone.utc)
                        .isoformat(timespec="seconds").replace("+00:00", "Z"),
        "CreatedUtc": datetime.fromtimestamp(stat.st_ctime, tz=timezone.utc)
                      .isoformat(timespec="seconds").replace("+00:00", "Z"),
        "IsHidden": _is_hidden(path),
        "IsSystem": False,  # No POSIX analog; can be flipped by rules if needed
        "IsOneDrivePlaceholder": False,  # No POSIX analog
        "Sha1": None,
        "MarkerFiles": "",
    }


def get_marker_files(dir_path, names: list | None = None) -> list:
    """Return up to 50 child file names in `dir_path` matching any pattern in `names`.

    `dir_path` may be a str or Path. If `names` is None, use the canonical
    cross-platform project marker list (mirrors Get-DiskInventoryMarkerFiles
    defaults in the Windows tool, with Linux-friendly additions).
    """
    dir_path = Path(dir_path) if not isinstance(dir_path, Path) else dir_path
    if names is None:
        names = [
            ".git", ".svn", ".hg", ".bzr",
            "package.json", "pyproject.toml", "requirements.txt", "Pipfile",
            "poetry.lock", "Cargo.toml", "go.mod", "go.sum",
            "pom.xml", "build.gradle", "build.gradle.kts",
            "Makefile", "CMakeLists.txt", "meson.build",
            "configure", "configure.ac", "Makefile.am",
            "*.csproj", "*.fsproj", "*.sln",
            "docker-compose.yml", "docker-compose.yaml", "Dockerfile",
            "snapcraft.yaml", "*.desktop", "*.spec",
            "*.AppImage", "*.deb", "*.rpm",
            "manifest.json",  # Snap/Flatpak
            "flake.nix", "shell.nix",
        ]

    if not dir_path.is_dir():
        return []

    out = []
    try:
        with os.scandir(str(dir_path)) as it:
            for entry in it:
                if entry.name in names or any(_glob_match(entry.name, n) for n in names):
                    out.append(entry.name)
                if len(out) >= 50:
                    break
    except OSError:
        pass
    return out


def _glob_match(name: str, pattern: str) -> bool:
    """Very small glob: only `*` is supported."""
    if "*" not in pattern:
        return name == pattern
    parts = pattern.split("*")
    pos = 0
    for i, part in enumerate(parts):
        if not part:
            continue
        idx = name.find(part, pos)
        if idx < 0:
            return False
        if i == 0 and idx != 0:
            return False
        pos = idx + len(part)
    if parts[-1]:
        return name.endswith(parts[-1])
    return True


def collect_inventory(scan_roots, config, compute_hashes: bool = False,
                      size_cache: dict | None = None,
                      max_items: int = 0,
                      progress_sink=None) -> dict:
    """Walk the filesystem from each scan root, producing an item list.

    Returns dict with keys: Items, Warnings, Stats, SizeCache.
    """
    exclude_globs = list(config.get("excludeGlobs", []))
    exclude_file_names = set(config.get("excludeFileNames", []))
    exclude_extensions = set(e.lower() for e in config.get("excludeExtensions", []))

    items = []
    warnings = []
    stats = {
        "FilesScanned": 0,
        "DirsScanned": 0,
        "CacheHits": 0,
        "CacheMisses": 0,
        "HashesComputed": 0,
        "Errors": 0,
        "StartUtc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "EndUtc": "",
        "TotalItems": 0,
    }

    if size_cache is None:
        size_cache = {}

    count = 0
    cap_reached = False

    for root in scan_roots:
        root_path = Path(root) if not isinstance(root, str) else Path(root)
        if not root_path.exists():
            warnings.append({"Path": str(root_path), "Reason": "scan root does not exist"})
            continue

        # Emit the root itself first
        try:
            items.append(_make_item(root_path))
            count += 1
            stats["DirsScanned"] += 1
        except Exception as e:
            warnings.append({"Path": str(root_path), "Reason": f"could not stat: {e}"})

        try:
            walker = os.walk(str(root_path), followlinks=False)
            for dirpath, dirnames, filenames in walker:
                # Filter in-place to avoid descending into excluded dirs
                kept_dirs = []
                for d in dirnames:
                    full = str(Path(dirpath) / d)
                    if _is_excluded(full, exclude_globs, exclude_file_names, exclude_extensions):
                        continue
                    kept_dirs.append(d)
                dirnames[:] = kept_dirs

                for name in filenames:
                    full = str(Path(dirpath) / name)
                    if _is_excluded(full, exclude_globs, exclude_file_names, exclude_extensions):
                        continue
                    p = Path(full)
                    try:
                        item = _make_item(p)
                        # Size + cache lookup
                        try:
                            stat = p.stat(follow_symlinks=False)
                            item["SizeBytes"] = stat.st_size
                            mtime = stat.st_mtime
                            length = stat.st_size
                            cache_key = full
                            entry = size_cache.get(cache_key)
                            if (entry is not None
                                    and entry.get("mtime") == mtime
                                    and entry.get("length") == length):
                                # Cache hit
                                stats["CacheHits"] += 1
                                item["SizeBytes"] = entry.get("size", stat.st_size)
                                if entry.get("sha1"):
                                    item["Sha1"] = entry["sha1"]
                            else:
                                stats["CacheMisses"] += 1
                                if compute_hashes:
                                    sha1 = _compute_sha1(p)
                                    item["Sha1"] = sha1
                                    if sha1:
                                        stats["HashesComputed"] += 1
                                size_cache[cache_key] = {
                                    "size": stat.st_size,
                                    "mtime": mtime,
                                    "length": length,
                                    "sha1": item["Sha1"],
                                }
                        except OSError:
                            pass
                        items.append(item)
                        count += 1
                        stats["FilesScanned"] += 1
                    except Exception as e:
                        warnings.append({"Path": full, "Reason": f"error reading: {e}"})
                        stats["Errors"] += 1

                    if progress_sink and count % 500 == 0:
                        try:
                            progress_sink(count, full)
                        except Exception:
                            pass

                    if max_items and count >= max_items:
                        cap_reached = True
                        break

                # Emit each directory after its files (so it appears after
                # its children in the listing — matches PS Get-ChildItem -Recurse).
                # But we still emit it; it's cheap.
                for d in kept_dirs:
                    full = str(Path(dirpath) / d)
                    items.append(_make_item(Path(full)))
                    count += 1
                    stats["DirsScanned"] += 1
                    if max_items and count >= max_items:
                        cap_reached = True
                        break

                if cap_reached:
                    break
            if cap_reached:
                break
        except Exception as e:
            warnings.append({"Path": str(root_path), "Reason": f"walk error: {e}"})

    stats["EndUtc"] = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    stats["TotalItems"] = len(items)

    return {
        "Items": items,
        "Warnings": warnings,
        "Stats": stats,
        "SizeCache": size_cache,
    }