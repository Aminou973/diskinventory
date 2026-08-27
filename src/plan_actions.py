"""
plan_actions — turns classified items into a proposed action list.

Mirrors src/Plan-Actions.ps1:
  - Honors overrides.json (shape: {"items": [{"path": "...", "action": "..."}]})
  - Re-checks excludeGlobs (force keep + reason)
  - Re-checks heavy cache paths (force keep + reason, unless override)
  - Computes destinations for quarantine/archive/group actions
  - Reversible for actions in {quarantine, archive, group, move, delete}

Differences from Windows tool:
  - No $env:SystemDrive prefix strip. Instead we derive the relative path
    from the nearest detected scan root (so the journal is portable).
"""

import json
import os
from pathlib import Path


_REVERSIBLE_ACTIONS = {"quarantine", "archive", "group", "move", "delete"}
_FORCE_KEEP_FROM_HEAVY_CACHE = True


def _load_overrides(overrides_path: str | None) -> dict:
    """Load overrides.json. Returns a dict {path: action}."""
    if not overrides_path:
        return {}
    p = Path(overrides_path)
    if not p.exists():
        return {}
    try:
        with p.open("r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception:
        return {}
    out = {}
    for item in data.get("items", []):
        path = item.get("path")
        action = item.get("action")
        if path and action:
            out[path] = action
    return out


def _is_in_exclude_glob(path: str, globs: list) -> str | None:
    for g in globs:
        if g in path:
            return g
    return None


def _is_in_heavy_cache(path: str, heavy_caches: list) -> str | None:
    for hc in heavy_caches:
        if hc.get("Path") and hc["Path"] in path:
            return hc["Path"]
    return None


def _derive_relative(path: str, scan_roots: list) -> str:
    """Derive a portable relative path under the nearest scan root.

    If the item is under any scan root, strip that root + leading separator.
    Otherwise fall back to using the path's own name (preserves uniqueness).
    """
    for root in scan_roots:
        if path.startswith(root + os.sep) or path == root:
            rel = path[len(root):].lstrip("/\\")
            return rel
    # Fallback: use just the basename (still reversibly unique within a run)
    return os.path.basename(path)


def plan_actions(classified, rules, config, overrides_path=None,
                 run_id: str = "", output_dir: str = "",
                 heavy_caches: list | None = None,
                 scan_roots: list | None = None) -> list:
    """Plan proposed actions. Returns a list of dicts.

    Each row: {Path, Action, Destination, SizeBytes, Category, Reason,
               RuleMatched, Reversible, Sha1}.
    """
    if heavy_caches is None:
        heavy_caches = []
    if scan_roots is None:
        scan_roots = []

    overrides = _load_overrides(overrides_path)
    exclude_globs = config.get("excludeGlobs", [])

    q_root = Path(output_dir) / (config.get("quarantineRootName", "_Quarantine")) / run_id
    a_root = Path(output_dir) / (config.get("archiveRootName", "_Archive")) / run_id
    g_root = Path(output_dir) / (config.get("groupRootName", "_Grouped")) / run_id

    plan = []
    for item in classified:
        path = item["Path"]
        action = item["Action"]
        category = item["Category"]
        rule_matched = item.get("RuleMatched", "")
        sha1 = item.get("Sha1")
        size = item.get("SizeBytes", 0)
        notes = item.get("Notes", "")

        # Apply override if any
        if path in overrides:
            action = overrides[path]
            reason = f"override:{action} (was: {item['Action']})"
        else:
            reason = notes or f"{category}:{rule_matched}"

        # Re-check exclude globs (safety)
        eg = _is_in_exclude_glob(path, exclude_globs)
        if eg is not None:
            action = "keep"
            reason = f"matches exclude glob '{eg}'"

        # Re-check heavy cache (safety)
        if _FORCE_KEEP_FROM_HEAVY_CACHE:
            hc = _is_in_heavy_cache(path, heavy_caches)
            if hc is not None and action in ("quarantine", "archive", "delete"):
                action = "keep"
                reason = f"in heavy-cache (would need explicit override): {hc}"

        # Compute destination
        destination = None
        if action in ("quarantine", "archive"):
            base_root = a_root if action == "archive" else q_root
            rel = _derive_relative(path, scan_roots)
            if rel:
                destination = str(base_root / rel)
            else:
                destination = str(base_root / item.get("Name", "unknown"))
        elif action == "group":
            rel = _derive_relative(path, scan_roots)
            if rel:
                destination = str(g_root / category / rel)
            else:
                destination = str(g_root / category / item.get("Name", "unknown"))
        elif action == "move":
            # Group move without a category folder
            rel = _derive_relative(path, scan_roots)
            destination = str(g_root / rel) if rel else None

        reversible = action in _REVERSIBLE_ACTIONS

        plan.append({
            "Path": path,
            "Action": action,
            "Destination": destination,
            "SizeBytes": size,
            "Category": category,
            "Reason": reason,
            "RuleMatched": rule_matched,
            "Reversible": reversible,
            "Sha1": sha1,
        })

    return plan