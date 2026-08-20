# -*- coding: utf-8 -*-
"""A response that sends headers and then goes silent must be abandoned, not waited on.

The timeout used to be cleared as soon as the headers arrived, so it only covered the
handshake. A stalled body was never aborted: the drive filled every slot, stopped
detecting, and the HUD went on reporting a healthy drive.
"""
import os, sys, pathlib
from dotenv import load_dotenv
ROOT = pathlib.Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
from playwright.sync_api import sync_playwright
from browser_test_utils import open_app

KEY = os.environ["OPENAI_API_KEY"]
BUDGET_MS = 45000        # the app's own REQUEST_TIMEOUT_MS is 30s; this allows for it

with sync_playwright() as p:
    b = p.chromium.launch(args=["--disable-web-security", "--allow-running-insecure-content"])
    pg = b.new_context(viewport={"width": 390, "height": 844}).new_page()
    open_app(pg, KEY)
    pg.wait_for_function("typeof StandaloneAPI !== 'undefined'", timeout=30000)

    r = pg.evaluate("""async () => {
      // Headers arrive, two SSE deltas arrive, then silence forever.
      const realFetch = window.fetch;
      let aborted = false;
      window.fetch = (url, init) => {
        if (!String(url).includes("api.openai.com")) return realFetch(url, init);
        const body = new ReadableStream({
          start(c) {
            const enc = new TextEncoder();
            c.enqueue(enc.encode('data: {"type":"response.output_text.delta","delta":"{\\\\"is_"}\\n\\n'));
            c.enqueue(enc.encode('data: {"type":"response.output_text.delta","delta":"pothole\\\\":"}\\n\\n'));
            // and then nothing, ever. A real fetch errors its body stream when the signal
            // aborts, so the mock has to do the same or the reader would never wake and
            // the test would be measuring itself rather than the app.
            if (init && init.signal) {
              init.signal.addEventListener("abort", () => {
                aborted = true;
                try { c.error(new DOMException("Aborted", "AbortError")); } catch (e) {}
              });
            }
          },
        });
        return Promise.resolve(new Response(body, {
          status: 200, headers: {"content-type": "text/event-stream"},
        }));
      };
      const c = document.createElement("canvas"); c.width = 64; c.height = 64;
      const blob = await new Promise((res) => c.toBlob(res, "image/jpeg", 0.8));
      const fd = new FormData();
      fd.append("photo", blob, "f.jpg");
      fd.append("lat", "12.9115"); fd.append("lng", "77.6427"); fd.append("drive_id", "stall");
      const t0 = performance.now();
      let outcome;
      try { await StandaloneAPI.handle("/api/frame", {method: "POST", body: fd}); outcome = "RESOLVED"; }
      catch (e) { outcome = "rejected: " + (e.message || "").slice(0, 60); }
      const ms = Math.round(performance.now() - t0);
      window.fetch = realFetch;
      return { outcome, ms, aborted };
    }""")
    b.close()

print(f"  outcome        : {r['outcome']}")
print(f"  took           : {r['ms']} ms")
print(f"  abort fired    : {r['aborted']}")
fails = []
if r["outcome"] == "RESOLVED":
    fails.append("a stalled body resolved, so the frame silently produced a verdict from nothing")
if not r["aborted"]:
    fails.append("the request was never aborted, so the slot would be held for the rest of the drive")
if r["ms"] > BUDGET_MS:
    fails.append(f"took {r['ms']} ms, beyond the {BUDGET_MS} ms budget")
print()
if fails:
    print("FAIL"); [print("  -", f) for f in fails]; sys.exit(1)
print("STALLED BODY TEST PASS")
