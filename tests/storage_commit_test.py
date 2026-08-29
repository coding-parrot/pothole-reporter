# -*- coding: utf-8 -*-
"""A write whose transaction aborts must reject, not report success."""
import os, sys, pathlib
from dotenv import load_dotenv
ROOT = pathlib.Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.launch(args=["--disable-web-security"])
    pg = b.new_context(viewport={"width":390,"height":844}).new_page()
    pg.goto("http://localhost:8765/"); pg.wait_for_load_state("networkidle")
    pg.wait_for_function("typeof StandaloneAPI !== 'undefined'", timeout=30000)
    r = pg.evaluate("""(async () => {
      // Abort the transaction from the request's success handler: this is the shape
      // Chrome produces when the device is out of storage. The request succeeds; the
      // transaction does not.
      const realPut = IDBObjectStore.prototype.put;
      IDBObjectStore.prototype.put = function (...args) {
        const req = realPut.apply(this, args);
        const tx = this.transaction;
        req.addEventListener("success", () => { try { tx.abort(); } catch (e) {} });
        return req;
      };
      let outcome;
      try {
        const fd = new FormData();
        fd.append("segment", new Blob([new Uint8Array(1024)], {type:"video/mp4"}), "c.mp4");
        fd.append("drive_id", "abort-test"); fd.append("seq", "0");
        await StandaloneAPI.handle("/api/footage", {method:"POST", body: fd});
        outcome = "RESOLVED";
      } catch (e) { outcome = "rejected: " + e.message.slice(0, 70); }
      IDBObjectStore.prototype.put = realPut;
      const back = await StandaloneAPI.handle("/api/footage").catch(() => []);
      const stored = back.filter((f) => String(f.drive_id) === "abort-test").length;
      return { outcome, stored };
    })()""")
    b.close()

print(f"  write outcome : {r['outcome']}")
print(f"  actually stored: {r['stored']} segments")
if r["outcome"] == "RESOLVED":
    print("\nFAIL: the write reported success for a transaction that rolled back")
    sys.exit(1)
if r["stored"]:
    print("\nFAIL: it rejected but the data is present, so the simulation is wrong")
    sys.exit(1)
print("\nOP COMMIT TEST PASS")
