# -*- coding: utf-8 -*-
"""The accepted photo's evidence copy is encoded while the detector is working.

After an accept, createReport re-encoded the whole photo with a synchronous
canvas.toDataURL (854 ms for 2250x4000 at 4x CPU), then fetched that data: URL back
into a Blob, and only then showed the detail screen. The encode does not depend on the
verdict. It now runs as an asynchronous toBlob beside the detector request; the only
toDataURL left is the detector's own frame, and the stored evidence is still the
complete frame.
"""

import sys

from playwright.sync_api import sync_playwright

from central_stub_harness import Central, capture, open_central

PROBE = r"""
(() => {
  window.__encodes = [];
  const toDataURL = HTMLCanvasElement.prototype.toDataURL;
  HTMLCanvasElement.prototype.toDataURL = function (...args) {
    window.__encodes.push({ kind: "toDataURL", w: this.width, h: this.height, at: performance.now() });
    return toDataURL.apply(this, args);
  };
  const toBlob = HTMLCanvasElement.prototype.toBlob;
  HTMLCanvasElement.prototype.toBlob = function (...args) {
    window.__encodes.push({ kind: "toBlob", w: this.width, h: this.height, at: performance.now() });
    return toBlob.apply(this, args);
  };
  // Hold the detector answer back so "while the detector works" is observable.
  const realFetch = window.fetch;
  window.fetch = async function (input, init) {
    const url = String(input && input.url || input);
    if (!url.includes("/v1/vision/detect")) return realFetch.call(this, input, init);
    const response = await realFetch.call(this, input, init);
    await new Promise((resolve) => setTimeout(resolve, 800));
    window.__verdictAt = performance.now();
    return response;
  };
})();
"""

fails = []
with sync_playwright() as playwright:
    central = Central()
    browser, page, dialogs, errors = open_central(playwright, central)
    try:
        page.context.add_init_script(script=PROBE)
        page.reload()
        page.wait_for_function("() => !!window.StandaloneAPI", timeout=30_000)
        page.locator("#home").wait_for(state="visible", timeout=30_000)
        page.wait_for_timeout(600)
        outcome, text = capture(page, dialogs)
        if outcome != "detail":
            fails.append(f"capture ended in {outcome} {text!r}")
        else:
            encodes, verdict_at = page.evaluate("[window.__encodes, window.__verdictAt]")
            data_urls = [e for e in encodes if e["kind"] == "toDataURL"]
            blobs = [e for e in encodes if e["kind"] == "toBlob"]
            if len(data_urls) != 1:
                fails.append(f"want only the detector frame through toDataURL, saw {data_urls}")
            if len(blobs) != 1:
                fails.append(f"want one asynchronous evidence encode, saw {blobs}")
            elif verdict_at is None or blobs[0]["at"] >= verdict_at:
                fails.append(f"the evidence encode began after the verdict: {blobs[0]} {verdict_at}")
            stored = page.evaluate("""async () => {
              const [r] = await StandaloneAPI.handle('/api/reports');
              const raw = (await StandaloneAPI.__pure.getReport(r.id)).photo_full;
              const blob = raw instanceof Blob ? raw
                : raw && raw.bytes ? new Blob([raw.bytes], { type: raw.type }) : null;
              if (!blob) return { status: r.status, kind: typeof raw };
              const bmp = await createImageBitmap(blob);
              return { status: r.status, type: blob.type, w: bmp.width, h: bmp.height };
            }""")
            if stored.get("status") != "draft" and stored.get("status") != "unrouted":
                fails.append(f"the report was not accepted: {stored}")
            if stored.get("type") != "image/jpeg" or (stored.get("w"), stored.get("h")) != (619, 1100):
                fails.append(f"the evidence is not the complete 619x1100 JPEG frame: {stored}")
        if errors:
            fails.append(f"page errors {errors[:3]}")
    finally:
        browser.close()

if fails:
    print("FAIL evidence encode overlap")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS evidence encode overlap")
