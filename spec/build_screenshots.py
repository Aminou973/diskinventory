"""
spec/build_screenshots.py — drive the live dashboard with a headless browser
and capture screenshots of the major UI surfaces into docs/screenshots/.

Captures (in order):
  01-dashboard.png         — the main /  page after a run
  02-categories.png        — cropped Categories + heavy-caches area
  03-fleet-dedup.png       — fleet dedup report (fleet-dedup.html)
  04-override-ui.png       — the offline inventory.html override UI

Usage:
    python spec/build_screenshots.py            # capture all
    python spec/build_screenshots.py --only 1   # capture only step 1

Notes:
  * Requires Playwright (or Selenium) + a browser binary. If neither is
    installed, the script degrades gracefully and writes SVG placeholders
    so the README gallery still has something to render.
  * Run from the repo root so paths resolve correctly.
"""

from __future__ import annotations

import argparse
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "docs" / "screenshots"


def _free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def _has_playwright() -> bool:
    try:
        import playwright  # noqa: F401
        return True
    except ImportError:
        return False


def _placeholder(name: str, title: str, subtitle: str = "") -> Path:
    """Write an SVG placeholder so the README gallery has something to show
    when no headless browser is installed."""
    OUT.mkdir(parents=True, exist_ok=True)
    p = OUT / name.replace(".png", ".svg")
    body = f"""<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 1200 720'>
  <defs>
    <linearGradient id='g' x1='0' x2='0' y1='0' y2='1'>
      <stop offset='0' stop-color='#222'/>
      <stop offset='1' stop-color='#111'/>
    </linearGradient>
  </defs>
  <rect width='1200' height='720' fill='url(#g)'/>
  <text x='60' y='80' font-family='-apple-system, Segoe UI, sans-serif'
        font-size='32' font-weight='600' fill='#eee'>{title}</text>
  <text x='60' y='120' font-family='-apple-system, Segoe UI, sans-serif'
        font-size='18' fill='#aaa'>{subtitle or 'Install Playwright (`pip install playwright && playwright install chromium`) to regenerate.'}</text>
  <g transform='translate(60,160)'>
    <rect width='320' height='110' rx='10' fill='#1c1c1c' stroke='#333'/>
    <rect x='340' width='320' height='110' rx='10' fill='#1c1c1c' stroke='#333'/>
    <rect x='680' width='320' height='110' rx='10' fill='#1c1c1c' stroke='#333'/>
    <rect width='1080' height='110' rx='10' fill='#171717' stroke='#333' y='130'/>
    <rect width='1080' height='250' rx='10' fill='#171717' stroke='#333' y='260'/>
  </g>
</svg>"""
    p.write_text(body, encoding="utf-8")
    print(f"  placeholder -> {p.relative_to(REPO)}")
    return p


def _wait_http(port: int, path: str = "/", timeout: float = 10.0) -> bool:
    import urllib.request
    import urllib.error
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=2) as r:
                if r.status == 200:
                    return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.3)
    return False


def _serve_run_dir(run_dir: Path, port: int) -> threading.Thread:
    """Start src.serve in a thread; return the thread."""
    sys.path.insert(0, str(REPO))
    sys.path.insert(0, str(REPO / "src"))
    from src import serve as serve_mod
    state = serve_mod._State(run_dir)
    env_p = run_dir / "environment.json"
    if env_p.is_file():
        import json as _json
        try:
            state.env = _json.loads(env_p.read_text(encoding="utf-8"))
        except Exception:
            pass
    inv = run_dir / "inventory.csv"
    if inv.is_file():
        import csv as _csv
        with open(inv, "r", encoding="utf-8", newline="") as fh:
            state.rows = list(_csv.DictReader(fh))

    def _run():
        serve_mod.serve_forever(state, host="127.0.0.1", port=port)
    t = threading.Thread(target=_run, daemon=True)
    t.start()
    return t


def _capture_with_playwright(url: str, out_path: Path, *, full_page: bool = True,
                             viewport=(1280, 800), wait_ms: int = 800) -> bool:
    try:
        from playwright.sync_api import sync_playwright  # type: ignore
    except ImportError:
        return False
    OUT.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(viewport={"width": viewport[0], "height": viewport[1]})
        page = context.new_page()
        page.goto(url, wait_until="networkidle", timeout=15000)
        page.wait_for_timeout(wait_ms)
        page.screenshot(path=str(out_path), full_page=full_page)
        browser.close()
    return True


