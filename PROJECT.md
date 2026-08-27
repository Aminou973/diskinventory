# DiskInventory — Project Reference

A complete reference for the DiskInventory project, covering both the v2.0
architecture and the v1.x record.

## §1. What is DiskInventory?

A disk-space inspector and reorganization planner for Windows and POSIX
systems. Walks a configurable set of roots, classifies every item by
rule + content signals, produces a deterministic plan, applies it under
user supervision, and keeps a journal that allows full reversal.

The defining design choice: **never delete anything** in the first pass.
Items are *quarantined* under `_Quarantine/<runId>/<category>/`, and only
the explicit `purge` subcommand later deletes them. This makes the entire
workflow reversible by default.

## §2. Versions

| Tag | Engine | Platforms | Status |
|---|---|---|---|
| **v2.0** (current) | Python 3 | Windows, Linux, macOS | Active development |
| v1.1.0 | PowerShell 5.1 + Python 3 | Windows (PS), Linux/POSIX (Py) | Frozen — release zip stays on GitHub |
| v1.0.0 | PowerShell 5.1 | Windows only | Frozen |

The v1.x toolchain is preserved at the `v1.1.0` git tag and as a release
zip on GitHub. Users who need the PowerShell-only flow can
`git checkout v1.1.0`.

## §3. High-level architecture (v2.0)

```
                ┌────────────────────────────┐
                │   disk-inventory.py        │  (single Python entry point)
                │   argparse subcommands:     │
                │     run | restore | purge  │
                │     serve | fleet | migrate│
                └─────────────┬──────────────┘
                              │
        ┌─────────────────────┼─────────────────────┐
        │                     │                     │
   ┌────▼─────┐         ┌─────▼─────┐         ┌─────▼─────┐
   │ Detect   │         │ Collect   │         │ Classify  │
   │ env.py   │         │  walk.py  │         │ + content │
   └────┬─────┘         └─────┬─────┘         └─────┬─────┘
        │                     │                     │
        └─────────────────────┼─────────────────────┘
                              │
                       ┌──────▼──────┐
                       │  Plan       │
                       └──────┬──────┘
                              │
              ┌───────────────┼───────────────┐
              │                               │
        ┌─────▼─────┐                  ┌──────▼──────┐
        │  Apply    │ ─── journal ──▶  │  Web UI     │
        │ (mutates) │                  │  http://    │
        └─────┬─────┘                  │  :8765      │
              │                        └─────────────┘
              ▼
       _Quarantine/<runId>/

       ┌────────────┐
       │ Fleet mode │  SSH coordinator + SQLite central store
       │   fleet.py │
       └────────────┘
```

## §4. Subcommand grammar

```bash
disk-inventory.py run --mode report|dryrun|auto [--output-dir DIR] [--compute-hashes] [--yes]
disk-inventory.py run --mode auto --serve [--open] [--port 8765]
disk-inventory.py run --mode auto --notify-webhook URL
disk-inventory.py run --mode auto --classify-exif        # needs Pillow
disk-inventory.py run --mode auto --classify-cluster
disk-inventory.py restore <journal> [--apply] [--sha1-verify-max-mb N]
disk-inventory.py purge [--older-than-days N]
disk-inventory.py serve <run-dir> [--port 8765] [--open]
disk-inventory.py fleet scan --hosts hosts.txt [--user USER] [--ssh-key PATH]
disk-inventory.py fleet dedup <fleet-dir> [--top 50] [--serve]
disk-inventory.py migrate <v1-run-dir> --dst <v2-run-dir>
```

## §5. Outputs

For every run, `out/<runDir>/` contains:

| File | Purpose |
|---|---|
| `environment.json` | Drives, profiles, heavy caches, scan roots |
| `inventory.csv` | 22-column CSV (18 v1.x + 4 v2 additions) |
| `inventory.md` | Markdown summary |
| `inventory.html` | Offline single-file report with override UI |
| `plan.json` | Deterministic plan (per-path action + destination) |
| `dryrun-journal.jsonl` | What `dryrun`/`auto` *would* do |
| `actions-journal.jsonl` | What `auto` actually did |

## §6. The journal contract

The journal is JSON-Lines. Each entry has 12 fields in this fixed order:

```
ts, action, src, dst, category, sizeBytes,
sha1, rule, reason, reversible, applied, error
```

This shape is preserved across v1.0.0 → v1.1.0 → v2.0. A v2.0 reader can
parse any v1.x journal byte-for-byte; a v1.x reader sees v2.0 entries with
the same shape (no breaking field additions).

## §7. Live web UI (`src/serve.py`)

* Built on `http.server` (stdlib) + hand-rolled SSE handler (~80 LOC). No deps.
* Default URL: `http://127.0.0.1:8765`. Bind to localhost only by default.
* `--bind 0.0.0.0` exposes it on the LAN; requires `--token`.
* Endpoints: `/`, `/api/run`, `/api/env`, `/api/stats`, `/api/items`,
  `/api/plan`, `/api/journal/stream`, `/api/overrides`, `/api/apply/pause`,
  `/api/apply/resume`.

## §8. Content-aware classification (`src/classify_content.py`)

