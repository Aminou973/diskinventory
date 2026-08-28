"""errorlog.py — collect skip-and-warn entries during a run.

v3 replaces the v2.0 behaviour of raising on filesystem errors during
walk / classify / apply. Instead, every problematic operation is
captured as a structured warning entry and the run continues.

The dashboard reads the same JSON array back via /api/warnings and
surfaces a tile count + last 20 messages.
"""

from __future__ import annotations

import json
import threading
import time
from pathlib import Path
from typing import Iterable


class ErrorLog:
    """Thread-safe in-memory collector with a flush-to-disk helper.

    Use ``log.stage(...)`` at the entry of each pipeline phase, then
    ``log.add(stage, ...error...)`` for individual problems. At end of
    run, call ``log.flush_jsonl(out_path)`` to write a JSON-Lines file
    whose schema is:

        {"ts": ISO8601, "stage": str, "phase": str,
         "path": str?, "error": str, "recoverable": bool}
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._entries: list[dict] = []

    def stage(self, name: str) -> None:
        """Begin a new phase (clears anything pending). Mostly a marker
        that lets the dashboard segment warnings by phase."""
        with self._lock:
            self._entries.append({
                "ts": _now(), "stage": name, "kind": "_stage_marker",
            })

    def add(self, stage: str, *, error: str, path: str = "",
            phase: str = "", recoverable: bool = True) -> dict:
        with self._lock:
            entry = {
                "ts": _now(),
                "stage": stage,
                "phase": phase,
                "path": path,
                "error": error,
                "recoverable": recoverable,
            }
            self._entries.append(entry)
            return entry

    def count(self) -> int:
        """Number of *real* warnings (excluding stage markers)."""
        with self._lock:
            return sum(1 for e in self._entries
                       if e.get("kind") != "_stage_marker")

    def tail(self, n: int = 20) -> list[dict]:
        with self._lock:
            non_markers = [e for e in self._entries
                           if e.get("kind") != "_stage_marker"]
            return list(non_markers[-n:])

    def all_entries(self) -> list[dict]:
        with self._lock:
            return list(self._entries)

    def flush_jsonl(self, out_path: Path) -> None:
        out_path = Path(out_path)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        with self._lock:
            entries = list(self._entries)
        with open(out_path, "w", encoding="utf-8") as fh:
            for e in entries:
                fh.write(json.dumps(e, ensure_ascii=False) + "\n")


def _now() -> str:
    # ISO 8601, second precision, UTC
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


__all__ = ["ErrorLog"]
