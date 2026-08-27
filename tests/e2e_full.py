"""
tests/e2e_full.py — drive the full v2.0 pipeline end-to-end against the
synthetic-tree fixture.

What this exercises:
  - env_detect
  - collect.collect
  - classify.classify_rows + classify_content.{sniff_mime,annotate_duplicate_groups,annotate_clusters}
  - plan.build_plan
  - apply.apply_plan (dryrun + apply)
  - export.{write_csv,write_markdown,write_html}
  - restore.restore (round-trip)

Run from the repo root:
    python tests/e2e_full.py
"""

from __future__ import annotations

import csv
import json
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))

from src import (  # noqa: E402
    collect, classify, classify_content, plan,
    apply, restore, export,
)


def _write(path: Path, body):
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(body, str):
        path.write_text(body, encoding="utf-8")
    else:
        path.write_bytes(body)


def build_minimal_tree(root: Path) -> None:
    if root.exists():
        shutil.rmtree(root)
    root.mkdir(parents=True)
    _write(root / "Projects" / "demo" / "package.json", '{"name":"demo"}')
    _write(root / "Projects" / "demo" / "README.md", "# demo")
    _write(root / "Projects" / "demo" / "leftover.tmp", "tmp")
    _write(root / "Documents" / "report.pdf", b"%PDF-1.4\n" + b"\x00" * 200)
    _write(root / "Documents" / "report-final.pdf", b"%PDF-1.4\n" + b"\x00" * 200)
    _write(root / "Pictures" / "shot.png", bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000d49444154789c6200010000000500010d0a2db40000000049454e44ae"
        "426082"))
    _write(root / "Pictures" / "shot-2.png", bytes.fromhex(
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000d49444154789c6200010000000500010d0a2db40000000049454e44ae"
        "426082"))
    _write(root / "Downloads" / "old.zip", b"PK\x03\x04" + b"\x00" * 100)
    _write(root / "Downloads" / "scratch.bak", "x")


def main() -> int:
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        tree = tmp / "tree"
        out = tmp / "out"
        out.mkdir(parents=True, exist_ok=True)
        build_minimal_tree(tree)

        env = {
            "RunId": "e2e-001",
            "TimestampUtc": "2026-08-27T12:00:00Z",
            "Os": {"Caption": "Test", "Version": "0", "Build": "0"},
            "PowerShell": "Test",
            "Locale": {"Ui": "en", "Culture": "en", "DisplayName": "en"},
            "Admin": False,
            "Drives": [], "UserProfiles": [], "HeavyCaches": [],
            "ProjectRoots": [],
            "ScanRoots": [{"Name": "root", "Path": str(tree)}],
            "ExcludedRoots": [],
        }

        rows = collect.collect(env, compute_hashes=True)
        if len(rows) < 10:
            failures.append(f"too few rows: {len(rows)}")
        classify.classify_rows(rows, tool_dir=REPO)
        for r in rows:
            if r.get("Kind") == "File":
                r["MIMEType"] = classify_content.sniff_mime(Path(r["Path"]))
        dups = classify_content.annotate_duplicate_groups(rows)
        clusters = classify_content.annotate_clusters(rows, threshold=0.5)
        plan_doc = plan.build_plan(rows, run_id=env["RunId"])
        (out / "environment.json").write_text(json.dumps(env, indent=2), encoding="utf-8")
        (out / "plan.json").write_text(json.dumps(plan_doc, indent=2), encoding="utf-8")
        summary = apply.apply_plan(plan_doc, journal_path=out / "actions-journal.jsonl",
                                   base_dir=out, what_if=False)
        export.write_csv(rows, out / "inventory.csv")
        export.write_markdown(env, rows, out / "inventory.md")
        export.write_html(env, rows, out / "inventory.html")

        # Validate outputs exist and are well-formed
        for name in ("environment.json", "plan.json", "actions-journal.jsonl",
                     "inventory.csv", "inventory.html", "inventory.md"):
            p = out / name
            if not p.is_file():
                failures.append(f"missing output: {name}")
                continue
            sz = p.stat().st_size
            if sz == 0:
                failures.append(f"empty output: {name}")
            else:
                print(f"OK {name}: {sz:,} bytes")

        # Round-trip restore
        r = restore.restore(out / "actions-journal.jsonl", apply=True, base_dir=out)
        if r["errors"] != 0:
            failures.append(f"restore errors: {r}")

        # Check that PDFs were detected
        pdf_rows = [row for row in rows if row.get("Category") == "Document"]
        if len(pdf_rows) < 3:
            failures.append(f"too few Document rows: {len(pdf_rows)}")

        # Check dups
        if dups < 1:
            failures.append(f"no duplicate groups found (dups={dups})")

        # Check clusters
        if clusters < 1:
            failures.append(f"no name clusters found (clusters={clusters})")

    if failures:
        print("\nFAIL:")
        for f in failures:
            print("  -", f)
        return 1
    print("\nE2E OK")
    return 0


if __name__ == "__main__":
    sys.exit(main())
