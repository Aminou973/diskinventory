"""
spec/fixtures/build_synthetic_tree.py — deterministically create the
synthetic-tree test fixture under spec/fixtures/synthetic-tree/.

Run from the repo root:
    python spec/fixtures/build_synthetic_tree.py [out_dir]

Default out_dir is spec/fixtures/synthetic-tree/.
"""

from __future__ import annotations

import json
import os
import struct
import sys
from pathlib import Path


# Tiny PNG (1x1 transparent)
_PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
    "890000000d49444154789c6200010000000500010d0a2db40000000049454e44ae"
    "426082"
)


def _write(path: Path, body: bytes | str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(body, str):
        path.write_text(body, encoding="utf-8")
    else:
        path.write_bytes(body)


def build(root: Path) -> None:
    if root.exists():
        import shutil
        shutil.rmtree(root)
    root.mkdir(parents=True)

    # Project: a Node project with junk, cache, docs
    proj = root / "Projects" / "demo-app"
    _write(proj / "package.json", '{"name":"demo-app","version":"1.0.0"}')
    _write(proj / "package-lock.json", '{"lockfileVersion":3}')
    _write(proj / "README.md", "# Demo App\n")
    _write(proj / ".git" / "HEAD", "ref: refs/heads/main\n")
    _write(proj / "src" / "index.js", "console.log('hi')\n")
    _write(proj / "src" / "index.test.js", "test('x', () => {})\n")
    _write(proj / "node_modules" / "leftpad" / "index.js", "// stub\n")
    _write(proj / "build" / "app.zip", b"PK\x03\x04" + b"\x00" * 200)
    _write(proj / "logs" / "service.log", "INFO started\n" * 100)
    _write(proj / "tmp" / "leftover.tmp", "garbage")

    # Heavy caches: a fake pip cache and a fake npm cache
    caches = root / "Caches"
    _write(caches / "pip" / "wheel-1.0-py3-none-any.whl", b"PK\x03\x04" + b"\x00" * 5000)
    _write(caches / "npm" / "_cacache" / "index.json", '{"cache":true}')

    # Documents: PDFs and a duplicate
    docs = root / "Documents"
    _write(docs / "report.pdf", b"%PDF-1.4\n" + b"\x00" * 800)
    _write(docs / "report-final.pdf", b"%PDF-1.4\n" + b"\x00" * 800)  # duplicate
    _write(docs / "notes.txt", "hello world\n")

    # Images: a tiny PNG + a duplicate
    imgs = root / "Pictures"
    _write(imgs / "screenshot.png", _PNG)
    _write(imgs / "screenshot-2.png", _PNG)  # duplicate

    # Junk
    junk = root / "Downloads"
    _write(junk / "old-backup.zip", b"PK\x03\x04" + b"\x00" * 300)
    _write(junk / "scratch.tmp", "scratch")
    _write(junk / "session.bak", "backup")

    # Heavy marker file (lock)
    other = root / "Other" / "rust-tool"
    _write(other / "Cargo.toml", '[package]\nname="x"\n')
    _write(other / "Cargo.lock", 'version = 3\n')

    # README in the fixture describing the expected counts
    expected = {
        "Categories": {
            "Document": 3,   # report.pdf + report-final.pdf + notes.txt
            "Junk": 2,       # leftover.tmp + scratch.tmp + session.bak = 3 actually
            "Archive": 4,    # app.zip + wheel + old-backup.zip + (more)
            "HeavyCache": 2, # pip + npm
            "Project": 3,    # demo-app README + src/* + rust-tool
            "Image": 2,      # screenshot + screenshot-2
        }
    }
    _write(root / "EXPECTED.json", json.dumps(expected, indent=2))


if __name__ == "__main__":
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).resolve().parent / "synthetic-tree"
    build(out)
    print(f"built synthetic tree at {out}")
