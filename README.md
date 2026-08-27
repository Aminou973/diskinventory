# DiskInventory

A portable, self-discovering disk-inventory + cleanup tool.

- **Windows**: PowerShell 5.1 (this folder). See [`Run.bat`](Run.bat) and
  [`Invoke-Inventory.ps1`](Invoke-Inventory.ps1).
- **Linux / macOS**: Python 3 (see [`README-LINUX.md`](README-LINUX.md)). Same
  engine, same journal format, same HTML report layout.

Both versions share the **same JSON-Lines journal format** — a journal produced
on one platform can be restored on the other.

The tool **auto-detects** its environment — no machine-specific paths are
hard-coded. Copy the right folder (Windows or Linux) to any box and run it.

## What it does

## What it does

- Discovers fixed drives, user profiles, OneDrive presence, heavy caches (UWP `LocalCache`,
  OneDrive, ollama, Python, `WinSxS`, `Windows\Installer`, etc.) at runtime.
- Walks every scan root, full recursion, with smart excludes (node_modules, venv,
  `__pycache__`, `WinSxS`, `dist`/`build`/`out` outside projects, …).
- Classifies every item into one of: `App`, `Project`, `Archive`, `Junk`, `HeavyCache`,
  `Data`, `System`, `Unknown`.
- Suggests an action per item: `keep`, `group`, `archive`, `quarantine`, `delete`.
  Only `Junk` ever defaults to `quarantine`; everything else defaults to `keep` unless
  you supply an override.
- Writes three reports from the same in-memory model: CSV (Excel-friendly), HTML
  (offline, filterable, with override UI), Markdown.
- Optional `Auto` mode: soft-deletes by moving items to `_Quarantine\<runId>\`, never
  permanent. Every action is journaled with SHA-1 of the moved file. Reversible until
  you run `-PurgeQuarantine`.

## Requirements

- Windows PowerShell 5.1 (in-box on Windows 10/11). No installation.
- No admin required for user-profile paths. Admin only if you want to scan
  `C:\Program Files` and `C:\Windows`.

## Layout

```
DiskInventory\
  Invoke-Inventory.ps1              # the only script you run
  src\
    Detect-Environment.ps1          # runtime env snapshot
    Collect-Inventory.ps1            # filesystem walk
    Classify-Items.ps1               # rule-based classifier
    Plan-Actions.ps1                 # action planning + overrides
    Apply-Actions.ps1                # the ONLY mutating module
    Export-Reports.ps1               # CSV + HTML + Markdown
    Restore-FromJournal.ps1          # undo from journal
  config\
    ClassificationRules.json         # editable classifier rules
    PathsToScan.json                 # editable probes + excludes
  out\                                # reports + journals + _Quarantine
  README.md                           # this file
```

## Quick start

```powershell
# 1. Inventory only (read-only, recommended first step)
.\Invoke-Inventory.ps1 -Mode Report

# 2. Dry-run with proposed actions (still read-only, writes a journal of intent)
.\Invoke-Inventory.ps1 -Mode DryRun

# 3. Open the HTML, review the action plan, click "Override" on anything you want kept.
#    The page downloads `overrides.json`.

# 4. Execute, honoring your overrides (asks for confirmation unless -Confirm is set)
.\Invoke-Inventory.ps1 -Mode Auto -HonorOverrides .\out\<runId>\overrides.json

# 5. If you change your mind, restore from the journal (default: preview only)
.\Invoke-Inventory.ps1 -Restore .\out\<runId>\actions-journal.jsonl          # what-if
.\Invoke-Inventory.ps1 -Restore .\out\<runId>\actions-journal.jsonl -Apply   # actually undo

