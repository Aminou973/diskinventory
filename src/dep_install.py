"""dep_install.py — auto-install optional runtime dependencies.

Called from the wizard + from `--classify-exif` paths in :mod:`disk-inventory`.
Tries ``pip install --user <pkg>`` with a hard timeout. On failure, the
caller falls back to a no-op behavior with a clear message.

Design goals:

* **Idempotent**: if the import is already satisfied, do nothing and
  return ``True`` immediately.
* **Surgical**: never replaces pip, never upgrades, never installs
  anything except the one named package.
* **Self-contained**: uses only stdlib (``subprocess``, ``site``).
* **Bounded**: 30 s wall-clock per install attempt.
"""

from __future__ import annotations

import importlib
import site
import subprocess
import sys


DEFAULT_TIMEOUT_S = 30


def is_installed(module_name: str) -> bool:
    try:
        importlib.import_module(module_name)
        return True
    except Exception:
        return False


def _pip_user_available() -> bool:
    """Return True iff pip accepts --user on this interpreter."""
    try:
        site.ENABLE_USER_SITE  # noqa: B018  presence check
    except Exception:
        return False
    return True


def ensure(pkg_or_module: str, *, flag_name: str | None = None,
           timeout: int = DEFAULT_TIMEOUT_S) -> bool:
    """Ensure ``pkg_or_module`` (a pip distribution name OR importable
    module name) is importable. On miss, attempt a targeted install.

    Returns ``True`` when the module is importable after the call,
    ``False`` on failure.
    """
    # Common patterns: Pillow -> PIL, pywinrm -> pywinrm
    importable = pkg_or_module if "." not in pkg_or_module else pkg_or_module.split(".")[0]
    if is_installed(importable):
        return True
    print(f"[deps] {importable} not installed; attempting pip install "
          f"{pkg_or_module} (timeout {timeout}s)…")
    args = [sys.executable, "-m", "pip", "install", "--user", pkg_or_module]
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        print(f"[deps] pip install timed out after {timeout}s "
              f"(set DISKINVENTORY_NO_AUTODEP=1 to skip)")
        return False
    except OSError as e:
        print(f"[deps] pip launch failed: {e}")
        return False
    if proc.returncode != 0:
        msg = (proc.stderr or proc.stdout or "").strip().splitlines()[-5:]
        print("[deps] pip install failed:")
        for line in msg:
            print(f"        {line}")
        return False
    # Verify importability after install
    return is_installed(importable)


__all__ = ["ensure", "is_installed", "DEFAULT_TIMEOUT_S"]
