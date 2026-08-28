#!/usr/bin/env python3
"""
disk-inventory.py — DiskInventory v3.0 entry point.

Subcommands (v3 grammar):
    (none)    — first-run wizard (or scan, if a config already exists)
    scan      — report-only run + auto dashboard
    clean     — plan + dryrun + optional apply (one Y/N prompt)
    apply     — apply a previously-built plan
    restore   — reverse a journal produced by `clean`/`apply`
    purge     — delete old _Quarantine/<runId>/ directories
    serve     — start the web UI for an existing run
    setup     — re-run the setup wizard
    doctor    — diagnose environment + suggest fixes
    fleet     — SSH/coord coordinator + cross-host dedup
    migrate   — read v1.x run dir, emit v2 layout

Backward compatibility:
    The v2.0 verb set (`run`, `--mode ...`) is also accepted as a
    deprecated alias that forwards to the v3 verbs.
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

# v3.0: hard gate on Python version. Must come before any syntax-using
#        imports under this entry script's path-resolution.
from src import python_guard  # noqa: F401  (side-effect: sys.exit(2) if too old)
python_guard.check()

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


VERSION = "3.0.0"


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


# --- run ------------------------------------------------------------------

def cmd_run(args: argparse.Namespace) -> int:
    """Deprecated alias for `scan` or `clean` based on `--mode`.

    Kept for v2.0 backward compatibility. Always prints a deprecation
    hint before forwarding to the canonical v3 verb.
    """
    print(f"[deprecated] 'run' is v2.0; use 'scan' or 'clean' instead.")
    # Make sure downstream verbs have all the fields they expect.
    new_args = argparse.Namespace(**{**vars(args),
                                      "run_id": getattr(args, "run_id", None),
                                      "serve": getattr(args, "serve", False),
                                      "port": getattr(args, "port", 8765),
                                      "bind": getattr(args, "bind", "127.0.0.1"),
                                      "token": getattr(args, "token", None),
                                      "open": getattr(args, "open", False)})
    if args.mode == "report":
        return _do_scan(new_args)
    if args.mode == "dryrun":
        # dryrun-only: turn interactive prompt off and skip apply
        new_args.yes = True
        return _do_clean(new_args)
    # auto
    return _do_clean(new_args)


def _do_scan(args: argparse.Namespace) -> int:
    """v3 'scan' verb: report-only run + auto dashboard. No applies."""
    output_dir = Path(args.output_dir).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    run_id = output_dir.name + "-" + datetime.now(timezone.utc).strftime("%H%M%S")
    print(f"[scan] DiskInventory v{VERSION} — run {run_id}")
    print(f"[scan] output: {output_dir}")

    # 1. Detect environment
    print("[scan] 1/5 detect environment…")
    env = env_detect.detect()
    if getattr(args, "scan_root", None):
        env["ScanRoots"] = [{"Name": f"Custom{i}", "Path": p}
                            for i, p in enumerate(args.scan_root)]
        print(f"[scan]   scan-roots overridden: {args.scan_root}")
    if args.run_id:
        env["RunId"] = args.run_id
    else:
        env["RunId"] = run_id
    (output_dir / "environment.json").write_text(
        json.dumps(env, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    # 2. Collect
    print("[scan] 2/5 collect inventory…")
    progress_lock = threading.Lock()
    last_print = [0.0]
    def _collect_prog(done: int, total: int = 0) -> None:
        with progress_lock:
            now = time.time()
            if now - last_print[0] > 0.5:
                last_print[0] = now
                print(f"[scan]   collected {done:,} paths…", flush=True)
    rows = collect.collect(env, compute_hashes=args.compute_hashes,
                           progress=_collect_prog)
    print(f"[scan]   {len(rows):,} paths collected")

    # 3. Classify
    print(f"[scan] 3/5 classify {len(rows):,} items…")
    classify.classify_rows(rows, tool_dir=HERE)
    from pathlib import Path as _P
    for r in rows:
        if r.get("Kind") == "File":
            try:
                r["MIMEType"] = classify_content.sniff_mime(_P(r["Path"]))
            except Exception:
                r["MIMEType"] = "application/octet-stream"
    if args.compute_hashes:
        dup = classify_content.annotate_duplicate_groups(rows)
        print(f"[scan]   SHA-1 dedup: {dup} group(s)")
    if args.classify_exif:
        from src import dep_install
        ok = dep_install.ensure("Pillow", flag_name="classify-exif")
        n = classify_content.annotate_exif_dates(rows) if ok else 0
        print(f"[scan]   EXIF: {n} image(s)")
    if args.classify_cluster:
        c = classify_content.annotate_clusters(rows)
        print(f"[scan]   clusters: {c}")

    # 4. Plan
    print("[scan] 4/5 plan…")
    overrides_path = output_dir / "overrides.json"
    overrides = plan.load_overrides(overrides_path) if overrides_path.is_file() else []
    plan_doc = plan.build_plan(rows, run_id=env["RunId"], overrides=overrides)
    (output_dir / "plan.json").write_text(
        json.dumps(plan_doc, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    counts = plan_doc["Totals"]["byAction"]
    print(f"[scan]   {sum(counts.values()):,} items planned: {counts}")

    # 5. Export
    print("[scan] 5/5 export…")
    export.write_csv(rows, output_dir / "inventory.csv")
    export.write_markdown(env, rows, output_dir / "inventory.md")
    warnings = []
    if not env.get("Admin"):
        warnings.append("Running without admin rights — some system locations may be skipped.")
    export.write_html(env, rows, output_dir / "inventory.html", warnings=warnings)

    # Daemonize the dashboard if requested (v3 fix for #11e).
    if getattr(args, "serve", False):
        port = _spawn_dashboard(output_dir, port=getattr(args, "port", 8765),
                                bind=getattr(args, "bind", "127.0.0.1"),
                                token=getattr(args, "token", None),
                                do_open=getattr(args, "open", False))
        if port:
            print(f"[scan] dashboard: http://127.0.0.1:{port}/")

    print(f"[scan] done. outputs in {output_dir}")
    return 0


def _do_clean(args: argparse.Namespace) -> int:
    """v3 'clean' verb: plan + dryrun + optional apply w/ prompt."""
    print(f"[clean] DiskInventory v{VERSION}")
    rc = _do_scan(args)
    if rc != 0:
        return rc
    plan_path = Path(args.output_dir).resolve() / "plan.json"
    if not plan_path.is_file():
        print("[clean] no plan produced; aborting")
        return 1
    plan_doc = json.loads(plan_path.read_text(encoding="utf-8"))
    items = plan_doc.get("Items", [])

    # 1. dryrun journal
    dryrun_summary = apply.apply_plan(
        plan_doc,
        journal_path=Path(args.output_dir).resolve() / "dryrun-journal.jsonl",
        base_dir=Path(args.output_dir).resolve(),
        what_if=True,
    )
    print(f"[clean] dryrun: {dryrun_summary}")
    if not any(items):
        print("[clean] no actions planned; nothing to apply")
        return 0

    # 2. prompt using TTY-aware module (defect #3)
    if not args.yes:
        from src.prompt import yes_no, show_planned_actions, fallback_apply_command
        show_planned_actions(items)
        fallback = fallback_apply_command(
            plan_path=plan_path,
            journal_path=Path(args.output_dir).resolve() / "actions-journal.jsonl",
            cmd_name=sys.argv[0] or "disk-inventory",
        )
        if not yes_no("[clean] Apply these actions?", fallback_command=fallback):
            print("[clean] apply aborted.")
            return 1

    # 3. live apply with broadcast (defects #4, #11d)
    from src import errorlog as _EL
    el = _EL.ErrorLog()
    el.stage("apply")
    live_summary = apply.apply_plan(
        plan_doc,
        journal_path=Path(args.output_dir).resolve() / "actions-journal.jsonl",
        base_dir=Path(args.output_dir).resolve(),
        what_if=False,
        error_log=el,
    )
    print(f"[clean] applied: {live_summary}")
    el.flush_jsonl(Path(args.output_dir).resolve() / "warnings.jsonl")

    # 4. Auto-purge per config (if a wizard config exists)
    purge_cfg = _load_purge_days()
    if purge_cfg > 0:
        from src import purge as _purge  # not yet split into its own module
        # Use cmd_purge via subprocess would be heavier; mirror logic inline
        cutoff = time.time() - (purge_cfg * 86400)
        qbase = Path(args.output_dir).resolve() / "_Quarantine"
        if qbase.is_dir():
            removed = 0
            for child in sorted(qbase.iterdir()):
                try:
                    if child.is_dir() and child.stat().st_mtime < cutoff:
                        shutil.rmtree(child, ignore_errors=True)
                        removed += 1
                except OSError:
                    continue
            print(f"[clean] auto-purge: {removed} old quarantine dir(s) removed")
    return 0


def _load_purge_days() -> int:
    try:
        from src.wizard import load_config
        cfg = load_config()
        if cfg and cfg.auto_purge_days and cfg.auto_purge_days > 0:
            return int(cfg.auto_purge_days)
    except Exception:
        pass
    return 0


def _spawn_dashboard(output_dir: Path, *, port: int, bind: str,
                     token: str | None, do_open: bool) -> int:
    """Start the dashboard in a daemon thread. Returns the bound port, or
    0 on failure. Doesn't block the CLI thread (defect #11e)."""
    from src.serve import _State, build_server
    state = _State(output_dir)
    try:
        state.load_rows()
        env_p = output_dir / "environment.json"
        if env_p.is_file():
            state.env = json.loads(env_p.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[dashboard] preload error: {e}")
    srv = build_server(state, host=bind, port=port, token=token,
                        fallback_port_ring=5)
    if srv is None:
        print(f"[dashboard] could not bind {bind}:{port} or any port in ring")
        return 0
    actual = int(srv.server_address[1])
    t = threading.Thread(target=srv.serve_forever, daemon=True,
                         name="disk-inventory-dashboard")
    t.start()
    print(f"[dashboard] http://{bind}:{actual}/  (Ctrl+C to stop)")
    if do_open:
        try:
            webbrowser.open(f"http://{bind}:{actual}/")
        except Exception:
            pass
    return actual


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


# --- v3 verbs: setup / doctor / scan / clean / apply --------------------

def cmd_setup(args: argparse.Namespace) -> int:
    """Open the setup wizard. If --no-wizard, just print the current
    config (or 'no config' if none saved)."""
    from src.wizard import needs_wizard, load_config
    if getattr(args, "no_wizard", False):
        if needs_wizard():
            print("[setup] no config found")
            return 0
        cfg = load_config()
        print(json.dumps({"scan_roots": cfg.scan_roots,
                          "compute_hashes": cfg.compute_hashes,
                          "classify_cluster": cfg.classify_cluster,
                          "classify_exif": cfg.classify_exif,
                          "auto_purge_days": cfg.auto_purge_days},
                         ensure_ascii=False, indent=2))
        return 0
    # Default behavior: start the dashboard at /setup
    output_dir = Path(getattr(args, "output_dir", "./out/latest")).resolve()
    output_dir.mkdir(parents=True, exist_ok=True)
    port = _spawn_dashboard(output_dir, port=getattr(args, "port", 8765),
                            bind=getattr(args, "bind", "127.0.0.1"),
                            token=getattr(args, "token", None),
                            do_open=getattr(args, "open", True))
    if port:
        print(f"[setup] wizard: http://127.0.0.1:{port}/setup")
    try:
        # Block so the user can finish the wizard.
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("\n[setup] interrupted")
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    """Print a green/yellow/red diagnostic report on the environment."""
    from src import doctor
    return doctor.run()


def cmd_apply(args: argparse.Namespace) -> int:
    """Apply a previously-built plan without re-running the scan."""
    plan_path = Path(getattr(args, "plan", "")).resolve()
    if not plan_path.is_file():
        print(f"[apply] plan not found: {plan_path}", file=sys.stderr)
        return 2
    base = Path(getattr(args, "base_dir", plan_path.parent)).resolve()
    plan_doc = json.loads(plan_path.read_text(encoding="utf-8"))
    journal = Path(getattr(args, "journal", base / "actions-journal.jsonl"))
    from src.prompt import yes_no, show_planned_actions, fallback_apply_command
    items = plan_doc.get("Items", [])
    if not items:
        print("[apply] no actions in plan")
        return 0
    show_planned_actions(items)
    if not args.yes and not yes_no("[apply] Apply these actions?",
                                  fallback_command=fallback_apply_command(
                                      plan_path=plan_path, journal_path=journal,
                                      cmd_name=sys.argv[0] or "disk-inventory")):
        print("[apply] aborted")
        return 1
    from src import errorlog as _EL
    el = _EL.ErrorLog()
    el.stage("apply")
    summary = apply.apply_plan(plan_doc, journal_path=journal,
                                base_dir=base, what_if=False, error_log=el)
    print(f"[apply] {summary}")
    el.flush_jsonl(base / "warnings.jsonl")
    return 0 if summary["errors"] == 0 else 1


# --- argparse ------------------------------------------------------------

def _common_root_args(p_out, p_serv, p_apply) -> None:
    """Attach the common --scan-root / --output-dir / serve flags to a
    parser without copy-pasting the same 11 lines in three places."""
    p_out.add_argument("--output-dir", default="./out/latest")
    p_out.add_argument("--scan-root", action="append", default=None,
                       help="Override ScanRoots (repeatable). Path can be a "
                            "file or directory.")
    p_out.add_argument("--compute-hashes", action="store_true",
                       help="SHA-1 hash every file (slow; enables dedup)")
    p_out.add_argument("--classify-exif", action="store_true",
                       help="Extract EXIF date grouping (needs Pillow)")
    p_out.add_argument("--classify-cluster", action="store_true",
                       help="Name-similarity clustering")
    if p_serv:
        p_serv.add_argument("--serve", action="store_true")
        p_serv.add_argument("--port", type=int, default=8765)
        p_serv.add_argument("--bind", default="127.0.0.1")
        p_serv.add_argument("--token", default=None)
        p_serv.add_argument("--open", action="store_true")
    if p_apply:
        p_apply.add_argument("--yes", action="store_true",
                             help="Skip the apply prompt")


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="disk-inventory",
        description=("DiskInventory v3.0 — fully automatic, content-aware "
                     "disk-space inspector with smart defaults, first-run "
                     "wizard, and skip-and-warn failure handling."),
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    sub = p.add_subparsers(dest="cmd", required=False)

    # --- v3 verbs ---
    p_scan = sub.add_parser("scan", help="report-only scan + dashboard")
    _common_root_args(p_scan, p_scan, p_apply=None)
    p_scan.add_argument("--run-id", default=None)
    p_scan.set_defaults(func=_do_scan)

    p_clean = sub.add_parser("clean",
                              help="plan + dryrun + optional apply (one prompt)")
    _common_root_args(p_clean, p_clean, p_apply=p_clean)
    p_clean.set_defaults(func=_do_clean)

    p_apply = sub.add_parser("apply", help="apply a plan built by a previous run")
    p_apply.add_argument("--plan", required=False, default="./out/latest/plan.json")
    p_apply.add_argument("--base-dir", default=None)
    p_apply.add_argument("--journal", default="./out/latest/actions-journal.jsonl")
    p_apply.add_argument("--yes", action="store_true")
    p_apply.set_defaults(func=cmd_apply)

    p_setup = sub.add_parser("setup", help="re-run the first-run wizard")
    p_setup.add_argument("--port", type=int, default=8765)
    p_setup.add_argument("--bind", default="127.0.0.1")
    p_setup.add_argument("--token", default=None)
    p_setup.add_argument("--output-dir", default="./out/latest")
    p_setup.add_argument("--open", action="store_true", default=True)
    p_setup.add_argument("--no-wizard", action="store_true",
                          help="print the saved config and exit (no server)")
    p_setup.set_defaults(func=cmd_setup)

    p_doctor = sub.add_parser("doctor", help="diagnose environment")
    p_doctor.set_defaults(func=cmd_doctor)

    # --- non-rewritten v2 verbs ---
    p_restore = sub.add_parser("restore", help="reverse an actions-journal")
    p_restore.add_argument("journal", help="path to actions-journal.jsonl")
    p_restore.add_argument("--apply", action="store_true")
    p_restore.add_argument("--base-dir", default=None)
    p_restore.add_argument("--sha1-verify-max-mb", type=int, default=0)
    p_restore.set_defaults(func=cmd_restore)

    p_purge = sub.add_parser("purge", help="remove old _Quarantine/<runId>/ dirs")
    p_purge.add_argument("--older-than-days", type=int, default=30)
    p_purge.add_argument("--base-dir", default=None)
    p_purge.add_argument("--dry-run", action="store_true")
    p_purge.set_defaults(func=cmd_purge)

    p_serve = sub.add_parser("serve", help="start the dashboard for an existing run")
    p_serve.add_argument("run_dir")
    p_serve.add_argument("--port", type=int, default=8765)
    p_serve.add_argument("--bind", default="127.0.0.1")
    p_serve.add_argument("--token", default=None)
    p_serve.add_argument("--open", action="store_true")
    p_serve.set_defaults(func=cmd_serve)

    # --- deprecated v2 aliases ---
    # `run --mode <...>` forwards to `scan` or `clean`
    p_run = sub.add_parser("run",
                           help="[DEPRECATED] use 'scan' or 'clean' instead")
    p_run.add_argument("--mode", choices=["report", "dryrun", "auto"],
                       default="report")
    _common_root_args(p_run, p_run, p_apply=p_run)
    p_run.set_defaults(func=cmd_run)

    # --- unchanged verbs ---
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

    p_mig = sub.add_parser("migrate", help="read v1.x run dir, write v2 layout")
    p_mig.add_argument("src", help="v1.x run directory")
    p_mig.add_argument("--dst", required=True, help="v2 output directory")
    p_mig.set_defaults(func=cmd_migrate)

    return p


def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]

    # Backward-compat: bare --mode flag (v1.x usage) is forwarded to `run`.
    known_top = {"scan", "clean", "apply", "setup", "doctor",
                 "run", "restore", "purge", "serve", "fleet", "migrate",
                 "-h", "--help", "--version"}
    if argv and argv[0] not in known_top:
        if "--mode" in argv:
            print("[warn] legacy --mode flag detected; "
                  "please use `disk-inventory.py run --mode ...`")
            argv = ["run"] + argv

    # Empty argv → wizard (first run) or scan (subsequent runs).
    if not argv:
        from src.wizard import needs_wizard, load_config
        if needs_wizard():
            return cmd_setup(argparse.Namespace(
                port=8765, bind="127.0.0.1", token=None,
                output_dir="./out/latest", open=True, no_wizard=False,
            ))
        # Existing config: jump straight to scan against the saved roots.
        cfg = load_config()
        if cfg is None or not cfg.scan_roots:
            return cmd_setup(argparse.Namespace(
                port=8765, bind="127.0.0.1", token=None,
                output_dir="./out/latest", open=True, no_wizard=False,
            ))
        roots = [r["Path"] for r in cfg.scan_roots if r.get("Path")]
        return _do_scan(argparse.Namespace(
            output_dir="./out/latest",
            scan_root=roots,
            compute_hashes=cfg.compute_hashes,
            classify_exif=cfg.classify_exif,
            classify_cluster=cfg.classify_cluster,
            run_id=None, serve=True, port=8765, bind="127.0.0.1",
            token=None, open=True,
        ))

    p = build_parser()
    args = p.parse_args(argv)
    if not getattr(args, "cmd", None):
        # No subcommand: print help
        p.print_help()
        return 0
    if args.cmd == "fleet" and not getattr(args, "fleet_cmd", None):
        p.parse_args(["fleet", "--help"])
        return 0
    try:
        return int(args.func(args) or 0)
    except KeyboardInterrupt:
        print("\n[abort] interrupted")
        return 130


if __name__ == "__main__":
    sys.exit(main())
