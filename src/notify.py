"""
notify — webhook + OS-native desktop notifications.

Webhook: urllib.request POST with 3x retry + exponential back-off.
Native: BurntToast / notify-send / terminal-notifier / osascript.

No third-party deps. Failures are warnings, never exceptions that abort
a run.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any


def _post_json(url: str, payload: dict, *, token: str | None = None,
               timeout: float = 10.0, retries: int = 3) -> bool:
    """POST `payload` as JSON to `url`. Returns True on 2xx."""
    body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    headers = {"Content-Type": "application/json", "User-Agent": "DiskInventory/2.0"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    delay = 1.0
    last_err = ""
    for attempt in range(retries):
        req = urllib.request.Request(url, data=body, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                if 200 <= resp.status < 300:
                    return True
                last_err = f"HTTP {resp.status}"
        except urllib.error.HTTPError as e:
            last_err = f"HTTP {e.code}: {e.reason}"
        except urllib.error.URLError as e:
            last_err = str(e)
        except (OSError, TimeoutError) as e:
            last_err = str(e)
        time.sleep(delay)
        delay = min(delay * 2, 8.0)
    print(f"[notify] webhook POST {url} failed after {retries} tries: {last_err}")
    return False


def fire_webhook(url: str, event: dict, *, token: str | None = None) -> bool:
    """Fire-and-forget webhook. The `event` dict is sent as the JSON body."""
    if not url:
        return False
    return _post_json(url, event, token=token)


# --- Native desktop notifications -----------------------------------------

def _notify_windows(title: str, body: str) -> bool:
    # Prefer BurntToast PowerShell module if available.
    ps_script = (
        "if (Get-Module -ListAvailable -Name BurntToast) {"
        "  Import-Module BurntToast; "
        f"  New-BurntToastNotification -Text '{title}','{body}'; "
        "  return $true "
        "} elseif (Get-Command msg.exe -ErrorAction SilentlyContinue) {"
        f"  msg * '{title}: {body}'; return $true "
        "} else { return $false }"
    )
    exe = shutil.which("powershell") or shutil.which("pwsh")
    if not exe:
        return False
    try:
        proc = subprocess.run(
            [exe, "-NoProfile", "-NonInteractive", "-Command", ps_script],
            capture_output=True, text=True, timeout=10, check=False,
        )
        return proc.returncode == 0 and "True" in proc.stdout
    except (OSError, subprocess.TimeoutExpired):
        return False


def _notify_macos(title: str, body: str) -> bool:
    tn = shutil.which("terminal-notifier")
    if tn:
        try:
            subprocess.run([tn, "-title", title, "-message", body],
                           timeout=10, check=False, capture_output=True)
            return True
        except (OSError, subprocess.TimeoutExpired):
            pass
    script = f'display notification "{body}" with title "{title}"'
    try:
        subprocess.run(["osascript", "-e", script],
                       timeout=10, check=False, capture_output=True)
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def _notify_linux(title: str, body: str) -> bool:
    ns = shutil.which("notify-send")
    if ns:
        try:
            subprocess.run([ns, title, body], timeout=10, check=False, capture_output=True)
            return True
        except (OSError, subprocess.TimeoutExpired):
            return False
    return False


def fire_native(title: str, body: str) -> bool:
    """Fire OS-native desktop notification. Returns True on success."""
    if os.name == "nt":
        return _notify_windows(title, body)
    if sys_platform() == "darwin":
        return _notify_macos(title, body)
    return _notify_linux(title, body)


def sys_platform() -> str:
    """Return 'darwin' / 'linux' / 'win32' / 'other' — keeps `import sys` local."""
    import sys
    p = sys.platform
    if p.startswith("darwin"):
        return "darwin"
    if p.startswith("linux"):
        return "linux"
    if p.startswith("win"):
        return "win32"
    return p


# --- Run-level event helpers ---------------------------------------------

def notify_run_started(*, run_id: str, mode: str, webhook: str | None = None,
                       webhook_token: str | None = None) -> None:
    payload = {"event": "run.started", "run_id": run_id, "mode": mode}
    if webhook:
        fire_webhook(webhook, payload, token=webhook_token)
    fire_native("DiskInventory", f"Run {run_id} started ({mode})")


def notify_run_finished(*, run_id: str, mode: str, totals: dict,
                        webhook: str | None = None,
                        webhook_token: str | None = None,
                        error: str | None = None) -> None:
    payload = {
        "event": "run.finished",
        "run_id": run_id,
        "mode": mode,
        "totals": totals,
        "error": error,
    }
    if webhook:
        fire_webhook(webhook, payload, token=webhook_token)
    title = "DiskInventory"
    body = f"Run {run_id} finished"
    if error:
        body += f" (error: {error})"
    fire_native(title, body)
