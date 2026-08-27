"""
classify_content — content-aware signals layered on top of the rule classifier.

Three signals (all opt-in except MIME sniffing, which is on by default):

  1. MIME sniffing (zero deps): first-byte magic for the most common formats.
     Populates `MIMEType` on every row.

  2. SHA-1 dedup (--compute-hashes): groups rows by hash. Populates
     `DuplicateGroup` with a stable group id for any file whose hash appears
     more than once.

  3. EXIF date grouping (--classify-exif): for image MIME types, reads
     DateTimeOriginal via Pillow (optional). Populates `ExifDate`.

  4. Name-similarity clustering (--classify-cluster): Levenshtein on
     basenames within a directory + size bucket, threshold 0.85.
     Populates `ClusterId`.

These signals never override `Category` — they only populate additional
fields that the report + dashboard + planner can use.
"""

from __future__ import annotations

import hashlib
import os
import struct
from collections import defaultdict
from pathlib import Path
from typing import Iterable

# --- MIME sniffing --------------------------------------------------------

# (offset, magic-bytes, mime-type) — first match wins.
_MAGIC = [
    (0, b"%PDF-",         "application/pdf"),
    (0, b"\x89PNG\r\n\x1a\n", "image/png"),
    (0, b"\xff\xd8\xff",      "image/jpeg"),
    (0, b"GIF87a",            "image/gif"),
    (0, b"GIF89a",            "image/gif"),
    (0, b"BM",                "image/bmp"),
    (0, b"RIFF",              "audio/wav"),  # RIFF....WAVE follows
    (0, b"ID3",               "audio/mpeg"),
    (0, b"\xff\xfb",          "audio/mpeg"),
    (0, b"OggS",              "audio/ogg"),
    (0, b"fLaC",              "audio/flac"),
    (0, b"PK\x03\x04",        "application/zip"),
    (0, b"PK\x05\x06",        "application/zip"),  # empty zip
    (0, b"7z\xbc\xaf\x27\x1c", "application/x-7z-compressed"),
    (0, b"Rar!\x1a\x07",      "application/vnd.rar"),
    (0, b"\x1f\x8b",          "application/gzip"),
    (0, b"BZh",               "application/x-bzip2"),
    (0, b"\xfd7zXZ\x00",      "application/x-xz"),
    (0, b"\x7fELF",           "application/x-elf"),
    (0, b"MZ",                "application/x-msdownload"),
    (0, b"\xca\xfe\xba\xbe",  "application/java-vm"),
    (4, b"ftyp",              "video/mp4"),
    (0, b"\x1aE\xdf\xa3",     "video/x-matroska"),
    (0, b"<?xml",             "application/xml"),
    (0, b"<!DOCTYPE html",    "text/html"),
    (0, b"<html",             "text/html"),
    (0, b"{\"",               "application/json"),
    (0, b"SQLite format 3",   "application/x-sqlite3"),
]


