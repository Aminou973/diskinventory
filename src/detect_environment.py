"""
detect_environment — runtime environment snapshot for Linux/macOS.

Mirrors src/Detect-Environment.ps1 on Windows but uses POSIX APIs:
  - OS: reads /etc/os-release
  - Runtime: platform.python_version() + sys.platform
  - Locale: locale.getlocale() + os.environ.get('LANG', ...)
  - Admin: os.geteuid() == 0
  - Fixed drives: parses /proc/mounts (or falls back to df) + shutil.disk_usage
  - User profiles: iterates /home/*, plus /root if running as root
  - Per-profile heavy caches: ~/.cache/pip, ~/.cache/electron, ~/.var/app/*, ~/snap/*,
    ~/.local/share/Trash, ~/.ollama/models, ~/.cargo/registry, ~/.rustup, ~/.gem,
    ~/.npm, ~/.nvm, /go/pkg, ~/.cache/yarn, ~/.cache/thumbnails
  - OS-level heavy paths: /var/cache, /var/tmp, /var/lib/flatpak/runtime, /var/lib/snapd
  - Project roots: ~/Projects, ~/Repos, ~/src, ~/code, ~/dev, /srv, /opt, /workspace
  - adminOnlyRoots: /usr, /usr/local, /boot, /etc, /var/lib/dpkg, /var/lib/rpm
"""

import json
import os
import shutil
import sys

# pwd is Unix-only; on Windows it's missing. We fall back gracefully.
try:
    import pwd  # type: ignore
    _HAS_PWD = True
except ImportError:
    pwd = None
    _HAS_PWD = False


def _is_root() -> bool:
    """Return True if running with uid 0 (root). On non-Unix, False."""
    try:
        return os.geteuid() == 0
    except AttributeError:
        return False
from datetime import datetime, timezone
from pathlib import Path


# Filesystem types in /proc/mounts that are NOT fixed drives
_PSEUDO_FS_TYPES = {
    "tmpfs", "devpts", "proc", "sysfs", "devtmpfs", "cgroup", "cgroup2",
    "pstore", "efivarfs", "bpf", "configfs", "debugfs", "tracefs", "hugetlbfs",
    "mqueue", "autofs", "fusectl", "binfmt_misc", "ramfs", "overlay", "squashfs",
    "fuse.gvfsd-fuse", "fuse.portal", "fuse.snapfuse",
}


