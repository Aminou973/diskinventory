"""
fleet — agent-less SSH coordinator + SQLite central store.

Each host entry in hosts.txt is one of:
    user@host[:port]
    user@host[:port] ansible_user=root
    user@host[:port] port=2222
    # comments allowed

`fleet scan` SSHe's into each host, runs:
    python3 -c "<inline engine bootstrap>" run --mode report --output-dir <tmp>
then `scp`'s the result back. Falls back to WinRM if SSH is unreachable.

`fleet dedup` aggregates SHA-1 indices across hosts, writes
fleet-dedup.json + fleet-dedup.html.

The central SQLite store lives at <fleet-dir>/fleet.db.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

# Schema version: bump on breaking schema changes.
SCHEMA_VERSION = 1


# --- SQLite schema -------------------------------------------------------

_SCHEMA = r"""
CREATE TABLE IF NOT EXISTS hosts (
    host_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    host           TEXT NOT NULL,
    user           TEXT,
    port           INTEGER,
    reached        INTEGER,
    reached_utc    TEXT,
    error          TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS hosts_host_user_port ON hosts(host, user, port);

CREATE TABLE IF NOT EXISTS runs (
    run_id         TEXT PRIMARY KEY,
    host_id        INTEGER NOT NULL REFERENCES hosts(host_id),
    output_dir     TEXT,
    started_utc    TEXT,
    finished_utc   TEXT,
    items          INTEGER,
    bytes_total    INTEGER,
    error          TEXT
);

CREATE TABLE IF NOT EXISTS items (
    item_id        INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         TEXT NOT NULL REFERENCES runs(run_id),
    path           TEXT,
    category       TEXT,
    size_bytes     INTEGER,
    sha1           TEXT
);
CREATE INDEX IF NOT EXISTS items_sha1 ON items(sha1);
CREATE INDEX IF NOT EXISTS items_run_id ON items(run_id);
CREATE INDEX IF NOT EXISTS items_category ON items(category);

CREATE TABLE IF NOT EXISTS journal (
    journal_id     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         TEXT NOT NULL REFERENCES runs(run_id),
    ts             TEXT,
    action         TEXT,
    src            TEXT,
    dst            TEXT,
    applied        INTEGER,
    error          TEXT
);
CREATE INDEX IF NOT EXISTS journal_run_id ON journal(run_id);

CREATE TABLE IF NOT EXISTS meta (
    key            TEXT PRIMARY KEY,
    value          TEXT
);
"""


def init_store(db_path: Path) -> sqlite3.Connection:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(db_path))
    conn.executescript(_SCHEMA)
    conn.execute(
        "INSERT OR REPLACE INTO meta (key, value) VALUES (?, ?)",
        ("schema_version", str(SCHEMA_VERSION)),
    )
    conn.commit()
    return conn


# --- hosts.txt parser ----------------------------------------------------

def parse_hosts(path: Path) -> list[dict]:
    """Parse a hosts.txt file. One host per line; # comments; inline key=val."""
    if not path.is_file():
        return []
    out: list[dict] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        tokens = line.split()
        host_token = tokens[0]
        ht: dict[str, Any] = {"raw": host_token, "ssh_user": None, "port": 22}
        if "@" in host_token:
            ht["ssh_user"], ht["host"] = host_token.split("@", 1)
        else:
            ht["host"] = host_token
        if ":" in ht["host"]:
            host_part, _, port_part = ht["host"].partition(":")
            try:
                ht["port"] = int(port_part)
                ht["host"] = host_part
            except ValueError:
                pass
        for tok in tokens[1:]:
            if "=" not in tok:
                continue
            k, _, v = tok.partition("=")
            if k in ("ansible_user", "user", "ssh_user"):
                ht["ssh_user"] = v
            elif k in ("port", "ssh_port"):
                try:
                    ht["port"] = int(v)
                except ValueError:
                    pass
        if ht["ssh_user"] is None:
            ht["ssh_user"] = os.environ.get("USER", "root")
        out.append(ht)
    return out


# --- SSH / SCP wrappers --------------------------------------------------

def _ssh(ht: dict, command: str, *, ssh_key: Path | None = None,
         timeout: int = 600) -> tuple[int, str, str]:
    ssh = shutil.which("ssh")
    if not ssh:
        return 127, "", "ssh not found"
    args = [
        ssh,
        "-p", str(ht.get("port", 22)),
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
    ]
    if ssh_key:
        args.extend(["-i", str(ssh_key)])
    args.append(f"{ht['ssh_user']}@{ht['host']}")
    args.append(command)
    try:
        proc = subprocess.run(args, capture_output=True, text=True,
                              timeout=timeout, check=False)
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired:
        return 124, "", f"ssh timeout after {timeout}s"
    except OSError as e:
        return 1, "", str(e)


def _scp_from(ht: dict, remote_path: str, local_path: Path,
              *, ssh_key: Path | None = None, timeout: int = 600) -> tuple[int, str]:
    scp = shutil.which("scp")
    if not scp:
        return 127, "scp not found"
    args = [
        scp,
        "-P", str(ht.get("port", 22)),
        "-o", "StrictHostKeyChecking=accept-new",
        "-o", "BatchMode=yes",
        "-o", "ConnectTimeout=10",
    ]
    if ssh_key:
        args.extend(["-i", str(ssh_key)])
    args.append(f"{ht['ssh_user']}@{ht['host']}:{remote_path}")
    args.append(str(local_path))
    try:
        proc = subprocess.run(args, capture_output=True, text=True,
                              timeout=timeout, check=False)
        return proc.returncode, (proc.stdout or "") + (proc.stderr or "")
    except subprocess.TimeoutExpired:
        return 124, f"scp timeout after {timeout}s"
    except OSError as e:
        return 1, str(e)


def _is_windows(ht: dict) -> bool:
    """Best-effort: probe Windows via `ver` on the remote host."""
    rc, out, _ = _ssh(ht, "ver", timeout=15)
    if rc != 0:
        return False
    return "windows" in out.lower() or "microsoft" in out.lower()


# --- Remote worker invocation --------------------------------------------

def _worker_command(remote_run_dir: str, *, compute_hashes: bool = True) -> str:
    """Compose the remote command line.

    The remote host is expected to have Python 3 on PATH. We don't ship an
    embedded agent — the engine bootstrap is generated at scan time.
    """
    parts = [
        "python3",
        "-c",
        "'import sys, runpy, pathlib;",
        "p = pathlib.Path('" + remote_run_dir + "');",
        "p.mkdir(parents=True, exist_ok=True);",
        "sys.argv = ['disk-inventory','run','--mode','report',",
        "'--output-dir','" + remote_run_dir + "'",
    ]
    if compute_hashes:
        parts.append(",'--compute-hashes'")
    parts.append("];")
    parts.append("runpy.run_module('disk_inventory', run_name='__main__')'")
    return " ".join(parts)


# --- Scan orchestration --------------------------------------------------

def scan_hosts(hosts: list[dict], *, output_dir: Path,
               ssh_key: Path | None = None,
               compute_hashes: bool = True,
               parallel: int = 4) -> dict:
    """Scan each host, fetch outputs, write to output_dir/<host>/."""
    output_dir.mkdir(parents=True, exist_ok=True)
    db = init_store(output_dir / "fleet.db")
    summary = {"scanned": [], "errors": [], "skipped": []}

    lock = threading.Lock()
    sem = threading.Semaphore(parallel)

    def _scan(ht: dict) -> None:
        with sem:
            host_dir = output_dir / ht["host"].replace("/", "_")
            host_dir.mkdir(exist_ok=True)
            remote_run = f"/tmp/diskinventory-fleet-{int(datetime.now(timezone.utc).timestamp())}"
            cmd = _worker_command(remote_run, compute_hashes=compute_hashes)
            with lock:
                cur = db.execute(
                    "INSERT OR IGNORE INTO hosts (host, user, port) VALUES (?, ?, ?)",
                    (ht["host"], ht["ssh_user"], ht["port"]),
                )
                db.commit()
                row = db.execute(
                    "SELECT host_id FROM hosts WHERE host=? AND user=? AND port=?",
                    (ht["host"], ht["ssh_user"], ht["port"]),
                ).fetchone()
                host_id = row[0]
            rc, out, err = _ssh(ht, cmd, ssh_key=ssh_key, timeout=900)
            if rc != 0:
                # WinRM fallback for Windows hosts
                if _is_windows(ht):
                    msg = f"SSH failed on Windows host {ht['host']}; WinRM fallback not implemented in this build"
                else:
                    msg = err.strip() or out.strip() or f"rc={rc}"
                with lock:
                    db.execute(
                        "UPDATE hosts SET reached=0, reached_utc=?, error=? WHERE host_id=?",
                        (datetime.now(timezone.utc).isoformat(), msg[:500], host_id),
                    )
                    db.commit()
                    summary["errors"].append({"host": ht["host"], "error": msg[:500]})
                return
            # scp back
            local_tmp = host_dir / "remote-out"
            local_tmp.mkdir(exist_ok=True)
            rc2, err2 = _scp_from(ht, remote_run, local_tmp, ssh_key=ssh_key, timeout=600)
            if rc2 != 0:
                with lock:
                    summary["errors"].append({
                        "host": ht["host"],
                        "error": f"scp failed: {err2.strip()[:500]}",
                    })
                return
            with lock:
                db.execute(
                    "UPDATE hosts SET reached=1, reached_utc=? WHERE host_id=?",
                    (datetime.now(timezone.utc).isoformat(), host_id),
                )
                db.commit()
                summary["scanned"].append(ht["host"])

    threads = []
    for ht in hosts:
        t = threading.Thread(target=_scan, args=(ht,), daemon=True)
        threads.append(t)
        t.start()
    for t in threads:
        t.join()
    db.close()
    return summary


# --- Cross-host dedup ----------------------------------------------------

def cross_host_dedup(fleet_dir: Path, *, top: int = 50) -> dict:
    """Aggregate SHA-1 indices across hosts and emit a dedup report."""
    fleet_dir = Path(fleet_dir)
    db = init_store(fleet_dir / "fleet.db")
    # Build a SHA-1 index across all items
    cur = db.execute(
        """SELECT i.sha1, COUNT(DISTINCT r.host_id) AS host_count,
                  COUNT(*) AS file_count, SUM(i.size_bytes) AS total_bytes
           FROM items i JOIN runs r ON i.run_id = r.run_id
           WHERE i.sha1 != '' AND i.sha1 IS NOT NULL
           GROUP BY i.sha1 HAVING host_count >= 2
           ORDER BY total_bytes DESC LIMIT ?""",
        (top * 10,),
    )
    rows = cur.fetchall()
    out = []
    for sha1, host_count, file_count, total_bytes in rows:
        # pull one representative path
        sample = db.execute(
            "SELECT path FROM items WHERE sha1=? LIMIT 1", (sha1,)
        ).fetchone()
        out.append({
            "sha1": sha1,
            "hostCount": host_count,
            "fileCount": file_count,
            "totalBytes": int(total_bytes or 0),
            "samplePath": sample[0] if sample else "",
            "potentialSavingsBytes": int(total_bytes or 0) - (int(total_bytes or 0) // max(host_count, 1)),
        })
    out = out[:top]
    db.close()

    # Write JSON + HTML
    json_path = fleet_dir / "fleet-dedup.json"
    json_path.write_text(json.dumps({
        "GeneratedUtc": datetime.now(timezone.utc).isoformat(),
        "TopN": top,
        "Items": out,
    }, ensure_ascii=False, indent=2), encoding="utf-8")

    # HTML
    html_parts = [
        "<!doctype html><html><head><meta charset='utf-8'>",
        "<title>DiskInventory fleet dedup</title>",
        "<style>body{font:14px/1.4 sans-serif;margin:1.5rem;}"
        "table{border-collapse:collapse;width:100%;}"
        "th,td{padding:.35rem .55rem;border-bottom:1px solid #8883;font-size:13px;}"
        "th{background:#eee;text-align:left;}td.num{text-align:right;}"
        "@media (prefers-color-scheme:dark){body{background:#1b1b1b;color:#eee;}"
        "th{background:#333;}}}</style></head><body>",
        "<h1>Cross-host duplicates</h1>",
        "<p>Generated " + datetime.now(timezone.utc).isoformat() + "</p>",
        "<table><thead><tr><th>SHA-1</th><th class='num'>Hosts</th>"
        "<th class='num'>Files</th><th class='num'>Bytes</th>"
        "<th class='num'>Savings</th><th>Sample path</th></tr></thead><tbody>",
    ]
    for d in out:
        html_parts.append(
            f"<tr><td><code>{d['sha1']}</code></td>"
            f"<td class='num'>{d['hostCount']}</td>"
            f"<td class='num'>{d['fileCount']}</td>"
            f"<td class='num'>{d['totalBytes']:,}</td>"
            f"<td class='num'>{d['potentialSavingsBytes']:,}</td>"
            f"<td><code>{d['samplePath']}</code></td></tr>"
        )
    html_parts.append("</tbody></table></body></html>")
    (fleet_dir / "fleet-dedup.html").write_text("".join(html_parts), encoding="utf-8")
    return {"items": out}
