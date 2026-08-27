# DiskInventory — Linux / macOS

A portable, self-discovering **disk inventory + cleanup** tool.
Same engine as the Windows version, rewritten in **Python 3 (stdlib only)**.
Auto-detects drives, user profiles, heavy caches, locale, and admin status
at runtime — **nothing hard-coded**.

## Requirements

- **Python 3.8 or newer**. Already installed on every modern Linux distro and
  on macOS (verify with `python3 --version`).
- No third-party packages. No network. No install.

## Quick start

```bash
# 1. Download DiskInventory-Linux-vX.Y.Z.zip from the GitHub release page.
# 2. Unzip anywhere.
unzip DiskInventory-Linux-vX.Y.Z.zip -d ~/tools/diskinventory
cd ~/tools/diskinventory

# 3. Make the launcher executable (one-time).
chmod +x disk-inventory.sh disk-inventory.py

# 4. First run (safe, read-only).
./disk-inventory.sh --mode report --output-dir out/01-report

# 5. Open the report in a browser.
xdg-open out/01-report/inventory.html     # Linux
open     out/01-report/inventory.html     # macOS
```

The report header shows what the tool detected about your machine: drives,
profiles, locale, heavy caches. Triage `Junk` / `Archive` categories, then
move on.

## Operating modes

| Mode | What it does | What it writes | Disk changes |
|---|---|---|---|
| `report` *(default)* | Scan + classify + write CSV/HTML/MD | `inventory.csv`, `inventory.html`, `inventory.md`, `environment.json`, `plan.json` | **None.** |
| `dryrun` | Everything `report` does, plus a journal of every action it *would* take | Same as report, plus `dryrun-journal.jsonl` | **None.** |
| `auto` | Everything `dryrun` does, then executes | Same as dryrun, plus `actions-journal.jsonl` + `_Quarantine/<runId>/` | **Only inside `_Quarantine/`.** Reversible. |

## Subcommands

```bash
# Reverse a journal (preview first, then apply).
./disk-inventory.sh restore out/03-auto/actions-journal.jsonl           # what-if preview
./disk-inventory.sh restore out/03-auto/actions-journal.jsonl --apply   # actually move back

# Permanently delete _Quarantine/<runId> contents older than N days.
./disk-inventory.sh purge --older-than-days 30
```

## Common flags

| Flag | Default | What it does |
|---|---|---|
| `--mode report\|dryrun\|auto` | `report` | Selects operating mode. |
| `--output-dir <path>` | `<script dir>/out/<runId>` | Where reports / journal / quarantine land. |
| `--config-dir <path>` | `<script dir>/config` | Override config dir. |
| `--roots-override <paths>` | (auto-detected) | Scan only these paths instead of all auto-detected ones. Useful for testing. |
| `--max-items <N>` | `0` (unlimited) | Cap on total items collected. |
| `--compute-hashes` | off | Compute SHA-1 of every file (slow on big trees). |
| `--honor-overrides <file>` | none | Path to an `overrides.json` from a prior HTML report. |
| `--yes` | off | Required for `auto` in non-interactive contexts (cron, CI), and to skip per-action prompts. |

## Safety guarantees

- **No permanent delete**. Every destructive action is `Move-Item`-equivalent:
  the file lands in `_Quarantine/<runId>/`. Reversible via `restore`.
- **Categories `App`, `System`, `Project`, `Data`, `HeavyCache` are force-`keep`**
  even in `auto` mode unless an explicit per-item override exists.
- **Heavy-cache paths** are flagged in the report and never auto-quarantined.
- **Exclude-glob re-check** at plan time: any path matching `excludeGlobs`
  is force-`keep`, even if a rule says otherwise.
- **Auto-mode without `--yes` asks once** before applying. Non-interactive
  Auto without `--yes` is **refused** (exit code 2).
- **Round-trip SHA-1 integrity**: every moved file's SHA-1 is logged at apply
  time and verified at restore time.

## Configuration

Two editable JSON files in `config/`:

- **`classification.linux.json`** — categories (System, App, Project,
  HeavyCache, Archive, Junk, Data, Unknown), their match rules, default
  actions, safety-net settings.
- **`paths_to_scan.linux.json`** — scan-root probes, exclude globs, exclude
  file names, exclude extensions, size-cache settings, output-dir names.

Both files are **probes**, not absolute paths — every entry is tested for
existence before being added to the scan. To override locally without
touching the shipped defaults, drop `Rules.local.json` and
`PathsToScan.local.json` in `config/` (the `.gitignore` already excludes
them — see the Windows README for the convention).

## Cross-platform note

The Windows version of DiskInventory is a PowerShell 5.1 tool. Both versions
share the **same JSON-Lines journal format** — a journal produced by the
Linux port can be restored by the Windows port (`Invoke-Inventory.ps1
-Restore <journal> -Apply`) and vice versa, as long as paths are accessible
from the machine doing the restore.

## Limitations on Linux (vs. Windows)

- **No OneDrive-placeholder detection**. The Linux OneDrive client serves
  files normally; the `IsOneDrivePlaceholder` column is always `false`.
- **No UWP/AppX app inventory** (Windows concept; replaced on Linux by
  Flatpak and Snap caches, which are auto-detected and surfaced as
  HeavyCaches).
- **No thumbcache detection** in `~/.cache/thumbnails` (replaces
  `AppData\Local\Microsoft\Windows\Explorer`).
- **No `FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS`** analog.

## License

See the repo.