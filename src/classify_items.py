"""
classify_items — applies the rules config to each collected item.

Mirrors src/Classify-Items.ps1: first-match against categories in fixed order:
  System, App, Project, HeavyCache, Archive, Junk, Data, Unknown.

Each rule block supports `match` with these keys (any combination):
  pathContains    list of substrings to look for in the path (case-sensitive)
  pathLike        list of glob patterns (only `*` is honored) — matches full path
  markerFiles     list of file/dir names; checked against the item itself
                  (if dir) or its parent (if file), with up to 50 entries
  fileExtensions  list of extensions (case-insensitive suffix match, leading .)
  nameLike        list of glob patterns matched against the basename
  nameEquals      list of basenames matched exactly

Safety net (matches Windows tool exactly):
  App, System, Project, Data, HeavyCache are forced to action=keep
  unless an explicit override is supplied.
"""

from collect_inventory import get_marker_files


# Module-level marker cache: {parent_dir_str: [name1, name2, ...]}
_MARKER_CACHE: dict[str, list] = {}


def _cached_marker_files(parent_dir: str, patterns: list) -> list:
    """Per-directory marker file cache."""
    key = (parent_dir, tuple(patterns))
    if key in _MARKER_CACHE:
        return _MARKER_CACHE[key]
    out = get_marker_files(parent_dir, patterns)
    _MARKER_CACHE[key] = out
    return out


def _glob_match(name: str, pattern: str) -> bool:
    """Tiny glob: only `*` is supported."""
    if "*" not in pattern:
        return name == pattern
    parts = pattern.split("*")
    pos = 0
    for i, part in enumerate(parts):
        if not part:
            continue
        idx = name.find(part, pos)
        if idx < 0:
            return False
        if i == 0 and idx != 0:
            return False
        pos = idx + len(part)
    if parts[-1]:
        return name.endswith(parts[-1])
    return True


def _path_contains(path: str, needles: list) -> bool:
    return any(n in path for n in needles)


def _path_like(path: str, patterns: list) -> bool:
    return any(_glob_match(path, p) for p in patterns)


def _ext_match(name: str, exts: list) -> bool:
    name_lower = name.lower()
    return any(name_lower.endswith(e.lower()) for e in exts)


def _name_like(name: str, patterns: list) -> bool:
    return any(_glob_match(name, p) for p in patterns)


def _name_equals(name: str, names: list) -> bool:
    return name in names


def _match_item(item: dict, match: dict) -> tuple[bool, str]:
    """Try to match an item against a single rule's match block.

    Returns (matched, rule_name_or_reason).
    """
    path = item["Path"]
    name = item["Name"]
    kind = item.get("Kind", "File")
    is_dir = (kind == "Dir")

    # pathContains
    if "pathContains" in match:
        if _path_contains(path, match["pathContains"]):
            return True, f"pathContains:{match['pathContains'][0]}"
        else:
            return False, ""

    # pathLike
    if "pathLike" in match:
        if _path_like(path, match["pathLike"]):
            return True, f"pathLike:{match['pathLike'][0]}"
        else:
            return False, ""

    # fileExtensions
    if "fileExtensions" in match:
        if not is_dir and _ext_match(name, match["fileExtensions"]):
            return True, f"fileExtensions:{match['fileExtensions'][0]}"
        else:
            return False, ""

    # markerFiles: check the item's dir, or the parent dir if the item is a file
    if "markerFiles" in match:
        import os
        target_dir = path if is_dir else os.path.dirname(path)
        markers = _cached_marker_files(target_dir, match["markerFiles"])
        for m in markers:
            for pattern in match["markerFiles"]:
                if _glob_match(m, pattern):
                    return True, f"marker:{pattern}"
        return False, ""

    # nameLike
    if "nameLike" in match:
        if _name_like(name, match["nameLike"]):
            return True, f"nameLike:{match['nameLike'][0]}"
        else:
            return False, ""

    # nameEquals
    if "nameEquals" in match:
        if _name_equals(name, match["nameEquals"]):
            return True, f"nameEquals:{name}"
        else:
            return False, ""

    return False, ""


def _match_category(item: dict, category_def: dict) -> tuple[bool, str]:
    """Try to match an item against a single category. Returns (matched, rule_matched)."""
    match = category_def.get("match", {})
    if not match:
        return False, ""
    return _match_item(item, match)


# Categories force-kept by safety net (matches Windows tool)
_SAFETY_KEEP = {"App", "System", "Project", "Data", "HeavyCache"}


def classify_items(items, rules, now_utc=None) -> list:
    """Classify every item; returns a new list with Category/Action/RuleMatched/Notes fields."""
    from datetime import datetime, timezone
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    # Order from config (System, App, Project, HeavyCache, Archive, Junk, Data, Unknown)
    categories = rules.get("categories", [])
    default_category = rules.get("defaultCategory", "Unknown")
    default_action = rules.get("defaultAction", "keep")
    archive_cfg = rules.get("archive", {}) or {}
    archive_older_days = archive_cfg.get("olderThanDays")

    out = []
    for item in items:
        item = dict(item)  # shallow copy so we don't mutate the input
        cat_matched = None
        rule_matched = ""
        notes = []

        for cat_def in categories:
            matched, reason = _match_category(item, cat_def)
            if matched:
                cat_matched = cat_def["name"]
                rule_matched = reason
                # Add notes per category
                if cat_matched == "Archive" and archive_older_days is not None:
                    try:
                        last_write = datetime.fromisoformat(item["LastWriteUtc"].replace("Z", "+00:00"))
                        age_days = (now_utc - last_write).days
                        if age_days >= archive_older_days:
                            notes.append(f"last write {age_days} days ago (>= {archive_older_days})")
                        else:
                            notes.append(f"last write {age_days} days ago (< {archive_older_days}, recent)")
                    except Exception:
                        notes.append("could not parse last-write date")
                break

        if cat_matched is None:
            cat_matched = default_category
            rule_matched = "default"

        item["Category"] = cat_matched
        item["RuleMatched"] = rule_matched
        item["Notes"] = "; ".join(notes) if notes else ""

        # Default action per category
        # We look for an `action` field on the category definition; otherwise default.
        cat_def = next((c for c in categories if c.get("name") == cat_matched), None)
        suggested = (cat_def or {}).get("action", default_action)
        item["Action"] = suggested
        item["SuggestedAction"] = suggested

        # Safety net: keep App/System/Project/Data/HeavyCache
        if cat_matched in _SAFETY_KEEP and suggested != "keep":
            item["Action"] = "keep"
            item["SuggestedAction"] = "keep"

        # OneDrive placeholder note (no-op on Linux but field kept for parity)
        if item.get("IsOneDrivePlaceholder"):
            item["Notes"] = (item["Notes"] + "; " if item["Notes"] else "") + "OneDrive placeholder"

        out.append(item)

    return out