# DiskInventory v3.0

A unified, content-aware disk-space inspector for Windows and Linux/macOS.

DiskInventory detects your environment, walks a curated list of scan
roots (smart defaults per OS), classifies every file (rule-based +
content signals: MIME, SHA-1 dedup, EXIF date grouping, name
clustering), produces a deterministic plan, applies it under your
supervision, and lets you watch the whole thing in a live browser
dashboard.

## What's new in v3.0

The v2.0 release (Nov 2025) shipped a working engine, but the
default flow was fragile: silent crashes from non-TTY `input()`
prompts, scanning `$HOME` whole-cloth, a dead "Pause" button,
launchers that closed silently, and a `disk-inventory` command
that did nothing when invoked without flags.

v3.0 fixes all of that and adds a one-command first-run wizard:

* **Zero-config first run.** `disk-inventory` (no args) opens a
  4-step setup wizard at `http://127.0.0.1:8765/setup`. The wizard
  writes `~/.diskinventory/config.json` so subsequent runs skip the
  wizard unless you re-invoke `disk-inventory setup`.
* **Smart scan roots.** Engine picks `Documents`, `Downloads`,
  `Desktop`, `Pictures`, `Videos`, `OneDrive` (Windows) instead of
  the full home profile. No more "stuck" first runs.
* **Three new verbs.** `scan` (report-only), `clean` (plan + optional
  apply w/ one Y/N prompt), `apply` (apply a saved plan). v2's
  `run --mode` still works as a deprecated alias.
* **Cross-shell fixes.** Pure-bash `disk-inventory.sh`, new
  `disk-inventory.ps1` with explicit UTF-8, fish-friendly
  `disk-inventory.fish`. All four honor `DISKINVENTORY_NOPAUSE=1`.
* **Skip-and-warn.** Every iteration in `collect`, `classify`, and
  `apply` is wrapped in `try/except`; per-file errors land in
  `warnings.jsonl` and surface in the dashboard tile count.
* **Working Pause.** The dashboard's "Pause apply" button now
  actually pauses — `apply_plan` checks `state.pause_flag.wait()`
  between items.
* **Live SSE.** `apply` pushes each journal entry through the
  state broadcast queue; the dashboard shows actions as they happen.
* **Doctor.** `disk-inventory doctor` prints a green/yellow/red
  report on Python version, free disk, permissions, optional deps,
  port 8765, and the default browser.
* **Daemonised dashboard.** `--serve` no longer holds the console
  hostage — the dashboard runs in a background thread.

## Install

```bash
# Linux / macOS
git clone https://github.com/Aminou973/diskinventory.git
cd diskinventory
./disk-inventory.sh --version
```

```powershell
# Windows (PowerShell 5.1+)
git clone https://github.com/Aminou973/diskinventory.git
cd diskinventory
.\disk-inventory.ps1 --version
# or
disk-inventory.bat --version
```

```fish
# fish
git clone https://github.com/Aminou973/diskinventory.git
cd diskinventory
./disk-inventory.fish --version
```

Requires **Python 3.8+** on `PATH`. No third-party packages
required. Optional extras (`Pillow`, `pywinrm`) are auto-installed
on demand by `disk-inventory doctor` or by the wizard when the
relevant feature is enabled.

## Quickstart

```bash
# 1. First run: wizard (saved to ~/.diskinventory/config.json)
disk-inventory

# 2. Snapshot (read-only)
disk-inventory scan --output-dir out/01-report

# 3. Plan + dry-run + apply (one Y/N prompt before any move)
disk-inventory clean --output-dir out/02-clean

# 4. Apply a previously-built plan
disk-inventory apply --yes

# 5. Restore if needed
disk-inventory restore out/02-clean/actions-journal.jsonl --apply

# 6. Purge old quarantine (default 30 days, configurable in wizard)
disk-inventory purge --older-than-days 30

# 7. Diagnose environment
disk-inventory doctor
```

If you already have a `~/.diskinventory/config.json`, plain
`disk-inventory` (no args) just runs a `scan` against the saved
roots. Re-run the wizard any time with `disk-inventory setup`.

## v3 subcommands

