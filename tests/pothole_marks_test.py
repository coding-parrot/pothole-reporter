# -*- coding: utf-8 -*-
"""The pothole is marked on the photo: in the app, and on the copy that goes in the email.

The owner (9 Oct 2026): "could you be kind enough to mark the pothole in the image you
take". The service now sends `marks` with a damaged answer: boxes as fractions of the
whole frame, from a detector that says where. Here the service answers one photo with a
mark (and with junk beside it) and a second photo with none:
  - the report keeps the mark, and only the mark that is a box inside the frame;
  - the detail photo and the full-screen viewer show an outline round the pothole, not a
    filled patch over it;
  - the stored photo is untouched: the outline is drawn on a copy;
  - Email attaches the marked copy and the untouched photo, in that order;
  - a report with no mark shows and sends its photo exactly as before.
"""

import json
import sys
from urllib.parse import urlparse

from playwright.sync_api import sync_playwright

import flow_harness as fh
from central_stub_harness import routed
from flow_harness import error_failures, open_flow, report_form_script

# The test photo is 160 x 120 grey with a dark patch at x 50..110, y 52..90.
MARK = {"x": 50 / 160, "y": 52 / 120, "w": 60 / 160, "h": 38 / 120}
state = {"marks": [MARK, {"x": 0.9, "y": 0.9, "w": 0.5, "h": 0.5}, {"x": "a"}, None]}


def service(route, request):
    path = urlparse(request.url).path
    # A body the shipped pack has an address for, so the report can be emailed.
    if routed(route, request, path, None):
        return None
    if path != "/v1/vision/detect":
        return fh.central_service(route, request)
    captured = {}

    class Recorder:
        def fulfill(self, **kwargs):
            captured.update(kwargs)

    fh.central_service(Recorder(), request)
    body = json.loads(captured["body"])
    if state["marks"] is not None:
        body["marks"] = state["marks"]
    route.fulfill(status=captured["status"], headers=captured["headers"], body=json.dumps(body))


# Orange outline pixels near the box's top edge, and whether its middle was painted over.
PIXELS = """
async (source) => {
  const image = new Image();
  await new Promise((resolve, reject) => { image.onload = resolve; image.onerror = reject; image.src = source; });
  const canvas = document.createElement("canvas");
  canvas.width = image.naturalWidth; canvas.height = image.naturalHeight;
  const g = canvas.getContext("2d");
  g.drawImage(image, 0, 0);
  const w = canvas.width, h = canvas.height;
  const orange = (x, y) => { const [r, gr, b] = g.getImageData(Math.round(x), Math.round(y), 1, 1).data;
    return r > 190 && gr > 70 && gr < 190 && b < 120; };
  let edge = 0;
  for (let dy = -0.08; dy <= 0.03; dy += 0.005) edge += orange(w * 0.5, h * (52 / 120 + dy)) ? 1 : 0;
  const [r, gr, b] = g.getImageData(Math.round(w * 0.5), Math.round(h * (71 / 120)), 1, 1).data;
  return { width: w, height: h, edge, middleDark: r < 90 && gr < 90 && b < 90 };
}
"""

