"""
restore_from_journal — reverses a journal produced by apply_actions.py
(or Apply-Actions.ps1 on Windows).

Mirrors src/Restore-FromJournal.ps1:
  - Reads a JSON Lines journal.
  - Skips blank lines, applied=false, missing src/dst, or actions not in
    {quarantine, archive, group, move, delete}.
  - For each remaining entry, ensures src's parent exists, then either moves
    or prints a "WOULD restore" line.
  - SHA-1 verifies files up to sha1_verify_max_bytes (default 1 GB);
    mismatches are warnings, not errors.

Cross-platform: only depends on the JSON Lines contract.
"""

import hashlib
import json
import os
import shutil
import sys


_RESTORE_ACTIONS = {"quarantine", "archive", "group", "move", "delete"}
_SHA1_CHUNK = 1024 * 1024


def _compute_sha1(path: str) -> str | None:
    try:
        h = hashlib.sha1()
        with open(path, "rb") as f:
            while True:
                chunk = f.read(_SHA1_CHUNK)
                if not chunk:
                    break
                h.update(chunk)
        return h.hexdigest().upper()
    except OSError:
        return None


def _is_dir(path: str) -> bool:
    try:
        return os.path.isdir(path)
    except OSError:
        return False


def restore_from_journal(journal_path: str,
                         sha1_verify_max_bytes: int = 1024 * 1024 * 1024,
                         sha1_verify_always: bool = False,
                         apply: bool = False,
                         what_if_preview: bool = True) -> dict:
    """Restore (or preview-restore) every entry in the journal.

    Returns {Restored, Skipped, Errored, Verified, Mismatched}.
    """
    restored = 0
    skipped = 0
    errored = 0
    verified = 0
    mismatched = 0

    with open(journal_path, "r", encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                e = json.loads(line)
            except json.JSONDecodeError:
                skipped += 1
                continue

            if not e.get("applied", False):
                skipped += 1
                continue

            action = e.get("action")
            src = e.get("src")
            dst = e.get("dst")
            if action not in _RESTORE_ACTIONS or not src or not dst:
                skipped += 1
                continue

            # Destination must still exist
            if not os.path.exists(dst):
                print(f"  WARN: destination missing, skipping: {dst}", flush=True)
                skipped += 1
                continue

            # SHA-1 verify (files only, size within cap or always)
            sha1_expected = e.get("sha1")
            size = e.get("sizeBytes") or 0
            if sha1_expected and not _is_dir(dst) and (sha1_verify_always or size <= sha1_verify_max_bytes):
                actual = _compute_sha1(dst)
                if actual and actual.upper() == sha1_expected.upper():
                    verified += 1
                elif actual:
                    mismatched += 1
                    print(f"  WARN: SHA-1 mismatch on {dst}: expected {sha1_expected}, got {actual}",
                          flush=True)

            # Ensure src parent exists
            src_parent = os.path.dirname(src)
            if src_parent:
                os.makedirs(src_parent, exist_ok=True)

            if apply:
                try:
                    shutil.move(dst, src)
                    restored += 1
                except OSError as ex:
                    errored += 1
                    print(f"  ERROR restoring {dst} -> {src}: {ex}", flush=True)
            else:
                print(f"  WOULD restore: {dst} -> {src}", flush=True)
                restored += 1  # count what we'd restore

    return {
        "Restored": restored,
        "Skipped": skipped,
        "Errored": errored,
        "Verified": verified,
        "Mismatched": mismatched,
    }