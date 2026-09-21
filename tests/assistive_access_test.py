# -*- coding: utf-8 -*-
"""The app works for a tester on TalkBack, a keyboard or switch access, and in sunlight.

Each case was measured on a phone-sized viewport:
  - pixels-a11y-2: the Drive Stop buttons were white on #ff5d5d, 3.01:1 at 17 px bold;
  - pixels-a11y-3: a disabled Drive or Photo button drew byte for byte like an enabled one;
  - pixels-a11y-4: no screen change moved focus (it fell to body), and the photo check,
    the Drive count and status, and the banner were silent to a screen reader;
  - pixels-a11y-5: report cards and drive groups were click-only divs, so Tab went from
    the Home actions straight to body, and the detail photo was the only way into the
    viewer with no name and no focus;
  - pixels-a11y-6: the viewer's close button was an unnamed 44x43 "x", the viewer was
    not a dialog, and the report arrows were named by their glyphs.
"""

import sys
import time

from playwright.sync_api import sync_playwright

from flow_harness import error_failures, open_flow


PIXEL_GIF = "data:image/gif;base64,R0lGODlhAQABAIAAAAAAAP///ywAAAAAAQABAAACAUwAOw=="

SEED = r"""
async ([now, photo]) => {
  const db = await new Promise((resolve, reject) => {
    const request = indexedDB.open("potholes");
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  await new Promise((resolve, reject) => {
    const tx = db.transaction(["drives", "reports"], "readwrite");
    const base = { status: "draft", photo_url: photo, is_pothole: true, assessment: "damaged",
                   damage_type: "pothole_cavity", size: "medium" };
    tx.objectStore("reports").put({ ...base, lat: 12.9716, lng: 77.5946, created_at: now,
                                    address: "First Test Road, Bengaluru" });
    tx.objectStore("reports").put({ ...base, lat: 12.9816, lng: 77.6046, created_at: now - 60,
                                    address: "Second Test Road, Bengaluru" });
    tx.objectStore("drives").put({ id: "a11y-drive", started_at: now - 600, ended_at: now - 300,
                                   checked: 4 });
    tx.objectStore("reports").put({ ...base, lat: 12.9916, lng: 77.6146, created_at: now - 500,
                                    drive_id: "a11y-drive", address: "Drive Test Road" });
    tx.oncomplete = resolve;
    tx.onerror = () => reject(tx.error);
  });
  db.close();
}
"""

CONTRAST = """
  const rgb = (value) => (value.match(/[\\d.]+/g) || []).map(Number);
  const lum = ([r, g, b]) => {
    const f = (c) => { c /= 255; return c <= .03928 ? c / 12.92 : Math.pow((c + .055) / 1.055, 2.4); };
    return .2126 * f(r) + .7152 * f(g) + .0722 * f(b);
  };
  const ratio = (a, b) => {
    const [x, y] = [lum(rgb(a)), lum(rgb(b))].sort((p, q) => q - p);
    return (x + .05) / (y + .05);
  };
"""

LIVE = """(id) => {
  for (let el = document.getElementById(id); el; el = el.parentElement) {
    if (el.getAttribute('aria-live') || ['status', 'alert'].includes(el.getAttribute('role'))) {
      return el.getAttribute('aria-live') || el.getAttribute('role');
    }
  }
  return null;
}"""

failures = []


def check(ok, message):
    if not ok:
        failures.append(message)


def home_ready(page):
    page.wait_for_function(
        "() => !document.getElementById('home').classList.contains('i18n-pending')")


