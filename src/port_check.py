"""port_check.py — find a free TCP port without random retry loops.

v2.0's ``serve_forever`` raised ``OSError: WinError 10048`` when port
8765 was already bound, which (a) crashed the run, and (b) left the
user staring at a browser tab that loaded nothing.

v3 first tries the user's requested port; on bind failure it scans a
small ring (default 8765..8770) for a free one. If none are free it
returns ``port=0`` which lets the kernel pick. The dashboard URL is
then derived from the *actual* bound port (``getsockname()[1]``).
"""

from __future__ import annotations

import socket
from typing import Optional


def can_bind(host: str, port: int) -> bool:
    """Return True iff we can bind a TCP socket on (host, port)."""
    s = None
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind((host, port))
        except (OSError, PermissionError):
            return False
        return True
    finally:
        if s is not None:
            try:
                s.close()
            except Exception:
                pass


def free_port(host: str, *, preferred: int = 8765,
              ring: int = 5) -> int:
    """Find a TCP port that's free on ``host``.

    Tries ``preferred`` first, then ``preferred + 1`` .. ``preferred + ring``.
    Falls back to ``0`` (kernel-assigned). Returns the chosen port.
    """
    for p in range(preferred, preferred + ring + 1):
        if can_bind(host, p):
            return p
    return 0


def bound_port(sock: socket.socket) -> int:
    """Return the kernel-assigned port of a freshly bound ``sock``."""
    return int(sock.getsockname()[1])


__all__ = ["can_bind", "free_port", "bound_port"]
