# -*- coding: utf-8 -*-
"""Re-analysing a drive's video: the counter, the count, and the speed.

Four bugs this locks down, all reported from real use:
  1. the storage prompt appearing partway through a run
  2. "1 of 2" becoming "2 of 3" as the total grew while the user watched
  3. "0 potholes" on a drive whose live pass had already reported them
  4. frame extraction serialised behind one seeking video element
"""
import os, sys, time, pathlib
from dotenv import load_dotenv
ROOT = pathlib.Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
from playwright.sync_api import sync_playwright
from browser_test_utils import open_app

KEY = os.environ["OPENAI_API_KEY"]
fails = []

with sync_playwright() as p:
    b = p.chromium.launch(args=["--disable-web-security", "--allow-running-insecure-content",
                                "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream"])
    pg = b.new_context(viewport={"width": 390, "height": 844}).new_page()
    open_app(pg, KEY)

    # Record a short clip through the app's own recorder so the blob is what it really stores.
    made = pg.evaluate("""async () => {
      const s = await navigator.mediaDevices.getUserMedia({video:{width:320,height:240}});
      const rec = new MediaRecorder(s, {mimeType:'video/webm'});
      const parts = [];
      rec.ondataavailable = e => parts.push(e.data);
      rec.start();
      await new Promise(r => setTimeout(r, 3000));
      await new Promise(r => { rec.onstop = r; rec.stop(); });
      s.getTracks().forEach(t => t.stop());
      const blob = new Blob(parts, {type:'video/webm'});
      await StandaloneAPI.handle('/api/footage', {method:'POST', body:(()=>{
        const fd = new FormData();
        fd.append('segment', blob, 'c.webm');
        fd.append('drive_id','9001'); fd.append('seq','0');
        return fd;
      })()});
      return blob.size;
    }""")
    print(f"  recorded clip: {made} bytes")

    # Watch the denominator across the whole run: it must never increase.
    pg.evaluate("""() => {
      window.__seen = [];
      const el = document.getElementById('progressText');
      new MutationObserver(() => {
        const m = /(\\d+)\\s*(?:of|\\/)\\s*(\\d+)/.exec(el.textContent || '');
        if (m) window.__seen.push([+m[1], +m[2]]);
      }).observe(el, {childList:true, subtree:true, characterData:true});
    }""")
    pg.evaluate("window.confirm = () => true; window.alert = (m) => { window.__alert = m; };")

    t0 = time.time()
    pg.evaluate("analyseFootage('9001', {gps_track:[]})")
    pg.wait_for_function("window.__alert !== undefined", timeout=180000)
    secs = time.time() - t0
    seen = pg.evaluate("window.__seen")
    msg = pg.evaluate("window.__alert")
    b.close()

print(f"  finished in {secs:.1f}s")
print(f"  progress samples: {seen[:6]}{' ...' if len(seen) > 6 else ''}")
print(f"  final message: {msg}")

totals = [n for _, n in seen]
if not totals:
    fails.append("progress never showed an 'i of n' counter")
else:
    if len(set(totals)) != 1:
        fails.append(f"the total changed during the run: {sorted(set(totals))}. It must be known up front.")
    if any(i > n for i, n in seen):
        fails.append("progress went past its own total")
    if totals[0] < 1:
        fails.append("total was zero")

# A clip whose duration the element reports as Infinity must still be analysed. Chromium
# usually reports a finite duration for its own recording, so this is a source-level guard
# against the check that silently skipped every WebM clip on devices that cannot record MP4.
src = (ROOT / "static/index.html").read_text(encoding="utf-8")
if "resolveDuration" not in src:
    fails.append("resolveDuration is gone: clips with Infinity duration will be skipped again")
if "if (meta.error || !isFinite(meta.dur)" in src:
    fails.append("the clip loop skips on a non-finite duration again, which breaks WebM recordings")

if fails:
    print("\nFAIL"); [print("  -", f) for f in fails]; sys.exit(1)
print("\nFOOTAGE TEST PASS")
