"""python_guard.py — bail out fast when run on a too-old interpreter.

DiskInventory v3.0 requires Python 3.8 or newer (f-strings + PEP 604 unions
in the engine, plus 'dict[str, str]' style annotations in some modules).
A user with only Python 2 or 3.7 on PATH used to get a `SyntaxError`
on the first line of `disk-inventory.py` and a confusing traceback.

Importing this module does the check and ``sys.exit(2)``s with a clear
message if the interpreter is too old. The launcher scripts
(`disk-inventory.bat`/`.sh`/`.ps1`/`.fish`) also do a version parse on
`python --version`, but importing here is the belt-and-braces guarantee
that nothing the user does can sneak past a too-old Python.
"""

from __future__ import annotations

import sys

MIN_PY = (3, 8)


def check(minimum=MIN_PY) -> None:
    """Print a friendly error and exit 2 if sys.version_info is < minimum."""
    if sys.version_info >= minimum:
        return
    need = ".".join(str(x) for x in minimum)
    have = "%d.%d.%d" % sys.version_info[:3]
    sys.stderr.write(
        f"[fatal] DiskInventory requires Python {need} or newer "
        f"(found {have}).\n"
        f"[fatal] Install Python 3.8+ from https://python.org and "
        f"ensure it is on PATH.\n"
    )
    sys.exit(2)


if __name__ == "__main__":
    check()
