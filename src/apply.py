"""
apply — execute the plan and write the journal.

The journal is JSON-Lines, one entry per attempted action, in this exact
field order (the contract v1.1.0 → v2.0 must preserve):
    ts, action, src, dst, category, sizeBytes, sha1, rule, reason,
    reversible, applied, error

We intentionally write fields in the same order so a bytewise diff between
v1.1.0 and v2.0 journals on the same plan is empty for equivalent entries.
"""

from __future__ import annotations

import json
import os
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

# Fixed field order — DO NOT REORDER.
JOURNAL_FIELDS = [
    "ts", "action", "src", "dst", "category", "sizeBytes",
    "sha1", "rule", "reason", "reversible", "applied", "error",
]


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _mk_entry(item: dict, applied: bool, error: str | None) -> dict:
    """Create a journal entry preserving the v1.1.0 → v2.0 field order."""
    return {
        "ts": _now_utc(),
        "action": item.get("action", ""),
        "src": item.get("path"),
        "dst": item.get("destination") or None,
        "category": item.get("category", ""),
        "sizeBytes": item.get("sizeBytes"),
        "sha1": item.get("sha1") or None,
        "rule": item.get("rule") or item.get("category"),
        "reason": item.get("reason", ""),
        "reversible": bool(item.get("reversible", True)),
        "applied": bool(applied),
        "error": error,
    }


def _apply_one(item: dict, *, base_dir: Path) -> tuple[bool, str | None]:
    """Move/copy a single item to its destination. Returns (ok, error)."""
    action = item.get("action", "")
    src = item.get("path", "")
    dst = item.get("destination", "")
    if action in ("keep",):
        return True, None
    if not src:
        return False, "missing src"
    if action in ("delete",):
        try:
            p = Path(src)
            if p.is_dir() and not p.is_symlink():
                shutil.rmtree(p)
            else:
                p.unlink()
            return True, None
        except OSError as e:
            return False, f"delete failed: {e}"
    if not dst:
        return False, "missing destination"
    try:
        dstdir = (base_dir / dst).parent
        dstdir.mkdir(parents=True, exist_ok=True)
        p = Path(src)
        if p.is_dir() and not p.is_symlink():
            shutil.move(str(p), str(base_dir / dst))
        else:
            shutil.move(str(p), str(base_dir / dst))
        return True, None
    except OSError as e:
        return False, f"move failed: {e}"


def apply_plan(
    plan: dict,
    *,
    journal_path: Path | str,
    base_dir: Path | str,
    what_if: bool = False,
    progress: Callable[[int, int], None] | None = None,
    pause_flag: threading.Event | None = None,
    broadcast: Callable[[dict], None] | None = None,
    error_log: Any | None = None,
) -> dict:
    """Walk the plan, mutate files (unless what_if), append to the journal.

    Returns a summary { applied: int, skipped: int, errors: int }.

    Optional v3 hooks:

    * ``pause_flag`` — a threading.Event; apply checks ``pause_flag.wait()``
      between items so the dashboard "Pause" button works during a live
      apply run. The pause is non-blocking when ``pause_flag`` is None.
    * ``broadcast(entry)`` — called with each journal entry as soon as it
      is written, so the dashboard's SSE stream shows it live.
    * ``error_log`` — an ``src.errorlog.ErrorLog`` instance; per-item
      errors are appended with the ``apply`` stage tag.
    """
    import threading
    journal_path = Path(journal_path) if not isinstance(journal_path, Path) else journal_path
    base = Path(base_dir) if not isinstance(base_dir, Path) else base_dir
    journal_path.parent.mkdir(parents=True, exist_ok=True)
    journal_path.touch(exist_ok=True)

    summary = {"applied": 0, "skipped": 0, "errors": 0}
    items = plan.get("Items", [])
    n = len(items)

    # Open in append + read so we never lose entries on crash.
    with open(journal_path, "a", encoding="utf-8") as fh:
        for i, item in enumerate(items):
            # Pause gate (non-blocking when None or set).
            if pause_flag is not None:
                try:
                    pause_flag.wait(timeout=0.05)
                except Exception:
                    pass
            action = item.get("action", "")
            error = None
            ok = False
            entry = None
            try:
                if what_if:
                    entry = _mk_entry({**item, "rule": item.get("category")}, False, None)
                    entry["applied"] = False
                    summary["skipped"] += 1
                else:
                    ok, error = _apply_one(item, base_dir=base)
                    entry = _mk_entry({**item, "rule": item.get("category")}, ok, error)
                    if ok:
                        entry["applied"] = True
                        summary["applied"] += 1
                    else:
                        entry["applied"] = False
                        summary["errors"] += 1
            except Exception as e:  # never let apply crash mid-run
                error = f"unexpected: {e}"
                summary["errors"] += 1
                if error_log is not None:
                    error_log.add("apply", error=error,
                                  path=str(item.get("path", "")),
                                  phase="apply_one")
                entry = _mk_entry(item, False, error)
                entry["applied"] = False
            fh.write(json.dumps(entry, ensure_ascii=False) + "\n")
            fh.flush()
            if broadcast is not None:
                try:
                    broadcast(entry)
                except Exception:
                    pass
            if progress and (i + 1) % 25 == 0:
                progress(i + 1, n)
    if progress:
        progress(n, n)
    return summary
