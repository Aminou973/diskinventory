"""
tests/test_journal_compat.py — backward-compat: a v1.1.0-format journal is
round-tripped through the v2 restore module.

This test writes a journal in the EXACT v1.1.0 shape (no MIMEType, no
DuplicateGroup), then verifies v2 restore reads it without errors.

Run from the repo root:
    python tests/test_journal_compat.py
"""

from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))

from src import restore, plan, apply  # noqa: E402


JOURNAL_FIELDS = [
    "ts", "action", "src", "dst", "category", "sizeBytes",
    "sha1", "rule", "reason", "reversible", "applied", "error",
]


def _v1_journal_entry(action: str, src: str, dst: str | None) -> dict:
    """Mimic the v1.1.0 PowerShell writer's exact output."""
    return {
        "ts": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "action": action,
        "src": src,
        "dst": dst,
        "category": "Junk",
        "sizeBytes": 100,
        "sha1": None,
        "rule": "Junk",
        "reason": "category Junk",
        "reversible": True,
        "applied": True,
        "error": None,
    }


def test_v1_journal_restore() -> int:
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # Lay down two source files and a journal that says "moved"
        a_src = tmp / "src_a.tmp"
        b_src = tmp / "src_b.tmp"
        a_dst_dir = tmp / "_Quarantine" / "Junk"
        a_dst = a_dst_dir / "src_a.tmp"
        b_dst = a_dst_dir / "src_b.tmp"
        a_src.write_text("A")
        b_src.write_text("B")
        a_dst_dir.mkdir(parents=True, exist_ok=True)
        a_dst.write_text("A")
        b_dst.write_text("B")
        # Source files are now 'gone' (simulating post-apply state)
        a_src.unlink()
        b_src.unlink()

        journal = tmp / "actions-journal.jsonl"
        with open(journal, "w", encoding="utf-8") as fh:
            for e in (_v1_journal_entry("quarantine", str(a_src), str(a_dst)),
                      _v1_journal_entry("quarantine", str(b_src), str(b_dst))):
                fh.write(json.dumps(e) + "\n")

        # Restore (dry-run first)
        s = restore.restore(journal, apply=False, base_dir=tmp)
        if s["total"] != 2 or s["errors"] != 0:
            failures.append(f"dry-run: unexpected summary {s}")

        # Now restore for real
        s = restore.restore(journal, apply=True, base_dir=tmp)
        if s["restored"] != 2 or s["errors"] != 0:
            failures.append(f"apply: unexpected summary {s}")
        if not a_src.is_file() or not b_src.is_file():
            failures.append("restore did not move files back")
        if a_dst.is_file() or b_dst.is_file():
            failures.append("restore left destination files in place")

    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("OK: v1.1.0 journal round-trips through v2 restore")
    return 0


def test_field_order_preserved() -> int:
    """v2 apply writes the journal with the exact v1.1.0 field order."""
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        src = tmp / "x.tmp"
        src.write_text("hi")
        dst = tmp / "_Quarantine" / "Junk" / "x.tmp"
        item = {
            "path": str(src),
            "action": "quarantine",
            "reason": "category Junk",
            "destination": "_Quarantine/Junk/x.tmp",
            "reversible": True,
            "category": "Junk",
            "sizeBytes": 2,
            "sha1": "",
        }
        plan_doc = {"Items": [item]}
        apply.apply_plan(plan_doc, journal_path=tmp / "journal.jsonl", base_dir=tmp)
        with open(tmp / "journal.jsonl", encoding="utf-8") as fh:
            line = fh.readline().strip()
        entry = json.loads(line)
        # Re-emit and check key order
        keys = list(entry.keys())
        vs_keys = list(_v1_journal_entry("quarantine", str(src), str(dst)).keys())
        if keys != vs_keys:
            failures.append(f"field order drift: {keys} vs {vs_keys}")
    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("OK: v2 journal field order matches v1.1.0")
    return 0


def test_plan_safety_keep() -> int:
    """SAFETY_KEEP categories never get a non-keep action without explicit override."""
    failures = []
    rows = [
        {"Path": "/usr/bin/ls", "Category": "App", "Action": "group", "RuleMatched": "App"},
        {"Path": "/etc/passwd", "Category": "System", "Action": "group", "RuleMatched": "System"},
        {"Path": "/home/me/proj", "Category": "Project", "Action": "group", "RuleMatched": "Project"},
        {"Path": "/tmp/garbage.tmp", "Category": "Junk", "Action": "quarantine", "RuleMatched": "Junk"},
    ]
    plan_doc = plan.build_plan(rows, run_id="r1")
    actions = {it["path"]: it["action"] for it in plan_doc["Items"]}
    if actions["/usr/bin/ls"] != "keep":
        failures.append("App default should be keep")
    if actions["/etc/passwd"] != "keep":
        failures.append("System default should be keep")
    if actions["/home/me/proj"] != "keep":
        failures.append("Project default should be keep")
    if actions["/tmp/garbage.tmp"] != "quarantine":
        failures.append("Junk should quarantine")
    # Now override App explicitly
    overrides = [{"path": "/usr/bin/ls", "action": "quarantine"}]
    plan_doc2 = plan.build_plan(rows, run_id="r1", overrides=overrides)
    actions2 = {it["path"]: it["action"] for it in plan_doc2["Items"]}
    if actions2["/usr/bin/ls"] != "quarantine":
        failures.append("explicit override on App should be honored")
    if failures:
        print("FAIL:")
        for f in failures:
            print(f"  - {f}")
        return 1
    print("OK: SAFETY_KEEP pins categories unless overridden")
    return 0


if __name__ == "__main__":
    results = []
    results.append(test_v1_journal_restore())
    results.append(test_field_order_preserved())
    results.append(test_plan_safety_keep())
    sys.exit(0 if all(r == 0 for r in results) else 1)
