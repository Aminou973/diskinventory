"""
tests/test_migrate.py — verify migrate.v1_to_v2 reads a v1.1.0 run dir and
emits a v2 layout with the new inventory column header.
"""

from __future__ import annotations

import csv
import json
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))

from src import migrate  # noqa: E402


V1_HEADER = [
    "Path", "Parent", "Name", "Kind", "SizeBytes",
    "LastWriteUtc", "CreatedUtc", "Category", "Action",
    "SuggestedAction", "PlannedDestination", "PlanAction",
    "RuleMatched", "IsHidden", "IsSystem", "IsOneDrivePlaceholder",
    "Sha1", "Notes",
]

V2_HEADER = migrate.V2_FIELDS


def test_v1_to_v2_migration() -> int:
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        src = Path(tmp) / "v1run"
        dst = Path(tmp) / "v2out"
        src.mkdir()

        # Write a v1.1.0 environment.json
        (src / "environment.json").write_text(
            json.dumps({
                "RunId": "20260827-120000",
                "TimestampUtc": "2026-08-27T12:00:00Z",
                "Os": {"Caption": "Windows 11", "Version": "10.0", "Build": "26200"},
                "PowerShell": "5.1",
                "Locale": {"Ui": "en-US", "Culture": "en-US", "DisplayName": "English (US)"},
                "Admin": True,
                "Drives": [], "UserProfiles": [], "HeavyCaches": [],
                "ProjectRoots": [], "ScanRoots": [], "ExcludedRoots": []
            }),
            encoding="utf-8")
        # Write a v1.1.0 inventory.csv (18 columns)
        with open(src / "inventory.csv", "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(V1_HEADER)
            w.writerow([r"C:\Users\me\file.tmp", r"C:\Users\me", "file.tmp", "File", "100",
                        "2026-08-27T12:00:00Z", "2026-08-27T12:00:00Z", "Junk", "quarantine",
                        "", "_Quarantine/Junk/file.tmp", "", "Junk", "False", "False", "False",
                        "", ""])
        # Write a v1.1.0 journal
        with open(src / "actions-journal.jsonl", "w", encoding="utf-8") as fh:
            fh.write(json.dumps({
                "ts": "2026-08-27T12:00:00Z",
                "action": "quarantine",
                "src": r"C:\Users\me\file.tmp",
                "dst": "_Quarantine/Junk/file.tmp",
                "category": "Junk",
                "sizeBytes": 100,
                "sha1": None,
                "rule": "Junk",
                "reason": "category Junk",
                "reversible": True,
                "applied": True,
                "error": None,
            }) + "\n")

        # Run migration
        summary = migrate.migrate_v1_to_v2(src, dst)
        if "v1run" not in summary.get("src", ""):
            failures.append("summary missing src")
        if not dst.is_dir():
            failures.append("dst not created")
            print("FAIL:")
            for f in failures:
                print("  -", f)
            return 1

        # Verify v2 inventory.csv has the new columns
        with open(dst / "inventory.csv", "r", encoding="utf-8", newline="") as fh:
            r = csv.reader(fh)
            header = next(r)
        if header != V2_HEADER:
            failures.append(f"v2 header mismatch:\n  got {header}\n  want {V2_HEADER}")
        # Verify environment.json copied verbatim
        env_text = (dst / "environment.json").read_text(encoding="utf-8")
        env = json.loads(env_text)
        if env.get("PowerShell") != "5.1":
            failures.append(f"environment.json not copied verbatim: {env.get('PowerShell')}")
        # Verify journal copied
        if not (dst / "actions-journal.jsonl").is_file():
            failures.append("journal not copied")
        # Verify migration marker
        if not (dst / "v2-migrated-from-v1.txt").is_file():
            failures.append("migration marker missing")

    if failures:
        print("FAIL:")
        for f in failures:
            print("  -", f)
        return 1
    print("OK: v1.1.0 -> v2.0 migration preserves all artifacts and adds v2 columns")
    return 0


if __name__ == "__main__":
    sys.exit(test_v1_to_v2_migration())