with sync_playwright() as p:
    browser, page, errors = open_flow(p, native=False, storage={"feedback_nudged": "1"})
    try:
        page.set_viewport_size({"width": 412, "height": 915})
        home_ready(page)
        page.evaluate(SEED, [int(time.time() * 1000), PIXEL_GIF])
        page.reload()
        home_ready(page)
        page.wait_for_function("document.querySelectorAll('#list [data-id]').length >= 2",
                               timeout=15_000)

        # pixels-a11y-3: a disabled control looks disabled.
        before = page.locator("#driveBtn").screenshot()
        page.evaluate("document.getElementById('driveBtn').disabled = true")
        after = page.locator("#driveBtn").screenshot()
        opacity = page.evaluate("parseFloat(getComputedStyle(document.getElementById('driveBtn')).opacity)")
        check(before != after and opacity < 1,
              f"pixels-a11y-3: disabled Drive looks enabled (opacity {opacity}, "
              f"identical: {before == after})")
        page.evaluate("document.getElementById('driveBtn').disabled = false")

        # pixels-a11y-4: the busy and counting text is announced.
        live = {id: page.evaluate(LIVE, id) for id in
                ("progressText", "driveCount", "driveStatus", "nativeDriveCount",
                 "nativeDriveStatus", "banner")}
        silent = [id for id, value in live.items() if not value]
        check(not silent, f"pixels-a11y-4: silent to a screen reader: {silent}")
        check(live["banner"] == "alert", f"pixels-a11y-4: #banner is {live['banner']!r}, not an alert")

        # pixels-a11y-5: Tab reaches every report card and the drive group.
        page.focus("#gearBtn")
        reached = []
        for _ in range(12):
            page.keyboard.press("Tab")
            reached.append(page.evaluate("""() => {
              const el = document.activeElement;
              const card = el.closest('[data-id]');
              if (card) return 'card:' + card.dataset.id;
              if (el.matches('[data-drive]')) return 'drive:' + el.dataset.drive;
              return el.id || el.tagName;
            }"""))
        cards = {r for r in reached if r.startswith("card:")}
        check(len(cards) >= 2, f"pixels-a11y-5: Tab skipped the report cards: {reached}")
        check("drive:a11y-drive" in reached, f"pixels-a11y-5: Tab skipped the drive group: {reached}")
        head = page.evaluate("""() => {
          const el = document.querySelector('[data-drive="a11y-drive"]');
          return { expanded: el.getAttribute('aria-expanded'), tag: el.tagName };
        }""")
        check(head["expanded"] == "false", f"pixels-a11y-5: drive group has no aria-expanded: {head}")
        page.focus('[data-drive="a11y-drive"]')
        page.keyboard.press("Enter")
        expanded = page.evaluate(
            "document.querySelector('[data-drive=\"a11y-drive\"]').getAttribute('aria-expanded')")
        check(expanded == "true", f"pixels-a11y-5: Enter did not expand the drive group ({expanded})")
        name = page.evaluate("""() => {
          const el = document.querySelector('#list [data-id]');
          const target = el.matches('button') ? el : el.querySelector('button:not([data-quick-email])');
          return target ? (target.getAttribute('aria-label') || target.textContent).trim() : '';
        }""")
        check("Test Road" in name, f"pixels-a11y-5: a report card has no useful name: {name!r}")

        opener = "#list [data-id] button:not([data-quick-email])"
        check(page.locator(opener).count() > 0,
              "pixels-a11y-5: a report card has no focusable control to open it")
        # Enter on a focused card opens the report, and focus moves into it (pixels-a11y-4).
        if page.locator(opener).count():
            page.focus(opener)
            page.keyboard.press("Enter")
        else:
            page.locator("#list [data-id]").first.click()
        page.wait_for_function("() => !document.getElementById('detail').classList.contains('hidden')")
        page.wait_for_timeout(200)
        inside = page.evaluate("document.getElementById('detail').contains(document.activeElement)")
        check(inside, "pixels-a11y-4: focus stayed behind after opening a report: "
              + page.evaluate("document.activeElement.tagName + '#' + document.activeElement.id"))

        # pixels-a11y-5 and -6: the photo is a named control and the viewer a dialog.
        zoom = page.evaluate("""() => {
          const el = document.querySelector('#detail [data-zoom]');
          const img = document.querySelector('#detail .big-photo');
          return { tag: el.tagName, name: el.getAttribute('aria-label') || '', alt: img.alt };
        }""")
        check(zoom["tag"] == "BUTTON" and zoom["name"],
              f"pixels-a11y-5: the full-size photo control is not a named button: {zoom}")
        check(bool(zoom["alt"]), f"pixels-a11y-5: the detail photo has no alt text: {zoom}")
        arrows = page.evaluate("""() => ['navPrev', 'navNext'].map((id) => {
          const el = document.getElementById(id);
          if (!el) return [id, null, 0];
          return [id, el.getAttribute('aria-label'), Math.round(el.getBoundingClientRect().width)];
        })""")
        check(all(label and width >= 48 for _, label, width in arrows),
              f"pixels-a11y-6: report arrows are unnamed or narrow: {arrows}")
        if zoom["tag"] != "BUTTON":
            page.evaluate("document.querySelector('#detail [data-zoom]').click()")
        else:
            page.focus("#detail [data-zoom]")
            page.keyboard.press("Enter")
        page.wait_for_function("() => !document.getElementById('viewer').classList.contains('hidden')")
        viewer = page.evaluate("""() => {
          const v = document.getElementById('viewer');
          const c = document.getElementById('viewerClose');
          const r = c.getBoundingClientRect();
          return { role: v.getAttribute('role'), modal: v.getAttribute('aria-modal'),
                   label: v.getAttribute('aria-label') || v.getAttribute('aria-labelledby'),
                   closeName: c.getAttribute('aria-label') || '', w: r.width, h: r.height,
                   focused: document.activeElement === c };
        }""")
        check(viewer["role"] == "dialog" and viewer["modal"] == "true" and viewer["label"],
              f"pixels-a11y-6: the photo viewer is not a labelled modal dialog: {viewer}")
        check(viewer["closeName"] and viewer["w"] >= 48 and viewer["h"] >= 48,
              f"pixels-a11y-6: viewer close is unnamed or under 48 px: {viewer}")
        check(viewer["focused"], f"pixels-a11y-6: focus did not move to viewer close: {viewer}")
        page.keyboard.press("Enter")
        back = page.evaluate("""() => document.getElementById('viewer').classList.contains('hidden')
          && !!document.activeElement.closest('#detail [data-zoom]')""")
        check(back, "pixels-a11y-6: closing the viewer did not return focus to the photo")

        # pixels-a11y-2: the Stop button reads at a glance.
        page.evaluate("closeViewer(); show('home')")
        page.click("#driveBtn")
        page.wait_for_function("() => !document.getElementById('drive').classList.contains('hidden')")
        stops = page.evaluate(CONTRAST + """
          return ['driveStop', 'nativeDriveStop'].map((id) => {
            const s = getComputedStyle(document.getElementById(id));
            return [id, Math.round(ratio(s.color, s.backgroundColor) * 100) / 100];
          });""".join(["() => {", "}"]))
        low = [pair for pair in stops if pair[1] < 4.5]
        check(not low, f"pixels-a11y-2: Stop text contrast under 4.5:1: {low}")
        failures.extend(error_failures(errors, "assistive access"))
    finally:
        browser.close()

if failures:
    print("FAIL")
    for failure in failures:
        print(" -", failure)
    sys.exit(1)
print("PASS: named, reachable, announced controls and a readable Stop")