# --- Step 1: dashboard -----------------------------------------------------

def step_dashboard(run_dir: Path) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    port = _free_port()
    _serve_run_dir(run_dir, port)
    if not _wait_http(port, "/"):
        return _placeholder("01-dashboard.png", "Live dashboard",
                            "Run the harness against a real run dir for an actual screenshot.")
    url = f"http://127.0.0.1:{port}/"
    out = OUT / "01-dashboard.png"
    if not _capture_with_playwright(url, out):
        return _placeholder("01-dashboard.png", "Live dashboard",
                            f"Served at {url}; install Playwright to capture.")
    print(f"  captured -> {out.relative_to(REPO)}")
    return out


# --- Step 2: fleet dedup ---------------------------------------------------

def step_fleet_dedup(fleet_dir: Path) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    html = fleet_dir / "fleet-dedup.html"
    if not html.is_file():
        return _placeholder("03-fleet-dedup.png", "Fleet dedup",
                            "Run `disk-inventory.py fleet dedup <fleet-dir>` first.")
    port = _free_port()
    import http.server
    import functools
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(fleet_dir))
    httpd = http.server.HTTPServer(("127.0.0.1", port), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        if not _wait_http(port, "/fleet-dedup.html"):
            return _placeholder("03-fleet-dedup.png", "Fleet dedup")
        url = f"http://127.0.0.1:{port}/fleet-dedup.html"
        out = OUT / "03-fleet-dedup.png"
        if not _capture_with_playwright(url, out):
            return _placeholder("03-fleet-dedup.png", "Fleet dedup",
                                f"Served at {url}; install Playwright to capture.")
        print(f"  captured -> {out.relative_to(REPO)}")
        return out
    finally:
        httpd.shutdown()


# --- Step 3: override UI (offline inventory.html) -------------------------

def step_override_ui(run_dir: Path) -> Path:
    OUT.mkdir(parents=True, exist_ok=True)
    html = run_dir / "inventory.html"
    if not html.is_file():
        return _placeholder("04-override-ui.png", "Override UI",
                            "Run a report-mode disk-inventory first; inventory.html is offline.")
    port = _free_port()
    import http.server
    import functools
    handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=str(run_dir))
    httpd = http.server.HTTPServer(("127.0.0.1", port), handler)
    t = threading.Thread(target=httpd.serve_forever, daemon=True)
    t.start()
    try:
        if not _wait_http(port, "/inventory.html"):
            return _placeholder("04-override-ui.png", "Override UI")
        url = f"http://127.0.0.1:{port}/inventory.html"
        out = OUT / "04-override-ui.png"
        if not _capture_with_playwright(url, out, full_page=False, viewport=(1280, 900)):
            return _placeholder("04-override-ui.png", "Override UI",
                                f"Served at {url}; install Playwright to capture.")
        print(f"  captured -> {out.relative_to(REPO)}")
        return out
    finally:
        httpd.shutdown()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--only", type=int, choices=[1, 2, 3], default=None,
                        help="only run this step (1=dashboard, 2=fleet dedup, 3=override UI)")
    parser.add_argument("--run-dir", default=None, help="run dir to serve (defaults to spec/fixtures/_run)")
    parser.add_argument("--fleet-dir", default=None, help="fleet dir to render (defaults to fleet-out)")
    args = parser.parse_args()

    run_dir = Path(args.run_dir).resolve() if args.run_dir else REPO / "spec" / "fixtures" / "_run"
    fleet_dir = Path(args.fleet_dir).resolve() if args.fleet_dir else REPO / "fleet-out"

    steps = {
        1: lambda: step_dashboard(run_dir),
        2: lambda: step_fleet_dedup(fleet_dir),
        3: lambda: step_override_ui(run_dir),
    }

    print("[screenshots] has_playwright =", _has_playwright())
    for n, fn in steps.items():
        if args.only and args.only != n:
            continue
        print(f"[screenshots] step {n}…")
        fn()
    print("[screenshots] done")
    return 0


if __name__ == "__main__":
    sys.exit(main())