fails = []
with sync_playwright() as playwright:
    browser, page, errors = open_flow(playwright)
    page.route(f"{fh.SERVICE}/**", service)
    page.locator("#home").wait_for(state="visible", timeout=30_000)
    page.wait_for_function("() => !!(window.StandaloneAPI && window.openDetail)", timeout=30_000)

    # ---------- a photo the service marks ----------
    report = page.evaluate(report_form_script())
    if not report or not report.get("id"):
        fails.append(f"no report was created: {report}")
    stored = page.evaluate("""async (id) => {
      const db = await new Promise((resolve, reject) => { const open = indexedDB.open("potholes");
        open.onsuccess = () => resolve(open.result); open.onerror = () => reject(open.error); });
      const row = await new Promise((resolve) => { const get = db.transaction("reports").objectStore("reports").get(id);
        get.onsuccess = () => resolve(get.result); });
      db.close();
      const url = URL.createObjectURL(row.photo instanceof Blob ? row.photo : new Blob([row.photo.bytes], { type: row.photo.type }));
      return { marks: row.marks || null, photo: url };
    }""", report["id"])
    if stored["marks"] != [MARK]:
        fails.append(f"the report did not keep exactly the one real mark: {stored['marks']}")
    plain = page.evaluate(PIXELS, stored["photo"])
    if plain["edge"]:
        fails.append("the stored photo has the outline drawn into it")

    errors.clear()
    page.evaluate("loadReports()")
    page.evaluate("openDetail(window.__flowReport, [window.__flowReport])")
    page.locator("#detail").wait_for(state="visible", timeout=30_000)
    try:
        page.wait_for_function("() => { const img = document.querySelector('#detail .big-photo');"
                               " return img && img.dataset.marked === '1' && img.complete && img.naturalWidth > 0; }",
                               timeout=15_000)
    except Exception:
        fails.append("the detail photo never became the marked copy")
    shown = page.evaluate(PIXELS, page.evaluate("() => document.querySelector('#detail .big-photo').src"))
    if shown["edge"] < 2:
        fails.append(f"the detail photo shows no outline at the pothole's edge: {shown}")
    if not shown["middleDark"]:
        fails.append("the mark paints over the pothole instead of outlining it")
    if (shown["width"], shown["height"]) != (plain["width"], plain["height"]):
        fails.append(f"the marked copy is not the whole frame at its own size: {shown} against {plain}")

    page.locator("#detail [data-zoom]").click()
    page.wait_for_function("() => !document.getElementById('viewer').classList.contains('hidden')")
    viewer = page.evaluate(PIXELS, page.evaluate("() => document.getElementById('viewerImg').src"))
    if viewer["edge"] < 2:
        fails.append(f"the full-screen viewer shows the photo without its mark: {viewer}")
    page.evaluate("() => closeViewer()")
    fails += error_failures(errors, "showing a marked report")

    if page.locator("#detail #sendBtn").count() != 1:
        fails.append("the marked report has no Email action in this harness")
    else:
        page.locator("#detail #sendBtn").click()
        page.wait_for_function("() => (window.__composerCalls || []).length > 0", timeout=30_000)
        attachments = page.evaluate("() => window.__composerCalls[0].attachments")
        names = [item.get("name") for item in attachments]
        if len(attachments) != 2 or not names[0].endswith("-marked.jpg") or names[1].endswith("-marked.jpg"):
            fails.append(f"Email should attach the marked copy, then the untouched photo: {names}")
        else:
            sent = [page.evaluate(PIXELS, "data:image/jpeg;base64," + item["path"]) for item in attachments]
            if sent[0]["edge"] < 2 or not sent[0]["middleDark"]:
                fails.append(f"the first attachment is not the photo with the pothole outlined: {sent[0]}")
            if sent[1]["edge"]:
                fails.append("the second attachment, the evidence photo, has the outline drawn on it")

    # ---------- a photo the service does not mark ----------
    state["marks"] = None
    page.evaluate("() => { window.__composerCalls = []; document.getElementById('backBtn') && document.getElementById('backBtn').click(); }")
    second = page.evaluate(report_form_script(lat=12.9816, lng=77.6046))
    if second.get("marks"):
        fails.append(f"a report with no mark from the service has one: {second.get('marks')}")
    page.evaluate("openDetail(window.__flowReport, [window.__flowReport])")
    page.locator("#detail").wait_for(state="visible", timeout=30_000)
    page.wait_for_timeout(1200)
    unmarked = page.evaluate("() => document.querySelector('#detail .big-photo').dataset.marked || null")
    if unmarked:
        fails.append("a report with no mark is shown as marked")
    if page.locator("#detail #sendBtn").count() == 1:
        page.locator("#detail #sendBtn").click()
        page.wait_for_function("() => (window.__composerCalls || []).length > 0", timeout=30_000)
        names = page.evaluate("() => window.__composerCalls[0].attachments.map((item) => item.name)")
        if len(names) != 1 or names[0].endswith("-marked.jpg"):
            fails.append(f"a report with no mark should attach its one photo as before: {names}")
    browser.close()

if fails:
    print("FAIL")
    for fail in fails:
        print("  -", fail)
    sys.exit(1)
print("POTHOLE MARKS TEST PASS")
