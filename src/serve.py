"""
serve — local web UI built on http.server stdlib.

Endpoints:
  GET  /                      — single-page dashboard
  GET  /api/run               — current run metadata
  GET  /api/env               — environment.json
  GET  /api/stats             — live counts
  GET  /api/items?...         — filtered items (host, category, action, q)
  GET  /api/plan              — current plan.json
  GET  /api/journal/stream    — SSE: tail of the journal file
  POST /api/overrides         — accept overrides.json, write to disk
  POST /api/apply/pause       — set pause flag (no-op if apply already done)
  POST /api/apply/resume      — clear pause flag

Bind: 127.0.0.1 by default; --bind 0.0.0.0 for LAN (requires --token).
"""

from __future__ import annotations

import json
import os
import re
import socketserver
import sys
import threading
import time
import urllib.parse
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable

# Live state shared between the run loop and the HTTP layer.
class _State:
    def __init__(self, run_dir: Path):
        self.run_dir = Path(run_dir)
        self.env: dict = {}
        self.rows: list[dict] = []
        self.plan: dict = {}
        self.stats: dict = {"items": 0, "applied": 0, "errors": 0}
        self.pause_flag = threading.Event()
        self.subscribers: list[deque] = []
        self.subs_lock = threading.Lock()

    def get_env(self) -> dict:
        return self.env or _read_json(self.run_dir / "environment.json", {})

    def get_plan(self) -> dict:
        return self.plan or _read_json(self.run_dir / "plan.json", {})

    def load_rows(self) -> list[dict]:
        if self.rows:
            return self.rows
        p = self.run_dir / "inventory.csv"
        if not p.is_file():
            return []
        import csv
        with open(p, "r", encoding="utf-8", newline="") as fh:
            reader = csv.DictReader(fh)
            self.rows = list(reader)
        return self.rows

    def tail_journal(self, n: int = 50) -> list[dict]:
        p = self.run_dir / "actions-journal.jsonl"
        if not p.is_file():
            return []
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            return []
        out = []
        for line in lines[-n:]:
            try:
                out.append(json.loads(line))
            except json.JSONDecodeError:
                continue
        return out

    def subscribe(self) -> deque:
        dq: deque = deque(maxlen=200)
        with self.subs_lock:
            self.subscribers.append(dq)
        return dq

    def unsubscribe(self, dq: deque) -> None:
        with self.subs_lock:
            try:
                self.subscribers.remove(dq)
            except ValueError:
                pass

    def broadcast(self, line: dict) -> None:
        with self.subs_lock:
            for dq in list(self.subscribers):
                try:
                    dq.append(line)
                except Exception:
                    pass


def _read_json(path: Path, default):
    if not path.is_file():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return default


def _stats_from_rows(rows: list[dict]) -> dict:
    from collections import Counter, defaultdict
    cats = Counter(r.get("Category", "Other") for r in rows)
    size_total = 0
    for r in rows:
        try:
            size_total += int(r.get("SizeBytes") or 0)
        except (TypeError, ValueError):
            pass
    return {
        "items": len(rows),
        "categories": len(cats),
        "totalBytes": size_total,
        "byCategory": dict(cats),
    }


# --- HTML dashboard -------------------------------------------------------

