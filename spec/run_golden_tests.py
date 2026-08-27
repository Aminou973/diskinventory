"""
spec/run_golden_tests.py — run the engine on the synthetic-tree fixture and
diff the outputs against spec/fixtures/golden-outputs/.

If --update is passed, the goldens are (re)written instead of being diffed.

Run from the repo root:
    python spec/run_golden_tests.py            # diff
    python spec/run_golden_tests.py --update   # regenerate goldens
"""

from __future__ import annotations

import csv
import hashlib
import json
import shutil
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
FIXTURE = REPO / "spec" / "fixtures" / "synthetic-tree"
GOLDEN = REPO / "spec" / "fixtures" / "golden-outputs"
TMP = REPO / "spec" / "fixtures" / "_run"


def _sha(path: Path) -> str:
    if not path.is_file():
        return ""
    h = hashlib.sha1()
    h.update(path.read_bytes())
    return h.hexdigest()


def _diff(name: str, generated: Path, golden: Path) -> tuple[bool, str]:
    if not generated.is_file():
        return False, f"{name}: generated missing"
    if not golden.is_file():
        return False, f"{name}: golden missing"
    a = _sha(generated)
    b = _sha(golden)
    if a != b:
        return False, f"{name}: SHA-1 differs ({a[:8]} vs {b[:8]})"
    return True, f"{name}: OK"


def run_one(tmp: Path) -> dict:
    """Run the v2 engine against FIXTURE, write outputs into tmp."""
    if tmp.exists():
        shutil.rmtree(tmp)
    tmp.mkdir(parents=True, exist_ok=True)

    sys.path.insert(0, str(REPO))
    sys.path.insert(0, str(REPO / "src"))
    from src import collect, classify, classify_content, export, plan
    from src.env_detect import _now_utc, _run_id

    env = {
        "RunId": _run_id(),
        "TimestampUtc": _now_utc(),
        "Os": {"Caption": "Test", "Version": "0", "Build": "0"},
        "PowerShell": "Test",
        "Locale": {"Ui": "en", "Culture": "en", "DisplayName": "en"},
        "Admin": False,
        "Drives": [],
        "UserProfiles": [],
        "HeavyCaches": [],
        "ProjectRoots": [],
        "ScanRoots": [{"Name": "root", "Path": str(FIXTURE)}],
        "ExcludedRoots": [],
    }
    rows = collect.collect(env)
    classify.classify_rows(rows, tool_dir=REPO)
    for r in rows:
        if r.get("Kind") == "File":
            r["MIMEType"] = classify_content.sniff_mime(__import__("pathlib").Path(r["Path"]))
    plan_doc = plan.build_plan(rows, run_id=env["RunId"])
    export.write_csv(rows, tmp / "inventory.csv")
    export.write_markdown(env, rows, tmp / "inventory.md")
    export.write_html(env, rows, tmp / "inventory.html", warnings=["golden test"])
    (tmp / "environment.json").write_text(json.dumps(env, indent=2), encoding="utf-8")
    (tmp / "plan.json").write_text(json.dumps(plan_doc, indent=2), encoding="utf-8")
    return {"rows": rows, "env": env, "plan": plan_doc}


def main() -> int:
    if "--update" in sys.argv:
        # Regenerate goldens
        if GOLDEN.exists():
            shutil.rmtree(GOLDEN)
        GOLDEN.mkdir(parents=True)
        run_one(GOLDEN)
        # Also keep TMP clean
        if TMP.exists():
            shutil.rmtree(TMP)
        print(f"updated goldens in {GOLDEN}")
        return 0

    if not FIXTURE.is_dir():
        print(f"fixture missing: {FIXTURE}; run build_synthetic_tree.py first")
        return 2
    if not GOLDEN.is_dir():
        print(f"goldens missing: {GOLDEN}; run with --update first")
        return 2

    generated = run_one(TMP)
    failures = []
    # Strict diff for deterministic files only; embed-timestamp files are
    # checked for shape, not bytes.
    for name in ("inventory.csv",):
        ok, msg = _diff(name, TMP / name, GOLDEN / name)
        print(msg)
        if not ok:
            failures.append(msg)
    # Shape check on timestamp-bearing files
    for name in ("environment.json", "plan.json", "inventory.md", "inventory.html"):
        gp = GOLDEN / name
        tp = TMP / name
        if not tp.is_file():
            print(f"{name}: generated missing")
            failures.append(name)
            continue
        if not gp.is_file():
            print(f"{name}: golden missing")
            failures.append(name)
            continue
        if tp.stat().st_size != gp.stat().st_size:
            # Size sanity (within 5% — goldens shift as features are added)
            ratio = abs(tp.stat().st_size - gp.stat().st_size) / gp.stat().st_size
            if ratio > 0.05:
                print(f"{name}: size drift {ratio:.0%} — re-run with --update")
                failures.append(name)
                continue
        print(f"{name}: size-only OK ({tp.stat().st_size:,}B)")
    if failures:
        print(f"\n{len(failures)} mismatch(es). Re-run with --update to refresh goldens.")
        return 1
    print("\nAll goldens match.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
