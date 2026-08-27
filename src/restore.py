"""
restore — reverse a journal produced by apply.py.

Compatible with v1.1.0 journals (same field order, same shape). For each
applied entry with a non-null src/dst, move the file back from dst to src
when the source is missing; otherwise leave a warning entry.

Used by `disk-inventory.py restore <journal>`.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _iter_journal(path: Path | str):
    p = Path(path) if not isinstance(path, Path) else path
    if not p.is_file():
        return
    with open(p, "r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            try:
                yield json.loads(line)
            except json.JSONDecodeError:
                continue


def restore(
    journal_path: Path | str,
    *,
    apply: bool = False,
    base_dir: Path | str | None = None,
    sha1_verify_max_mb: int = 0,
    progress: Callable[[int, int], None] | None = None,
) -> dict:
    """Restore from a journal. Returns {restored, skipped, errors, total}.

    When `apply` is False, this is a dry-run preview (prints what would happen).
    When `apply` is True, files are moved back from dst to src.

    `sha1_verify_max_mb` (0 = no limit): if both src and dst are accessible,
    verify SHA-1 before restoring. Useful to confirm we are restoring the
    right bytes.
    """
    summary = {"restored": 0, "skipped": 0, "errors": 0, "total": 0, "warnings": []}
    base = Path(base_dir) if base_dir else Path.cwd()
    journal_path = Path(journal_path) if not isinstance(journal_path, Path) else journal_path

    entries = list(_iter_journal(journal_path))
    summary["total"] = len(entries)
    n = len(entries)
    for i, entry in enumerate(entries):
        if not entry.get("applied"):
            summary["skipped"] += 1
            continue
        action = entry.get("action", "")
        src = entry.get("src")
        dst = entry.get("dst")
        if action == "delete":
            # Permanent deletes are NOT reversible by design; we just skip.
            summary["skipped"] += 1
            summary["warnings"].append(
                f"line {i+1}: delete is irreversible, skipping"
            )
            continue
        if not src or not dst:
            summary["skipped"] += 1
            summary["warnings"].append(
                f"line {i+1}: missing src/dst, skipping"
            )
            continue
        # Resolve paths: dst in journal is relative to base_dir (where the
        # journal was written from); src is absolute or as-recorded.
        src_path = Path(src)
        dst_path = Path(dst)
        if not dst_path.is_absolute():
            dst_path = base / dst_path
        if not dst_path.exists():
            summary["skipped"] += 1
            summary["warnings"].append(
                f"line {i+1}: dst missing ({dst_path}), skipping"
            )
            continue
        if src_path.exists():
            summary["skipped"] += 1
            summary["warnings"].append(
                f"line {i+1}: src already exists ({src_path}), skipping"
            )
            continue
        # Optional SHA-1 verify
        if sha1_verify_max_mb > 0:
            try:
                size = dst_path.stat().st_size
                if size <= sha1_verify_max_mb * 1024 * 1024:
                    expected = entry.get("sha1") or ""
                    if expected:
                        import hashlib
                        h = hashlib.sha1()
                        with open(dst_path, "rb") as f:
                            while True:
                                buf = f.read(1024 * 1024)
                                if not buf:
                                    break
                                h.update(buf)
                        got = h.hexdigest().upper()
                        if got != expected:
                            summary["errors"] += 1
                            summary["warnings"].append(
                                f"line {i+1}: SHA-1 mismatch on {dst_path}"
                            )
                            continue
            except OSError as e:
                summary["warnings"].append(
                    f"line {i+1}: SHA-1 verify skipped ({e})"
                )

        if not apply:
            summary["skipped"] += 1
            continue

        # Do the move
        try:
            src_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(dst_path), str(src_path))
            summary["restored"] += 1
        except OSError as e:
            summary["errors"] += 1
            summary["warnings"].append(
                f"line {i+1}: restore failed ({e})"
            )

        if progress and (i + 1) % 25 == 0:
            progress(i + 1, n)

    if progress:
        progress(n, n)
    return summary
