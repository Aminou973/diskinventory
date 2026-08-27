#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
disk-inventory — Linux/macOS entry point for DiskInventory.

Single command to:
  - Scan the current machine (auto-detected).
  - Classify every item.
  - Write CSV + HTML + Markdown reports.
  - Optionally dry-run, execute, restore, or purge the quarantine.

Subcommands:
  run          (default) scan + classify + export; optionally apply
  restore      reverse a journal (default --what-if)
  purge        permanently delete _Quarantine/<runId> contents older than N days

Modes (for `run`):
  report       (default, read-only)
  dryrun       writes a journal of intent, no disk changes
  auto         executes the plan, soft-deletes only (move to _Quarantine)

Examples:
  ./disk-inventory.py --mode report --output-dir out/01-report
  ./disk-inventory.py --mode dryrun --output-dir out/02-dryrun
  ./disk-inventory.py --mode auto   --output-dir out/03-auto --yes
  ./disk-inventory.py restore out/03-auto/actions-journal.jsonl --apply
  ./disk-inventory.py purge --older-than-days 30

The tool is self-discovering: it uses Path(__file__).parent to find its own
location, then loads modules from src/. No install, no env vars.
"""

import argparse
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

# ---- Resolve tool location --------------------------------------------------

TOOL_DIR = Path(__file__).resolve().parent
SRC_DIR = TOOL_DIR / "src"
CONF_DIR_DEFAULT = TOOL_DIR / "config"

# Ensure src/ is importable
sys.path.insert(0, str(SRC_DIR))

from detect_environment import detect_environment        # noqa: E402
from collect_inventory import collect_inventory          # noqa: E402
from classify_items import classify_items                # noqa: E402
from plan_actions import plan_actions                    # noqa: E402
from apply_actions import apply_actions                  # noqa: E402
from export_reports import export_reports                # noqa: E402
from restore_from_journal import restore_from_journal    # noqa: E402


# ---- Helpers ----------------------------------------------------------------

def _now_run_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")


def _is_non_interactive() -> bool:
    """Return True if stdin is not a TTY (cron, CI, redirected input)."""
    try:
        return not sys.stdin.isatty()
    except Exception:
        return True


def _load_config(path: Path):
    """Load a JSON config file. Returns parsed object (dict or list)."""
    if not path.exists():
        raise SystemExit(f"Missing config file: {path}")
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def _make_outdir(outdir: Path) -> Path:
    outdir.mkdir(parents=True, exist_ok=True)
    return outdir


def _prompt_yes_no(question: str, default_no: bool = True) -> bool:
    """Ask a Y/N question on stdin. Returns True only on Y/y."""
    suffix = "[y/N]" if default_no else "[Y/n]"
    try:
        ans = input(f"{question} {suffix} ").strip()
    except EOFError:
        return False
    if not ans:
        return not default_no
    return ans.lower() in ("y", "yes")


# ---- Subcommand: run --------------------------------------------------------

def cmd_run(args) -> int:
    mode = args.mode
    outdir = Path(args.output_dir).expanduser().resolve()
    conf_dir = Path(args.config_dir).expanduser().resolve() if args.config_dir else CONF_DIR_DEFAULT
    rules_path = conf_dir / "classification.linux.json"
    paths_path = conf_dir / "paths_to_scan.linux.json"

    rules = _load_config(rules_path)
    paths_config = _load_config(paths_path)

    run_id = _now_run_id()
    _make_outdir(outdir)

    print(f"=== DiskInventory :: Mode = {mode} ===", flush=True)
    print(f"Output dir: {outdir}", flush=True)
    print(f"Tool dir:   {TOOL_DIR}", flush=True)

    # Step 1: Detect environment
    print("[1/5] Detecting environment...", flush=True)
    env = detect_environment(outdir, paths_config)
    print(f"       Drives: {len(env['Drives'])}  Profiles: {len(env['UserProfiles'])}"
          f"  Scan roots: {len(env['ScanRoots'])}", flush=True)

    # Step 2: Collect inventory
    if args.roots_override:
        roots = [str(r) for r in args.roots_override]
    else:
        roots = [str(r["Path"]) for r in env["ScanRoots"]]
    print(f"[2/5] Collecting inventory from {len(roots)} root(s)...", flush=True)

    size_cache = None
    if paths_config.get("sizeCacheEnabled", False):
        cache_file = outdir / paths_config.get("sizeCacheFileName", "size-cache.json")
        if cache_file.exists():
            try:
                with cache_file.open("r", encoding="utf-8") as f:
                    size_cache = json.load(f)
            except Exception:
                size_cache = None
        else:
            size_cache = {}

    def progress(c, p):
        print(f"       ... {c} items (current: {p})", flush=True)

    collected = collect_inventory(
        scan_roots=roots,
        config=paths_config,
        compute_hashes=args.compute_hashes,
        size_cache=size_cache,
        max_items=args.max_items,
        progress_sink=progress,
    )
    print(f"       Items: {collected['Stats']['TotalItems']}"
          f" (files: {collected['Stats']['FilesScanned']}"
          f", dirs: {collected['Stats']['DirsScanned']})"
          f"  Warnings: {len(collected['Warnings'])}"
          f"  Cache hits: {collected['Stats']['CacheHits']}",
          flush=True)

    # Persist size cache
    if paths_config.get("sizeCacheEnabled") and size_cache is not None:
        cache_file = outdir / paths_config.get("sizeCacheFileName", "size-cache.json")
        try:
            with cache_file.open("w", encoding="utf-8") as f:
                json.dump(size_cache, f, ensure_ascii=False)
        except Exception:
            pass

    # Step 3: Classify
    print("[3/5] Classifying items...", flush=True)
    classified = classify_items(collected["Items"], rules)
    by_cat = {}
    for item in classified:
        by_cat[item["Category"]] = by_cat.get(item["Category"], 0) + 1
    print("       Categories:", flush=True)
    for cat in sorted(by_cat, key=lambda k: -by_cat[k]):
        print(f"         - {cat:12s} {by_cat[cat]:8d}", flush=True)

    # Step 4: Plan
    print("[4/5] Planning actions...", flush=True)
    plan = plan_actions(
        classified=classified,
        rules=rules,
        config=paths_config,
        overrides_path=args.honor_overrides,
        run_id=run_id,
        output_dir=str(outdir),
        heavy_caches=env["HeavyCaches"],
    )
    by_act = {}
    for p in plan:
        by_act[p["Action"]] = by_act.get(p["Action"], 0) + 1
    print("       Planned actions:", flush=True)
    for act in sorted(by_act, key=lambda k: -by_act[k]):
        print(f"         - {act:12s} {by_act[act]:8d}", flush=True)

    # Step 5: Export reports (always)
    print("[5/5] Writing reports...", flush=True)
    paths = export_reports(
        classified=classified,
        plan=plan,
        environment=env,
        stats=collected["Stats"],
        warnings=collected["Warnings"],
        output_dir=str(outdir),
        report_prefix="inventory",
        mode=mode,
    )
    print(f"       CSV:      {paths['CsvPath']}", flush=True)
    print(f"       HTML:     {paths['HtmlPath']}", flush=True)
    print(f"       Markdown: {paths['MarkdownPath']}", flush=True)

    # Write plan.json for transparency
    try:
        with (outdir / "plan.json").open("w", encoding="utf-8") as f:
            json.dump(plan, f, ensure_ascii=False, separators=(",", ":"))
    except Exception:
        pass

    # Apply actions if Auto or DryRun
    non_interactive = _is_non_interactive()
    if mode == "auto":
        journal_path = outdir / "actions-journal.jsonl"
        if not args.yes and not non_interactive:
            print("", flush=True)
            print(f"About to APPLY {len(plan)} planned action(s).", flush=True)
            if not _prompt_yes_no("Type Y to proceed, anything else to abort.", default_no=True):
                print("Aborted. Reports already written to " + str(outdir), flush=True)
                return 2
        elif non_interactive and not args.yes:
            print("", flush=True)
            print("Non-interactive mode detected and --yes not set. Refusing to run auto without explicit --yes.",
                  flush=True)
            print(f"Reports have been written to {outdir}. Re-run with --yes to execute.", flush=True)
            return 2
        print("Applying actions...", flush=True)
        result = apply_actions(plan=plan, journal_path=str(journal_path), prompt=args.yes)
        print(f"       Applied: {result['Applied']}  Skipped: {result['Skipped']}  Errored: {result['Errored']}",
              flush=True)
        print(f"       Journal: {result['JournalPath']}", flush=True)
    elif mode == "dryrun":
        journal_path = outdir / "dryrun-journal.jsonl"
        print("DryRun: writing dryrun-journal...", flush=True)
        result = apply_actions(plan=plan, journal_path=str(journal_path), what_if=True)
        print(f"       Journal entries: {result['Applied'] + result['Skipped'] + result['Errored']} (no disk changes)",
              flush=True)
        print(f"       Journal: {result['JournalPath']}", flush=True)

    print("", flush=True)
    print(f"Done. Open {paths['HtmlPath']} in a browser to review.", flush=True)
    return 0


# ---- Subcommand: restore ----------------------------------------------------

def cmd_restore(args) -> int:
    journal = Path(args.journal).expanduser().resolve()
    if not journal.exists():
        print(f"Journal not found: {journal}", file=sys.stderr)
        return 2
    print(f"=== Restore from journal: {journal} ===", flush=True)
    sha1_cap = args.sha1_verify_max_mb * 1024 * 1024
    result = restore_from_journal(
        journal_path=str(journal),
        sha1_verify_max_bytes=sha1_cap,
        apply=args.apply,
        what_if_preview=(not args.apply),
    )
    print(f"Restored: {result['Restored']}  Skipped: {result['Skipped']}"
          f"  Errored: {result['Errored']}  Verified: {result['Verified']}"
          f"  Mismatched: {result['Mismatched']}", flush=True)
    return 0


# ---- Subcommand: purge ------------------------------------------------------

def cmd_purge(args) -> int:
    root_base = Path(args.output_dir).expanduser().resolve() if args.output_dir else TOOL_DIR / "out"
    quarantine = root_base / "_Quarantine"
    if not quarantine.exists():
        print(f"No _Quarantine at {quarantine}", flush=True)
        return 0
    cutoff = time.time() - (args.older_than_days * 86400)
    print(f"Purging quarantine subdirs older than {args.older_than_days} days...", flush=True)
    removed = 0
    for entry in quarantine.iterdir():
        if not entry.is_dir():
            continue
        try:
            mtime = entry.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            print(f"  Removing {entry}", flush=True)
            try:
                import shutil
                shutil.rmtree(entry)
                removed += 1
            except OSError:
                pass
    print(f"Purged {removed} quarantine run(s).", flush=True)
    return 0


# ---- Argument parser --------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="disk-inventory",
        description="Self-discovering disk inventory + cleanup tool (Linux/macOS).",
    )
    sub = parser.add_subparsers(dest="subcommand")

    # `run` subcommand (also the default when no subcommand is given)
    run = sub.add_parser("run", help="Scan + classify + export + (optionally) apply")
    run.add_argument("--mode", choices=("report", "dryrun", "auto"), default="report",
                     help="Operating mode: report (default, read-only), dryrun (writes journal), auto (executes).")
    run.add_argument("--output-dir", default=None,
                     help="Where reports + journal + quarantine land. Default: <script dir>/out/<runId>.")
    run.add_argument("--config-dir", default=None,
                     help="Override path to the config/ directory. Default: <script dir>/config.")
    run.add_argument("--roots-override", nargs="*", default=None,
                     help="Optional list of paths to scan INSTEAD of auto-detected ones.")
    run.add_argument("--max-items", type=int, default=0,
                     help="Optional cap on total items collected. 0 = unlimited.")
    run.add_argument("--compute-hashes", action="store_true",
                     help="If set, compute SHA-1 of every file (slow).")
    run.add_argument("--honor-overrides", default=None,
                     help="Path to an overrides.json (produced by the HTML report) to apply on top of rules.")
    run.add_argument("--yes", action="store_true",
                     help="Required for auto mode in non-interactive contexts, or to skip per-action prompts.")
    run.set_defaults(func=cmd_run)

    # `restore` subcommand
    restore = sub.add_parser("restore", help="Reverse a journal")
    restore.add_argument("journal", help="Path to actions-journal.jsonl")
    restore.add_argument("--apply", action="store_true",
                         help="Actually move files (default is --what-if preview).")
    restore.add_argument("--sha1-verify-max-mb", type=int, default=1024,
                         help="Max file size in MB for SHA-1 verification during restore (default 1024).")
    restore.set_defaults(func=cmd_restore)

    # `purge` subcommand
    purge = sub.add_parser("purge", help="Permanently delete _Quarantine/<runId> contents older than N days")
    purge.add_argument("--output-dir", default=None,
                       help="Where _Quarantine lives. Default: <script dir>/out.")
    purge.add_argument("--older-than-days", type=int, default=30,
                       help="Age threshold for purge (default 30). USE WITH CARE.")
    purge.set_defaults(func=cmd_purge)

    return parser


def main(argv=None) -> int:
    parser = build_parser()

    # If the first positional looks like a subcommand, parse normally.
    # Otherwise treat the whole argv as a `run` invocation (matches PS tool UX).
    raw = list(sys.argv[1:] if argv is None else argv)
    if not raw or raw[0] not in ("run", "restore", "purge", "-h", "--help"):
        raw = ["run"] + raw

    args = parser.parse_args(raw)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())