"""prompt.py — TTY-aware Y/N prompts with a non-interactive fallback.

When ``disk-inventory clean`` is run from a non-TTY shell (PowerShell
pipeline, double-clicked bat, Task Scheduler, CI), the v2.0
``input("[run] Apply? Type 'yes' to proceed: ")`` raised ``EOFError``
and dropped a traceback into the user's console.

v3 replaces that single ``input()`` with ``yes_no()`` which:

* detects ``sys.stdin.isatty()``,
* reads a single line from stdin when it IS a TTY (with a sensible
  ``[y/N]`` default),
* if NOT a TTY, prints one final block describing the planned actions
  and the exact command the user can re-run to actually apply them
  (``disk-inventory apply --yes --plan <file>``).

The ``--yes`` flag at the CLI level bypasses the prompt entirely; this
module exists only for the interactive path.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Callable, Iterable


def yes_no(question: str, *, default: bool = False,
           fallback_command: str | None = None) -> bool:
    """Return True iff the user typed 'yes' (or 'y') on stdin.

    ``question`` is printed verbatim followed by `` [y/N] `` (capitalised
    per the default). When stdin is not a TTY the prompt becomes a
    single-shot advisory message: prints the ``fallback_command`` (if
    provided) and returns ``default``.
    """
    suffix = " [Y/n] " if default else " [y/N] "
    if sys.stdin.isatty():
        try:
            resp = input(question + suffix).strip().lower()
        except (EOFError, KeyboardInterrupt):
            sys.stderr.write("\n[prompt] no input; aborting apply.\n")
            return False
        if resp in ("y", "yes"):
            return True
        if resp in ("n", "no"):
            return False
        return default
    # Non-interactive shell
    sys.stderr.write(
        "\n[prompt] stdin is not a TTY — apply cannot prompt.\n"
    )
    if fallback_command:
        sys.stderr.write(
            f"[prompt] To apply without prompting, run:\n"
            f"  {fallback_command}\n"
        )
    return False


def show_planned_actions(actions: Iterable[dict], *, limit: int = 10) -> None:
    """Print up to ``limit`` planned actions and a count of the rest."""
    actions = list(actions)
    if not actions:
        sys.stderr.write("[prompt] (no actions planned)\n")
        return
    for a in actions[:limit]:
        line = "  " + _fmt_action(a)
        sys.stderr.write(line + "\n")
    if len(actions) > limit:
        sys.stderr.write(
            f"  ... and {len(actions) - limit} more\n"
        )
    sys.stderr.write(f"[prompt] {len(actions)} action(s) total\n")


def _fmt_action(a: dict) -> str:
    src = a.get("src", a.get("path", "?"))
    dst = a.get("dst", a.get("destination", "-"))
    rule = a.get("rule") or a.get("category") or ""
    rule = f"  [{rule}]" if rule else ""
    return f"{src}  ->  {dst}{rule}"


def fallback_apply_command(*, plan_path: Path | str,
                            journal_path: Path | str,
                            cmd_name: str = "disk-inventory",
                            extra: str = "") -> str:
    """Compose the one-liner the user can run to apply without prompting."""
    return (
        f"{cmd_name} apply --yes --plan "
        f"\"{Path(plan_path).resolve()}\" --journal "
        f"\"{Path(journal_path).resolve()}\" {extra}".rstrip()
    )