| Command | Purpose |
|---|---|
| `disk-inventory` | Wizard (first run), or scan against saved roots |
| `scan` | Report-only run; optional dashboard + auto-open |
| `clean` | Plan + dryrun + apply, with one TTY-aware Y/N prompt |
| `apply --plan FILE` | Apply a saved plan without re-running the scan |
| `setup` | Re-run the first-run wizard / print current config (`--no-wizard`) |
| `doctor` | Print green/yellow/red diagnostic report |
| `restore <journal>` | Reverse an actions journal (v1.x / v2 compatible) |
| `purge` | Delete old `_Quarantine/<runId>/` dirs |
| `serve <run_dir>` | Start the dashboard for an existing run |
| `run --mode ...` | **[DEPRECATED]** aliases `scan` (mode=report) or `clean` (mode=dryrun/auto) |
| `fleet scan --hosts FILE` | Multi-host SSH coordinator |
| `fleet dedup <fleet_dir>` | Cross-host SHA-1 dedup report |
| `migrate <v1-dir> --dst <v2-dir>` | Re-emit a v1.x run dir in v2 layout |

## Outputs

For every run, `out/<runDir>/` contains:

| File | Description |
|---|---|
| `environment.json` | Drives, profiles, heavy caches, scan roots |
| `inventory.csv` | One row per file/dir, 22 columns (18 v1.x + 4 v2 additions) |
| `inventory.md` | Markdown summary |
| `inventory.html` | Offline single-file report with override UI |
| `plan.json` | Deterministic plan (per-path action + destination) |
| `dryrun-journal.jsonl` | What `clean` *would* do |
| `actions-journal.jsonl` | What `clean`/`apply` actually did |
| `warnings.jsonl` | Per-step skip-and-warn entries (v3) |

## Backward compatibility

The journal format, `environment.json`, CSV column order, and
`overrides.json` are unchanged from v1.1.0 / v2.0. v2.0 added 4
columns to the right of the CSV header (`MIMEType`,
`DuplicateGroup`, `ExifDate`, `ClusterId`); v1.1.0 readers skip
unknown columns.

To re-emit an old v1.1.0 run dir under the v2 layout:

```bash
disk-inventory migrate old-run-dir --dst new-run-dir
```

To serve the dashboard over a v1.1.0 run dir directly:

```bash
disk-inventory serve old-run-dir
```

## Dashboard

When `--serve` is passed (or the wizard's `--open` is on), a
single-page dashboard runs at `http://127.0.0.1:8765`:

* Live tiles (items, categories, total bytes, applied, errors, warnings)
* Categories table
* Top-N by size (paginated, filterable)
* Live journal tail via Server-Sent Events
* **Pause / resume controls** (now actually work during apply)

Bind defaults to `127.0.0.1`. To expose on the LAN, pass
`--bind 0.0.0.0 --token <secret>` and call clients with
`Authorization: Bearer <secret>`.

The wizard lives at `/setup` on the same port — open it in any
browser to change scan roots, optional features, or purge schedule.

### Live dashboard

![Live dashboard](docs/screenshots/01-dashboard.svg)

### Fleet dedup

![Fleet dedup report](docs/screenshots/03-fleet-dedup.svg)

### Offline override UI (`inventory.html`)

![Override UI](docs/screenshots/04-override-ui.svg)

> To regenerate real PNGs from a running run, install Playwright and run
> `python spec/build_screenshots.py`. See
> [docs/SCREENSHOTS.md](docs/SCREENSHOTS.md).

## Fleet mode

`hosts.txt` format (one per line, `#` comments):

```
user@laptop.local
admin@fileserver.local ansible_user=root
workstation port=2222 os=linux
```

```bash
disk-inventory fleet scan --hosts hosts.txt --output-dir fleet-out --compute-hashes
disk-inventory fleet dedup fleet-out --top 100 --serve
```

A central SQLite store (`fleet.db`) tracks hosts, runs, items, and
SHA-1 indices for cross-host dedup. (Note: v3 still expects each
remote host to have the `diskinventory` source on PATH; v3.1 will
relax that.)

## Layout

```
disk-inventory.py        # entry point
disk-inventory.sh        # POSIX launcher
disk-inventory.bat       # Windows cmd launcher
disk-inventory.ps1       # Windows PowerShell launcher  (NEW in v3)
disk-inventory.fish      # fish launcher              (NEW in v3)
src/                     # Python modules
spec/                    # JSON schemas + golden fixtures
config/                  # base + overlay classification
tests/                   # smoke + compat + golden
```

## License

MIT.
