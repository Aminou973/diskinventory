"""
classify — rule-based classifier (v1.1.0 contract preserved + v2.0 overlays).

A rule has the shape:
    {
        "category": "<CategoryName>",
        "match": {
            "pathContains": [...],   # any-of, case-insensitive
            "pathLike":     [...],   # glob-style match on the path
            "markerFiles":  [...],   # basename match in marker-files list (collected.py)
            "fileExtensions": [...], # ".exe", ".dll" — leading dot optional
            "nameLike":     [...],   # glob on basename
            "nameEquals":   [...]    # exact basename match (case-insensitive)
        }
    }

First match wins. The `_SAFETY_KEEP` set pins categories that the apply step
must NEVER auto-mutate, even with a `group` plan.
"""

from __future__ import annotations

import fnmatch
import json
import os
from pathlib import Path
from typing import Any, Iterable

# Categories the planner/apply pipeline treats as immutable regardless of the
# override file. Matches classify_items.py from v1.1.0.
SAFETY_KEEP = {"App", "System", "Project", "Data", "HeavyCache"}


# Built-in fallback rules — used when config/classification.json is missing.
_DEFAULT_RULES = [
    {
        "category": "App",
        "match": {"pathContains": [
            "program files", "program files (x86)", "appdata\\local\\programs",
            "usr/bin", "usr/local/bin", "opt/", "/applications/",
        ]}
    },
    {
        "category": "System",
        "match": {"pathContains": [
            "\\windows\\", "/usr/lib", "/usr/share", "/etc",
            "/var/lib", "/var/cache", "/boot", "/sys/", "/proc/",
            "programdata", "$recycle.bin", "system volume information",
        ]}
    },
    {
        "category": "HeavyCache",
        "match": {"markerFiles": [
            "package-lock.json", "yarn.lock", "pnpm-lock.yaml",
            "Cargo.lock", "go.sum", "Pipfile.lock", "poetry.lock",
        ]}
    },
    {
        "category": "HeavyCache",
        "match": {"pathContains": [
            "\\.gradle\\", "\\.nuget\\packages", "\\.cargo\\registry",
            "\\.m2\\repository", "\\.npm\\_cacache", "\\.cache\\pip",
            "\\.cache\\yarn", "\\.cache\\pnpm", "\\.docker\\",
            "/go/pkg/mod", "/.cargo/registry", "/.npm/_cacache",
            "/.cache/pip", "/.cache/yarn", "/.cache/pnpm",
        ]}
    },
    {
        "category": "Project",
        "match": {"markerFiles": [
            ".git", ".svn", ".hg", "package.json", "pyproject.toml",
            "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
            "build.gradle.kts", "composer.json", "*.csproj", "*.sln",
        ]}
    },
    {
        "category": "Archive",
        "match": {"fileExtensions": [
            ".zip", ".7z", ".rar", ".tar", ".gz", ".tgz", ".bz2",
            ".xz", ".iso", ".img",
        ]}
    },
    {
        "category": "Image",
        "match": {"fileExtensions": [
            ".jpg", ".jpeg", ".png", ".gif", ".webp", ".tiff",
            ".heic", ".bmp", ".raw", ".cr2", ".nef",
        ]}
    },
    {
        "category": "Video",
        "match": {"fileExtensions": [
            ".mp4", ".mkv", ".mov", ".avi", ".wmv", ".flv", ".webm",
        ]}
    },
    {
        "category": "Audio",
        "match": {"fileExtensions": [
            ".mp3", ".flac", ".wav", ".aac", ".ogg", ".m4a", ".wma",
        ]}
    },
    {
        "category": "Document",
        "match": {"fileExtensions": [
            ".pdf", ".doc", ".docx", ".odt", ".rtf", ".txt", ".md",
            ".xls", ".xlsx", ".ppt", ".pptx", ".epub",
        ]}
    },
    {
        "category": "Executable",
        "match": {"fileExtensions": [".exe", ".msi", ".bat", ".cmd", ".sh"]}
    },
    {
        "category": "Junk",
        "match": {"fileExtensions": [
            ".tmp", ".temp", ".bak", ".old", ".log", ".cache",
        ]}
    },
]


def _norm_ext(ext: str) -> str:
    e = ext.lower().lstrip(".")
    return "." + e if e else ""


def _ext_of(p: str) -> str:
    return os.path.splitext(p)[1].lower()


def _norm(s: str) -> str:
    return s.replace("\\", "/").lower()


