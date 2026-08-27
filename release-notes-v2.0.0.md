DiskInventory v2.0 — Unified Python engine, content-aware classification, live web UI, fleet mode.

## What's in v2.0

* **One engine, two wrappers.** Python 3 is canonical. `disk-inventory.bat` and `disk-inventory.sh` are the only OS-specific code. The PowerShell toolchain (v1.x) is retired; the v1.1.0 zip stays on GitHub forever.
* **Live web UI.** `disk-inventory.py run --mode auto --serve` starts a dashboard on `http://127.0.0.1:8765` with SSE journal streaming, override upload, and pause/resume controls.
* **Content-aware classification.** MIME sniffing (zero deps), SHA-1 dedup (`--compute-hashes`), EXIF date grouping (`--classify-exif`), name-similarity clustering (`--classify-cluster`).
* **Notifications.** Generic webhook (`--notify-webhook URL`) + OS-native desktop (BurntToast on Windows, `notify-send` on Linux, `terminal-notifier` on macOS).
* **Fleet mode.** `disk-inventory.py fleet scan --hosts hosts.txt` SSHes into each host, runs the worker, pulls back artifacts. `fleet dedup` aggregates cross-host SHA-1 duplicates into `fleet-dedup.html`.
* **Backward compatible.** v1.1.0 journals restore cleanly. CSV column order, environment.json, and overrides.json shapes are preserved.

## Install

```bash
# Linux / macOS
git clone https://github.com/Aminou973/diskinventory.git
cd diskinventory
./disk-inventory.sh --version
```

```powershell
# Windows
git clone https://github.com/Aminou973/diskinventory.git
cd diskinventory
disk-inventory.bat --version
```

## Quickstart

```bash
disk-inventory.py run --mode report --output-dir out/01-report
disk-inventory.py run --mode dryrun --output-dir out/02-dryrun
disk-inventory.py run --mode auto --output-dir out/03-auto --serve --open --yes
disk-inventory.py restore out/03-auto/actions-journal.jsonl --apply
disk-inventory.py purge --older-than-days 30
```

## Subcommands

| Command | Purpose |
|---|---|
| `run --mode {report,dryrun,auto}` | Detect → collect → classify → plan → (apply) → export |
| `restore <journal>` | Reverse an actions journal (v1.x and v2 compatible) |
| `purge` | Delete old `_Quarantine/<runId>/` dirs |
| `serve <run_dir>` | Start the dashboard for an existing run |
| `fleet scan --hosts hosts.txt` | SSH into each host, run the worker, fetch outputs |
| `fleet dedup <fleet_dir>` | Cross-host SHA-1 dedup report |
| `migrate <v1-dir> --dst <v2-dir>` | Re-emit a v1.x run dir in v2 layout |

## Verification

Five test suites pass:

* `tests/test_journal_compat.py` — v1.1.0 journal round-trip + field order + SAFETY_KEEP
* `tests/test_classify_content.py` — MIME sniff, SHA-1 dedup, name clustering, EXIF no-op
* `tests/test_serve.py` — http.server dashboard end-to-end (every endpoint 200)
* `tests/test_migrate.py` — v1.1.0 → v2.0 migration preserves artifacts
* `spec/run_golden_tests.py` — synthetic-tree fixture diff against goldens

## Migration from v1.x

* **Journal format unchanged** — `restore` works on v1.x and v2.0 journals.
* **`environment.json` unchanged** — `serve` works on v1.x run dirs.
* **CSV column order unchanged** — Excel/Sheets users won't notice.
* **`overrides.json` unchanged** — override UI works the same.
* **Config files DO change** — v2 uses base + overlay pattern. Run `disk-inventory.py migrate <v1-dir> --dst <v2-dir>` to convert.
* **PowerShell tool removed.** Users who relied on the PS-only flow can `git checkout v1.1.0`.

## License

MIT.
