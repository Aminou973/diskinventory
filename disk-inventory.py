#!/usr/bin/env python3
"""
disk-inventory.py — DiskInventory v2.0 entry point.

Subcommands:
    run      — detect → collect → classify → plan → (apply) → export
    restore  — reverse a journal produced by `run`
    purge    — delete old _Quarantine/<runId>/ directories
    serve    — start the local web UI for an existing run
    fleet    — SSH/coord coordinator + cross-host dedup
    migrate  — read v1.x run dir, emit v2 layout

Backward compatibility:
    The legacy v1.1.0 `--mode` flag is also accepted (prints a deprecation
    hint, then forwards to `run --mode ...`).
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import threading
import time
import webbrowser
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "src"
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from src import env_detect          # noqa: E402
from src import collect             # noqa: E402
from src import classify            # noqa: E402
from src import classify_content    # noqa: E402
from src import plan                # noqa: E402
from src import apply               # noqa: E402
from src import restore             # noqa: E402
from src import export              # noqa: E402
from src import notify              # noqa: E402
from src import serve as serve_mod   # noqa: E402
from src import fleet               # noqa: E402
from src import migrate             # noqa: E402


VERSION = "2.0.0"


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- run ------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = output_dir.name + "-" + datetime.now(timezone.utc).strftime("%H%M%S")

    print(f"[run] DiskInventory v{VERSION} — run {run_id} (mode={args.mode})")
    print(f"[run] output: {output_dir}")

    # 1. Detect environment
    print("[run] 1/5 detect environment…")
    env = env_detect.detect()
    # Use the user-provided RunId if any
    if args.run_id:
        env["RunId"] = args.run_id
    else:
        env["RunId"] = run_id
    (output_dir / "environment.json").write_text(
        json.dumps(env, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    if args.notify_webhook:
        notify.notify_run_started(run_id=env["RunId"], mode=args.mode,
                                  webhook=args.notify_webhook,
                                  webhook_token=args.notify_webhook_token)

    # 2. Collect
    print("[run] 2/5 collect inventory…")
    progress_lock = threading.Lock()
    last_print = [0.0]
    def _collect_prog(done: int, total: int) -> None:
        with progress_lock:
            now = time.time()
            if now - last_print[0] > 0.5:
                last_print[0] = now
                print(f"[run]   collected {done:,} paths…", flush=True)
    rows = collect.collect(env, compute_hashes=args.compute_hashes,
                           progress=_collect_prog)

    # 3. Classify
    print(f"[run] 3/5 classify {len(rows):,} items…")
    classify.classify_rows(rows, tool_dir=HERE)
    # MIME sniffing (default-on, no deps)
    mime_count = 0
    for r in rows:
        if r.get("Kind") == "File":
            try:
                from pathlib import Path as _P
                r["MIMEType"] = classify_content.sniff_mime(_P(r["Path"]))
                mime_count += 1
            except Exception:
                r["MIMEType"] = "application/octet-stream"
    # Optional content signals
    if args.compute_hashes:
        print(f"[run]   SHA-1 dedup…")
        dup = classify_content.annotate_duplicate_groups(rows)
        print(f"[run]   {dup} duplicate group(s) found")
    if args.classify_exif:
        print(f"[run]   EXIF date grouping…")
        n = classify_content.annotate_exif_dates(rows)
        print(f"[run]   {n} image(s) annotated with EXIF dates")
    if args.classify_cluster:
        print(f"[run]   name-similarity clustering…")
        c = classify_content.annotate_clusters(rows)
        print(f"[run]   {c} cluster(s) found")

    # 4. Plan
    print("[run] 4/5 plan actions…")
    overrides_path = output_dir / "overrides.json"
    overrides = plan.load_overrides(overrides_path) if overrides_path.is_file() else []
    plan_doc = plan.build_plan(rows, run_id=env["RunId"], overrides=overrides)
    (output_dir / "plan.json").write_text(
        json.dumps(plan_doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    counts = plan_doc["Totals"]["byAction"]
    print(f"[run]   {sum(counts.values()):,} items planned: {counts}")

    # 5. Export CSV/MD/HTML always; Apply only in dryrun/auto
    print("[run] 5/5 export…")
    export.write_csv(rows, output_dir / "inventory.csv")
    export.write_markdown(env, rows, output_dir / "inventory.md")
    warnings = []
    if not env.get("Admin"):
        warnings.append("Running without admin rights — some system locations may be skipped.")
    export.write_html(env, rows, output_dir / "inventory.html", warnings=warnings)

    summary: dict = {"applied": 0, "errors": 0, "skipped": 0}
    if args.mode in ("dryrun", "auto"):
        # Always write a dryrun journal
        dryrun_summary = apply.apply_plan(plan_doc, journal_path=output_dir / "dryrun-journal.jsonl",
                                          base_dir=output_dir, what_if=True)
        print(f"[run]   dryrun journal: {dryrun_summary}")
    if args.mode == "auto":
        if not args.yes:
            resp = input("[run] Apply? Type 'yes' to proceed: ").strip().lower()
            if resp != "yes":
                print("[run] Apply aborted.")
                return 1
        live_summary = apply.apply_plan(plan_doc, journal_path=output_dir / "actions-journal.jsonl",
                                        base_dir=output_dir, what_if=False)
        summary.update(live_summary)
        print(f"[run]   applied: {live_summary}")

    # Optional: start the dashboard
    if args.serve:
        from src.serve import _State
        state = _State(output_dir)
        try:
            state.serve_thread = None
            state.load_rows()
            if (output_dir / "environment.json").is_file():
                state.env = json.loads((output_dir / "environment.json").read_text(encoding="utf-8"))
        except Exception as e:
            print(f"[run] dashboard preload error: {e}")
        port = args.port
        host = args.bind
        token = args.token
        url = f"http://{host}:{port}/"
        print(f"[run] dashboard: {url}")
        if args.open:
            try:
                webbrowser.open(url)
            except Exception:
                pass
        # Run server in foreground until Ctrl+C
        serve_mod.serve_forever(state, host=host, port=port, token=token)

    if args.notify_webhook:
        notify.notify_run_finished(run_id=env["RunId"], mode=args.mode,
                                   totals=counts,
                                   webhook=args.notify_webhook,
                                   webhook_token=args.notify_webhook_token)
    print(f"[run] done. outputs in {output_dir}")
    return 0


# --- restore --------------------------------------------------------------

def cmd_restore(args: argparse.Namespace) -> int:
    journal_path = Path(args.journal).resolve()
    if not journal_path.is_file():
        print(f"[restore] journal not found: {journal_path}", file=sys.stderr)
        return 2
    base = Path(args.base_dir).resolve() if args.base_dir else journal_path.parent.parent
    print(f"[restore] journal: {journal_path}")
    print(f"[restore] base dir: {base}")
    print(f"[restore] mode: {'APPLY' if args.apply else 'dry-run'}")
    summary = restore.restore(journal_path, apply=args.apply, base_dir=base,
                              sha1_verify_max_mb=args.sha1_verify_max_mb or 0)
    print(f"[restore] {summary}")
    if not args.apply:
        print("[restore] pass --apply to actually move files back")
    return 0 if summary["errors"] == 0 else 1


# --- purge ---------------------------------------------------------------

def cmd_purge(args: argparse.Namespace) -> int:
    base = Path(args.base_dir).resolve() if args.base_dir else Path.cwd() / "_Quarantine"
    if not base.is_dir():
        print(f"[purge] no quarantine dir: {base}")
        return 0
    cutoff = time.time() - (args.older_than_days * 86400)
    removed = 0
    for child in sorted(base.iterdir()):
        if not child.is_dir():
            continue
        try:
            mtime = child.stat().st_mtime
        except OSError:
            continue
        if mtime < cutoff:
            if args.dry_run:
                print(f"[purge] WOULD remove: {child}")
            else:
                shutil.rmtree(child, ignore_errors=True)
                print(f"[purge] removed: {child}")
            removed += 1
    print(f"[purge] {removed} director{'y' if removed == 1 else 'ies'} {'would be' if args.dry_run else ''} removed")
    return 0


# --- serve (existing run dir) --------------------------------------------

def cmd_serve(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).resolve()
    if not run_dir.is_dir():
        print(f"[serve] not a directory: {run_dir}", file=sys.stderr)
        return 2
    state = serve_mod._State(run_dir)
    env_p = run_dir / "environment.json"
    if env_p.is_file():
        try:
            state.env = json.loads(env_p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    inv = run_dir / "inventory.csv"
    if inv.is_file():
        import csv as _csv
        with open(inv, "r", encoding="utf-8", newline="") as fh:
            state.rows = list(_csv.DictReader(fh))
    plan_p = run_dir / "plan.json"
    if plan_p.is_file():
        try:
            state.plan = json.loads(plan_p.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    url = f"http://{args.bind}:{args.port}/"
    print(f"[serve] {url}  (run dir: {run_dir})")
    if args.open:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    serve_mod.serve_forever(state, host=args.bind, port=args.port, token=args.token)
    return 0


# --- fleet ---------------------------------------------------------------

def cmd_fleet_scan(args: argparse.Namespace) -> int:
    hosts = fleet.parse_hosts(Path(args.hosts))
    if not hosts:
        print(f"[fleet] no hosts parsed from {args.hosts}", file=sys.stderr)
        return 1
    output_dir = Path(args.output_dir).resolve()
    ssh_key = Path(args.ssh_key).resolve() if args.ssh_key else None
    print(f"[fleet] scanning {len(hosts)} host(s)…")
    summary = fleet.scan_hosts(hosts, output_dir=output_dir, ssh_key=ssh_key,
                               compute_hashes=args.compute_hashes,
                               parallel=args.parallel)
    print(f"[fleet] scanned: {len(summary['scanned'])}, "
          f"errors: {len(summary['errors'])}")
    return 0 if not summary["errors"] else 1


def cmd_fleet_dedup(args: argparse.Namespace) -> int:
    fleet_dir = Path(args.fleet_dir).resolve()
    if not fleet_dir.is_dir():
        print(f"[fleet] not a directory: {fleet_dir}", file=sys.stderr)
        return 2
    summary = fleet.cross_host_dedup(fleet_dir, top=args.top)
    print(f"[fleet] dedup: {len(summary['items'])} group(s) "
          f"written to {fleet_dir}/fleet-dedup.html")
    if args.serve:
        return cmd_serve(argparse.Namespace(
            run_dir=str(fleet_dir),
            bind=args.bind, port=args.port, token=args.token, open=args.open,
        ))
    return 0


# --- migrate -------------------------------------------------------------

def cmd_migrate(args: argparse.Namespace) -> int:
    src = Path(args.src).resolve()
    dst = Path(args.dst).resolve()
    if not src.is_dir():
        print(f"[migrate] not a directory: {src}", file=sys.stderr)
        return 2
    summary = migrate.migrate_v1_to_v2(src, dst)
    print(f"[migrate] {summary}")
    return 0


# --- argparse ------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="disk-inventory",
        description="DiskInventory v2.0 — unify, classify, dedup, dashboard, fleet.",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sub = p.add_subparsers(dest="cmd", required=False)

    # run
    p_run = sub.add_parser("run", help="detect → collect → classify → plan → apply/export")
    p_run.add_argument("--mode", choices=["report", "dryrun", "auto"], default="report")
    p_run.add_argument("--output-dir", default="./out/latest")
    p_run.add_argument("--compute-hashes", action="store_true",
                       help="SHA-1 hash every file (slow; enables dedup)")
    p_run.add_argument("--classify-exif", action="store_true",
                       help="Extract EXIF date grouping (needs Pillow)")
    p_run.add_argument("--classify-cluster", action="store_true",
                       help="Name-similarity clustering (O(n²), capped per parent)")
    p_run.add_argument("--yes", action="store_true", help="Skip the apply prompt")
    p_run.add_argument("--run-id", default=None, help="Override the RunId")
    p_run.add_argument("--serve", action="store_true",
                       help="Start the web UI on :8765 after this run")
    p_run.add_argument("--port", type=int, default=8765)
    p_run.add_argument("--bind", default="127.0.0.1")
    p_run.add_argument("--token", default=None,
                       help="Bearer token required when bind != 127.0.0.1")
    p_run.add_argument("--open", action="store_true",
                       help="Open the dashboard URL in the default browser")
    p_run.add_argument("--notify-webhook", default=None, help="POST events to URL")
    p_run.add_argument("--notify-webhook-token", default=None)
    p_run.set_defaults(func=cmd_run)

    # restore
    p_restore = sub.add_parser("restore", help="reverse an actions-journal")
    p_restore.add_argument("journal", help="path to actions-journal.jsonl")
    p_restore.add_argument("--apply", action="store_true")
    p_restore.add_argument("--base-dir", default=None)
    p_restore.add_argument("--sha1-verify-max-mb", type=int, default=0)
    p_restore.set_defaults(func=cmd_restore)

    # purge
    p_purge = sub.add_parser("purge", help="remove old _Quarantine/<runId>/ dirs")
    p_purge.add_argument("--older-than-days", type=int, default=30)
    p_purge.add_argument("--base-dir", default=None)
    p_purge.add_argument("--dry-run", action="store_true")
    p_purge.set_defaults(func=cmd_purge)

    # serve
    p_serve = sub.add_parser("serve", help="start the dashboard for an existing run")
    p_serve.add_argument("run_dir")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument("--bind", default="127.0.0.1")
    p_serve.add_argument("--token", default=None)
    p_serve.add_argument("--open", action="store_true")
    p_serve.set_defaults(func=cmd_serve)

    # fleet
    p_fleet = sub.add_parser("fleet", help="multi-host scan + dedup")
    sub_fleet = p_fleet.add_subparsers(dest="fleet_cmd", required=True)

    p_fs = sub_fleet.add_parser("scan")
    p_fs.add_argument("--hosts", required=True)
    p_fs.add_argument("--output-dir", default="./fleet-out")
    p_fs.add_argument("--ssh-key", default=None)
    p_fs.add_argument("--compute-hashes", action="store_true")
    p_fs.add_argument("--parallel", type=int, default=4)
    p_fs.set_defaults(func=cmd_fleet_scan)

    p_fd = sub_fleet.add_parser("dedup")
    p_fd.add_argument("fleet_dir")
    p_fd.add_argument("--top", type=int, default=50)
    p_fd.add_argument("--serve", action="store_true")
    p_fd.add_argument("--port", type=int, default=8765)
    p_fd.add_argument("--bind", default="127.0.0.1")
    p_fd.add_argument("--token", default=None)
    p_fd.add_argument("--open", action="store_true")
    p_fd.set_defaults(func=cmd_fleet_dedup)

    # migrate
    p_mig = sub.add_parser("migrate", help="read v1.x run dir, write v2 layout")
    p_mig.add_argument("src", help="v1.x run directory")
    p_mig.add_argument("--dst", required=True, help="v2 output directory")
    p_mig.set_defaults(func=cmd_migrate)

    return p


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]
    # Backward-compat: legacy `--mode` flag is accepted and forwarded to `run`
    legacy_mode = None
    if argv and not argv[0] in {"run", "restore", "purge", "serve", "fleet",
                                "migrate", "-h", "--help", "--version"}:
        if "--mode" in argv:
            legacy_mode = argv[argv.index("--mode") + 1] if argv.index("--mode") + 1 < len(argv) else "report"
            print("[warn] legacy --mode flag detected; please use `disk-inventory.py run --mode ...`")
            argv = ["run"] + argv
    p = build_parser()
    args = p.parse_args(argv)
    if not getattr(args, "cmd", None):
        # No subcommand: print help
        p.print_help()
        return 0
    if args.cmd == "fleet" and not getattr(args, "fleet_cmd", None):
        # `fleet` with no subcommand: print fleet help
        p.parse_args(["fleet", "--help"])
        return 0
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\n[abort] interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
