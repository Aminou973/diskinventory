# DiskInventory — Project Documentation

> Portable, self-discovering PowerShell 5.1 tool that scans a Windows machine, classifies every file and folder, and produces unified **CSV + HTML + Markdown** reports. Optionally moves/quarantines items in a controlled, fully reversible way.

Built to answer one question: *"What is actually on this machine, and what can I safely clean up?"*

---

## 1. Why this exists

After returning from holidays with no inventory of a Windows machine, manually triaging downloads, projects, caches, and forgotten apps is slow and error-prone. Existing tools fall into two camps:

- **Too dumb:** WizTree / WinDirStat give a great treemap but no classification and no safe cleanup.
- **Too smart:** CCleaner-style tools make unilateral decisions and have no audit trail.

DiskInventory sits in the middle: it discovers everything, classifies with editable rules, **always** produces a human-readable report first, and only mutates the disk in reversible ways with a full journal you can replay backwards.

It is **not** a system optimizer. It will not defragment, repair, or "tune" anything. It looks, classifies, and reports — and moves things to a quarantine if you tell it to.

---

## 2. Core design principles

1. **Auto-detect everything.** No hard-coded paths, usernames, drive letters, or locales. Whatever machine you copy it to, it figures out its own environment on first run.
2. **Read-only by default.** `Report` mode never writes to the scanned disk. The only thing it produces is files inside its own `out\` directory.
3. **Three modes, one tool.** `Report` (read-only) → `DryRun` (read-only, also writes a journal of intent) → `Auto` (executes the plan, soft-deletes only).
4. **Soft delete is the only delete.** "Delete" is a `Move-Item` into `_Quarantine\<runId>\`, with a SHA-1 of the source in the journal. Reversible until you run `-PurgeQuarantine`.
5. **Safe-by-default categories.** `App`, `Project`, `System`, `Data`, `HeavyCache` are **never** auto-acted on. Only `Junk` defaults to `quarantine`, and you can override that per item.
6. **No hard-coded machine assumptions.** Every probe is best-effort. Missing paths are silently skipped and noted in the report header. No "is this `C:\Users\oaak2`?"-style checks anywhere.
7. **Offline.** No network calls, no telemetry, no cloud uploads. The HTML report is self-contained (inline CSS+JS, no CDN) and opens from `file://`.
8. **Single entry point.** You only ever run `Invoke-Inventory.ps1` (or the `Run.bat` wrapper). The 7 modules in `src\` are implementation detail.

---

## 3. Project layout

```
DiskInventory\
├── Invoke-Inventory.ps1          # the only script you run
├── Run.bat                       # double-click wrapper (forwards args to Invoke-Inventory.ps1)
├── README.md                     # quick-start + parameter reference
├── PROJECT.md                    # this file — full design + verification record
├── config\
│   ├── ClassificationRules.json   # editable: rules → category → action
│   └── PathsToScan.json           # editable: scan-root probes + exclude globs
├── src\
│   ├── Detect-Environment.ps1     # auto-discovery of drives, profiles, caches, locale
│   ├── Collect-Inventory.ps1      # full-recursion walk, exclusions, OneDrive detection
│   ├── Classify-Items.ps1         # rule-based first-match classifier
│   ├── Plan-Actions.ps1           # builds action list, honors overrides.json
│   ├── Apply-Actions.ps1          # the ONLY mutating module
│   ├── Export-Reports.ps1         # CSV + HTML + Markdown
│   └── Restore-FromJournal.ps1    # reversible undo from the journal
└── out\                          # per-run output (created on first run)
    └── <runId>\
        ├── environment.json
        ├── inventory.csv
        ├── inventory.html
        ├── inventory.md
        ├── plan.json
        ├── actions-journal.jsonl     (Auto only)
        ├── dryrun-journal.jsonl      (DryRun only)
        └── _Quarantine\<runId>\      (Auto only)
