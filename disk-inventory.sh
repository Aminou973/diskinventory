#!/usr/bin/env bash
# disk-inventory.sh - POSIX launcher for DiskInventory v3.0.
#
#  -  Forces UTF-8 locale so non-ASCII paths print cleanly.
#  -  Guards `pipefail` (a bashism) behind BASH_VERSION; exits cleanly
#     on dash/yash/ksh.
#  -  Tries python3, then python (on systems where the latter is also 3.x).
#  -  Honors DISKINVENTORY_NOPAUSE / DISKINVENTORY_NO_PAUSE for CI use.

# Bootstrap environment but don't fail if LC_ALL isn't honored
if [ -n "${BASH_VERSION-}" ]; then
    set -u
    if set -o | grep -q pipefail; then
        set -o pipefail
    fi
fi

case "${LC_ALL-}${LANG-}" in
    *UTF-8*|*utf8*|*UTF8*) ;;
    *) export LC_ALL=C.UTF-8 LANG=C.UTF-8 ;;
esac

# Resolve the directory this script lives in (POSIX-compatible).
HERE="$(cd "$(dirname "$0")" && pwd -P)"
cd "$HERE" || exit 127

# Pick Python interpreter: prefer python3, fall back to python.
PY=""
if command -v python3 >/dev/null 2>&1; then
    PY="$(command -v python3)"
elif command -v python >/dev/null 2>&1; then
    # Honour the case where python IS the Python 3 binary.
    ver="$(python -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)"
    case "$ver" in
        3.*) PY="$(command -v python)" ;;
    esac
fi

if [ -z "$PY" ]; then
    echo "[error] Python 3 is required. Install from https://python.org" >&2
    exit 127
fi

# Friendly version check; the engine also checks but failing fast avoids a
# 5-second traceback on Python 2.x hosts.
ver="$("$PY" -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)"
case "$ver" in
    3.[8-9]*|3.[1-9][0-9]*) ;;
    *)
        echo "[error] Python 3.8+ required (found $ver)" >&2
        exit 2
        ;;
esac

# Skip pause only when explicitly requested
if [ "${DISKINVENTORY_NOPAUSE:-}${DISKINVENTORY_NO_PAUSE:-}" = "1" ]; then
    NO_PAUSE=1
else
    NO_PAUSE=0
fi

if [ -t 0 ] && [ -t 1 ] && [ "$NO_PAUSE" != "1" ]; then
    trap 'rc=$?; echo; [ "${rc:-0}" -ne 0 ] && echo "[exit] rc=$rc"; exit $rc' EXIT INT
fi

"$PY" "$HERE/disk-inventory.py" "$@"
rc=$?

if [ -t 0 ] && [ "$NO_PAUSE" != "1" ]; then
    if [ "$rc" -ne 0 ]; then
        echo "[exit] disk-inventory finished with rc=$rc"
        echo "Press enter to close..."
        read -r _
    fi
fi

exit "$rc"
