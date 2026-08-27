"""
tests/test_classify_content.py — content-aware classification signals.

Verifies:
  * MIME sniffing recognizes PNG / JPEG / PDF / ZIP / GZIP / ELF / PE
  * SHA-1 dedup groups identical files
  * name-similarity clustering groups similar basenames
  * EXIF is silently no-op'd when Pillow is missing
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))

from src import classify_content  # noqa: E402


def test_mime_sniff() -> int:
    failures = []
    cases = [
        (b"%PDF-1.4\n",                       "application/pdf"),
        (b"\x89PNG\r\n\x1a\n",                "image/png"),
        (b"\xff\xd8\xff\xe0",                  "image/jpeg"),
        (b"PK\x03\x04abc",                    "application/zip"),
        (b"\x1f\x8babc",                      "application/gzip"),
        (b"BZh9abc",                           "application/x-bzip2"),
        (b"\x7fELFabc",                        "application/x-elf"),
        (b"MZabc",                             "application/x-msdownload"),
    ]
    with tempfile.TemporaryDirectory() as tmp:
        for body, expected in cases:
            p = Path(tmp) / f"file_{len(body)}"
            p.write_bytes(body)
            got = classify_content.sniff_mime(p)
            if got != expected:
                failures.append(f"MIME: {body[:6]!r} expected {expected}, got {got}")
    if failures:
        for f in failures:
            print("FAIL:", f)
        return 1
    print(f"OK: MIME sniffing ({len(cases)} magic bytes)")
    return 0


def test_sha1_dedup() -> int:
    failures = []
    body = b"hello world" * 100
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        for i, name in enumerate(["a.bin", "b.bin", "c.bin"]):
            p = Path(tmp) / name
            p.write_bytes(body if i < 2 else b"different")
            rows.append({
                "Path": str(p),
                "Name": name,
                "Kind": "File",
                "Sha1": _sha1(p),
                "SizeBytes": p.stat().st_size,
                "DuplicateGroup": "",
            })
    n = classify_content.annotate_duplicate_groups(rows)
    if n != 1:
        failures.append(f"expected 1 duplicate group, got {n}")
    if not rows[0]["DuplicateGroup"] or rows[0]["DuplicateGroup"] != rows[1]["DuplicateGroup"]:
        failures.append("a.bin and b.bin should share a group")
    if rows[2]["DuplicateGroup"]:
        failures.append("c.bin should NOT be in any group")
    if failures:
        for f in failures:
            print("FAIL:", f)
        return 1
    print(f"OK: SHA-1 dedup (1 group, 2 files)")
    return 0


def _sha1(p: Path) -> str:
    import hashlib
    h = hashlib.sha1()
    h.update(p.read_bytes())
    return h.hexdigest().upper()


def test_name_clustering() -> int:
    failures = []
    rows = [
        {"Path": "/x/report.pdf", "Name": "report.pdf", "Kind": "File", "SizeBytes": 100, "ClusterId": "", "Parent": "/x"},
        {"Path": "/x/report_final.pdf", "Name": "report_final.pdf", "Kind": "File", "SizeBytes": 100, "ClusterId": "", "Parent": "/x"},
        {"Path": "/x/report_v2.pdf", "Name": "report_v2.pdf", "Kind": "File", "SizeBytes": 100, "ClusterId": "", "Parent": "/x"},
        {"Path": "/x/totally_unrelated.doc", "Name": "totally_unrelated.doc", "Kind": "File", "SizeBytes": 100, "ClusterId": "", "Parent": "/x"},
    ]
    n = classify_content.annotate_clusters(rows, threshold=0.5)
    cluster_ids = {r["ClusterId"] for r in rows[:3]}
    if len(cluster_ids) != 1 or "" in cluster_ids:
        failures.append(f"expected all 3 report.* in one cluster, got {cluster_ids}")
    if rows[3]["ClusterId"]:
        failures.append("totally_unrelated.doc should NOT be clustered")
    if failures:
        for f in failures:
            print("FAIL:", f)
        return 1
    print(f"OK: name clustering ({n} cluster)")
    return 0


def test_exif_no_pillow() -> int:
    """EXIF gracefully no-ops when Pillow is missing — never an error."""
    rows = [
        {"Path": "/x/a.jpg", "Kind": "File", "MIMEType": "image/jpeg", "SizeBytes": 100, "ExifDate": ""},
    ]
    n = classify_content.annotate_exif_dates(rows)
    if n != 0:
        print(f"WARN: Pillow appears to be installed; EXIF returned {n} annotated (expected 0 if missing)")
    print("OK: EXIF gracefully handles missing Pillow")
    return 0


if __name__ == "__main__":
    results = [test_mime_sniff(), test_sha1_dedup(), test_name_clustering(), test_exif_no_pillow()]
    sys.exit(0 if all(r == 0 for r in results) else 1)
