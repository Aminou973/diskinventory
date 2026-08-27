"""
migrate — read v1.x outputs and re-emit them in v2 layout.

The v1.x → v2.0 migration contract:

  * journal format unchanged   — restore works on both directions
  * environment.json unchanged — serve.py accepts both
  * CSV columns unchanged      — v1.x reports open in any reader
  * overrides.json unchanged   — override UI works on both
  * config/ DO change          — base + overlay pattern in v2

So `migrate` does only one thing: take a v1.x run dir and rewrite it under
a v2 layout (optionally adding the new content-aware columns when the input
inventory.csv didn't have them).

We intentionally keep this lightweight — heavy lifting lives in collect.py
+ classify_content.py on a fresh run.
"""

from __future__ import annotations

import csv
import json
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# v2.0 inventory columns; v1.1.0 readers skip unknown trailing columns.
V2_FIELDS = [
    "Path", "Parent", "Name", "Kind", "SizeBytes",
    "LastWriteUtc", "CreatedUtc", "Category", "Action",
    "SuggestedAction", "PlannedDestination", "PlanAction",
    "RuleMatched", "IsHidden", "IsSystem", "IsOneDrivePlaceholder",
    "Sha1", "Notes",
    # v2.0 additions (right of v1.1.0)
    "MIMEType", "DuplicateGroup", "ExifDate", "ClusterId",
]


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def detect_v1_run_dir(path: Path) -> dict:
    """Return a dict of files recognized in a v1.x run dir."""
    if not path.is_dir():
        raise FileNotFoundError(f"not a directory: {path}")
    files = {
        "environment": path / "environment.json",
        "inventory":   path / "inventory.csv",
        "plan":        path / "plan.json",
        "journal":     path / "actions-journal.jsonl",
        "dryrun":      path / "dryrun-journal.jsonl",
        "overrides":   path / "overrides.json",
    }
    present = {k: str(v) for k, v in files.items() if v.is_file()}
    return present


def migrate_v1_to_v2(src_dir: Path, dst_dir: Path) -> dict:
    """Copy v1.x outputs to dst_dir, optionally rewriting inventory.csv to v2."""
    src_dir = Path(src_dir)
    dst_dir = Path(dst_dir)
    dst_dir.mkdir(parents=True, exist_ok=True)
    present = detect_v1_run_dir(src_dir)
    summary: dict[str, Any] = {"copied": [], "rewritten": [], "skipped": []}

    for kind, src in present.items():
        dst = dst_dir / Path(src).name
        if kind == "inventory":
            _rewrite_inventory(Path(src), dst)
            summary["rewritten"].append(str(src))
            continue
        shutil.copy2(src, dst)
        summary["copied"].append(str(src))

    # Add a marker so future readers know this came from v1.x
    (dst_dir / "v2-migrated-from-v1.txt").write_text(
        f"Migrated from {src_dir} at {_now_utc()}\n", encoding="utf-8"
    )
    summary["dst"] = str(dst_dir)
    summary["src"] = str(src_dir)
    return summary


def _rewrite_inventory(src: Path, dst: Path) -> None:
    """Read v1.x inventory.csv, write v2 inventory.csv with the new columns
    populated to '' if missing in v1.x."""
    with open(src, "r", encoding="utf-8", newline="") as fh:
        reader = csv.DictReader(fh)
        v1_fields = reader.fieldnames or []
        rows = list(reader)
    with open(dst, "w", encoding="utf-8", newline="") as fh:
        writer = csv.writer(fh, quoting=csv.QUOTE_MINIMAL, lineterminator="\n")
        writer.writerow(V2_FIELDS)
        for row in rows:
            out = [row.get(f, "") for f in V2_FIELDS]
            writer.writerow(out)
