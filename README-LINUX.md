# DiskInventory on Linux / macOS

See [README.md](README.md) for the cross-platform quickstart.

This file is the Linux/macOS-specific reference. Most of what you need is
in the main README; this file adds the POSIX-only nuances.

## POSIX specifics

* `pwd` is imported lazily in `src/env_detect.py` — Windows is allowed to
  `from src.env_detect import detect_environment` without the module
  existing.
* `_is_root()` in `src/env_detect.py` uses `os.geteuid()` (Unix-only);
  on non-Unix it returns False.
* `disk-inventory.sh` resolves to its own directory before running
  `python3 disk-inventory.py`. This lets you invoke it from anywhere
  with `~/.local/bin/disk-inventory` symlinked to it.
* Heavy-cache detection on Linux walks `~/.cache/pip`, `~/.npm`,
  `~/.cargo/registry`, `~/.m2/repository`, `~/.gradle/caches`, etc.
* `/proc/mounts` is used on Linux for drive detection; on macOS we fall
  back to `df -kP`.

## Native desktop notifications

The `--notify-webhook` flag is universal; the OS-native fallback requires:

* **Linux**: `notify-send` (libnotify; present on every mainstream desktop).
* **macOS**: `terminal-notifier` preferred; `osascript -e 'display
  notification'` as fallback.

If neither is found, the notification is silently skipped (no error).

## Fleet mode on POSIX workers

The remote worker command on POSIX is:

```bash
python3 -c '...' disk-inventory.py run --mode report
```

The coordinator (your machine) needs `ssh` and `scp` on `PATH`.

## Building the wheel

```bash
python3 -m build
```

(Requires the `build` package; only needed if you want to redistribute.)
