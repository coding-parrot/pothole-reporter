# -*- coding: utf-8 -*-
"""A report card names what happened to it, and keeps saying what the model found.

Every unrouted report wore the chip "Outside coverage", although the line under it said
"No GPS", "location too coarse" or "no address for this body". After Email, the verdict
slot was replaced by the reopen instruction, so the size and the detection verdict were
gone. An accepted report never showed the model's description in full: the draft card
left it out and the viewer cut it to one line. The viewer's swipe to the next report
was not mentioned anywhere.
"""

import sys

from playwright.sync_api import sync_playwright

from flow_harness import error_failures, open_flow

BASE = {"lat": 12.9716, "lng": 77.5946, "size": "medium", "damage_type": "pothole",
        "assessment": "clear", "created_at": 1_758_000_000,
        "description": ("A cavity with a broken rim is visible on the travelled lane, "
                        "with loose gravel around its edge and water pooled at the bottom.")}

UNROUTED = {
    "no_location": "chip_no_gps",
    "location_uncertain": "chip_gps_coarse",
    "jurisdiction_unavailable": "chip_routing_unavailable",
    "no_address_for_body": "chip_no_address",
    "national_highway": "chip_highway",
}

fails = []
with sync_playwright() as playwright:
    browser, page, errors = open_flow(playwright)
    try:
        page.locator("#home").wait_for(state="visible", timeout=30_000)
        page.wait_for_timeout(1000)

        def detail(report, list_=None):
            page.evaluate("([r, list]) => { openDetail(r, list || undefined); show('detail'); }",
                          [report, list_])
            return page.evaluate("""() => ({
              chip: document.querySelector('#detail .chip')?.textContent || '',
              verdict: document.querySelector('#detail .verdict')?.textContent.trim() || '',
              text: document.querySelector('#detail').innerText })""")

        for reason, key in UNROUTED.items():
            want = page.evaluate("(key) => t(key)", key)
            if want == key:
                fails.append(f"{key} has no string")
                continue
            got = detail({**BASE, "id": f"u-{reason}", "status": "unrouted",
                          "unrouted_reason": reason, "road_ref": "NH 48"})
            if got["chip"] != want:
                fails.append(f"unrouted {reason}: chip {got['chip']!r}, expected {want!r}")
        # A reason with no shorter name keeps the general chip.
        got = detail({**BASE, "id": "u-other", "status": "unrouted", "unrouted_reason": "outside_state"})
        general = page.evaluate("() => t('chip_unrouted')")
        if got["chip"] != general:
            fails.append(f"unrouted outside_state: chip {got['chip']!r}, expected {general!r}")

        detected = page.evaluate("() => t('verdict_detected')")
        size = page.evaluate("() => tSize('medium')")
        reopen = page.evaluate("() => t('verdict_queued')")
        for status in ("queued", "sent"):
            got = detail({**BASE, "id": f"s-{status}", "status": status})
            if detected not in got["verdict"] or size not in got["verdict"]:
                fails.append(f"{status}: verdict slot reads {got['verdict']!r}, lost the detection")
            if reopen in got["verdict"]:
                fails.append(f"{status}: the reopen instruction is still styled as the verdict")
            if reopen not in got["text"]:
                fails.append(f"{status}: the reopen instruction is no longer shown")

        got = detail({**BASE, "id": "d-1", "status": "draft"})
        if BASE["description"] not in got["text"]:
            fails.append("draft: the model's description is not on the card")

        pair = [{**BASE, "id": "v-1", "status": "draft"}, {**BASE, "id": "v-2", "status": "draft"}]
        detail(pair[0], pair)
        page.evaluate("() => openViewer(detailList[0])")
        clamp = page.evaluate("""() => {
          const el = document.querySelector('#viewerMeta .viewer-description');
          const style = getComputedStyle(el);
          return { wrap: style.whiteSpace, lines: style.webkitLineClamp,
                   full: el.scrollHeight <= el.clientHeight + 1 || style.webkitLineClamp === '2' };
        }""")
        if clamp["wrap"] == "nowrap":
            fails.append("viewer: the description is still cut to one line")
        if clamp["lines"] != "2":
            fails.append(f"viewer: the description clamp is {clamp['lines']!r}, expected 2 lines")
        swipe = page.evaluate("() => t('zoom_hint_swipe')")
        hint = page.inner_text("#viewerHint")
        if swipe == "zoom_hint_swipe" or swipe not in hint:
            fails.append(f"viewer with two reports: hint {hint!r} does not mention swiping")
        page.evaluate("() => closeViewer()")
        detail(pair[0], [pair[0]])
        page.evaluate("() => openViewer(detailList[0])")
        if swipe in page.inner_text("#viewerHint"):
            fails.append("viewer with one report: the hint offers a swipe that goes nowhere")
        page.evaluate("() => closeViewer()")
        fails += error_failures(errors, "report status copy")
    except Exception as error:
        fails.append(f"flow broke: {str(error)[:300]}")
    finally:
        browser.close()

if fails:
    print("FAIL photo report status copy")
    for failure in fails:
        print(" -", failure)
    sys.exit(1)
print("PASS photo report status copy")