_DASHBOARD_HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>DiskInventory — Live Dashboard</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 14px/1.45 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         margin: 0; padding: 1.5rem; background: Canvas; color: CanvasText; }
  h1 { margin: 0 0 .25rem; font-size: 1.4rem; }
  .status { display: inline-block; padding: .1rem .6rem; border-radius: 999px;
            background: color-mix(in oklab, Canvas 86%, CanvasText 14%);
            font-size: .8rem; }
  .status.live { background: #2c7; color: #fff; }
  .tile-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(170px, 1fr));
               gap: .75rem; margin: 1rem 0; }
  .tile { padding: .75rem 1rem; border: 1px solid #8884; border-radius: 8px;
          background: color-mix(in oklab, Canvas 92%, CanvasText 8%); }
  .tile .v { font-size: 1.4rem; font-weight: 600; font-variant-numeric: tabular-nums; }
  .tile .k { font-size: .8rem; opacity: .75; text-transform: uppercase; letter-spacing: .04em; }
  table { border-collapse: collapse; width: 100%; margin: .5rem 0; }
  th, td { padding: .35rem .55rem; border-bottom: 1px solid #8883; text-align: left; font-size: 13px; }
  th { background: color-mix(in oklab, Canvas 86%, CanvasText 14%); position: sticky; top: 0; }
  td.num { text-align: right; font-variant-numeric: tabular-nums; }
  .journal { font: 12px/1.35 ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
             max-height: 360px; overflow: auto; padding: .5rem;
             background: color-mix(in oklab, Canvas 94%, CanvasText 6%);
             border: 1px solid #8883; border-radius: 6px; }
  .journal div { padding: 1px 0; }
  .journal .ok { color: #2c7; }
  .journal .err { color: #e44; }
  .controls { margin: .75rem 0; }
  .controls button { padding: .4rem .8rem; cursor: pointer; margin-right: .5rem; }
</style>
</head>
<body>
<h1>DiskInventory — Live Dashboard</h1>
<p>Run <code id="runId">…</code> · <span class="status live" id="connStatus">connecting…</span></p>

<div class="tile-grid" id="tiles"></div>

<div class="controls">
  <button id="pauseBtn">Pause apply</button>
  <button id="resumeBtn">Resume apply</button>
</div>

<h2>Categories</h2>
<table id="cats"><thead>
  <tr><th>Category</th><th class="num">Items</th><th class="num">Bytes</th></tr>
</thead><tbody></tbody></table>

<h2>Top 50 by size</h2>
<table id="big"><thead>
  <tr><th>Path</th><th class="num">Size</th><th>Category</th></tr>
</thead><tbody></tbody></table>

<h2>Journal (live tail)</h2>
<div class="journal" id="journal"></div>

<script>
(function () {
  'use strict';
  function $(id) { return document.getElementById(id); }
  function jget(url, cb) {
    fetch(url, { cache: 'no-store' }).then(function (r) {
      return r.ok ? r.json() : null;
    }).then(function (j) { cb(j); }).catch(function () { cb(null); });
  }
  function fmtBytes(n) { return (n || 0).toLocaleString(); }

  jget('/api/run', function (run) {
    if (run) $('runId').textContent = run.RunId || '(unknown)';
  });
  jget('/api/stats', function (s) {
    if (!s) return;
    var t = [['Items', s.items || 0],
             ['Categories', s.categories || 0],
             ['Total bytes', fmtBytes(s.totalBytes || 0)],
             ['Applied', s.applied || 0],
             ['Errors', s.errors || 0]];
    var tiles = $('tiles'); tiles.innerHTML = '';
    t.forEach(function (p) {
      var d = document.createElement('div'); d.className = 'tile';
      d.innerHTML = '<div class="v">' + p[1] + '</div><div class="k">' + p[0] + '</div>';
      tiles.appendChild(d);
    });
  });
  jget('/api/items?limit=50&sort=size', function (items) {
    if (!items) return;
    var body = $('big').getElementsByTagName('tbody')[0];
    body.innerHTML = '';
    items.forEach(function (r) {
      var tr = document.createElement('tr');
      tr.innerHTML = '<td><code>' + (r.Path || '') + '</code></td>' +
                     '<td class="num">' + fmtBytes(r.SizeBytes || 0) + '</td>' +
                     '<td>' + (r.Category || '') + '</td>';
      body.appendChild(tr);
    });
    var catBody = $('cats').getElementsByTagName('tbody')[0];
    catBody.innerHTML = '';
    var byCat = {};
    items.forEach(function (r) {
      var k = r.Category || 'Other';
      byCat[k] = byCat[k] || { count: 0, bytes: 0 };
      byCat[k].count += 1;
      byCat[k].bytes += (r.SizeBytes || 0);
    });
    Object.keys(byCat).sort(function(a,b){return byCat[b].bytes - byCat[a].bytes;}).forEach(function (k) {
      var tr = document.createElement('tr');
      tr.innerHTML = '<td>' + k + '</td><td class="num">' + byCat[k].count + '</td><td class="num">' + fmtBytes(byCat[k].bytes) + '</td>';
      catBody.appendChild(tr);
    });
  });

  // Journal SSE
  var journal = $('journal');
  function appendJournal(line) {
    var d = document.createElement('div');
    var cls = line.error ? 'err' : (line.applied ? 'ok' : '');
    d.className = cls;
    d.textContent = '[' + (line.ts || '') + '] ' + (line.action || '') +
                    '  ' + (line.src || '') +
                    (line.dst ? '  →  ' + line.dst : '') +
                    (line.error ? '  ERROR: ' + line.error : '');
    journal.appendChild(d);
    while (journal.childNodes.length > 200) journal.removeChild(journal.firstChild);
    journal.scrollTop = journal.scrollHeight;
  }
  function startSSE() {
    var es = new EventSource('/api/journal/stream');
    es.onopen = function () { $('connStatus').textContent = 'live'; $('connStatus').className = 'status live'; };
    es.onerror = function () { $('connStatus').textContent = 'reconnecting…'; $('connStatus').className = 'status'; };
    es.onmessage = function (e) {
      try {
        var line = JSON.parse(e.data);
        appendJournal(line);
      } catch (err) { /* ignore */ }
    };
  }
  startSSE();

  // Pause / resume
  $('pauseBtn').onclick = function () {
    fetch('/api/apply/pause', { method: 'POST' });
  };
  $('resumeBtn').onclick = function () {
    fetch('/api/apply/resume', { method: 'POST' });
  };
})();
</script>
</body>
</html>
"""


# --- HTTP handler --------------------------------------------------------

class _Handler(BaseHTTPRequestHandler):
    state: _State = None  # set by build_server
    token: str | None = None

    def log_message(self, fmt, *args):  # quieter logs
        sys.stderr.write("[serve] " + (fmt % args) + "\n")

    def _authorized(self) -> bool:
        if not self.token:
            return True
        auth = self.headers.get("Authorization", "")
        return auth == f"Bearer {self.token}"

    def _json(self, status: int, payload: Any) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _text(self, status: int, body: bytes, content_type: str = "text/plain") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):  # noqa: N802 (BaseHTTPRequestHandler API)
        if not self._authorized():
            self._json(401, {"error": "unauthorized"})
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path or "/"
        if path == "/":
            body = _DASHBOARD_HTML.encode("utf-8")
            self._text(200, body, "text/html; charset=utf-8")
            return
        if path == "/setup":
            # First-run wizard
            from src.wizard import setup_html
            from src.scan_roots import smart_defaults
            try:
                roots = smart_defaults()
            except Exception:
                roots = []
            from src import env_detect  # for VERSION constant if present
            engine = getattr(env_detect, "VERSION", "3.0")
            body = setup_html(engine_version=engine,
                              smart_roots=roots).encode("utf-8")
            self._text(200, body, "text/html; charset=utf-8")
            return
        if path == "/api/run":
            self._json(200, {
                "RunId": self.state.get_env().get("RunId", ""),
                "StartedUtc": self.state.get_env().get("TimestampUtc", ""),
            })
            return
        if path == "/api/env":
            self._json(200, self.state.get_env())
            return
        if path == "/api/stats":
            rows = self.state.load_rows()
            self._json(200, _stats_from_rows(rows))
            return
        if path == "/api/plan":
            self._json(200, self.state.get_plan())
            return
        if path == "/api/items":
            self._items(parsed.query)
            return
        if path == "/api/journal/stream":
            self._sse()
            return
        self._json(404, {"error": "not found", "path": path})

    def _items(self, qs: str) -> None:
        params = urllib.parse.parse_qs(qs)
        rows = self.state.load_rows()
        def get(k):
            v = params.get(k, [""])
            return v[0] if v else ""
        host = get("host")
        category = get("category")
        action = get("action")
        q = get("q").lower()
        limit = int(get("limit") or 1000)
        sort = get("sort")
        out = []
        for r in rows:
            if host and host not in (r.get("Path", "")):
                continue
            if category and r.get("Category", "") != category:
                continue
            if action and r.get("Action", "") != action:
                continue
            if q and q not in (r.get("Path", "") + r.get("Name", "")).lower():
                continue
            out.append(r)
        if sort == "size":
            out.sort(key=lambda r: -int(r.get("SizeBytes") or 0))
        out = out[:limit]
        self._json(200, out)

    def _sse(self) -> None:
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        dq = self.state.subscribe()
        try:
            # Initial backfill
            for entry in self.state.tail_journal(50):
                self.wfile.write(b"data: " + json.dumps(entry, ensure_ascii=False).encode("utf-8") + b"\n\n")
                self.wfile.flush()
            while True:
                try:
                    item = dq.popleft()
                except IndexError:
                    time.sleep(0.5)
                    # heartbeat
                    self.wfile.write(b": ping\n\n")
                    self.wfile.flush()
                    continue
                self.wfile.write(b"data: " + json.dumps(item, ensure_ascii=False).encode("utf-8") + b"\n\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, OSError):
            pass
        finally:
            self.state.unsubscribe(dq)

    def do_POST(self):  # noqa: N802
        if not self._authorized():
            self._json(401, {"error": "unauthorized"})
            return
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path or "/"
        length = int(self.headers.get("Content-Length", 0) or 0)
        body = self.rfile.read(length) if length else b""
        if path == "/api/overrides":
            try:
                payload = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                self._json(400, {"error": "invalid json"})
                return
            items = payload.get("items", [])
            out = self.state.run_dir / "overrides.json"
            out.write_text(json.dumps({"items": items}, ensure_ascii=False, indent=2),
                           encoding="utf-8")
            self._json(200, {"ok": True, "saved": len(items)})
            return
        if path == "/api/apply/pause":
            self.state.pause_flag.set()
            self._json(200, {"ok": True, "paused": True})
            return
        if path == "/api/apply/resume":
            self.state.pause_flag.clear()
            self._json(200, {"ok": True, "paused": False})
            return
        if path == "/api/setup":
            # Persist wizard config to ~/.diskinventory/config.json
            try:
                payload = json.loads(body.decode("utf-8"))
            except json.JSONDecodeError:
                self._json(400, {"error": "invalid json"})
                return
            from src.wizard import save_config, WizardConfig
            try:
                cfg = WizardConfig(
                    scan_roots=payload.get("scan_roots", []),
                    compute_hashes=bool(payload.get("compute_hashes", False)),
                    classify_cluster=bool(payload.get("classify_cluster", False)),
                    classify_exif=bool(payload.get("classify_exif", False)),
                    auto_purge_days=int(payload.get("auto_purge_days", 30)),
                    notify_webhook=payload.get("notify_webhook", ""),
                )
                out = save_config(cfg)
            except Exception as e:
                self._json(500, {"error": str(e)})
                return
            self._json(200, {"ok": True, "saved": str(out)})
            return
        self._json(404, {"error": "not found", "path": path})


# --- Public entry ---------------------------------------------------------

def build_server(state: _State, *, host: str = "127.0.0.1", port: int = 8765,
                 token: str | None = None,
                 fallback_port_ring: int = 0) -> ThreadingHTTPServer | None:
    """Bind a ThreadingHTTPServer. On port-in-use, scan ``preferred..+ring``.

    Returns None if no port could be bound. v3 callers should use the
    actual port from the bound ``srv`` (``srv.server_address[1]``).
    """
    from src.port_check import free_port
    chosen = port
    if not fallback_port_ring:
        srv = ThreadingHTTPServer((host, port), _Handler)
    else:
        chosen = free_port(host, preferred=port, ring=fallback_port_ring)
        if chosen == 0:
            return None
        srv = ThreadingHTTPServer((host, chosen), _Handler)
    srv.RequestHandlerClass.state = state
    srv.RequestHandlerClass.token = token
    return srv


def serve_forever(state: _State, *, host: str, port: int, token: str | None = None) -> int:
    """Bind and serve. Returns the port the server actually bound (may differ
    from the requested port when ``fallback_port_ring`` is engaged via
    :func:`build_server`). Returns ``0`` on total failure.
    """
    srv = build_server(state, host=host, port=port, token=token)
    if srv is None:
        print(f"[serve] could not bind {host}:{port}; no free port in ring")
        return 0
    actual = int(srv.server_address[1])
    print(f"[serve] http://{host}:{actual} (run dir: {state.run_dir})")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
    return actual