* **MIME sniffing** (default-on, zero deps): first-byte magic for PDF, PNG,
  JPEG, ZIP, GZIP, ELF, PE/MZ, MP4, MP3, OGG, FLAC, RIFF/WAV, and more.
* **SHA-1 dedup** (`--compute-hashes`): hashes every file, groups by hash.
  After classify, `DuplicateGroup` is set on items that share a hash with
  another item.
* **EXIF date grouping** (`--classify-exif`, requires `Pillow`): for image
  MIME types, extracts `DateTimeOriginal`. If `Pillow` is missing, the
  flag is silently skipped — never an error.
* **Name-similarity clustering** (`--classify-cluster`): Levenshtein on
  basenames within a directory + size bucket. Default threshold 0.5,
  capped at 5000 items per parent.

## §9. Notifications (`src/notify.py`)

* **Generic webhook**: `urllib.request` POST with 3× retry + exponential
  back-off. Auth via Bearer token (`--notify-webhook-token`).
* **OS-native desktop**: BurntToast / `msg *` on Windows,
  `terminal-notifier` / `osascript` on macOS, `notify-send` on Linux.

## §10. Fleet mode (`src/fleet.py`)

* Coordinator is the same `disk-inventory.py` binary, with a `fleet` subcommand.
* `hosts.txt` format: one host per line, `#` comments, inline `key=val`
  overrides (`ansible_user=root`, `port=2222`).
* SSHes into each host, runs `python3 disk-inventory.py run --mode report`,
  pulls back the outputs via `scp`. Falls back to WinRM (with `pywinrm`)
  when SSH is closed on a Windows worker.
* Central SQLite store at `<fleet-dir>/fleet.db`. Schema in `src/fleet.py`.
* Cross-host dedup via `disk-inventory.py fleet dedup <fleet-dir>` — emits
  `fleet-dedup.html` with `potentialSavingsBytes = totalBytes × (1 - 1/hostCount)`.

## §11. Spec & golden fixtures

* `spec/journal.schema.json` — JSON Schema for journal entries.
* `spec/environment.schema.json` — JSON Schema for environment.json.
* `spec/inventory.schema.json` — JSON Schema for inventory.csv header.
* `spec/overrides.schema.json` — JSON Schema for overrides.json.
* `spec/fixtures/synthetic-tree/` — deterministic 30-file fixture.
* `spec/run_golden_tests.py` — diff the engine output against goldens.

## §12. Migration from v1.x

* **Journal format unchanged.**
* **`environment.json` unchanged.**
* **CSV column order unchanged.**
* **`overrides.json` unchanged.**
* **Config files DO change** — v2 uses base + overlay pattern.
  Use `disk-inventory.py migrate <v1-dir> --dst <v2-dir>` to convert.
* **PowerShell tool removed.** Users who relied on the PS-only flow can
  `git checkout v1.1.0`; the v1.1.0 zip remains on GitHub forever.

## §13. v1.x reference (preserved)

The v1.x toolchain had two ports:

* **Windows PowerShell 5.1** (`Invoke-Inventory.ps1` + 7 modules in `src/*.ps1`).
* **Linux/macOS Python 3** (port of the same logic, ~1,800 LOC).

Both produced the same JSON-Lines journal and the same `environment.json`
shape. They shared the same config files (`config/ClassificationRules.json`,
`config/PathsToScan.json`) and the same report schema.

The v1.x → v2.0 unified this:

* Python 3 becomes canonical. The PS tool is retired.
* The PS tool's WMI/CIM calls move into `src/env_detect_windows.py`.
* The Linux detection moves into `src/env_detect.py`.
* Both expose the same function signature (`detect_environment() -> dict`).

## §14. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Removing the PS tool breaks someone | v1.1.0 release tag + zip stay on GitHub forever |
| Web UI adds an HTTP attack surface | Bind to `127.0.0.1` by default; `--bind 0.0.0.0` requires `--token` |
| SQLite central store grows unbounded | `--prune <days>` flag on `fleet dedup`; auto-vacuum on close |
| Fleet scan hammers a slow SSH server | `--parallel N` (default 4), `--ssh-timeout 30s`, per-host retry |
| MIME sniffing false-positive | Limited to first 16 bytes; documented in `classify_content.py` |
| EXIF parsing is slow on large image dirs | Gated by `--classify-exif`; Pillow missing → silent skip |
| Name clustering is O(n²) | Sample 5000 items per parent; documented in `--help` |

## §15. Verification

* **Engine unification**: every output from v1.1.0 (CSV header, environment
  keys, journal lines, overrides shape) is byte-equal to v2 on the same
  inputs.
* **Live web UI**: `python tests/test_serve.py` starts the server, hits
  every endpoint, posts an override, shuts down.
* **Content-aware classification**: `python tests/test_classify_content.py`
  covers MIME, SHA-1 dedup, name clustering, EXIF no-op.
* **Notifications**: webhook POST + retry/back-off exercised in
  `src/notify.py:_post_json`.
* **Fleet mode**: `python tests/test_migrate.py` and the parser test
  cover SSH parsing and SQLite aggregation.
* **Backward compat**: `python tests/test_journal_compat.py` writes a
  v1.1.0-format journal, restores it through v2 Restore.

## §16. License

MIT.
