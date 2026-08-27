"""
plan — turn classified rows into a deterministic plan + accept overrides.

The plan shape (per-item) matches v1.1.0's plan.json so existing reports can
diff against v2 plans. Overrides are read from overrides.json (same shape as
the HTML override UI's POST body, validated against spec/overrides.schema.json
in tests).
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from . import classify as classify_mod

VALID_ACTIONS = {"keep", "group", "archive", "quarantine", "delete", "move"}


def _quarantine_dir(run_id: str) -> str:
    return f"_Quarantine/{run_id}"


def build_plan(
    rows: list[dict],
    *,
    run_id: str,
    overrides: list[dict] | None = None,
) -> dict:
    """Compose the plan.json payload from classified rows.

    Each row contributes one plan entry:
        {
            "path":   ...,
            "action": "keep" | "group" | ... ,
            "reason": "...",
            "destination": "<quarantine path or empty>",
            "reversible": bool,
            "category": "..."
        }
    """
    overrides_by_path: dict[str, str] = {}
    if overrides:
        for item in overrides:
            p = item.get("path", "")
            a = item.get("action", "")
            if p and a in VALID_ACTIONS:
                overrides_by_path[p] = a

    plan_items: list[dict] = []
    counts: dict[str, int] = {}
    for row in rows:
        cat = row.get("Category", "Other")
        action = overrides_by_path.get(row.get("Path", "")) or row.get("Action", "group")
        if action not in VALID_ACTIONS:
            action = "group"
        # SAFETY: pinned categories default to keep, even if a rule said otherwise.
        if cat in classify_mod.SAFETY_KEEP and action not in ("keep",):
            # If the user explicitly overrode an unsafe category, we still honor it
            if row.get("Path", "") not in overrides_by_path:
                action = "keep"
        destination = ""
        reversible = True
        reason_parts: list[str] = []
        if action == "keep":
            reason_parts.append("default keep")
            if cat in classify_mod.SAFETY_KEEP:
                reason_parts.append(f"safety-pinned category: {cat}")
        elif action == "quarantine":
            destination = f"{_quarantine_dir(run_id)}/{cat}/{row.get('Name', '')}"
            reason_parts.append(f"category {cat}")
        elif action == "archive":
            destination = f"_Archive/{run_id}/{cat}/{row.get('Name', '')}"
            reason_parts.append(f"category {cat}")
        elif action == "group":
            destination = f"_Grouped/{run_id}/{cat}/{row.get('Name', '')}"
            reason_parts.append(f"category {cat}")
        elif action == "move":
            destination = f"_Moved/{run_id}/{row.get('Name', '')}"
            reversible = True
            reason_parts.append("user move")
        elif action == "delete":
            destination = ""
            reversible = False
            reason_parts.append("explicit delete")
        reason_parts.append(f"rule={row.get('RuleMatched', '')}")
        plan_items.append({
            "path": row.get("Path", ""),
            "action": action,
            "reason": "; ".join(reason_parts),
            "destination": destination,
            "reversible": reversible,
            "category": cat,
            "sizeBytes": int(row.get("SizeBytes") or 0) if str(row.get("SizeBytes", "")).isdigit() else 0,
            "sha1": row.get("Sha1", ""),
        })
        counts[action] = counts.get(action, 0) + 1

    plan = {
        "RunId": run_id,
        "TimestampUtc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "Totals": {
            "items": len(plan_items),
            "byAction": counts,
        },
        "Items": plan_items,
    }
    return plan


def load_overrides(path: Path | str) -> list[dict]:
    """Read overrides.json (or the body of a POST /api/overrides)."""
    p = Path(path) if not isinstance(path, Path) else path
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return []
    return data.get("items", [])