# 6. 30+ days later, permanently purge the quarantine
.\Invoke-Inventory.ps1 -PurgeQuarantine -OlderThanDays 30
```

## Modes

| Mode | Scans | Writes reports | Writes journal | Mutates disk? |
|---|---|---|---|---|
| `Report` *(default)* | yes | yes | no | **no** |
| `DryRun` | yes | yes | yes (intent only) | **no** |
| `Auto` | yes | yes | yes (applied) | **yes — soft-delete to `_Quarantine` only** |

## Common parameters

| Parameter | Default | Description |
|---|---|---|
| `-OutputDir` | `<tool>\out\<runId>` | Where reports + journal + quarantine land |
| `-ConfigDir` | `<tool>\config` | Where to read the two JSON config files |
| `-RootsOverride` | *(auto)* | Scan only these paths. Skips auto-detection. |
| `-MaxItems` | 0 (unlimited) | Cap on total items; useful for a small test run |
| `-ComputeHashes` | off | Compute SHA-1 for every file (slow; needed for round-trip verification) |
| `-HonorOverrides` | *(none)* | Path to `overrides.json` to apply on top of rules |
| `-Confirm` | off | Prompt before each action in `Auto` mode |

## Subcommands

### `-Restore <journalPath>`

Reverses actions from a journal. Default is `-WhatIf` (preview only). Pass `-Apply` to
actually restore files. Verifies SHA-1 against the journal entry for files smaller than
`-Sha1VerifyMaxMB` (default 1024 MB).

### `-PurgeQuarantine [-OlderThanDays N]`

Permanently deletes `_Quarantine\<runId>\` subdirectories whose `LastWriteTime` is older
than N days. **This is the only way to lose data permanently.** Default N = 30.

## Configuration

### `config/ClassificationRules.json`

The heart of the tool. Edit to retune the classifier without touching code.

- `categories[]` — array of `{ name, action, match: { pathContains, markerFiles, nameLike, nameEquals, pathLike } }`. First match wins.
- `junk.filePatterns` / `junk.folderPatterns` — quick lists of well-known junk filenames.
- `archive.folderPatterns` / `archive.fileExtensions` — what counts as "archive".
- `archive.olderThanDays` — only flag a folder as `Archive` if last modified longer ago than this (default 180).
- `defaultCategory` / `defaultAction` — what to assign when nothing matches.
- `deleteAllowedCategories` — safety list; the tool will *never* default to `delete` for items outside this list.

### `config/PathsToScan.json`

- `scanRoots.allFixedDrives` — include every drive `Get-PSDrive` reports.
- `scanRoots.allUserProfiles` — include every profile under `C:\Users\`.
- `scanRoots.perProfileStandardFolders` / `perProfileOptionalFolders` — what to scan inside each profile.
- `scanRoots.commonProjectRoots` / `perUserProjectRoots` — project roots to include.
- `scanRoots.adminOnlyRoots` — included only when running elevated.
- `excludeGlobs` — path-substring excludes (e.g. `node_modules`, `WinSxS`).
- `excludeFileNames` / `excludeExtensions` — file-level excludes.
- `quarantineRootName` / `archiveRootName` / `groupRootName` — subfolder names.
- `sizeCacheEnabled` / `sizeCacheFileName` — disk-cached collector.

## Safety guarantees

1. **No hard delete ever** unless you run `-PurgeQuarantine` later.
2. `App`, `Project`, `System`, `Data`, `HeavyCache` are *never* default-acted on, even in `Auto` mode.
3. Items under any auto-detected heavy-cache path require an explicit per-item override to be quarantined.
4. Every applied action is journaled with SHA-1 of the source file (when small enough), making round-trip integrity verifiable.
5. The tool refuses to act on any path that matches an exclude glob.
6. `-Mode Auto` asks for confirmation before doing anything.
7. The HTML report is fully offline; nothing is uploaded anywhere.

## Output

Each run writes to `out\<runId>\`:

- `environment.json` — full detected environment snapshot.
- `inventory.csv` — every item, one row per item, sortable in Excel.
- `inventory.html` — interactive report with filter, sort, and per-item override UI.
- `inventory.md` — Markdown summary.
- `plan.json` — the proposed action list (machine-readable).
- `actions-journal.jsonl` (Auto) / `dryrun-journal.jsonl` (DryRun) — append-only journal.
- `_Quarantine\<runId>\` (Auto only) — soft-deleted items, waiting to be purged or restored.

## Limitations & caveats

- Scans are full-recursion. On a single 250 GB C: drive with a typical user profile,
  expect 5–15 minutes the first time, 30–60 seconds on re-runs thanks to the size cache.
- OneDrive files are detected as placeholders; their on-disk size is what's reported.
- The HTML report embeds the first 2000 rows in the page (more would make the page
  slow to render). The CSV is always complete.
- The tool makes no network calls. It does not phone home, telemetry, or cloud uploads.
- PowerShell 5.1 only. If you have PowerShell 7 installed, the tool still works but
  uses the 5.1 features of the language by design.

## License

Public domain / MIT — use freely.
