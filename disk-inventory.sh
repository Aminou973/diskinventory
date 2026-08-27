#!/usr/bin/env bash
# ============================================================
#  DiskInventory launcher (Linux/macOS)
#  Run from anywhere; the script cd's to its own dir, then
#  invokes disk-inventory.py with all args forwarded.
# ============================================================

set -e

# Resolve our own directory (works with symlinks)
TOOL_DIR="$(cd "$(dirname "$(readlink -f "$0" 2>/dev/null || python3 -c 'import os,sys;print(os.path.realpath(sys.argv[1]))' "$0")")" && pwd)"
cd "$TOOL_DIR"

# Prefer python3 (always present on Linux/macOS).
if ! command -v python3 >/dev/null 2>&1; then
    echo "ERROR: python3 not found in PATH. Install Python 3 first." >&2
    exit 127
fi

python3 "$TOOL_DIR/disk-inventory.py" "$@"
RC=$?

echo ""
echo "============================================================"
echo " DiskInventory finished with exit code $RC."
echo " Reports are under out/ inside $TOOL_DIR."
echo "============================================================"

# Pause only if we were launched from an interactive terminal (so the window
# doesn't disappear on double-click).
if [ -t 0 ] && [ -z "${DISK_INVENTORY_NO_PAUSE:-}" ]; then
    read -r -p "Press Enter to close..." _
fi

exit $RC