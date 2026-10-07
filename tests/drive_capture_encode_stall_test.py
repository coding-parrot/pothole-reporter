# -*- coding: utf-8 -*-
"""A frame capture must not wait on canvas.toBlob.

On real phones (AWS Device Farm, 7 Oct 2026: Pixel 8a on Android 17, Galaxy A13 5G on
Android 11, Galaxy A15 on Android 14, all on WebView 153) the first frames of a drive
sometimes never came back. The preview played and the fix was fresh, yet each capture
outlived its 3 s limit; after three the drive said "Camera paused (another app may be
using it)" and reopened a camera that had been streaming to it all along, about 10 s
into the drive. drawImage is synchronous, so the call that did not answer was
canvas.toBlob, which Chromium encodes when the page next has idle time. Desktop Chromium
always has idle time, so no browser suite ever saw it.

Here toBlob is made to never answer. The drive must still sample frames, keep its one
camera, and never tell the driver the camera is paused. The frame it samples must be a
whole-frame JPEG the size of the preview.
"""

import sys

from playwright.sync_api import sync_playwright

from web_drive_harness import open_web_drive

SILENT_TO_BLOB = """
(() => {
  const open = navigator.mediaDevices.getUserMedia.bind(navigator.mediaDevices);
  window.__cameraOpens = 0;
  navigator.mediaDevices.getUserMedia = (constraints) => { window.__cameraOpens += 1; return open(constraints); };
  window.__toBlobCalls = 0;
  window.__realToBlob = HTMLCanvasElement.prototype.toBlob;
  HTMLCanvasElement.prototype.toBlob = function () { window.__toBlobCalls += 1; };
})();
"""

fails = []
with sync_playwright() as playwright:
    browser, page, dialogs, errors = open_web_drive(playwright)
    try:
        page.context.add_init_script(script=SILENT_TO_BLOB)
        page.reload()
        page.wait_for_function("() => !!window.StandaloneAPI && window.api.__stubbed", timeout=30_000)
        page.locator("#home").wait_for(state="visible", timeout=30_000)
        lost = page.evaluate("t('camera_lost')")
        page.locator("#driveBtn").click()
        page.locator("#driveStop").wait_for(state="visible", timeout=30_000)
        seen = []
        for _ in range(52):  # 13 s: three 3 s capture limits and the reopen fit inside
            page.wait_for_timeout(250)
            text = page.evaluate("document.getElementById('driveStatus').textContent")
            if not seen or seen[-1] != text:
                seen.append(text)
        state = page.evaluate("""() => ({
          captured: drive ? drive.tally.captured : -1, opens: window.__cameraOpens,
          asked: window.__toBlobCalls, sent: window.__frameStub.calls,
        })""")
        if any(lost in text for text in seen):
            fails.append(f"the drive said the camera was paused: {seen}")
        if state["opens"] != 1:
            fails.append(f"the camera was opened {state['opens']} times; a working camera is opened once")
        if state["captured"] < 5 or state["sent"] < 5:
            fails.append(f"13 s of driving sampled {state['captured']} frames and sent {state['sent']}; "
                         f"want at least 5 of each (toBlob was asked {state['asked']} times)")

        frame = page.evaluate("""async () => {
          const video = document.getElementById('driveVideo');
          const blob = await Promise.race([grabPreview(video),
            new Promise((resolve) => setTimeout(() => resolve(null), 2500))]);
          if (!blob) return null;
          const head = new Uint8Array(await blob.slice(0, 3).arrayBuffer());
          const bitmap = await createImageBitmap(blob);
          return { type: blob.type, size: blob.size, jpeg: head[0] === 0xFF && head[1] === 0xD8,
                   width: bitmap.width, height: bitmap.height,
                   videoWidth: video.videoWidth, videoHeight: video.videoHeight };
        }""")
        if not frame:
            fails.append("grabPreview gave no frame within 2.5 s when toBlob stayed silent")
        elif not (frame["jpeg"] and frame["type"] == "image/jpeg" and frame["size"] > 1000
                  and frame["width"] == frame["videoWidth"] and frame["height"] == frame["videoHeight"]):
            fails.append(f"the frame taken without toBlob is not a whole-frame JPEG: {frame}")

        # toBlob answering late must not hand the engine a second frame or an error.
        late = page.evaluate("""async () => {
          HTMLCanvasElement.prototype.toBlob = function (callback, type, quality) {
            setTimeout(() => window.__realToBlob.call(this, callback, type, quality), 1500);
          };
          let answers = 0;
          const first = await grabPreview(document.getElementById('driveVideo')).then((blob) => { answers += 1; return blob; });
          await new Promise((resolve) => setTimeout(resolve, 2200));
          return { answers, size: first ? first.size : 0 };
        }""")
        if late["answers"] != 1 or late["size"] < 1000:
            fails.append(f"a late toBlob changed the capture's answer: {late}")

        # With toBlob working, it is still the one that answers: no second encode.
        normal = page.evaluate("""async () => {
          HTMLCanvasElement.prototype.toBlob = window.__realToBlob;
          let dataUrls = 0;
          const realDataUrl = HTMLCanvasElement.prototype.toDataURL;
          HTMLCanvasElement.prototype.toDataURL = function (...args) { dataUrls += 1; return realDataUrl.apply(this, args); };
          const blob = await grabPreview(document.getElementById('driveVideo'));
          await new Promise((resolve) => setTimeout(resolve, 900));
          HTMLCanvasElement.prototype.toDataURL = realDataUrl;
          return { size: blob ? blob.size : 0, dataUrls };
        }""")
        if normal["size"] < 1000 or normal["dataUrls"] != 0:
            fails.append(f"with toBlob working the capture encoded twice or failed: {normal}")
        fails += [f"page error: {error}" for error in errors[:3]]
    finally:
        browser.close()

if fails:
    print("FAIL drive capture encode stall")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS drive capture encode stall")
