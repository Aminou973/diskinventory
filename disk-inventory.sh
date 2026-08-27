#!/usr/bin/env bash
# disk-inventory.sh — POSIX launcher for DiskInventory v2.0
# Resolves to the script directory so relative paths in src/, config/ work.

set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

if command -v python3 >/dev/null 2>&1; then
    PY=python3
elif command -v python >/dev/null 2>&1; then
    PY=python
else
    echo "[error] Python 3 is required. Install from https://python.org and ensure python3 is on PATH." >&2
    exit 127
fi

exec "$PY" "$HERE/disk-inventory.py" "$@"
