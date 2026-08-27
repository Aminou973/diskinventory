"""
tests/test_serve.py — minimal smoke for the http.server dashboard.

Starts serve.build_server on an ephemeral port, hits /, /api/run, /api/stats,
/api/env, /api/items, then shuts down.

Run from the repo root:
    python tests/test_serve.py
"""

from __future__ import annotations

import csv
import http.client
import json
import os
import socket
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
sys.path.insert(0, str(REPO / "src"))

from src.serve import build_server, _State  # noqa: E402


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _hit(port: int, path: str, method: str = "GET", body: bytes = b"") -> tuple[int, bytes]:
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
    try:
        conn.request(method, path, body=body, headers={"Content-Type": "application/json"})
        r = conn.getresponse()
        return r.status, r.read()
    finally:
        conn.close()


def test_serve_smoke() -> int:
    failures = []
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        # Lay down a minimal run dir
        (tmp / "environment.json").write_text(
            json.dumps({"RunId": "test-001", "TimestampUtc": "2026-01-01T00:00:00Z",
                        "Os": {"Caption": "Test"}, "Admin": False,
                        "Drives": [], "UserProfiles": [], "HeavyCaches": [],
                        "ProjectRoots": [], "ScanRoots": [], "ExcludedRoots": []}),
            encoding="utf-8")
        with open(tmp / "inventory.csv", "w", encoding="utf-8", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["Path", "Parent", "Name", "Kind", "SizeBytes",
                        "LastWriteUtc", "CreatedUtc", "Category", "Action",
                        "SuggestedAction", "PlannedDestination", "PlanAction",
                        "RuleMatched", "IsHidden", "IsSystem", "IsOneDrivePlaceholder",
                        "Sha1", "Notes", "MIMEType", "DuplicateGroup", "ExifDate", "ClusterId"])
            w.writerow([str(tmp / "a.txt"), str(tmp), "a.txt", "File", "5",
                        "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z", "Document", "",
                        "", "", "", "Document", "False", "False", "False", "", "",
                        "text/plain", "", "", ""])

        state = _State(tmp)
        state.env = json.loads((tmp / "environment.json").read_text(encoding="utf-8"))
        port = _free_port()
        srv = build_server(state, host="127.0.0.1", port=port)
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        time.sleep(0.5)
        try:
            for path in ("/", "/api/run", "/api/env", "/api/stats", "/api/items?limit=5"):
                code, body = _hit(port, path)
                if code != 200:
                    failures.append(f"{path}: status {code}")
                else:
                    print(f"OK {path}: 200 ({len(body)} bytes)")
            # POST overrides
            code, body = _hit(port, "/api/overrides", "POST",
                              json.dumps({"items": [{"path": "/x", "action": "keep"}]}).encode())
            if code != 200:
                failures.append(f"POST /api/overrides: status {code}")
            else:
                payload = json.loads(body)
                if not payload.get("ok"):
                    failures.append("POST /api/overrides: payload not ok")
                else:
                    print(f"OK POST /api/overrides: {payload}")
        finally:
            srv.shutdown()
            srv.server_close()
    if failures:
        print("FAIL:")
        for f in failures:
            print("  -", f)
        return 1
    print("\nAll serve smoke checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(test_serve_smoke())
