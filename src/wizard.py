"""wizard.py — first-run setup wizard.

Single-page HTML served at ``/setup``. The page is fully self-contained:
no external CSS, no JS libraries, no fetch to any CDN. It POSTs the
chosen scan roots + features to ``/api/setup``, which returns 302 to
``/`` once the config is persisted.

Persistence: ``~/.diskinventory/config.json`` (resolved from
``Path.home()``). Subsequent runs read this file and skip the wizard
unless the user explicitly invokes ``disk-inventory setup`` again.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, asdict
from pathlib import Path

WIZARD_VERSION = "3.0"

CONFIG_PATH = Path.home() / ".diskinventory" / "config.json"


@dataclass
class WizardConfig:
    scan_roots: list[dict]
    compute_hashes: bool = False
    classify_cluster: bool = False
    classify_exif: bool = False
    auto_purge_days: int = 30
    notify_webhook: str = ""
    version: str = WIZARD_VERSION


def load_config(path: Path | None = None) -> WizardConfig | None:
    p = Path(path) if path else CONFIG_PATH
    if not p.is_file():
        return None
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return WizardConfig(
        scan_roots=d.get("scan_roots", []),
        compute_hashes=bool(d.get("compute_hashes", False)),
        classify_cluster=bool(d.get("classify_cluster", False)),
        classify_exif=bool(d.get("classify_exif", False)),
        auto_purge_days=int(d.get("auto_purge_days", 30)),
        notify_webhook=d.get("notify_webhook", ""),
        version=d.get("version", WIZARD_VERSION),
    )


def save_config(cfg: WizardConfig, path: Path | None = None) -> Path:
    p = Path(path) if path else CONFIG_PATH
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(asdict(cfg), ensure_ascii=False, indent=2),
                 encoding="utf-8")
    return p


def needs_wizard(path: Path | None = None) -> bool:
    """True iff no config exists OR the config is for an older wizard version."""
    cfg = load_config(path)
    if cfg is None:
        return True
    if cfg.version != WIZARD_VERSION:
        return True
    return not cfg.scan_roots


def setup_html(engine_version: str = "",
               smart_roots: list[dict] | None = None) -> str:
    """Return the HTML page for the /setup route (vanilla JS, no CDN)."""
    roots_json = json.dumps(smart_roots or [], ensure_ascii=False)
    return _WIZARD_HTML_TEMPLATE \
        .replace("{{ENGINE_VERSION}}", engine_version) \
        .replace("{{SMART_ROOTS_JSON}}", roots_json)


# ---------------------------------------------------------------------------
# Self-contained wizard HTML. No external CSS, no CDNs, vanilla JS only.
# ---------------------------------------------------------------------------

_WIZARD_HTML_TEMPLATE = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>DiskInventory — Setup</title>
<style>
  :root { color-scheme: light dark; }
  body { font: 14px/1.5 -apple-system, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
         margin: 0; background: Canvas; color: CanvasText; }
  .wrap { max-width: 760px; margin: 0 auto; padding: 2rem 1.5rem; }
  h1 { margin: 0 0 .25rem; font-size: 1.6rem; }
  h2 { margin: 1.5rem 0 .5rem; font-size: 1.15rem; }
  .pill { display: inline-block; padding: .15rem .65rem;
          border-radius: 999px; font-size: .8rem;
          background: color-mix(in oklab, Canvas 86%, CanvasText 14%); }
  .panel { display: none; }
  .panel.show { display: block; }
  button { padding: .55rem 1rem; cursor: pointer; }
  button.primary { background: #246cff; color: #fff; border: 0; border-radius: 6px; }
  button.secondary { background: transparent; border: 1px solid #8886;
                     color: CanvasText; border-radius: 6px; }
  ul.roots { list-style: none; padding: 0; margin: .25rem 0; }
  ul.roots li { padding: .35rem .5rem; border-bottom: 1px solid #8883;
                display: flex; align-items: center; gap: .5rem; }
  ul.roots li code { flex: 1; }
  ul.roots li button { font-size: .8rem; padding: .2rem .5rem; }
  .feat { padding: .5rem; border: 1px solid #8883; border-radius: 6px;
          margin: .35rem 0; }
  .feat label { display: flex; align-items: center; gap: .5rem; cursor: pointer; }
  .small { font-size: .85rem; opacity: .8; }
  .err { color: #c33; }
  .ok  { color: #2c7; }
  .nav { display: flex; justify-content: space-between; margin-top: 1.5rem; }
</style>
</head>
<body>
<div class="wrap">
  <h1>DiskInventory Setup</h1>
  <p><span class="pill">engine: {{ENGINE_VERSION}}</span> <span class="pill">first run</span></p>

  <div id="step1" class="panel show">
    <h2>1. Welcome</h2>
    <p>DiskInventory is a content-aware disk-space inspector. We pick sensible
       defaults from your home directory, run a read-only scan, and let you
       decide what to keep.</p>
    <p class="small">Nothing in this step is irreversible. You can re-run this
       wizard any time with <code>disk-inventory setup</code>.</p>
    <div class="nav"><span></span>
      <button class="primary" id="s1next">Next →</button></div>
  </div>

  <div id="step2" class="panel">
    <h2>2. Scan roots</h2>
    <p>We picked these from your home directory. Add or remove as needed.</p>
    <ul class="roots" id="rootsList"></ul>
    <div>
      <input id="newRoot" placeholder="/full/path/to/folder" style="width: 70%;">
      <button class="secondary" id="addRoot">Add</button>
    </div>
    <p class="small">First-run defaults are curated; "Home" is the fallback
       if nothing else is detected.</p>
    <div class="nav">
      <button class="secondary" id="s2back">← Back</button>
      <button class="primary" id="s2next">Next →</button></div>
  </div>

  <div id="step3" class="panel">
    <h2>3. Optional features</h2>
    <div class="feat">
      <label><input type="checkbox" id="optHashes"> SHA-1 hash every file
        <span class="small">— enables duplicate detection; O(file) per file</span></label>
    </div>
    <div class="feat">
      <label><input type="checkbox" id="optCluster">
        Name-similarity clustering
        <span class="small">— groups files whose names look alike</span></label>
    </div>
    <div class="feat">
      <label><input type="checkbox" id="optExif">
        EXIF date grouping
        <span class="small">— requires Pillow (auto-installs if enabled)</span></label>
    </div>
    <div class="feat">
      <label><input type="checkbox" id="optWebhook" checked>
        Desktop notifications
        <span class="small">— native toasts on completion</span></label>
    </div>
    <h3>Auto-purge quarantine</h3>
    <p><input type="number" id="purgeDays" value="30" min="1" max="365" style="width:4rem"> days
       (set to <code>0</code> to disable).</p>
    <div class="nav">
      <button class="secondary" id="s3back">← Back</button>
      <button class="primary" id="s3next">Next →</button></div>
  </div>

  <div id="step4" class="panel">
    <h2>4. Review &amp; launch</h2>
    <div id="review"></div>
    <p class="small">Clicking <strong>Run scan</strong> writes this config to
       <code>~/.diskinventory/config.json</code> and kicks off the scan. The
       dashboard will open at <code>http://127.0.0.1:8765/</code> when ready.</p>
    <div class="nav">
      <button class="secondary" id="s4back">← Back</button>
      <button class="primary" id="s4launch">Run scan</button></div>
    <p id="status" class="small"></p>
  </div>

<script>
(function () {
  'use strict';
  var SMART_ROOTS = {{SMART_ROOTS_JSON}};
  var state = { roots: SMART_ROOTS.slice() };

  function $(id) { return document.getElementById(id); }
  function showStep(n) {
    [1,2,3,4].forEach(function(k) {
      $('step' + k).classList.toggle('show', k === n);
    });
  }

  function renderRoots() {
    var ul = $('rootsList'); ul.innerHTML = '';
    state.roots.forEach(function (r, idx) {
      var li = document.createElement('li');
      li.innerHTML = '<code>' + (r.Path || r.path || '') + '</code>' +
                     '<span class="small">' + (r.Name || r.name || '') + '</span>' +
                     '<button data-idx="' + idx + '">remove</button>';
      ul.appendChild(li);
    });
    Array.prototype.forEach.call(ul.getElementsByTagName('button'),
      function (b) {
        b.onclick = function () {
          var i = parseInt(b.getAttribute('data-idx'), 10);
          state.roots.splice(i, 1); renderRoots();
        };
      });
  }

  $('s1next').onclick = function () { renderRoots(); showStep(2); };
  $('s2back').onclick = function () { showStep(1); };
  $('s2next').onclick = function () {
    if (!state.roots.length) {
      alert('Add at least one scan root (or press Back to use defaults).');
      return;
    }
    showStep(3);
  };
  $('s3back').onclick = function () { showStep(2); };
  $('s3next').onclick = function () {
    var review = $('review');
    review.innerHTML =
      '<h3>Scan roots</h3><ul>' +
      state.roots.map(function (r) {
        return '<li><code>' + (r.Path || r.path || '') + '</code>' +
               ' <span class="small">' + (r.Name || r.name || '') + '</span></li>';
      }).join('') + '</ul>' +
      '<h3>Features</h3><ul>' +
      '<li>SHA-1 dedup: '  + ($('optHashes').checked  ? 'on' : 'off') + '</li>' +
      '<li>Clustering: '  + ($('optCluster').checked ? 'on' : 'off') + '</li>' +
      '<li>EXIF dates: '  + ($('optExif').checked    ? 'on (Pillow auto-install)' : 'off') + '</li>' +
      '<li>Notifications: ' + ($('optWebhook').checked ? 'on' : 'off') + '</li>' +
      '<li>Auto-purge quarantine: ' + $('purgeDays').value + ' days</li>' +
      '</ul>';
    showStep(4);
  };
  $('s4back').onclick = function () { showStep(3); };
  $('addRoot').onclick = function () {
    var v = $('newRoot').value.trim();
    if (!v) return;
    state.roots.push({ Name: 'Custom', Path: v, Source: 'user' });
    $('newRoot').value = '';
    renderRoots();
  };

  $('s4launch').onclick = function () {
    var payload = {
      scan_roots: state.roots,
      compute_hashes: $('optHashes').checked,
      classify_cluster: $('optCluster').checked,
      classify_exif: $('optExif').checked,
      auto_purge_days: parseInt($('purgeDays').value, 10) || 0,
      notify_webhook: $('optWebhook').checked ? '' : '',
      version: '3.0'
    };
    $('s4launch').disabled = true;
    $('status').textContent = 'Saving config…';
    fetch('/api/setup', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify(payload)
    }).then(function (r) {
      if (r.status === 200 || r.status === 204) {
        $('status').innerHTML = '<span class="ok">Saved. Kicking off scan…</span>';
        // No redirect: the dashboard will refresh itself when /api/run updates.
        setTimeout(function () { window.location = '/'; }, 2500);
      } else {
        $('s4launch').disabled = false;
        $('status').innerHTML = '<span class="err">Failed: HTTP '
          + r.status + '</span>';
      }
    }).catch(function (e) {
      $('s4launch').disabled = false;
      $('status').innerHTML = '<span class="err">' + e + '</span>';
    });
  };
})();
</script>
</body></html>
"""


__all__ = [
    "WIZARD_VERSION",
    "WizardConfig",
    "load_config",
    "save_config",
    "needs_wizard",
    "setup_html",
    "CONFIG_PATH",
]
