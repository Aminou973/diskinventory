#!/usr/bin/env fish
# disk-inventory.fish - fish shell launcher for DiskInventory v3.0.
#
#  -  Uses fish-native syntax (no bash `set -euo pipefail`).
#  -  Sets UTF-8 locale.
#  -  Honors DISKINVENTORY_NOPAUSE / DISKINVENTORY_NO_PAUSE.

# Resolve the directory this script lives in.
set -l HERE (status --current-filename | path resolve | path dirname)

# Force UTF-8 when the surrounding environment is C / POSIX.
switch "$LC_ALL$LANG"
    case '*UTF-8*' '*utf8*' '*UTF8*'
        # already UTF-8
    case '*'
        set -gx LC_ALL C.UTF-8
        set -gx LANG C.UTF-8
end

cd $HERE
or begin
    echo "[error] cannot cd to $HERE" >&2
    exit 127
end

# Pick Python 3.
set -l PY ""
if command -v python3 >/dev/null
    set PY (command -v python3)
else if command -v python >/dev/null
    set -l ver (python -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)
    if string match -qr '^3\.' -- $ver
        set PY (command -v python)
    end
end

if test -z "$PY"
    echo "[error] Python 3 is required." >&2
    exit 127
end

# Friendly version check.
set -l ver ($PY -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null)
switch "$ver"
    case '3.8*' '3.9*' '3.1*' '3.2*' '3.3*' '3.4*' '3.5*' '3.6*' '3.7*' '3.8.*' '3.9.*' '3.1[0-9].*' '3.2[0-9].*'
        # supported
    case '*'
        echo "[error] Python 3.8+ required (found $ver)" >&2
        exit 2
end

set -l rc 0
if set -q DISKINVENTORY_NOPAUSE; and test "$DISKINVENTORY_NOPAUSE" = "1"
    set -l rc 0
end
if set -q DISKINVENTORY_NO_PAUSE; and test "$DISKINVENTORY_NO_PAUSE" = "1"
    set -l rc 0
end

$PY "$HERE/disk-inventory.py" $argv
set rc $status

if not set -q DISKINVENTORY_NOPAUSE; or test "$DISKINVENTORY_NOPAUSE" != "1"
    if not set -q DISKINVENTORY_NO_PAUSE; or test "$DISKINVENTORY_NO_PAUSE" != "1"
        if isatty stdout
            if test "$rc" -ne 0
                echo "[exit] disk-inventory rc=$rc"
                read -P "Press enter to close" _
            end
        end
    end
end

exit $rc