def load_rules(tool_dir: Path) -> list[dict]:
    """Load classification rules from config/ or fall back to defaults.

    Order: base + (linux|windows) overlay. The overlay is additive — extra
    rules appended to the base, with first-match ordering preserved.
    """
    cfg_dir = tool_dir / "config"
    base_path = cfg_dir / "classification.json"
    overlay_name = "classification.windows.json" if os.name == "nt" else "classification.linux.json"
    overlay_path = cfg_dir / overlay_name

    rules: list[dict] = []
    if base_path.is_file():
        try:
            rules.extend(json.loads(base_path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            rules.extend(_DEFAULT_RULES)
    else:
        rules.extend(_DEFAULT_RULES)

    if overlay_path.is_file():
        try:
            rules.extend(json.loads(overlay_path.read_text(encoding="utf-8")))
        except json.JSONDecodeError:
            pass

    return rules


def _matches_path_contains(rule: dict, path_lc: str) -> bool:
    for needle in rule.get("pathContains", []):
        if needle.lower() in path_lc:
            return True
    return False


def _matches_path_like(rule: dict, path: str) -> bool:
    for pattern in rule.get("pathLike", []):
        if fnmatch.fnmatch(path, pattern):
            return True
    return False


def _matches_marker_files(rule: dict, markers: Iterable[str]) -> bool:
    expected = [m.lower() for m in rule.get("markerFiles", [])]
    if not expected:
        return False
    for m in markers:
        ml = m.lower()
        for e in expected:
            if ml == e or fnmatch.fnmatch(ml, e):
                return True
    return False


def _matches_extensions(rule: dict, ext: str) -> bool:
    wanted = [_norm_ext(e) for e in rule.get("fileExtensions", [])]
    if not wanted:
        return False
    return ext in wanted


def _matches_name(rule: dict, name: str) -> bool:
    nl = name.lower()
    for pattern in rule.get("nameLike", []):
        if fnmatch.fnmatch(nl, pattern.lower()):
            return True
    for exact in rule.get("nameEquals", []):
        if nl == exact.lower():
            return True
    return False


def _matches(rule: dict, row: dict, markers: list[str]) -> bool:
    m = rule.get("match", {})
    if not m:
        return False
    path = row.get("Path", "")
    path_lc = _norm(path)
    name = row.get("Name", "")
    ext = _ext_of(path)
    if m.get("pathContains") and _matches_path_contains(m, path_lc):
        return True
    if m.get("pathLike") and _matches_path_like(m, path):
        return True
    if m.get("markerFiles") and _matches_marker_files(m, markers):
        return True
    if m.get("fileExtensions") and _matches_extensions(m, ext):
        return True
    if m.get("nameLike") or m.get("nameEquals"):
        if _matches_name(m, name):
            return True
    return False


# ---------------------------------------------------------------------------

def get_marker_files(dir_path: Path | str, names: list | None = None) -> list[str]:
    """List marker file basenames present in dir_path (top-level only).

    Accepts Path or str (handy in tests).
    """
    if not isinstance(dir_path, Path):
        dir_path = Path(dir_path)
    if not dir_path.is_dir():
        return []
    wanted = set(names or [
        ".git", ".svn", ".hg", "package.json", "pyproject.toml",
        "Cargo.toml", "go.mod", "pom.xml", "build.gradle",
        "build.gradle.kts", "composer.json", "package-lock.json",
        "yarn.lock", "pnpm-lock.yaml", "Cargo.lock", "go.sum",
        "Pipfile.lock", "poetry.lock",
    ])
    found: list[str] = []
    try:
        for entry in dir_path.iterdir():
            if entry.name in wanted:
                found.append(entry.name)
    except OSError:
        pass
    return found


def classify_rows(rows: list[dict], *, tool_dir: Path) -> None:
    """Annotate each row in-place with Category and Action.

    Mutates `rows` directly; returns None. After this call every row has a
    non-empty Category (the fallback bucket is "Other").
    """
    rules = load_rules(tool_dir)
    # Group rows by parent directory for marker-file detection efficiency
    by_parent: dict[str, list[dict]] = {}
    for row in rows:
        by_parent.setdefault(row.get("Parent", ""), []).append(row)

    markers_by_parent: dict[str, list[str]] = {}
    for parent, _ in by_parent.items():
        if not parent:
            continue
        markers_by_parent[parent] = get_marker_files(parent)

    for row in rows:
        markers = markers_by_parent.get(row.get("Parent", ""), [])
        category = "Other"
        for rule in rules:
            if _matches(rule, row, markers):
                category = rule.get("category", "Other")
                row["RuleMatched"] = rule.get("category", "")
                break
        row["Category"] = category
        row["Action"] = _default_action_for(category)


def _default_action_for(category: str) -> str:
    """Suggest a default apply action per category. Mirrors v1.1.0."""
    if category in SAFETY_KEEP:
        return "keep"
    if category in ("Junk",):
        return "quarantine"
    if category in ("Archive", "HeavyCache"):
        return "group"
    if category in ("Image", "Video", "Audio", "Document"):
        return "group"
    return "group"