def sniff_mime(path: Path, *, read_bytes: int = 16) -> str:
    """Return a MIME type string by reading the first `read_bytes` of `path`.

    Returns "application/octet-stream" on failure or unknown magic.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(read_bytes)
    except OSError:
        return "application/octet-stream"
    if not head:
        return "application/octet-stream"
    for off, magic, mime in _MAGIC:
        end = off + len(magic)
        if head[off:end] == magic:
            # RIFF/WAV refinement
            if mime == "audio/wav" and len(head) >= 12 and head[8:12] != b"WAVE":
                continue
            # JSON refinement (don't false-positive on a "{ " inside binary)
            if mime == "application/json":
                # Crude check: must be mostly printable
                if not all(c < 0x80 or c in (0x09, 0x0a, 0x0d) for c in head[:8]):
                    continue
            return mime
    return "application/octet-stream"


# --- SHA-1 dedup ----------------------------------------------------------

def annotate_duplicate_groups(rows: list[dict]) -> int:
    """Populate DuplicateGroup on rows that share a SHA-1 with another row.

    Returns the number of duplicate groups identified (groups with > 1 file).
    """
    by_hash: dict[str, list[dict]] = defaultdict(list)
    for r in rows:
        h = r.get("Sha1", "")
        if h:
            by_hash[h].append(r)
    group_count = 0
    for i, (h, group) in enumerate(by_hash.items(), start=1):
        if len(group) <= 1:
            continue
        gid = f"G{i:04d}"
        for r in group:
            r["DuplicateGroup"] = gid
        group_count += 1
    return group_count


# --- EXIF date grouping (Pillow opt-in) -----------------------------------

def annotate_exif_dates(rows: list[dict], *, max_bytes: int = 25 * 1024 * 1024) -> int:
    """Populate ExifDate for image rows. Returns count of rows annotated.

    Pillow is optional. If not installed, returns 0 silently.
    """
    try:
        from PIL import Image  # type: ignore
        from PIL.ExifTags import TAGS  # type: ignore
    except ImportError:
        return 0
    EXIF_TAG_DATETIME = None
    EXIF_TAG_DATETIME_ORIGINAL = None
    EXIF_TAG_DATETIME_DIGITIZED = None
    for tag_id, name in TAGS.items():
        if name == "DateTime":
            EXIF_TAG_DATETIME = tag_id
        elif name == "DateTimeOriginal":
            EXIF_TAG_DATETIME_ORIGINAL = tag_id
        elif name == "DateTimeDigitized":
            EXIF_TAG_DATETIME_DIGITIZED = tag_id

    annotated = 0
    for r in rows:
        if not (r.get("MIMEType") or "").startswith("image/"):
            continue
        try:
            size = int(r.get("SizeBytes") or 0)
        except (ValueError, TypeError):
            size = 0
        if size and size > max_bytes:
            continue
        path = r.get("Path", "")
        if not path:
            continue
        try:
            with Image.open(path) as im:
                exif = im.getexif() or {}
        except (OSError, ValueError):
            continue
        dt = None
        for tag in (EXIF_TAG_DATETIME_ORIGINAL, EXIF_TAG_DATETIME_DIGITIZED, EXIF_TAG_DATETIME):
            if tag and tag in exif:
                dt = exif[tag]
                break
        if dt:
            # EXIF DateTime is "YYYY:MM:DD HH:MM:SS" — normalize to ISO date
            try:
                d, t = dt.split(" ", 1)
                d = d.replace(":", "-", 2)
                r["ExifDate"] = f"{d}T{t}"
                annotated += 1
            except (ValueError, AttributeError):
                pass
    return annotated


# --- Name-similarity clustering -------------------------------------------

def _levenshtein(a: str, b: str) -> int:
    """Classic Wagner-Fischer Levenshtein. O(len(a)*len(b))."""
    if a == b:
        return 0
    if not a:
        return len(b)
    if not b:
        return len(a)
    prev = list(range(len(b) + 1))
    cur = [0] * (len(b) + 1)
    for i, ca in enumerate(a, start=1):
        cur[0] = i
        for j, cb in enumerate(b, start=1):
            cost = 0 if ca == cb else 1
            cur[j] = min(
                prev[j] + 1,      # deletion
                cur[j - 1] + 1,   # insertion
                prev[j - 1] + cost,  # substitution
            )
        prev, cur = cur, prev
    return prev[len(b)]


def _similar(a: str, b: str) -> float:
    """Return 1 - normalized distance. 1.0 = identical, 0.0 = nothing in common."""
    if not a or not b:
        return 0.0
    d = _levenshtein(a, b)
    longest = max(len(a), len(b))
    return 1.0 - (d / longest)


def annotate_clusters(rows: list[dict], *, threshold: float = 0.85, sample_per_parent: int = 5000) -> int:
    """Group rows by (parent, size-bucket) and find similar basenames.

    Size bucket = log2 rounded to nearest 16KiB. This keeps "report.pdf" and
    "report_final.pdf" together without grouping unrelated same-basename files
    in different directories.
    """
    import math
    grouped: dict[tuple, list[dict]] = defaultdict(list)
    for r in rows:
        if r.get("Kind") != "File":
            continue
        try:
            size = int(r.get("SizeBytes") or 0)
        except (ValueError, TypeError):
            size = 0
        bucket = int(math.log2(size + 1) // 14) if size else 0
        key = (r.get("Parent", ""), bucket)
        if len(grouped[key]) >= sample_per_parent:
            continue
        grouped[key].append(r)

    cluster_count = 0
    next_id = 1
    for key, group in grouped.items():
        n = len(group)
        if n < 2:
            continue
        # Union-find
        parent_uf = list(range(n))
        def find(x: int) -> int:
            while parent_uf[x] != x:
                parent_uf[x] = parent_uf[parent_uf[x]]
                x = parent_uf[x]
            return x
        def union(x: int, y: int) -> None:
            rx, ry = find(x), find(y)
            if rx != ry:
                parent_uf[rx] = ry
        # Compare every pair (capped by sample_per_parent)
        for i in range(n):
            for j in range(i + 1, n):
                a = group[i].get("Name", "")
                b = group[j].get("Name", "")
                if _similar(a.lower(), b.lower()) >= threshold:
                    union(i, j)
        roots: dict[int, list[int]] = defaultdict(list)
        for i in range(n):
            roots[find(i)].append(i)
        for root, members in roots.items():
            if len(members) <= 1:
                continue
            cid = f"C{next_id:04d}"
            next_id += 1
            cluster_count += 1
            for m in members:
                group[m]["ClusterId"] = cid
    return cluster_count
