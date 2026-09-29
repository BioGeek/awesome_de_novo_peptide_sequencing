#!/usr/bin/env python3
"""Fail if any chart on the rendered site has a colliding or clipped label.

Serves _site over a loopback HTTP server, drives it in headless Chrome over
CDP, waits for the OJS cells to settle, and then measures, for every chart SVG
on the page, three things from real getBoundingClientRect() boxes:

  * text-text overlaps      two labels on top of each other
  * text-circle overlaps    a label lying over a dot
  * text outside the SVG    a label clipped by the frame

All three should be 0. They were not: the first run found 7 label-label and 15
label-dot collisions in the architectures swim lane, 49 labels centred on their
own dot in "The long view" (Plot's dx and textAnchor are constants, not
channels, so a function applies neither), 123 and 457 overlapping label pairs
in the two force-directed networks, and five axis labels touching a tick.

Why the DOM and not the SVG source: every one of these depends on the rendered
width of a string in a particular font at a particular size. A layout that
reserves space from a character count -- which all of the greedy label placers
in index.qmd do -- is exactly the kind that looks right in code and overlaps on
screen, so the only honest check is to measure the glyphs.

Needs a rendered _site (quarto render) and google-chrome. Run it with

    uv run --with websockets python3 check_chart_overlap.py

Exits 1 if anything collides, listing the worst offenders per chart.
"""

import json, subprocess, sys, time, urllib.request, shutil, os, signal, socket, atexit, http.server, threading, functools
from websockets.sync.client import connect

import tempfile
from pathlib import Path

SITE = str(Path(__file__).parent / "_site")
CHROME = (shutil.which("google-chrome") or shutil.which("chromium")
          or shutil.which("chromium-browser"))
if not CHROME:
    sys.exit("no chrome on PATH")
if not Path(SITE, "index.html").exists():
    sys.exit(f"no rendered site at {SITE}: run `uv run quarto render index.qmd` first")
# A throwaway profile per run: a surviving browser holding the debug port is
# what once made three runs attach to a stale, already-broken tab.
PROFILE = tempfile.mkdtemp(prefix="chart-overlap-")

handler = functools.partial(http.server.SimpleHTTPRequestHandler, directory=SITE)
httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
PORT = httpd.server_address[1]
threading.Thread(target=httpd.serve_forever, daemon=True).start()

_s = socket.socket(); _s.bind(("127.0.0.1", 0)); DBG = _s.getsockname()[1]; _s.close()
chrome = subprocess.Popen(
    [CHROME, "--headless=new", f"--remote-debugging-port={DBG}", f"--user-data-dir={PROFILE}",
     "--no-first-run", "--disable-gpu", "--window-size=1600,4000",
     "--disable-background-timer-throttling", "--disable-renderer-backgrounding",
     "--disable-backgrounding-occluded-windows", "--disable-ipc-flooding-protection",
     f"http://127.0.0.1:{PORT}/index.html"],
    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)

@atexit.register
def _reap():
    if chrome.poll() is None:
        chrome.send_signal(signal.SIGTERM)
        try: chrome.wait(timeout=10)
        except Exception: chrome.kill()
    shutil.rmtree(PROFILE, ignore_errors=True)

def ws_url():
    for _ in range(80):
        try:
            for t in json.load(urllib.request.urlopen(f"http://127.0.0.1:{DBG}/json")):
                if t.get("type") == "page" and "webSocketDebuggerUrl" in t:
                    return t["webSocketDebuggerUrl"]
        except Exception: pass
        time.sleep(0.5)
    raise SystemExit("no CDP target")

ws = connect(ws_url(), max_size=256 * 1024 * 1024)
_id = [0]
def ev(expr):
    _id[0] += 1
    ws.send(json.dumps({"id": _id[0], "method": "Runtime.evaluate",
                        "params": {"expression": expr, "returnByValue": True}}))
    while True:
        m = json.loads(ws.recv())
        if m.get("id") == _id[0]:
            r = m.get("result", {})
            if "exceptionDetails" in r:
                raise SystemExit("JS error: " + json.dumps(r["exceptionDetails"])[:600])
            return r["result"].get("value")

PROBE = r"""
(() => {
  const inter = (a, b) => {
    const w = Math.min(a.right, b.right) - Math.max(a.left, b.left);
    const h = Math.min(a.bottom, b.bottom) - Math.max(a.top, b.top);
    return (w > 1.5 && h > 1.5) ? Math.round(Math.min(w, h)) : 0;
  };
  const out = {errs: document.querySelectorAll('.observablehq--error').length, charts: []};
  for (const sec of document.querySelectorAll('section[id]')) {
    let n = 0;
    for (const svg of sec.querySelectorAll('svg')) {
      const texts = Array.from(svg.querySelectorAll('text'))
        .filter(t => t.textContent.trim() && t.getBoundingClientRect().width > 0);
      if (texts.length < 2) continue;
      const circles = Array.from(svg.querySelectorAll('circle'));
      const box = svg.getBoundingClientRect();
      const scale = box.width / (+svg.getAttribute('width') || box.width) || 1;
      const tb = texts.map(t => ({t: t.textContent.trim().slice(0,38), r: t.getBoundingClientRect()}));
      const tt = [], tc = [], over = [];
      for (let i = 0; i < tb.length; i++) for (let j = i + 1; j < tb.length; j++) {
        const o = inter(tb[i].r, tb[j].r);
        if (o) tt.push([Math.round(o / scale), tb[i].t + ' | ' + tb[j].t]);
      }
      for (const x of tb) for (const c of circles) {
        const o = inter(x.r, c.getBoundingClientRect());
        if (o) tc.push([Math.round(o / scale), x.t]);
      }
      for (const x of tb) {
        const d = Math.max(box.left - x.r.left, x.r.right - box.right,
                           box.top - x.r.top, x.r.bottom - box.bottom);
        if (d > 1.5) over.push([Math.round(d / scale), x.t]);
      }
      out.charts.push({sec: sec.id + '#' + (n++), texts: texts.length,
        h: Math.round(+svg.getAttribute('height') || box.height),
        tt: tt.length, tt_top: tt.sort((a,b)=>b[0]-a[0]).slice(0,5),
        tc: tc.length, tc_top: tc.sort((a,b)=>b[0]-a[0]).slice(0,5),
        out: over.length, out_top: over.sort((a,b)=>b[0]-a[0]).slice(0,3)});
    }
  }
  return out;
})()
"""

t0 = time.time(); last = None
while time.time() - t0 < 150:
    r = ev(PROBE)
    if r and r.get("charts") and r == last:
        break
    last = r; time.sleep(3)
res = last or {"errs": 1, "charts": []}
ws.close()

if not res["charts"]:
    print("no charts measured: the page never settled")
    sys.exit(1)

seen = {}
for c in res["charts"]:
    seen[c["sec"]] = c
bad = [c for c in seen.values() if c["tt"] or c["tc"] or c["out"]]
for c in bad:
    print(f"{c['sec']}: {c['tt']} label-label, {c['tc']} label-dot, {c['out']} clipped")
    for key, what in (("tt_top", "overlap"), ("tc_top", "over a dot"), ("out_top", "clipped")):
        for row in c[key][:4]:
            print(f"    {row[0]:>4} px {what}: {row[1]}")
print(f"{len(seen)} chart SVGs, {res['errs']} OJS errors, "
      f"{len(bad)} with a collision")
sys.exit(1 if (bad or res["errs"]) else 0)
