"""
collect — walk scan roots and build the inventory rows.

Mirrors v1.1.0's Collect-Inventory.ps1 / collect_inventory.py behaviour but
adds hooks for content-aware classification (MIME, optional SHA-1 dedup,
optional EXIF, optional name clustering).

Output: a list of item dicts in the v1.1.0 field order, plus optional
content-aware fields populated to '' when the feature is off.
"""

from __future__ import annotations

import hashlib
import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable, Iterator

# Stable column order (v1.1.0 + v2.0 additions).
INVENTORY_FIELDS = [
    "Path", "Parent", "Name", "Kind", "SizeBytes",
    "LastWriteUtc", "CreatedUtc", "Category", "Action",
    "SuggestedAction", "PlannedDestination", "PlanAction",
    "RuleMatched", "IsHidden", "IsSystem", "IsOneDrivePlaceholder",
    "Sha1", "Notes",
    # v2.0 additions (right of v1.1.0 set; v1.1.0 readers skip unknown columns)
    "MIMEType", "DuplicateGroup", "ExifDate", "ClusterId",
]


def _now_utc_iso(stamp: float) -> str:
    return datetime.fromtimestamp(stamp, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _kind_of(p: Path) -> str:
    try:
        if p.is_symlink():
            return "Symlink"
        if p.is_dir():
            return "Directory"
        if p.is_file():
            return "File"
    except OSError:
        pass
    return "Other"


def _is_hidden_posix(p: Path) -> bool:
    name = p.name
    if name.startswith("."):
        return True
    try:
        st = p.stat()
    except OSError:
        return False
    # On macOS the hidden flag is the UF_HIDDEN bit in st_flags; on Linux we
    # approximate with the dot-prefix check.
    flags = getattr(st, "st_flags", 0)
    if flags and (flags & 0x8000):
        return True
    return False


def _is_hidden_windows(p: Path) -> bool:
    name = p.name
    if name.startswith(".") or name.startswith("~"):
        return True
    try:
        attrs = os.stat(p, follow_symlinks=False).st_file_attributes
    except (OSError, AttributeError):
        return False
    return bool(attrs & 0x2)  # FILE_ATTRIBUTE_HIDDEN


def _is_hidden(p: Path) -> bool:
    if os.name == "nt":
        return _is_hidden_windows(p)
    return _is_hidden_posix(p)


def _is_system_posix(p: Path) -> bool:
    """Treat anything owned by root and not in a user's home as 'system'."""
    try:
        st = p.stat()
    except OSError:
        return False
    if st.st_uid != 0:
        return False
    home = str(Path.home())
    return not str(p).startswith(home)


def _is_one_drive_placeholder(p: Path) -> bool:
    """Windows-only: detect OneDrive cloud placeholders (reparse points)."""
    if os.name != "nt":
        return False
    try:
        attrs = os.stat(p, follow_symlinks=False).st_file_attributes
    except (OSError, AttributeError):
        return False
    return bool(attrs & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT


# ---------------------------------------------------------------------------

def walk(
    roots: Iterable[Path],
    *,
    exclude: Iterable[Path] = (),
    max_depth: int = 12,
    follow_symlinks: bool = False,
) -> Iterator[Path]:
    """Yield every path under each root, honouring exclude prefixes."""
    excl = {str(Path(e).resolve()) for e in exclude}
    for root in roots:
        rp = Path(root)
        if not rp.exists():
            continue
        root_depth = len(rp.parts)
        try:
            for dirpath, dirnames, filenames in os.walk(str(rp), followlinks=follow_symlinks):
                # Depth check
                pdir = Path(dirpath)
                depth = len(pdir.parts) - root_depth
                if depth > max_depth:
                    dirnames[:] = []
                    continue
                # Excluded roots: prune
                if any(str(pdir) == e or str(pdir).startswith(e + os.sep) for e in excl):
                    dirnames[:] = []
                    continue
                # First, the directory itself
                yield pdir
                # Then its files
                for fn in filenames:
                    yield pdir / fn
        except (PermissionError, OSError):
            continue


def make_row(p: Path, *, category: str = "", rule_matched: str = "") -> dict:
    """Build a single inventory row in INVENTORY_FIELDS order."""
    try:
        st = p.stat()
        size = int(st.st_size)
        mtime = _now_utc_iso(st.st_mtime)
        ctime = _now_utc_iso(st.st_ctime)
    except OSError:
        size = 0
        mtime = ""
        ctime = ""
    return {
        "Path": str(p),
        "Parent": str(p.parent),
        "Name": p.name,
        "Kind": _kind_of(p),
        "SizeBytes": size,
        "LastWriteUtc": mtime,
        "CreatedUtc": ctime,
        "Category": category,
        "Action": "",          # filled by classifier
        "SuggestedAction": "", # filled by classifier
        "PlannedDestination": "",
        "PlanAction": "",
        "RuleMatched": rule_matched,
        "IsHidden": _is_hidden(p),
        "IsSystem": _is_system_posix(p) if os.name != "nt" else False,
        "IsOneDrivePlaceholder": _is_one_drive_placeholder(p),
        "Sha1": "",
        "Notes": "",
        # v2.0 additions (default-empty; populated later by classify_content)
        "MIMEType": "",
        "DuplicateGroup": "",
        "ExifDate": "",
        "ClusterId": "",
    }


# ---------------------------------------------------------------------------

def collect(
    env: dict,
    *,
    compute_hashes: bool = False,
    hash_max_bytes: int = 0,
    progress: Callable[[int, int], None] | None = None,
) -> list[dict]:
    """Walk the scan roots, build rows, optionally SHA-1 small files.

    `hash_max_bytes` (0 = no limit) skips hashing of files larger than the
    threshold. With `compute_hashes=False`, no SHA-1 is computed at all.
    """
    roots = [Path(r["Path"]) for r in env.get("ScanRoots", []) if r.get("Path")]
    excl = [Path(r["Path"]) for r in env.get("ExcludedRoots", []) if r.get("Path")]
    rows: list[dict] = []
    for i, p in enumerate(walk(roots, exclude=excl)):
        if not p.exists():
            continue
        row = make_row(p)
        rows.append(row)
        if progress and i and i % 500 == 0:
            progress(i, 0)

    if compute_hashes:
        _hash_rows(rows, max_bytes=hash_max_bytes, progress=progress)

    if progress:
        progress(len(rows), len(rows))
    return rows


def _hash_rows(
    rows: list[dict],
    *,
    max_bytes: int = 0,
    progress: Callable[[int, int], None] | None = None,
) -> None:
    h = hashlib.sha1()
    n = len(rows)
    for i, row in enumerate(rows):
        if row["Kind"] != "File":
            row["Sha1"] = ""
            continue
        try:
            size = int(row.get("SizeBytes") or 0)
        except (TypeError, ValueError):
            size = 0
        if max_bytes and size and size > max_bytes:
            row["Sha1"] = ""
            continue
        try:
            with open(row["Path"], "rb") as f:
                while True:
                    buf = f.read(1024 * 1024)
                    if not buf:
                        break
                    h.update(buf)
            row["Sha1"] = h.hexdigest().upper()
        except OSError:
            row["Sha1"] = ""
        h = hashlib.sha1()
        if progress and i and i % 100 == 0:
            progress(i, n)


# ---------------------------------------------------------------------------

def rows_to_csv(rows: list[dict]) -> str:
    """Render rows in INVENTORY_FIELDS order, RFC 4180-ish quoting."""
    import csv, io
    buf = io.StringIO()
    w = csv.writer(buf, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
    w.writerow(INVENTORY_FIELDS)
    for r in rows:
        w.writerow([r.get(c, "") for c in INVENTORY_FIELDS])
    return buf.getvalue()
