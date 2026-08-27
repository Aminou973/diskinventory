"""DiskInventory v2.0 — unified Python engine.

Subpackages / modules:
    env_detect, env_detect_windows  — environment detection (POSIX + Windows)
    collect                          — walk scan roots, build inventory rows
    classify                         — rule-based classifier
    classify_content                 — MIME sniff, SHA-1 dedup, EXIF, clustering
    plan                             — planner + overrides
    apply                            — mutating apply + JSON Lines journal
    restore                          — reverse a journal
    export                           — CSV + Markdown + offline HTML
    serve                            — local web UI (http.server + SSE)
    notify                           — webhook + OS-native desktop
    fleet                            — SSH coordinator + SQLite central store
    migrate                          — read v1.x run dir, emit v2 layout
"""
__version__ = "2.0.0"