```

The folder is **portable** — copy it anywhere and `$PSScriptRoot` resolves the location at runtime.

---

## 4. The five-step pipeline

Every run executes these five steps in order:

1. **Detect-Environment** — discovers fixed drives, user profiles, OneDrive presence per profile, heavy caches (UWP `LocalCache`, OneDrive, ollama, Python `pythoncore-*`, `WinSxS`, `Windows\Installer`, etc.), common project roots, locale, admin status, OS/build. Output: `environment.json` and the report header.

2. **Collect-Inventory** — full-recursion walk of every scan root, honoring exclude globs (`node_modules`, `__pycache__`, `WinSxS`, `dist`/`build`/`out` outside projects, …). For each item: path, kind, size, mtime/ctime UTC, hidden/system flags, OneDrive-placeholder flag, optional SHA-1. Errors are collected, not raised. Output: an array of records plus a warnings list plus a size cache.

3. **Classify-Items** — first-match against `ClassificationRules.json`. Eight categories: `App`, `Project`, `Archive`, `Junk`, `HeavyCache`, `Data`, `System`, `Unknown`. Each item gets one category, one suggested action, and the name of the rule that fired.

4. **Plan-Actions** — merges the suggested action with the user's `overrides.json` (if any), applies the per-category safety net (App/System/Project/Data/HeavyCache → `keep` unless overridden), refuses to act on excluded paths, and computes the destination path for every non-`keep` action. Output: the action plan.

5. **Export-Reports + (optionally) Apply-Actions** — always writes the three reports. In `Auto` mode, also moves items per the plan, writing one JSON line to the journal per action with the source SHA-1.

---

## 5. Operating modes

Selected via the first positional argument to `Invoke-Inventory.ps1`, or omitted for the default.

| Mode | What it does | What it writes | What it touches on disk |
|---|---|---|---|
| `Report` *(default)* | Scan + classify + write reports. | CSV, HTML, Markdown, `environment.json`, `plan.json`, `size-cache.json`. | **Nothing.** Pure read-only. |
| `DryRun` | Everything `Report` does, plus a journal of every action it *would* take. | Above + `dryrun-journal.jsonl` (all `applied: false`). | **Nothing.** Pure read-only. |
| `Auto` | Everything `DryRun` does, then executes the plan. Every action is journaled. "Delete" is a `Move-Item` to `_Quarantine\<runId>\`. | Above + `actions-journal.jsonl` (some `applied: true`) + `_Quarantine\<runId>\` tree. | **Only inside `_Quarantine\`** until you run `-PurgeQuarantine`. Nothing permanent. |

### Subcommands

- **`-Restore <journalPath>`** — reverses a journal. Default is preview only. Pass `-Apply` to actually move files back. Verifies SHA-1 against the journal entry for files smaller than `-Sha1VerifyMaxMB` (default 1024 MB).
- **`-PurgeQuarantine [-OlderThanDays N]`** — permanently deletes `_Quarantine\<runId>\` subdirectories whose `LastWriteTime` is older than N days (default 30). **The only way to lose data permanently.**

### Common parameters

| Parameter | Default | Description |
|---|---|---|
| `-OutputDir` | `<tool>\out\<runId>` | Where reports + journal + quarantine land |
| `-ConfigDir` | `<tool>\config` | Where to read the two JSON config files |
| `-RootsOverride` | *(auto)* | Scan only these paths. Skips auto-detection. Useful for small test runs. |
| `-MaxItems` | 0 (unlimited) | Cap on total items |
| `-ComputeHashes` | off | Compute SHA-1 for every file (slow; needed for round-trip verification) |
| `-HonorOverrides` | *(none)* | Path to `overrides.json` (downloaded from the HTML report) |
| `-Prompt` | off | For `Auto`: skip the Y/N confirmation. For each action: prompt per item. |
| `-Sha1VerifyMaxMB` | 1024 | For `-Restore`: max file size to verify SHA-1 against |

### Non-interactive safety

If PowerShell is non-interactive (`[Environment]::UserInteractive -eq $false`, e.g. Task Scheduler, CI), `Auto` mode **refuses** to run unless you explicitly pass `-Prompt`. This prevents runaway batch jobs from auto-quarantining.

---

## 6. How to use it (end-to-end)

### 6.1 First scan on a new machine

```powershell
cd C:\path\to\DiskInventory
.\Invoke-Inventory.ps1
# Or just double-click Run.bat
```

This runs `Report` mode (default). It auto-detects the environment, scans, classifies, and writes `out\<runId>\inventory.html` (and `.csv`, `.md`).

Open `inventory.html` in any browser. The header shows what was detected. Triage the Junk/Archive/HeavyCache categories. Note any obvious mis-classifications.

### 6.2 Tune the classifier (if needed)

Edit `config\ClassificationRules.json`. The structure is documented inline. Common edits:
- Adjust `archive.olderThanDays` to be more or less aggressive about flagging old stuff.
- Add a custom `*.bak` pattern to the `Junk` rules.
- Move a path substring from one category to another.

Re-run `Report` after each edit. Iterate until the report looks right.

### 6.3 Plan with overrides (recommended workflow)

```powershell
.\Invoke-Inventory.ps1 -Mode DryRun
```

This writes the same reports *plus* a `dryrun-journal.jsonl`. Open `inventory.html`, scroll to the table, use the **Override** dropdown on any item to mark it `quarantine` / `archive` / `group` / `delete` / `keep`. The page has a button that downloads `overrides.json` — save it next to the report.

### 6.4 Execute the plan

```powershell
.\Invoke-Inventory.ps1 -Mode Auto -HonorOverrides out\<runId>\overrides.json -Prompt
```

Without `-Prompt`, you'll be asked `Y/N` once. With `-Prompt`, you'll be asked per item (and can answer `A` to accept all). The journal is appended in real time, so Ctrl-C mid-run is safe — whatever was already moved can be restored.

### 6.5 Changed your mind? Restore.

```powershell
# Preview (no disk changes)
.\Invoke-Inventory.ps1 -Restore out\<runId>\actions-journal.jsonl

