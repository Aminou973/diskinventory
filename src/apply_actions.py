"""
apply_actions — the ONLY mutating module.

Mirrors src/Apply-Actions.ps1:
  - Per plan entry, writes a JSON Lines journal entry, then either skips,
    errors, or moves the file/directory.
  - When what_if=True, never touches the disk; logs applied=false for every entry.
  - When prompt=True, asks per action with Y/N/A (accept-all accelerator).
  - Prompts only when interactive (sys.stdin.isatty()); silent otherwise.

Journal entry shape (fixed field order; cross-platform contract):
  ts, action, src, dst, category, sizeBytes, sha1, rule, reason,
  reversible, applied, error

This must match the JSON Lines produced by Apply-Actions.ps1 exactly so a
journal can be restored cross-platform by Restore-FromJournal.ps1 / restore_from_journal.py.
"""

import json
import os
import shutil
import sys
from datetime import datetime, timezone


JOURNAL_FIELDS = [
    "ts", "action", "src", "dst", "category", "sizeBytes",
    "sha1", "rule", "reason", "reversible", "applied", "error",
]


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _is_interactive() -> bool:
    try:
        return sys.stdin.isatty()
    except Exception:
        return False


def _prompt_y_n_a(question: str) -> str:
    """Ask Y/N/A (accept-all). Returns 'y', 'n', or 'a'. Empty/EOF → 'n'."""
    if not _is_interactive():
        return "y"  # non-interactive: silently accept (gate is at the entry point)
    try:
        ans = input(f"{question} [Y/n/A(ccept all)] ").strip().lower()
    except EOFError:
        return "n"
    return ans[:1] if ans else "n"


def _make_entry(plan_row: dict, ts: str) -> dict:
    """Build an ordered journal entry dict with all fields present."""
    entry = {f: None for f in JOURNAL_FIELDS}
    entry["ts"] = ts
    entry["action"] = plan_row.get("Action")
    entry["src"] = plan_row.get("Path")
    entry["dst"] = plan_row.get("Destination")
    entry["category"] = plan_row.get("Category")
    entry["sizeBytes"] = plan_row.get("SizeBytes")
    entry["sha1"] = plan_row.get("Sha1")
    entry["rule"] = plan_row.get("RuleMatched")
    entry["reason"] = plan_row.get("Reason")
    entry["reversible"] = bool(plan_row.get("Reversible"))
    entry["applied"] = False
    entry["error"] = None
    return entry


def apply_actions(plan, journal_path: str, prompt: bool = False,
                  what_if: bool = False) -> dict:
    """Apply every entry in the plan, writing the journal as we go.

    Returns {Applied, Skipped, Errored, JournalPath}.
    """
    applied = 0
    skipped = 0
    errored = 0
    accept_all = False

    # Ensure journal dir exists
    jp = os.path.dirname(journal_path)
    if jp:
        os.makedirs(jp, exist_ok=True)

    with open(journal_path, "a", encoding="utf-8") as jf:
        for row in plan:
            ts = _now_iso()
            entry = _make_entry(row, ts)

            action = row.get("Action")

            # 1. keep or empty action → skip
            if not action or action == "keep":
                entry["reason"] = (entry["reason"] or "") + "; kept"
                jf.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
                skipped += 1
                continue

            # 2. No destination → error
            dst = row.get("Destination")
            if not dst:
                entry["reason"] = (entry["reason"] or "") + "; no destination computed"
                entry["error"] = "no destination"
                jf.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
                errored += 1
                continue

            # 3. Source missing → error
            src = row.get("Path")
            if not src or not os.path.exists(src):
                entry["reason"] = (entry["reason"] or "") + "; source not found"
                entry["error"] = "source not found"
                jf.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
                errored += 1
                continue

            # 4. Prompt gate (only in interactive + non-WhatIf mode)
            if prompt and not accept_all and not what_if:
                ans = _prompt_y_n_a(f"  Apply {action} on {src} -> {dst}?")
                if ans == "a":
                    accept_all = True
                elif ans != "y":
                    entry["reason"] = (entry["reason"] or "") + "; user skipped"
                    entry["error"] = "user skipped"
                    jf.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")
                    skipped += 1
                    continue

            # 5. Move (or no-op for WhatIf)
            try:
                os.makedirs(os.path.dirname(dst), exist_ok=True)
                if what_if:
                    # No disk change; applied stays False
                    pass
                else:
                    # shutil.move handles cross-filesystem moves
                    shutil.move(src, dst)
                    entry["applied"] = True
                    applied += 1
            except Exception as e:
                entry["error"] = str(e)
                entry["applied"] = False
                errored += 1

            jf.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")

    return {
        "Applied": applied,
        "Skipped": skipped,
        "Errored": errored,
        "JournalPath": journal_path,
    }