def _read_os_release() -> dict:
    """Parse /etc/os-release into a dict."""
    out = {"PRETTY_NAME": "Unknown", "VERSION_ID": "", "VERSION_CODENAME": ""}
    for path in ("/etc/os-release", "/usr/lib/os-release"):
        try:
            with open(path, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#") or "=" not in line:
                        continue
                    k, _, v = line.partition("=")
                    v = v.strip('"').strip("'")
                    if k in out:
                        out[k] = v
            return out
        except OSError:
            continue
    return out


def _list_fixed_drives() -> list:
    """Return a list of mount-point dicts, skipping pseudo filesystems."""
    mounts = []
    # Try /proc/mounts first (Linux); fall back to statvfs on root.
    try:
        with open("/proc/mounts", "r", encoding="utf-8") as f:
            for line in f:
                parts = line.split()
                if len(parts) < 3:
                    continue
                device, mount_point, fs_type = parts[0], parts[1], parts[2]
                if fs_type.lower() in _PSEUDO_FS_TYPES:
                    continue
                # Skip duplicate mount points (e.g. bind mounts)
                if any(m["Root"] == mount_point for m in mounts):
                    continue
                try:
                    usage = shutil.disk_usage(mount_point)
                except OSError:
                    continue
                mounts.append({
                    "Name": mount_point,
                    "Root": mount_point,
                    "Used": usage.used,
                    "Free": usage.free,
                    "Total": usage.total,
                    "Description": f"{fs_type} on {device}",
                })
    except OSError:
        # macOS or systems without /proc/mounts: just inspect root
        try:
            usage = shutil.disk_usage("/")
            mounts.append({
                "Name": "/",
                "Root": "/",
                "Used": usage.used,
                "Free": usage.free,
                "Total": usage.total,
                "Description": "root filesystem",
            })
        except OSError:
            pass
    return mounts


def _list_user_profiles() -> list:
    """Return a list of {Name, Path} dicts for each real user home directory."""
    profiles = []

    # Iterate /home/* (Linux convention)
    home = Path("/home")
    if home.is_dir():
        try:
            for entry in home.iterdir():
                if not entry.is_dir():
                    continue
                # Skip non-real entries (lost+found, etc.)
                if entry.name.startswith(".") or entry.name in ("lost+found",):
                    continue
                # Require it to look like a real home: must be owned by a user
                # and contain .config or Documents (XDG).
                if (entry / ".config").is_dir() or (entry / "Documents").is_dir():
                    profiles.append({"Name": entry.name, "Path": str(entry)})
        except OSError:
            pass

    # Add /root if running as root
    if _is_root():
        rp = Path("/root")
        if rp.is_dir() and not any(p["Path"] == str(rp) for p in profiles):
            profiles.append({"Name": "root", "Path": str(rp)})

    # Also enumerate via pwd module for completeness (covers macOS, non-/home)
    if not _HAS_PWD:
        return profiles
    seen = {p["Path"] for p in profiles}
    try:
        for entry in pwd.getpwall():
            pw_dir = entry.pw_dir
            if not pw_dir or pw_dir in seen:
                continue
            if pw_dir in ("/nonexistent", "/var/empty"):
                continue
            p = Path(pw_dir)
            if not p.is_dir():
                continue
            # Skip service accounts: shell ending in nologin/false and UID < 1000
            # (Linux convention) unless the home is non-standard.
            try:
                shell = entry.pw_shell or ""
                uid = entry.pw_uid
            except KeyError:
                shell, uid = "", -1
            if shell.endswith(("nologin", "false")) and uid < 1000:
                # Could still be a valid desktop user (rare); only skip if home is empty
                if not any(p.iterdir()):
                    continue
            seen.add(pw_dir)
            profiles.append({"Name": entry.pw_name, "Path": pw_dir})
    except Exception:
        pass

    return profiles


def _path_size(path: Path) -> int:
    """Return total bytes used by `path` (recursive). Returns 0 on error."""
    total = 0
    try:
        for root, dirs, files in os.walk(path, followlinks=False):
            for name in files:
                try:
                    total += (Path(root) / name).stat().st_size
                except OSError:
                    continue
    except OSError:
        return 0
    return total


def _probe_heavy_cache(profile_path: str, kind: str, label: str, rel: str) -> dict | None:
    """Compute size of a known heavy-cache path inside a profile, if it exists."""
    base = Path(profile_path)
    target = base / rel
    if not target.exists():
        return None
    return {
        "Kind": kind,
        "Label": label,
        "Path": str(target),
        "SizeBytes": _path_size(target),
    }


def _probe_heavy_caches_for_profile(profile_path: str) -> list:
    """Discover Linux heavy caches for one user profile."""
    probes = [
        ("OneDrive", "OneDrive sync client cache", ".cache/OneDrive"),
        ("OneDrive", "OneDrive config", ".config/OneDrive"),
        ("Flatpak", "Flatpak per-app cache", ".var/app"),
        ("Snap", "Snap per-app cache", "snap"),
        ("Trash", "XDG Trash (deleted files)", ".local/share/Trash"),
        ("Ollama", "ollama model cache", ".ollama/models"),
        ("Pip", "pip cache", ".cache/pip"),
        ("Yarn", "yarn cache", ".cache/yarn"),
        ("Electron", "Electron cache", ".cache/electron"),
        ("npm", "npm cache", ".npm"),
        ("Cargo", "Cargo registry cache", ".cargo/registry"),
        ("Rustup", "Rustup toolchains", ".rustup"),
        ("Gem", "Ruby gem cache", ".gem"),
        ("nvm", "nvm node versions", ".nvm"),
        ("Go", "Go module cache", "go/pkg"),
        ("Thumbnails", "XDG thumbnails cache", ".cache/thumbnails"),
        ("Maven", "Maven local repo", ".m2/repository"),
        ("Gradle", "Gradle cache", ".gradle"),
        ("Docker", "Docker build cache (per-user)", ".docker"),
        ("Steam", "Steam game cache", ".local/share/Steam/steamapps/common"),
    ]
    found = []
    for kind, label, rel in probes:
        hit = _probe_heavy_cache(profile_path, kind, label, rel)
        if hit:
            found.append(hit)
    return found


def _probe_heavy_caches_system() -> list:
    """Discover OS-level heavy cache paths."""
    probes = [
        ("AptCache", "APT package cache", "/var/cache/apt/archives"),
        ("PacmanCache", "Pacman package cache", "/var/cache/pacman/pkg"),
        ("DnfCache", "DNF/YUM package cache", "/var/cache/dnf"),
        ("FlatpakRuntime", "Flatpak shared runtime", "/var/lib/flatpak/runtime"),
        ("SnapCache", "Snap shared cache", "/var/lib/snapd/cache"),
        ("SystemTmp", "System temp", "/var/tmp"),
        ("JournalLogs", "systemd journal", "/var/log/journal"),
    ]
    found = []
    for kind, label, path_str in probes:
        p = Path(path_str)
        if p.exists():
            found.append({
                "Kind": kind,
                "Label": label,
                "Path": str(p),
                "SizeBytes": _path_size(p),
            })
    return found


def _expand_per_profile(profile_path: str, config) -> list:
    """Build the list of per-profile scan paths."""
    out = []
    base = Path(profile_path)

    standard = config.get("scanRoots", {}).get("perProfileStandardFolders", [])
    for name in standard:
        p = base / name
        if p.is_dir():
            out.append({"Kind": f"profile:{name}", "Path": str(p), "Note": ""})

    optional = config.get("scanRoots", {}).get("perProfileOptionalFolders", [])
    for name in optional:
        p = base / name
        if p.is_dir():
            out.append({"Kind": f"profile-opt:{name}", "Path": str(p), "Note": ""})

    # Per-user project roots
    for name in config.get("scanRoots", {}).get("perUserProjectRoots", []):
        p = base / name
        if p.is_dir():
            out.append({"Kind": f"profile-proj:{name}", "Path": str(p), "Note": ""})
    return out


def _probe_common_project_roots(config) -> list:
    """Probe the shared project root paths."""
    out = []
    for path_str in config.get("scanRoots", {}).get("commonProjectRoots", []):
        p = Path(path_str)
        if p.is_dir():
            out.append({"Kind": "project-root", "Path": str(p), "Note": ""})
    return out


def _probe_admin_roots(config, is_admin: bool) -> tuple:
    """Return (included, excluded) lists for adminOnlyRoots."""
    included, excluded = [], []
    for path_str in config.get("scanRoots", {}).get("adminOnlyRoots", []):
        p = Path(path_str)
        if not p.exists():
            continue
        entry = {"Kind": "admin-root", "Path": str(p), "Note": ""}
        if is_admin:
            included.append(entry)
        else:
            excluded.append({"Path": str(p), "Reason": "Not running as root (admin/root required)"})
    return included, excluded


def detect_environment(output_dir, config) -> dict:
    """Detect the runtime environment and write environment.json.

    Parameters
    ----------
    output_dir : str or Path
        Where to write environment.json.
    config : dict
        Parsed paths_to_scan config.

    Returns
    -------
    dict
        The detected environment snapshot.
    """
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    # os.geteuid() is Unix-only. On Linux/macOS it's always present; the guard
    # exists so this module also imports on Windows for dev/testing.
    is_admin = _is_root()
    os_info = _read_os_release()

    env = {
        "RunId": datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S"),
        "TimestampUtc": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "Os": {
            "Caption": os_info.get("PRETTY_NAME", "Unknown"),
            "Version": os_info.get("VERSION_ID", ""),
            "Build": os_info.get("VERSION_CODENAME", ""),
        },
        "PowerShell": f"Python {sys.version.split()[0]} on {sys.platform}",
        "Locale": {
            "Ui": os.environ.get("LANG", "C"),
            "Culture": os.environ.get("LC_ALL", os.environ.get("LANG", "C")),
            "DisplayName": os.environ.get("LANG", "C"),
        },
        "Admin": is_admin,
        "Drives": _list_fixed_drives(),
        "UserProfiles": _list_user_profiles(),
        "HeavyCaches": [],
        "ProjectRoots": [],
        "ScanRoots": [],
        "ExcludedRoots": [],
    }

    # Discover per-profile heavy caches
    for prof in env["UserProfiles"]:
        env["HeavyCaches"].extend(_probe_heavy_caches_for_profile(prof["Path"]))
    env["HeavyCaches"].extend(_probe_heavy_caches_system())

    # Discover project roots
    env["ProjectRoots"] = _probe_common_project_roots(config)

    # Build scan roots
    scan_roots = []

    # Fixed drives
    if config.get("scanRoots", {}).get("allFixedDrives", False):
        for d in env["Drives"]:
            scan_roots.append({"Kind": "drive", "Path": d["Root"], "Note": d["Description"]})

    # Per-profile folders
    if config.get("scanRoots", {}).get("allUserProfiles", False):
        for prof in env["UserProfiles"]:
            scan_roots.extend(_expand_per_profile(prof["Path"], config))

    # Admin-only roots
    admin_inc, admin_exc = _probe_admin_roots(config, is_admin)
    scan_roots.extend(admin_inc)
    env["ExcludedRoots"].extend(admin_exc)

    env["ScanRoots"] = scan_roots

    # Write environment.json
    env_path = output_dir / "environment.json"
    try:
        with env_path.open("w", encoding="utf-8") as f:
            json.dump(env, f, ensure_ascii=False, indent=2)
    except OSError:
        pass

    return env