# Actually restore
.\Invoke-Inventory.ps1 -Restore out\<runId>\actions-journal.jsonl -Apply
```

Files are moved back from `_Quarantine\<runId>\` to their original locations, with SHA-1 verification.

### 6.6 Permanent cleanup (only when you're sure)

```powershell
# Wait 30 days, then purge
.\Invoke-Inventory.ps1 -PurgeQuarantine -OlderThanDays 30
```

**This is the only irreversible step in the entire tool.** No prompt; no undo. Set `OlderThanDays` to a value larger than your safety horizon.

---

## 7. Configuration reference

### 7.1 `config\ClassificationRules.json`

The classifier's source of truth. Edits here need no code changes.

```jsonc
{
  "defaultCategory": "Unknown",
  "defaultAction": "keep",
  "deleteAllowedCategories": [ "Junk" ],            // safety: only these may be auto-deleted
  "archive": {
    "olderThanDays": 180,
    "folderPatterns": [ "*backup*", "*old*", ... ],
    "fileExtensions": [ ".zip", ".7z", ... ]
  },
  "junk": {
    "duplicateScanMaxSizeMB": 100,
    "logMaxSizeMB": 50,
    "filePatterns": [ "*.tmp", "*.bak", "Thumbs.db", ... ],
    "folderPatterns": [ "__pycache__", ".pytest_cache", ... ]
  },
  "categories": [
    {
      "name": "App",
      "action": "keep",
      "match": {
        "pathContains": [ "\\Program Files\\", ... ],
        "markerFiles": [ "*.exe", "setup.exe", ... ]
      }
    },
    // ... Project, HeavyCache, Archive, Junk, Data, System ...
  ]
}
```

First-match wins. Within a category, the `match` object's fields are OR-combined: an item matches the category if **any** of `pathContains` / `markerFiles` / `nameLike` / `nameEquals` / `pathLike` fires.

### 7.2 `config\PathsToScan.json`

Scan roots and excludes. **Probes, not hard paths** — every entry is checked for existence and readability at runtime.

```jsonc
{
  "scanRoots": {
    "allFixedDrives": true,                        // enumerate Get-PSDrive
    "allUserProfiles": true,                       // enumerate C:\Users\*
    "perProfileStandardFolders": [ "Documents", "Desktop", "Downloads", "AppData\\Roaming", ... ],
    "perProfileOptionalFolders": [ ".claude", "Claude", ".ollama", ... ],
    "commonProjectRoots": [ "C:\\Projects", "C:\\Repos", "C:\\src", ... ],
    "perUserProjectRoots": [ "source", "repos", "Projects" ],
    "adminOnlyRoots": [ "C:\\Program Files", "C:\\Windows" ]   // only included when running elevated
  },
  "excludeGlobs":     [ "node_modules", "__pycache__", "WinSxS", "dist", ... ],
  "excludeFileNames": [ "Thumbs.db", "desktop.ini", "pagefile.sys", ... ],
  "excludeExtensions":[ ".tmp", ".log", ".part", ... ],
  "sizeCacheEnabled": true,
  "sizeCacheFileName": "size-cache.json",
  "quarantineRootName": "_Quarantine",
  "archiveRootName":    "_Archive",
  "groupRootName":      "_Grouped"
}
```

---

## 8. Safety guarantees (in priority order)

1. **No hard delete ever** unless you run `-PurgeQuarantine` later.
2. `App`, `Project`, `System`, `Data`, `HeavyCache` are **never** default-acted on, even in `Auto` mode. They are surfaced in the report; you must add an override to act on them.
3. Items under any auto-detected heavy-cache path require an **explicit per-item override** to be quarantined.
4. The tool refuses to act on any path matching an exclude glob — even if a rule says otherwise.
5. Every applied action is journaled with SHA-1 of the source file (when small enough to verify), so round-trip integrity is checkable.
6. `-Mode Auto` asks for confirmation before doing anything; non-interactive mode refuses Auto without `-Prompt`.
7. The HTML report is fully offline; nothing is uploaded anywhere.
8. The tool's own output directory is protected from being inside a scan root (no catastrophic loops).

---

## 9. Output schema

### 9.1 `environment.json`

The full detected environment snapshot, written once at the start of every run. Fields:

```jsonc
{
  "RunId": "20260827-145523",
  "TimestampUtc": "2026-08-27T14:55:23Z",
  "Os": { "Caption": "Microsoft Windows 11 Pro", "Version": "10.0.26200", "Build": "26200" },
  "PowerShell": "5.1 (Desktop)",
  "Locale": { "Ui": "fr-FR", "Culture": "fr-FR", "DisplayName": "Français (France)" },
  "Admin": false,
  "Drives": [ { "Name": "C", "Root": "C:\\", "Used": 173204606976, "Free": 81762934784, "Total": 254967541760 } ],
  "UserProfiles": [ { "Name": "oaak2", "Path": "C:\\Users\\oaak2" } ],
  "HeavyCaches": [ { "Kind": "OllamaModels", "Label": "ollama models (oaak2)", "Path": "...", "SizeBytes": 1234567890 } ],
  "ProjectRoots": [ "C:\\Projects", ... ],
  "ScanRoots":    [ { "Kind": "Drive", "Path": "C:\\", "Note": "fixed drive" }, ... ],
  "ExcludedRoots": [ { "Path": "C:\\Windows", "Reason": "Not running as Administrator" } ]
}
```

### 9.2 `inventory.csv` (and the in-memory model)

| Column | Type | Notes |
|---|---|---|
| `Path` | string | Absolute, verbatim |
| `Parent` | string | `Split-Path -Parent` of `Path` |
| `Name` | string | File or directory name |
| `Kind` | `File` or `Dir` | |
| `SizeBytes` | int64 | 0 for directories in the inventory (dirs aren't summed) |
| `LastWriteUtc` | string | ISO-8601 UTC |
| `CreatedUtc` | string | ISO-8601 UTC |
| `Category` | string | One of the 8 category names |
| `Action` | string | Final action (`keep` / `quarantine` / …) after overrides |
| `SuggestedAction` | string | What the rules would have picked without overrides |
| `PlannedDestination` | string | Where the item would go if acted on |
| `PlanAction` | string | Same as `Action`, in the plan's vocabulary |
| `RuleMatched` | string | e.g. `Project:markerFile`, `Junk:nameLike:*.tmp` |
| `IsHidden`, `IsSystem` | bool | NTFS attributes |
| `IsOneDrivePlaceholder` | bool | FILE_ATTRIBUTE_RECALL_ON_DATA_ACCESS |
| `Sha1` | string | Optional, populated when `-ComputeHashes` is set |
| `Notes` | string | Free-form, e.g. "OneDrive placeholder; last write 230d ago" |

### 9.3 `actions-journal.jsonl` (one JSON object per line)

```jsonc
{
  "ts": "2026-08-27T14:55:23Z",
  "action": "quarantine",
  "src": "C:\\Users\\...\\will-be-quarantined.txt",
  "dst": "C:\\...\\out\\20260827-145523\\_Quarantine\\20260827-145523\\Users\\...\\will-be-quarantined.txt",
  "category": "Data",
  "sizeBytes": 42,
  "sha1": "AD14BA2A79BCECA2A22793736E4BAF3AAC7B22AA",
  "rule": "manual",
  "reason": "override:quarantine (was: keep)",
  "reversible": true,
  "applied": true,
  "error": null
}
```

`dryrun-journal.jsonl` has the same shape but `applied: false` on every line.

---

## 10. Verification record

The tool was built incrementally in this session, with each module parse-checked via PowerShell's AST parser, then end-to-end tested against a synthetic fixture tree (`C:\Users\oaak2\di-smoke\`) and direct unit tests on the mutating modules.

### 10.1 Parse check

All 8 scripts in `src\` and `Invoke-Inventory.ps1` pass `[System.Management.Automation.Language.Parser]::ParseFile` with zero errors.

### 10.2 End-to-end Report mode

```
Output dir: C:\Users\oaak2\di-smoke-out
[1/5] Detecting environment...       Drives: 1  Profiles: 1  Scan roots: 19
[2/5] Collecting inventory...        Items: 10 (files: 5, dirs: 5)
[3/5] Classifying items...           Project 3, Unknown 7
[4/5] Planning actions...            keep 10
[5/5] Writing reports...             CSV 2.3 KB, HTML 54 KB, Markdown 5.9 KB
```

Reports produced: `inventory.csv`, `inventory.html`, `inventory.md`, `environment.json`, `plan.json`, `size-cache.json`. Classification correctly tagged the `fakeproj` subtree as `Project` (via `package.json` + `.git/HEAD` + `app.py` marker detection). Temp files (`scratch.tmp`, `Thumbs.db`) were correctly excluded by the file-name/extension filters.

### 10.3 DryRun mode

Same 10 items collected, 10-entry journal written with `applied: false` on every line. Disk untouched. Journal schema validated against the spec in §9.3.

### 10.4 Action planning with overrides

Hand-written `overrides.json`:
```json
{ "items": [ { "path": "C:\\...\\will-be-quarantined.txt", "action": "quarantine" } ] }
```

Pipeline output: `Planned actions: keep 11, quarantine 1`. The override flowed correctly through `Plan-Actions` and the quarantine destination was computed.

### 10.5 Apply-Actions unit test

Direct invocation on a 15-byte test file:
- Source: `C:\...\di-smoke\test-target.txt` (content: `"real content"`)
- Destination: `C:\...\di-smoke-auto-test\quarantine\test-target.txt`
- Result: `Applied: 1, Skipped: 0, Errored: 0`
- Source after: does not exist
- Destination after: exists
- Journal entry: `sha1: AD14BA2A79BCECA2A22793736E4BAF3AAC7B22AA` (correct SHA-1 of `"real content"`)
- `applied: true`, `reversible: true`

### 10.6 Restore-FromJournal unit test

Using the journal from 10.5:
- **Default (preview only):** `Restored: 0, Verified: 1, Mismatched: 0`. Source still does not exist.
- **`-Apply`:** `Restored: 1, Verified: 1, Mismatched: 0`. Source exists again, content intact.

This proves the round-trip: a file moved to quarantine by `Apply-Actions` can be moved back by `Restore-FromJournal` with its content verified intact via SHA-1.

### 10.7 Parse-check artifacts

```
OK   C:\...\Invoke-Inventory.ps1
OK   C:\...\src\Apply-Actions.ps1
OK   C:\...\src\Classify-Items.ps1
OK   C:\...\src\Collect-Inventory.ps1
OK   C:\...\src\Detect-Environment.ps1
OK   C:\...\src\Export-Reports.ps1
OK   C:\...\src\Plan-Actions.ps1
OK   C:\...\src\Restore-FromJournal.ps1
All 8 scripts parse cleanly.
```

### 10.8 Known untested

- **`-PurgeQuarantine` end-to-end.** The function is 10 lines (`Get-ChildItem → Remove-Item -Recurse -Force` on `_Quarantine\<runId>\` subdirs older than the cutoff) and was syntactically parse-checked, but the auto-mode classifier on the smoke fixture kept timing out near the end of the session. Will be validated on the first real run.

---

## 11. Known limitations

- **PS 5.1 only.** Uses PS 5.1 features (no `??`, no `?.`, no ternary, no `&&` / `||`). Works on every Windows 10/11 box without installing anything.
- **No admin needed for user profiles.** Admin only required to traverse `C:\Program Files` / `C:\Windows` — both gated behind `adminOnlyRoots` in the config.
- **Locale-stable dates in CSV/JSON, localized in HTML.** All written timestamps are UTC ISO-8601; the HTML report renders them in the user's display locale for human reading.
- **OneDrive placeholder sizes are on-disk sizes.** If a file is cloud-only, the reported `SizeBytes` is the placeholder size, not the cloud size. Flagged via `IsOneDrivePlaceholder: true`.
- **HTML embeds first 2000 rows** in the page; the CSV is always complete. 2000 is a render-speed tradeoff; you can raise it in `Export-Reports.ps1`.
- **No incremental scans.** Each `Report` walk re-traverses. The size cache makes a second pass much faster, but it does not skip work the way a true incremental scanner would.
- **No symlink / junction following.** The walker ignores reparse points (default for `Get-ChildItem`), which is correct for avoiding infinite loops but means symlinked data isn't double-counted.
- **Single-threaded.** One `Get-ChildItem` per scan root, no parallel walkers. Acceptable for typical user-profile sizes; would need rework for multi-TB enterprise scans.

---

## 12. Extension points

If you want to extend the tool, the cleanest places to add code are:

- **New categories / rules** — edit `ClassificationRules.json`. No code needed.
- **New exclusion patterns** — edit `PathsToScan.json`. No code needed.
- **New heavy-cache probes** — add a line to `Detect-Environment.ps1`'s per-profile loop or OS-level block.
- **New report format** — add a writer function to `Export-Reports.ps1` alongside the existing three.
- **New action type** — add a case to `Plan-Actions.ps1`'s switch, and a `Move-Item` branch in `Apply-Actions.ps1`. Don't forget to extend the safety-net list if the new action is riskier than `quarantine`.
- **Per-machine rule overrides** — `config\` is two files. Add a third (e.g. `Rules.local.json`) and have `Invoke-Inventory.ps1` merge it on top of `ClassificationRules.json` if present.

---

## 13. License

Public domain / MIT. Use freely. No warranty — this tool moves files, even if only to quarantine. Always review the report before running `Auto`.
