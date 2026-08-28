"""doctor.py — diagnostic tool for DiskInventory.

Run via ``disk-inventory doctor`` on any host. Returns a green / yellow
/ red report on:

* Python version (>= 3.8 required)
* free disk space at the user home
* writeability of the engine's output directory (default ``./out``)
* availability of optional deps (Pillow / pywinrm)
* whether port 8765 is bound
* whether a browser can be opened (via the default webbrowser module)

The report's exit code:

* 0 — everything green
* 1 — at least one yellow (advisory)
* 2 — at least one red (blocker)
"""

from __future__ import annotations

import os
import shutil
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

VERSION_INFO = sys.version_info

REQUIRED_PY = (3, 8)


@dataclass
class CheckResult:
    name: str
    status: str  # "green", "yellow", "red"
    detail: str


def _g(name: str, msg: str) -> CheckResult:
    return CheckResult(name, "green", msg)


def _y(name: str, msg: str) -> CheckResult:
    return CheckResult(name, "yellow", msg)


def _r(name: str, msg: str) -> CheckResult:
    return CheckResult(name, "red", msg)


# ---------------------------------------------------------------------------

def check_python() -> CheckResult:
    if VERSION_INFO >= REQUIRED_PY:
        return _g("Python", f"{VERSION_INFO[0]}.{VERSION_INFO[1]}.{VERSION_INFO[2]}")
    return _r(
        "Python",
        f"{VERSION_INFO[0]}.{VERSION_INFO[1]}.{VERSION_INFO[2]} "
        f"(need 3.8+; install from https://python.org)"
    )


def check_free_disk() -> CheckResult:
    home = Path.home()
    try:
        usage = shutil.disk_usage(str(home))
    except OSError as e:
        return _y("Free disk", f"unavailable: {e}")
    free_gb = usage.free / (1024 ** 3)
    if free_gb < 1.0:
        return _r("Free disk", f"{free_gb:.2f} GB free")
    if free_gb < 5.0:
        return _y("Free disk", f"{free_gb:.1f} GB free (low)")
    return _g("Free disk", f"{free_gb:.1f} GB free at {home}")


def check_output_writable() -> CheckResult:
    out = Path("./out").resolve()
    try:
        out.mkdir(parents=True, exist_ok=True)
        probe = out / ".disk-inventory-doctor.txt"
        probe.write_text("ok", encoding="utf-8")
        probe.unlink()
    except OSError as e:
        return _r("Output dir", f"{out}: {e}")
    return _g("Output dir", str(out))


def check_optional_deps() -> list[CheckResult]:
    out = []
    for mod, friendly in (("PIL", "Pillow (EXIF date grouping)"),
                          ("pywinrm", "pywinrm (WinRM fallback)")):
        try:
            __import__(mod)
            out.append(_g(f"Optional: {mod}", f"installed — {friendly}"))
        except Exception:
            out.append(_y(f"Optional: {mod}", f"missing — {friendly}"))
    return out


def check_port_available(port: int = 8765) -> CheckResult:
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
        except OSError:
            return _y("Port 8765", f"already in use (dashboard will pick the next free one)")
        return _g("Port 8765", "free")
    finally:
        if s is not None:
            try:
                s.close()
            except Exception:
                pass


def check_browser() -> CheckResult:
    try:
        import webbrowser
        browser = webbrowser.get()
        return _g("Browser", f"{type(browser).__name__}")
    except Exception as e:
        return _y("Browser", f"no default browser ({e}); use --no-wizard")


def check_scan_roots() -> CheckResult:
    from src.scan_roots import smart_defaults
    try:
        roots = smart_defaults()
    except Exception as e:
        return _r("Smart scan-roots", f"{e}")
    if not roots:
        return _r("Smart scan-roots", "no paths resolved")
    paths = "; ".join(r["Path"] for r in roots)
    return _g("Smart scan-roots", f"{len(roots)}: {paths}")


# ---------------------------------------------------------------------------

def run() -> int:
    """Execute all checks and print a readable report. Returns the exit
    code (0 green, 1 mixed/yellow, 2 red)."""
    checks: list[CheckResult] = []
    checks.append(check_python())
    checks.append(check_free_disk())
    checks.append(check_output_writable())
    checks.append(check_port_available())
    checks.append(check_browser())
    checks.append(check_scan_roots())
    checks.extend(check_optional_deps())

    print(f"DiskInventory doctor — {len(checks)} check(s)")
    print("-" * 60)
    worst = "green"
    for c in checks:
        icon = {"green": "OK  ", "yellow": "WARN", "red": "FAIL"}[c.status]
        print(f"  [{icon}] {c.name}: {c.detail}")
        if c.status == "yellow" and worst == "green":
            worst = "yellow"
        elif c.status == "red":
            worst = "red"
    print("-" * 60)
    if worst == "green":
        print("All checks green. `disk-inventory` (no args) will run.")
        return 0
    if worst == "yellow":
        print("Warnings present. DiskInventory will still run.")
        return 1
    print("BLOCKERS present. Fix the failures above before running.")
    return 2


if __name__ == "__main__":
    sys.exit(run())
